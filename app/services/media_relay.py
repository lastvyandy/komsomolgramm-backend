"""Temporary delivery only. Durable media belongs to the receiving devices."""
import asyncio
import os
import logging
from sqlalchemy import select, update, func, text
from app.db.session import async_session_maker
from app.models.models import AttachmentRecipient, DispatchAttachment, SovietMember, utc_now

async def admit_media(db, size):
    # Serialize quota admission across API processes.
    await db.execute(text("SELECT pg_advisory_xact_lock(87531021)"))
    used = await db.scalar(select(func.coalesce(func.sum(func.octet_length(DispatchAttachment.data)), 0)))
    quota = int(os.environ.get("MEDIA_RELAY_QUOTA_BYTES", str(256 * 1024 * 1024)))
    if used + size > quota:
        from fastapi import HTTPException
        raise HTTPException(507, "Temporary media relay is full; try later")

async def register_recipients(db, message):
    members = (await db.scalars(select(SovietMember.comrade_id).where(
        SovietMember.soviet_id == message.soviet_id,
        SovietMember.comrade_id != message.sender_id))).all()
    if members:
        db.add_all([AttachmentRecipient(dispatch_id=message.id, comrade_id=member) for member in set(members)])
    else:
        message.attachment.data = None

async def cleanup_once():
    async with async_session_maker() as db:
        await db.execute(update(DispatchAttachment).where(
            DispatchAttachment.expires_at <= utc_now(),
            DispatchAttachment.data.is_not(None)).values(data=None))
        await db.commit()

async def cleanup_loop():
    while True:
        try:
            await cleanup_once()
        except Exception:
            logging.getLogger(__name__).exception("Temporary media cleanup failed; retrying")
        await asyncio.sleep(30)
