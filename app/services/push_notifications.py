import asyncio
import logging
import os
import uuid
from datetime import timedelta

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import delete, select

from app.db.session import async_session_maker
from app.models.models import PushDevice, SovietMember

logger = logging.getLogger(__name__)
_sender_limit = asyncio.Semaphore(2)
_app = None


def configure_push():
    global _app
    path = os.environ.get("FIREBASE_CREDENTIALS")
    if not path:
        return
    _app = firebase_admin.initialize_app(
        credentials.Certificate(path), options={"httpTimeout": 10}
    )


async def send_push(chat_id: uuid.UUID, payload: dict):
    if _app is None:
        return
    try:
        sender_id = uuid.UUID(payload["sender_id"])
        message_id = str(uuid.UUID(payload["id"]))
        async with _sender_limit:
            async with async_session_maker() as db:
                devices = (await db.execute(
                    select(PushDevice.token_hash, PushDevice.token, PushDevice.comrade_id)
                    .join(SovietMember, SovietMember.comrade_id == PushDevice.comrade_id)
                    .where(SovietMember.soviet_id == chat_id,
                           SovietMember.comrade_id != sender_id)
                )).all()
            for start in range(0, len(devices), 500):
                batch = devices[start:start + 500]
                # Each device receives only identifiers. Android applies local mute
                # preferences and shows a generic preview on the lock screen.
                messages = [
                    messaging.Message(
                        token=device.token,
                        data={
                            "recipient_id": str(device.comrade_id),
                            "chat_id": str(chat_id),
                            "message_id": message_id,
                            "sender_id": str(sender_id),
                        },
                        android=messaging.AndroidConfig(
                            priority="high", ttl=timedelta(hours=1)
                        ),
                    )
                    for device in batch
                ]
                result = await asyncio.to_thread(
                    messaging.send_each, messages, app=_app
                )
                invalid = [
                    device.token_hash
                    for device, response in zip(batch, result.responses)
                    if isinstance(response.exception, messaging.UnregisteredError)
                ]
                if invalid:
                    async with async_session_maker() as db:
                        await db.execute(delete(PushDevice).where(
                            PushDevice.token_hash.in_(invalid)
                        ))
                        await db.commit()
    except Exception:
        logger.exception("Push notification delivery failed")
