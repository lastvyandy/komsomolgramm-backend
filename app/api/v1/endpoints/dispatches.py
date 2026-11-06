import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, SovietMember, SovietReadCursor, Dispatch, DispatchAttachment
from app.schemas.dispatch import AttachmentOut, DispatchCreate, DispatchOut
from app.api.v1.endpoints.ws import get_socket_manager

router = APIRouter()

@router.get("/{soviet_id}", response_model=list[DispatchOut])
async def get_soviet_dispatches(
    soviet_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
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
    stmt = (
        select(Dispatch)
        .options(selectinload(Dispatch.sender), selectinload(Dispatch.attachment))
        .where(
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged == False  # noqa: E712
        )
        .order_by(Dispatch.created_at.desc(), Dispatch.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    dispatches = result.scalars().all()

    # Revertir para entregar en orden cronológico (los más antiguos primero)
    return list(reversed(dispatches))

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
        .options(selectinload(Dispatch.sender))
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


def attachment_query(soviet_id: uuid.UUID):
    return select(DispatchAttachment).join(Dispatch).where(
        Dispatch.soviet_id == soviet_id, Dispatch.is_purged.is_(False),
    )


@router.post("/{soviet_id}/attachments", response_model=DispatchOut, status_code=201)
async def send_attachment(
    soviet_id: uuid.UUID,
    request: Request,
    name: str = Query(min_length=1, max_length=180),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await require_membership(db, soviet_id, current_comrade)
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
        soviet_id=soviet_id, sender=current_comrade, attachment=attachment,
        content={"image": "Фото", "video": "Видео", "audio": "Аудио"}[mime_type.split("/")[0]],
    )
    db.add(dispatch)
    await db.flush()
    result = DispatchOut.model_validate(dispatch)
    await db.commit()

    event = result.model_dump(mode="json", exclude={"sender", "is_purged"})
    event.update(
        type="dispatch", sender_id=str(result.sender.id),
        sender_callsign=result.sender.callsign, sender_username=result.sender.username,
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
    statement = attachment_query(soviet_id).where(filters[kind]).order_by(
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
        attachment_query(soviet_id).with_only_columns(
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
