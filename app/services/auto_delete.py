"""Durable per-message deadlines; changing a policy affects future messages only."""
import asyncio
import logging
from sqlalchemy import select, delete
from app.models.models import AutoDeleteDeadline, Dispatch, DispatchHidden, DispatchAttachment, utc_now

async def expire_due(db, now=None):
    # skip_locked allows multiple API workers without processing a deadline twice.
    deadlines = (await db.scalars(select(AutoDeleteDeadline).where(
        AutoDeleteDeadline.completed.is_(False), AutoDeleteDeadline.expires_at <= (now or utc_now()))
        .order_by(AutoDeleteDeadline.expires_at).limit(200).with_for_update(skip_locked=True))).all()
    events = []
    for deadline in deadlines:
        message = await db.get(Dispatch, deadline.dispatch_id)
        if message is not None and not message.is_purged:
            if deadline.for_everyone:
                # The author identity is recorded when the deadline is created.
                if message.sender_id == deadline.comrade_id:
                    message.is_purged = True
                    message.content = ""
                    message.rich = None
                    await db.execute(delete(DispatchAttachment).where(DispatchAttachment.dispatch_id == message.id))
            elif await db.get(DispatchHidden, (message.id, deadline.comrade_id)) is None:
                db.add(DispatchHidden(dispatch_id=message.id, comrade_id=deadline.comrade_id))
            # Private deletions are fetched only by their owner. Older Android clients
            # do not understand viewer_id and would wrongly hide another user's copy.
            if deadline.for_everyone:
                events.append((deadline.soviet_id, {"type": "delete_dispatch", "id": str(message.id)}))
        deadline.completed = True
    await db.commit()
    return events

async def cleanup_loop():
    from app.db.session import async_session_maker
    from app.api.v1.endpoints.ws import get_socket_manager
    while True:
        try:
            async with async_session_maker() as db:
                events = await expire_due(db)
            for chat, event in events:
                try:
                    await get_socket_manager().publish_dispatch(chat, event)
                except Exception:
                    logging.getLogger(__name__).exception("Auto-delete saved; live event unavailable")
        except Exception:
            logging.getLogger(__name__).exception("Auto-delete will retry")
        await asyncio.sleep(5)
