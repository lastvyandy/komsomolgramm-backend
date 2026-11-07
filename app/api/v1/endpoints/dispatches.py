import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, SovietMember, SovietReadCursor, Dispatch, DispatchAttachment, DispatchReaction, DispatchForward, DispatchHidden
from app.schemas.dispatch import AttachmentOut, DispatchCreate, DispatchOut, ReplyOut, ReactionCount, ForwardMessages, DeleteMessages
from app.api.v1.endpoints.ws import get_socket_manager
from app.services.replies import reply_summaries, require_reply

router = APIRouter()

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
    try:
        await get_socket_manager().publish_dispatch(soviet_id, {
            "type": "read", "reader_id": str(current_comrade.id),
            "message_id": str(cursor.message_id), "message_created_at": cursor.message_created_at.isoformat(),
        })
    except Exception:
        logger.exception("Read cursor stored but live delivery failed")
    return Response(status_code=204)


@router.post("/{soviet_id}/messages", status_code=204)
async def create_text_dispatch(
    soviet_id: uuid.UUID,
    payload: DispatchCreate,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a text dispatch for quick replies and other authenticated clients."""
    await require_membership(db, soviet_id, current_comrade)
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
    )


@router.post("/{soviet_id}/attachments", response_model=DispatchOut, status_code=201)
async def send_attachment(
    soviet_id: uuid.UUID,
    request: Request,
    name: str = Query(min_length=1, max_length=180),
    reply_to_id: uuid.UUID | None = Query(default=None),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
    reply_id, reply = await require_reply(db, soviet_id, reply_to_id)
    mime_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if mime_type not in ATTACHMENT_TYPES:
        raise HTTPException(415, "Unsupported attachment type")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "Attachment exceeds 20 MiB")
        data.extend(chunk)
    if not data:
        raise HTTPException(400, "Empty attachment")

    attachment = DispatchAttachment(name=name, mime_type=mime_type, data=bytes(data))
    dispatch = Dispatch(
        soviet_id=soviet_id, sender=current_comrade, attachment=attachment, reply_to_id=reply_id, forwarded=None,
        content={"image": "Фото", "video": "Видео", "audio": "Аудио"}[mime_type.split("/")[0]],
    )
    db.add(dispatch)
    await db.flush()
    result = DispatchOut.model_validate(dispatch).model_copy(update={"reply": ReplyOut.model_validate(reply) if reply else None})
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
            DispatchAttachment.data, DispatchAttachment.mime_type,
        ).where(DispatchAttachment.dispatch_id == dispatch_id)
    )).one_or_none()
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
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
    return [{"reader_id": str(person), "message_id": str(message) if message else None,
             "message_created_at": created.isoformat() if created else None} for person, message, created in rows]


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
            data = await db.scalar(select(DispatchAttachment.data).where(
                DispatchAttachment.dispatch_id == original.id))
            attachment = DispatchAttachment(name=original.attachment.name,
                mime_type=original.attachment.mime_type, data=data)
        copy = Dispatch(soviet_id=payload.target_chat_id, sender=current,
            content=original.content, attachment=attachment,
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
