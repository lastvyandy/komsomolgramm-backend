import uuid
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.models import Dispatch


async def reply_summaries(db, soviet_id, ids):
    if not ids:
        return {}
    messages = (await db.scalars(select(Dispatch).options(selectinload(Dispatch.sender), selectinload(Dispatch.attachment)).where(
        Dispatch.soviet_id == soviet_id, Dispatch.id.in_(ids), Dispatch.is_purged.is_(False)
    ))).all()
    return {m.id: {"id": str(m.id), "sender_name": m.sender.callsign if m.sender else "", "content": m.content[:160], "media_type": m.attachment.mime_type if m.attachment else None} for m in messages}


async def require_reply(db, soviet_id, raw_id):
    if raw_id is None:
        return None, None
    try:
        target_id = uuid.UUID(str(raw_id))
    except ValueError:
        raise HTTPException(422, "Invalid reply target")
    summaries = await reply_summaries(db, soviet_id, [target_id])
    if target_id not in summaries:
        raise HTTPException(404, "Reply target not found in this chat")
    return target_id, summaries[target_id]
