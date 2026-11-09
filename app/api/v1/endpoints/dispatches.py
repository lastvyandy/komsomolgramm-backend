import logging
from datetime import timezone
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload, undefer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, SovietMember, SovietReadCursor, Dispatch, DispatchAttachment, DispatchReaction, DispatchForward, DispatchHidden, AttachmentRecipient, DispatchChoice, utc_now
from app.schemas.dispatch import AttachmentOut, DispatchCreate, QueuedDispatchCreate, DispatchOut, ReplyOut, ReactionCount, ForwardMessages, DeleteMessages
from app.api.v1.endpoints.ws import get_socket_manager
from app.services.councils import require_council_publisher
from app.services.replies import reply_summaries, require_reply

from app.services.media_relay import admit_media, register_recipients

router = APIRouter()

from app.schemas.rich_message import RichCreate, ChoiceIn

async def rich_message(db, chat_id, dispatch_id, current):
    await require_membership(db, chat_id, current)
    message = await db.scalar(select(Dispatch).where(
        Dispatch.id == dispatch_id, Dispatch.soviet_id == chat_id,
        Dispatch.is_purged.is_(False), visible_to(current.id)))
    if message is None or not message.rich:
        raise HTTPException(404, "Rich message not found")
    return message

@router.post("/{soviet_id}/rich", response_model=DispatchOut, status_code=201)
async def send_rich(soviet_id: uuid.UUID, payload: RichCreate,
                    current: Comrade = Depends(get_current_comrade),
                    db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    await require_council_publisher(db, soviet_id, current.id)
    try:
        identifier = uuid.UUID(payload.client_id)
        reply_to = uuid.UUID(payload.reply_to_id) if payload.reply_to_id else None
    except ValueError:
        raise HTTPException(422, "Invalid message identifier")
    existing = await db.scalar(select(Dispatch).options(
        selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(Dispatch.id == identifier))
    if existing is not None:
        if existing.soviet_id != soviet_id or existing.sender_id != current.id:
            raise HTTPException(409, "Identifier already used")
        return DispatchOut.model_validate(existing)
    reply_id, reply = await require_reply(db, soviet_id, reply_to)
    data = payload.rich.model_dump(mode="json")
    kind = data["kind"]
    if kind == "location":
        text = f"📍 {data['label']}\nhttps://yandex.ru/maps/?pt={data['longitude']},{data['latitude']}&z=16"
    elif kind == "contact":
        text = f"👤 {data['name']}\n{data['phone']}"
    elif kind == "app_contact":
        contact = await db.get(Comrade, uuid.UUID(data["user_id"]))
        if contact is None or not contact.is_active:
            raise HTTPException(404, "Application contact unavailable")
        data["name"] = contact.callsign
        data["username"] = contact.username
        text = f"👤 {contact.callsign} · @{contact.username}"
    elif kind == "poll":
        text = "📊 " + data["question"] + "\n" + "\n".join(data["options"])
    else:
        text = f"📅 {data['title']}\n{data['starts_at']}\n{data['place']}"
    dispatch = Dispatch(id=identifier, soviet_id=soviet_id, sender=current,
                        content=text, rich=data, reply_to_id=reply_id)
    db.add(dispatch)
    await db.flush()
    # Explicit relationships avoid asynchronous lazy loading during response serialization.
    dispatch.attachment = None
    dispatch.forwarded = None
    result = DispatchOut.model_validate(dispatch).model_copy(update={
        "reply": ReplyOut.model_validate(reply) if reply else None})
    await db.commit()
    event = result.model_dump(mode="json", exclude={"sender", "is_purged"})
    event.update(type="dispatch", sender_id=str(current.id), sender_callsign=current.callsign,
                 sender_username=current.username, bubble_color=current.bubble_color)
    try:
        await get_socket_manager().publish_dispatch(soviet_id, event)
    except Exception:
        logger.exception("Rich message saved but live delivery failed")
    return result

@router.get("/{soviet_id}/rich/{dispatch_id}/responses")
async def rich_responses(soviet_id: uuid.UUID, dispatch_id: uuid.UUID,
                         current: Comrade = Depends(get_current_comrade),
                         db: AsyncSession = Depends(get_async_db)):
    message = await rich_message(db, soviet_id, dispatch_id, current)
    kind = message.rich["kind"]
    if kind not in ("poll", "event"):
        raise HTTPException(422, "Message does not accept responses")
    rows = (await db.scalars(select(DispatchChoice).join(
        SovietMember, (SovietMember.comrade_id == DispatchChoice.comrade_id) &
                      (SovietMember.soviet_id == soviet_id)).where(
        DispatchChoice.dispatch_id == dispatch_id))).all()
    count = len(message.rich["options"]) if kind == "poll" else 3
    totals = [0] * count
    mine = []
    voters = 0
    for row in rows:
        choices = sorted({v for v in row.choices if 0 <= v < count})
        if choices:
            voters += 1
        for value in choices:
            totals[value] += 1
        if row.comrade_id == current.id:
            mine = choices
    return {"counts": totals, "my_choices": mine, "participants": voters}

@router.put("/{soviet_id}/rich/{dispatch_id}/responses")
async def respond_rich(soviet_id: uuid.UUID, dispatch_id: uuid.UUID, payload: ChoiceIn,
                       current: Comrade = Depends(get_current_comrade),
                       db: AsyncSession = Depends(get_async_db)):
    message = await rich_message(db, soviet_id, dispatch_id, current)
    data = message.rich
    count = len(data["options"]) if data["kind"] == "poll" else 3 if data["kind"] == "event" else 0
    choices = sorted(set(payload.choices))
    if not count or any(value < 0 or value >= count for value in choices):
        raise HTTPException(422, "Invalid response")
    if (data["kind"] == "event" or not data.get("multiple", False)) and len(choices) > 1:
        raise HTTPException(422, "Only one response allowed")
    statement = insert(DispatchChoice).values(
        dispatch_id=dispatch_id, comrade_id=current.id, choices=choices)
    await db.execute(statement.on_conflict_do_update(
        index_elements=[DispatchChoice.dispatch_id, DispatchChoice.comrade_id],
        set_={"choices": choices}))
    await db.commit()
    return await rich_responses(soviet_id, dispatch_id, current, db)

@router.get("/pending-media")
async def pending_media(include_audio: bool = True,
                        current: Comrade = Depends(get_current_comrade),
                        db: AsyncSession = Depends(get_async_db)):
    statement = select(DispatchAttachment, Dispatch.soviet_id).join(
        Dispatch, Dispatch.id == DispatchAttachment.dispatch_id).join(
        AttachmentRecipient, AttachmentRecipient.dispatch_id == Dispatch.id).join(
        SovietMember, (SovietMember.soviet_id == Dispatch.soviet_id) &
                      (SovietMember.comrade_id == current.id)).where(
        AttachmentRecipient.comrade_id == current.id,
        AttachmentRecipient.downloaded.is_(False),
        DispatchAttachment.expires_at > utc_now(),
        DispatchAttachment.data.is_not(None),
        Dispatch.is_purged.is_(False), DispatchAttachment.view_once.is_(False), visible_to(current.id))
    if not include_audio:
        statement = statement.where(~DispatchAttachment.mime_type.startswith("audio/"))
    rows = (await db.execute(statement.order_by(Dispatch.created_at).limit(100))).all()
    return [{"chat_id": str(chat_id), "attachment": AttachmentOut.model_validate(attachment)}
            for attachment, chat_id in rows]

@router.post("/{soviet_id}/attachments/{dispatch_id}/received", status_code=204)
async def media_received(soviet_id: uuid.UUID, dispatch_id: uuid.UUID,
                         current: Comrade = Depends(get_current_comrade),
                         db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    attachment = await db.scalar(select(DispatchAttachment).join(Dispatch).where(
        DispatchAttachment.dispatch_id == dispatch_id, Dispatch.soviet_id == soviet_id).with_for_update())
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    if attachment.view_once:
        raise HTTPException(409,"Use the one-time viewer")
    recipient = await db.get(AttachmentRecipient, (dispatch_id, current.id))
    if recipient is not None:
        recipient.downloaded = True
        await db.flush()
        pending = await db.scalar(select(func.count()).select_from(AttachmentRecipient).where(
            AttachmentRecipient.dispatch_id == dispatch_id, AttachmentRecipient.downloaded.is_(False)))
        if not pending:
            attachment.data = None
    await db.commit()
    return Response(status_code=204)

@router.get("/{soviet_id}/messages/{dispatch_id}", response_model=DispatchOut)
async def message_metadata(soviet_id: uuid.UUID, dispatch_id: uuid.UUID,
                           current: Comrade = Depends(get_current_comrade),
                           db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    message = await db.scalar(select(Dispatch).options(
        selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(
        Dispatch.id == dispatch_id, Dispatch.soviet_id == soviet_id,
        Dispatch.is_purged.is_(False), visible_to(current.id)))
    if message is None:
        raise HTTPException(404, "Message not found")
    from app.services.view_once import annotate_opened
    await annotate_opened(db,[message],current.id)
    return message


@router.get("/{soviet_id}", response_model=list[DispatchOut])
async def get_soviet_dispatches(
    soviet_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    before_id: uuid.UUID | None = Query(default=None),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Obtiene el historial de despachos de un Soviet asegurando que el solicitante sea miembro."""
    # 1. Validar membresía
    membership_query = select(SovietMember).where(
        SovietMember.soviet_id == soviet_id,
        SovietMember.comrade_id == current_comrade.id
    )
    mem_res = await db.execute(membership_query)
    if not mem_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. No posee credenciales para inspeccionar este archivo."
        )

    # 2. Consultar despachos ordenados por fecha ascendente para el cliente móvil
    conditions = [Dispatch.soviet_id == soviet_id, Dispatch.is_purged.is_(False), visible_to(current_comrade.id)]
    if before_id is not None:
        cursor = await db.scalar(select(Dispatch).where(
            Dispatch.id == before_id,
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged.is_(False),
        ))
        if cursor is None:
            raise HTTPException(404, "History cursor not found")
        conditions.append(tuple_(Dispatch.created_at, Dispatch.id) < tuple_(cursor.created_at, cursor.id))
        offset = 0
    stmt = (
        select(Dispatch)
        .options(selectinload(Dispatch.sender), selectinload(Dispatch.attachment))
        .where(*conditions)
        .order_by(Dispatch.created_at.desc(), Dispatch.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    dispatches = result.scalars().all()
    from app.services.view_once import annotate_opened
    await annotate_opened(db,dispatches,current_comrade.id)

    # Revertir para entregar en orden cronológico (los más antiguos primero)
    replies = await reply_summaries(db, soviet_id, {m.reply_to_id for m in dispatches if m.reply_to_id})
    message_ids = [message.id for message in dispatches]
    reaction_counts: dict[uuid.UUID, dict[str, int]] = {}
    my_reactions: dict[uuid.UUID, str] = {}
    if message_ids:
        reactions = (await db.execute(select(DispatchReaction).where(
            DispatchReaction.dispatch_id.in_(message_ids)
        ))).scalars().all()
        for reaction in reactions:
            by_emoji = reaction_counts.setdefault(reaction.dispatch_id, {})
            by_emoji[reaction.emoji] = by_emoji.get(reaction.emoji, 0) + 1
            if reaction.comrade_id == current_comrade.id:
                my_reactions[reaction.dispatch_id] = reaction.emoji

    result = []
    for message in reversed(dispatches):
        result.append(DispatchOut.model_validate(message).model_copy(update={
            "reply": ReplyOut.model_validate(replies[message.reply_to_id]) if message.reply_to_id in replies else None,
            "reactions": [
                ReactionCount(stamp=stamp, count=count)
                for stamp, count in sorted(
                    reaction_counts.get(message.id, {}).items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ],
            "my_reaction": my_reactions.get(message.id),
        }))
    return result

@router.put("/{soviet_id}/read/{dispatch_id}", status_code=204)
async def mark_dispatch_read(
    soviet_id: uuid.UUID,
    dispatch_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    membership_id = await db.scalar(
        select(SovietMember.id).where(
            SovietMember.soviet_id == soviet_id,
            SovietMember.comrade_id == current_comrade.id,
        )
    )
    if membership_id is None:
        raise HTTPException(403, "Chat membership required")

    message_created_at = await db.scalar(
        select(Dispatch.created_at).where(
            Dispatch.id == dispatch_id,
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged.is_(False),
        )
    )
    if message_created_at is None:
        raise HTTPException(404, "Message not found in this chat")

    statement = insert(SovietReadCursor).values(
        membership_id=membership_id,
        message_created_at=message_created_at,
        message_id=dispatch_id,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[SovietReadCursor.membership_id],
        set_={
            "message_created_at": statement.excluded.message_created_at,
            "message_id": statement.excluded.message_id,
        },
        where=tuple_(SovietReadCursor.message_created_at, SovietReadCursor.message_id) <
            tuple_(statement.excluded.message_created_at, statement.excluded.message_id),
    ))
    await db.commit()
    cursor = await db.get(SovietReadCursor, membership_id)
    from app.services.privacy import audience
    from app.models.models import Contact
    rule=audience(current_comrade,"read_receipts")
    allowed=None
    if rule!="all":
        allowed=[str(current_comrade.id)]
        if rule=="contacts":
            allowed += [str(p) for p in (await db.scalars(select(Contact.person_id).where(Contact.owner_id==current_comrade.id))).all()]
    try:
        await get_socket_manager().publish_dispatch(soviet_id, {
            "type": "read", "reader_id": str(current_comrade.id), "allowed_viewers": allowed,
            "message_id": str(cursor.message_id), "message_created_at": cursor.message_created_at.isoformat(),
        })
    except Exception:
        logger.exception("Read cursor stored but live delivery failed")
    return Response(status_code=204)



@router.post("/{soviet_id}/queued-messages", response_model=DispatchOut, status_code=201)
async def create_queued_dispatch(soviet_id: uuid.UUID, payload: QueuedDispatchCreate,
                                 current: Comrade = Depends(get_current_comrade),
                                 db: AsyncSession = Depends(get_async_db)):
    """Persist a device-generated UUID once, including concurrent retries."""
    owner = current.id
    await require_membership(db, soviet_id, current)
    await require_council_publisher(db, soviet_id, current.id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "Message cannot be empty")

    async def existing_result():
        message = await db.scalar(select(Dispatch).options(
            selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(Dispatch.id == payload.client_id))
        if message is None:
            return None
        if message.sender_id != owner or message.soviet_id != soviet_id:
            raise HTTPException(409, "Identifier already used")
        replies = await reply_summaries(db, soviet_id, {message.reply_to_id} if message.reply_to_id else set())
        return DispatchOut.model_validate(message).model_copy(update={
            "reply": ReplyOut.model_validate(replies[message.reply_to_id]) if message.reply_to_id in replies else None})

    existing = await existing_result()
    if existing is not None:
        return existing
    reply_id, reply = await require_reply(db, soviet_id, payload.reply_to_id)
    message = Dispatch(id=payload.client_id, soviet_id=soviet_id, sender=current, content=content, reply_to_id=reply_id)
    db.add(message)
    try:
        await db.flush()
        message.attachment = None
        message.forwarded = None
        result = DispatchOut.model_validate(message).model_copy(update={
            "reply": ReplyOut.model_validate(reply) if reply else None})
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await existing_result()
        if existing is not None:
            return existing
        raise
    event = result.model_dump(mode="json", exclude={"sender", "is_purged"})
    event.update(type="dispatch", sender_id=str(owner), sender_callsign=result.sender.callsign,
                 sender_username=result.sender.username, bubble_color=result.sender.bubble_color)
    try:
        await get_socket_manager().publish_dispatch(soviet_id, event)
    except Exception:
        logger.exception("Queued message saved but live delivery failed")
    return result


@router.post("/{soviet_id}/messages", status_code=204)
async def create_text_dispatch(
    soviet_id: uuid.UUID,
    payload: DispatchCreate,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a text dispatch for quick replies and other authenticated clients."""
    await require_membership(db, soviet_id, current_comrade)
    await require_council_publisher(db, soviet_id, current_comrade.id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "Message cannot be empty")

    message = Dispatch(soviet_id=soviet_id, sender=current_comrade, content=content)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    event = {
        "type": "dispatch",
        "id": str(message.id),
        "soviet_id": str(soviet_id),
        "sender_id": str(current_comrade.id),
        "sender_username": current_comrade.username,
        "sender_callsign": current_comrade.callsign,
        "bubble_color": current_comrade.bubble_color,
        "content": content,
        "created_at": message.created_at.isoformat(),
        "reply_to_id": None,
        "reply": None,
    }
    try:
        await get_socket_manager().publish_dispatch(soviet_id, event)
    except Exception:
        logger.exception("Message %s saved but live delivery failed", message.id)
    return Response(status_code=204)


@router.patch("/{soviet_id}/messages/{dispatch_id}", response_model=DispatchOut)
async def edit_dispatch(
    soviet_id: uuid.UUID,
    dispatch_id: uuid.UUID,
    payload: DispatchCreate,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
    await require_council_publisher(db, soviet_id, current_comrade.id)
    statement = (
        select(Dispatch)
        .options(selectinload(Dispatch.sender), selectinload(Dispatch.attachment))
        .where(
            Dispatch.id == dispatch_id,
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged.is_(False),
        )
    )
    dispatch = (await db.execute(statement)).scalar_one_or_none()
    if dispatch is None:
        raise HTTPException(404, "Message not found")
    if dispatch.sender_id != current_comrade.id:
        raise HTTPException(403, "Only the author can edit this message")
    if dispatch.rich is not None:
        raise HTTPException(422, "Structured messages cannot be edited as plain text")
    has_attachment = await db.scalar(
        select(DispatchAttachment.dispatch_id).where(DispatchAttachment.dispatch_id == dispatch_id)
    )
    if has_attachment is not None:
        raise HTTPException(400, "Media captions cannot be edited yet")

    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "Message cannot be empty")
    dispatch.content = content
    result = DispatchOut.model_validate(dispatch)
    await db.commit()

    try:
        await get_socket_manager().publish_dispatch(soviet_id, {
            "type": "edit_dispatch",
            "id": str(dispatch.id),
            "sender_id": str(current_comrade.id),
            "content": content,
        })
    except Exception:
        logger.exception("Message %s saved but edit notification failed", dispatch.id)
    return result


@router.delete("/{soviet_id}/messages/{dispatch_id}", status_code=204)
async def delete_dispatch(
    soviet_id: uuid.UUID,
    dispatch_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
    await require_council_publisher(db, soviet_id, current_comrade.id)
    statement = (
        select(Dispatch)
        .where(
            Dispatch.id == dispatch_id,
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged.is_(False),
        )
    )
    dispatch = (await db.execute(statement)).scalar_one_or_none()
    if dispatch is None:
        raise HTTPException(404, "Message not found")
    if dispatch.sender_id != current_comrade.id:
        raise HTTPException(403, "Only the author can delete this message")

    await db.execute(
        delete(DispatchAttachment).where(DispatchAttachment.dispatch_id == dispatch_id)
    )
    dispatch.is_purged = True
    message_id = str(dispatch.id)
    await db.commit()

    try:
        await get_socket_manager().publish_dispatch(soviet_id, {
            "type": "delete_dispatch",
            "id": message_id,
            "sender_id": str(current_comrade.id),
        })
    except Exception:
        logger.exception("Message %s deleted but delete notification failed", message_id)
    return Response(status_code=204)


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ATTACHMENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif",
    "video/mp4", "video/webm", "video/3gpp", "video/quicktime",
    "audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/opus",
    "application/pdf", "application/zip", "application/octet-stream",
    "application/vnd.android.package-archive", "text/plain", "text/csv",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "audio/wav", "audio/x-wav", "audio/flac", "audio/webm", "audio/3gpp",
}
logger = logging.getLogger(__name__)


async def require_membership(db: AsyncSession, soviet_id: uuid.UUID, comrade: Comrade):
    membership = await db.scalar(select(SovietMember.id).where(
        SovietMember.soviet_id == soviet_id, SovietMember.comrade_id == comrade.id,
    ).limit(1))
    if membership is None:
        raise HTTPException(403, "Chat membership required")


def attachment_query(soviet_id: uuid.UUID, viewer_id: uuid.UUID):
    return select(DispatchAttachment).join(Dispatch).where(
        Dispatch.soviet_id == soviet_id, Dispatch.is_purged.is_(False), visible_to(viewer_id),
        DispatchAttachment.view_once.is_(False),
    )


@router.post("/{soviet_id}/attachments", response_model=DispatchOut, status_code=201)
async def send_attachment(
    soviet_id: uuid.UUID,
    request: Request,
    name: str = Query(min_length=1, max_length=180),
    reply_to_id: uuid.UUID | None = Query(default=None),
    is_sticker: bool = Query(default=False),
    is_round_video: bool = Query(default=False),
    view_once: bool = Query(default=False),
    forward_from_id: uuid.UUID | None = Query(default=None),
    client_id: uuid.UUID | None = Query(default=None),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    if request.headers.get("X-Media-Storage") != "device-v1":
        raise HTTPException(426, "Update the app before sending device-owned media")
    await require_membership(db, soviet_id, current_comrade)
    await require_council_publisher(db, soviet_id, current_comrade.id)
    if client_id is not None:
        existing = await db.scalar(select(Dispatch).options(
            selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(Dispatch.id == client_id))
        if existing is not None:
            if existing.sender_id != current_comrade.id or existing.soviet_id != soviet_id:
                raise HTTPException(409, "Client message identifier already used")
            return DispatchOut.model_validate(existing)
    reply_id, reply = await require_reply(db, soviet_id, reply_to_id)
    mime_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    document_type = re.fullmatch(r"(?:application|text)/[a-z0-9][a-z0-9.+_-]{0,66}", mime_type)
    if mime_type not in ATTACHMENT_TYPES and not document_type:
        raise HTTPException(415, "Unsupported attachment type")
    if view_once:
        if mime_type not in ("image/jpeg","image/png","image/webp") or is_sticker or is_round_video:
            raise HTTPException(422,"View once supports still photos only")
        recipients=await db.scalar(select(func.count()).select_from(SovietMember).where(
            SovietMember.soviet_id==soviet_id,SovietMember.comrade_id!=current_comrade.id))
        if not recipients:raise HTTPException(422,"Select a recipient for view once")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "Attachment exceeds 20 MiB")
        data.extend(chunk)
    if not data:
        raise HTTPException(400, "Empty attachment")

    if is_sticker and (mime_type != "image/webp" or len(data) > 512 * 1024 or
                       bytes(data[:4]) != b"RIFF" or bytes(data[8:12]) != b"WEBP"):
        raise HTTPException(422, "Sticker must be a WebP file up to 512 KiB")
    if is_round_video and (mime_type != "video/mp4" or is_sticker or
            len(data) < 12 or bytes(data[4:8]) != b"ftyp"):
        raise HTTPException(422, "Video messages must be MP4 files")
    await admit_media(db, len(data))
    forwarded = None
    if forward_from_id is not None:
        source = await db.scalar(select(Dispatch).options(
            selectinload(Dispatch.sender), selectinload(Dispatch.forwarded)).where(
            Dispatch.id == forward_from_id, Dispatch.is_purged.is_(False), visible_to(current_comrade.id)))
        if source is None:
            raise HTTPException(404, "Forward source unavailable")
        await require_membership(db, source.soviet_id, current_comrade)
        source_attachment=await db.get(DispatchAttachment,source.id)
        if source_attachment and source_attachment.view_once:
            raise HTTPException(403,"View once media cannot be forwarded")
        forwarded = DispatchForward(sender_name=source.forwarded.sender_name if source.forwarded else
                                    source.sender.callsign if source.sender else "Camarada")
    attachment = DispatchAttachment(name=name, mime_type=mime_type, data=bytes(data), is_sticker=is_sticker, is_round_video=is_round_video, view_once=view_once)
    dispatch = Dispatch(
        id=client_id or uuid.uuid4(),
        soviet_id=soviet_id, sender=current_comrade, attachment=attachment, reply_to_id=reply_id, forwarded=forwarded,
        content="Sticker" if is_sticker else {"image": "Фото", "video": "Видео", "audio": "Аудио"}.get(mime_type.split("/")[0], "Файл"),
    )
    db.add(dispatch)
    await db.flush()
    result = DispatchOut.model_validate(dispatch).model_copy(update={"reply": ReplyOut.model_validate(reply) if reply else None})
    await register_recipients(db, dispatch)
    await db.commit()

    event = result.model_dump(mode="json", exclude={"sender", "is_purged"})
    event.update(
        type="dispatch", sender_id=str(result.sender.id),
        sender_callsign=result.sender.callsign, sender_username=result.sender.username,
        bubble_color=result.sender.bubble_color,
    )
    try:
        await get_socket_manager().publish_dispatch(soviet_id, event)
    except Exception:
        logger.exception("Attachment %s saved but live delivery failed", result.id)
    return result


@router.get("/{soviet_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    soviet_id: uuid.UUID,
    kind: str = Query(default="media", pattern="^(media|gif|audio)$"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
    mime_type = DispatchAttachment.mime_type
    filters = {
        "media": (mime_type.startswith("image/") & (mime_type != "image/gif")) | mime_type.startswith("video/"),
        "gif": mime_type == "image/gif",
        "audio": mime_type.startswith("audio/"),
    }
    statement = attachment_query(soviet_id, current_comrade.id).where(filters[kind]).order_by(
        Dispatch.created_at.desc(), Dispatch.id.desc(),
    ).offset(offset).limit(limit)
    return (await db.scalars(statement)).all()


@router.get("/{soviet_id}/attachments/{dispatch_id}")
async def download_attachment(
    soviet_id: uuid.UUID,
    dispatch_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
    attachment = (await db.execute(
        attachment_query(soviet_id, current_comrade.id).with_only_columns(
            DispatchAttachment.data, DispatchAttachment.mime_type, DispatchAttachment.expires_at,
        ).where(DispatchAttachment.dispatch_id == dispatch_id)
    )).one_or_none()
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    expires = attachment.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if attachment.data is None or expires <= utc_now():
        raise HTTPException(410, "Media is no longer in temporary delivery storage")
    return Response(attachment.data, media_type=attachment.mime_type, headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": "attachment",
        "X-Content-Type-Options": "nosniff",
    })



@router.get("/{soviet_id}/readers")
async def read_receipts(soviet_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    rows = (await db.execute(select(SovietMember.comrade_id, SovietReadCursor.message_id, SovietReadCursor.message_created_at)
        .outerjoin(SovietReadCursor, SovietReadCursor.membership_id == SovietMember.id)
        .where(SovietMember.soviet_id == soviet_id, SovietMember.comrade_id != current.id))).all()
    from app.services.privacy import allows
    return [{"reader_id": str(person), "message_id": str(message) if message else None,
             "message_created_at": created.isoformat() if created else None} for person, message, created in rows
             if await allows(db,person,"read_receipts",current.id)]


@router.get("/{soviet_id}/context/{dispatch_id}", response_model=list[DispatchOut])
async def reply_context(soviet_id: uuid.UUID, dispatch_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    target = await db.scalar(select(Dispatch).where(Dispatch.id == dispatch_id, Dispatch.soviet_id == soviet_id, Dispatch.is_purged.is_(False), visible_to(current.id)))
    if target is None:
        raise HTTPException(404, "Message not found")
    base = select(Dispatch).options(selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(
        Dispatch.soviet_id == soviet_id, Dispatch.is_purged.is_(False), visible_to(current.id))
    before = (await db.scalars(base.where(tuple_(Dispatch.created_at, Dispatch.id) <= (target.created_at, target.id))
        .order_by(Dispatch.created_at.desc(), Dispatch.id.desc()).limit(26))).all()
    after = (await db.scalars(base.where(tuple_(Dispatch.created_at, Dispatch.id) > (target.created_at, target.id))
        .order_by(Dispatch.created_at, Dispatch.id).limit(25))).all()
    messages = list(reversed(before)) + list(after)
    replies = await reply_summaries(db, soviet_id, {m.reply_to_id for m in messages if m.reply_to_id})
    return [DispatchOut.model_validate(m).model_copy(update={"reply": ReplyOut.model_validate(replies[m.reply_to_id]) if m.reply_to_id in replies else None}) for m in messages]



def visible_to(person_id: uuid.UUID):
    return ~select(DispatchHidden.dispatch_id).where(
        DispatchHidden.dispatch_id == Dispatch.id, DispatchHidden.comrade_id == person_id,
    ).exists()


@router.post("/{soviet_id}/forward", response_model=list[DispatchOut], status_code=201)
async def forward_messages(soviet_id: uuid.UUID, payload: ForwardMessages,
                           current: Comrade = Depends(get_current_comrade),
                           db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    await require_membership(db, payload.target_chat_id, current)
    await require_council_publisher(db, payload.target_chat_id, current.id)
    ids = set(payload.message_ids)
    originals = (await db.scalars(select(Dispatch).options(
        selectinload(Dispatch.sender), selectinload(Dispatch.attachment),
    ).where(Dispatch.soviet_id == soviet_id, Dispatch.id.in_(ids),
            Dispatch.is_purged.is_(False), visible_to(current.id))
      .order_by(Dispatch.created_at, Dispatch.id))).all()
    if len(originals) != len(ids):
        raise HTTPException(404, "One or more source messages are unavailable")
    copies = []
    for original in originals:
        attachment = None
        if original.attachment is not None:
            raise HTTPException(409, "Forward media from its local device copy")
        copy = Dispatch(soviet_id=payload.target_chat_id, sender=current,
            content=original.content, rich=original.rich, attachment=attachment,
            forwarded=DispatchForward(sender_name=original.forwarded.sender_name if original.forwarded
                else original.sender.callsign if original.sender else "Camarada"))
        db.add(copy)
        copies.append(copy)
    await db.flush()
    results = [DispatchOut.model_validate(message) for message in copies]
    await db.commit()
    for result in results:
        event = result.model_dump(mode="json", exclude={"sender", "is_purged"})
        event.update(type="dispatch", sender_id=str(current.id), sender_callsign=current.callsign,
            sender_username=current.username, bubble_color=current.bubble_color)
        try:
            await get_socket_manager().publish_dispatch(payload.target_chat_id, event)
        except Exception:
            logger.exception("Forward stored but live delivery failed")
    return results


@router.post("/{soviet_id}/delete-messages", status_code=204)
async def delete_messages(soviet_id: uuid.UUID, payload: DeleteMessages,
                          current: Comrade = Depends(get_current_comrade),
                          db: AsyncSession = Depends(get_async_db)):
    await require_membership(db, soviet_id, current)
    ids = set(payload.message_ids)
    messages = (await db.scalars(select(Dispatch).where(Dispatch.soviet_id == soviet_id,
        Dispatch.id.in_(ids), Dispatch.is_purged.is_(False)))).all()
    if len(messages) != len(ids):
        raise HTTPException(404, "One or more messages are unavailable")
    if payload.for_everyone:
        await require_council_publisher(db, soviet_id, current.id)
        if any(message.sender_id != current.id for message in messages):
            raise HTTPException(403, "Only your own messages can be deleted for everyone")
        await db.execute(delete(DispatchAttachment).where(DispatchAttachment.dispatch_id.in_(ids)))
        for message in messages:
            message.is_purged = True
    else:
        await db.execute(insert(DispatchHidden).values([
            {"dispatch_id": message_id, "comrade_id": current.id} for message_id in ids
        ]).on_conflict_do_nothing())
    await db.commit()
    if payload.for_everyone:
        for message_id in ids:
            try:
                await get_socket_manager().publish_dispatch(soviet_id, {
                    "type": "delete_dispatch", "id": str(message_id), "sender_id": str(current.id)})
            except Exception:
                logger.exception("Batch deletion stored but live delivery failed")
    return Response(status_code=204)



@router.post("/{soviet_id}/attachments/{dispatch_id}/open-once")
async def open_once(soviet_id:uuid.UUID,dispatch_id:uuid.UUID,
                    current:Comrade=Depends(get_current_comrade),db:AsyncSession=Depends(get_async_db)):
    await require_membership(db,soviet_id,current)
    row=(await db.execute(select(DispatchAttachment,Dispatch.sender_id).join(Dispatch,
        Dispatch.id==DispatchAttachment.dispatch_id).options(undefer(DispatchAttachment.data)).where(
        DispatchAttachment.dispatch_id==dispatch_id,Dispatch.soviet_id==soviet_id,
        Dispatch.is_purged.is_(False),visible_to(current.id)).with_for_update())).one_or_none()
    if row is None:raise HTTPException(404,"Photo not found")
    attachment,sender=row
    if not attachment.view_once:raise HTTPException(422,"Not view once media")
    if sender==current.id:raise HTTPException(403,"Sender cannot reopen view once media")
    recipient=await db.get(AttachmentRecipient,(dispatch_id,current.id))
    if recipient is None:raise HTTPException(403,"Not an original recipient")
    expires=attachment.expires_at
    if expires.tzinfo is None:expires=expires.replace(tzinfo=timezone.utc)
    if recipient.downloaded or attachment.data is None or expires<=utc_now():
        raise HTTPException(410,"Photo opened or expired")
    # Consume in the locked SQL transaction before returning bytes. Lost responses
    # cannot be retried: at-most-once access is intentional, including across devices.
    content=attachment.data
    claimed=await db.scalar(update(AttachmentRecipient).where(
        AttachmentRecipient.dispatch_id==dispatch_id,AttachmentRecipient.comrade_id==current.id,
        AttachmentRecipient.downloaded.is_(False)).values(downloaded=True).returning(AttachmentRecipient.dispatch_id))
    if claimed is None:raise HTTPException(410,"Photo already opened")
    await db.flush()
    remaining=await db.scalar(select(func.count()).select_from(AttachmentRecipient).where(
        AttachmentRecipient.dispatch_id==dispatch_id,AttachmentRecipient.downloaded.is_(False)))
    if not remaining:attachment.data=None
    await db.commit()
    from app.services.privacy import allows
    allowed=[str(current.id)]
    if sender and await allows(db,current.id,"read_receipts",sender):allowed.append(str(sender))
    try:
        await get_socket_manager().publish_dispatch(soviet_id,{
            "type":"view_once_opened","id":str(dispatch_id),"allowed_viewers":allowed})
    except Exception:logger.exception("View once consumed; receipt will synchronize from history")
    return Response(content,media_type=attachment.mime_type,headers={
        "Cache-Control":"no-store, max-age=0","Pragma":"no-cache","X-Content-Type-Options":"nosniff"})


@router.post("/{soviet_id}/view-once-photo",response_model=DispatchOut,status_code=201)
async def send_once_photo(soviet_id:uuid.UUID,request:Request,
                          name:str=Query(min_length=1,max_length=180),
                          reply_to_id:uuid.UUID|None=None,client_id:uuid.UUID|None=None,
                          current:Comrade=Depends(get_current_comrade),db:AsyncSession=Depends(get_async_db)):
    # A dedicated route fails closed against older servers that ignore unknown flags.
    return await send_attachment(soviet_id=soviet_id,request=request,name=name,reply_to_id=reply_to_id,
        is_sticker=False,is_round_video=False,view_once=True,forward_from_id=None,
        client_id=client_id,current_comrade=current,db=db)
