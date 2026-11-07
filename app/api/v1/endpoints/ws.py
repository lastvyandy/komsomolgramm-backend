import time
import uuid
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.security import decode_access_token
from app.db.session import async_session_maker
from app.models.models import Comrade, SovietMember, Dispatch, DispatchReaction
from app.services.socket_manager import SovietSocketManager
from app.services.replies import require_reply

router = APIRouter()
socket_manager: SovietSocketManager | None = None


def get_socket_manager() -> SovietSocketManager:
    if socket_manager is None:
        raise RuntimeError("Socket manager has not been initialized")
    return socket_manager


@router.websocket("/soviets/{soviet_id}")
async def soviet_websocket_endpoint(websocket: WebSocket, soviet_id: uuid.UUID, token: str = Query(...)):
    manager = get_socket_manager()
    payload = decode_access_token(token)
    try:
        comrade_id = uuid.UUID(payload["sub"]) if payload else None
    except (ValueError, KeyError, TypeError):
        comrade_id = None
    if comrade_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with async_session_maker() as db:
        membership = await db.scalar(select(SovietMember).options(selectinload(SovietMember.comrade)).where(
            SovietMember.soviet_id == soviet_id, SovietMember.comrade_id == comrade_id))
        if not membership or not membership.comrade.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        sender_callsign = membership.comrade.callsign
        sender_username = membership.comrade.username

    await manager.connect(soviet_id, websocket)
    last_typing = 0.0
    last_buzz = 0.0
    was_typing = False
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            event_type = data.get("type", "dispatch")
            if event_type == "typing":
                active = data.get("active") is True
                now = time.monotonic()
                if (active and now - last_typing >= 2) or (not active and was_typing):
                    await manager.publish_dispatch(soviet_id, {
                        "type": "typing", "sender_id": str(comrade_id),
                        "sender_callsign": sender_callsign, "active": active,
                    })
                    if active:
                        last_typing = now
                    was_typing = active
                continue
            if event_type == "buzz":
                now = time.monotonic()
                if now - last_buzz < 5:
                    await websocket.send_json({"type": "error", "code": "buzz_rate_limited"})
                    continue
                await manager.publish_dispatch(soviet_id, {
                    "type": "buzz",
                    "id": str(uuid.uuid4()),
                    "sender_id": str(comrade_id),
                    "sender_callsign": sender_callsign,
                })
                last_buzz = now
                continue
            if event_type == "reaction":
                try:
                    target_id = uuid.UUID(str(data.get("dispatch_id")))
                except (ValueError, TypeError):
                    continue
                stamp = data.get("stamp")
                if not isinstance(stamp, str) or not 1 <= len(stamp) <= 32:
                    continue
                active_reaction = None
                reaction_summary = []
                async with async_session_maker() as db:
                    target = await db.scalar(select(Dispatch.id).where(
                        Dispatch.id == target_id,
                        Dispatch.soviet_id == soviet_id,
                        Dispatch.is_purged.is_(False),
                    ))
                    if target is None:
                        continue
                    reaction = await db.get(DispatchReaction, (target_id, comrade_id))
                    if reaction is not None and reaction.emoji == stamp:
                        await db.delete(reaction)
                    elif reaction is not None:
                        reaction.emoji = stamp
                        active_reaction = stamp
                    else:
                        db.add(DispatchReaction(
                            dispatch_id=target_id,
                            comrade_id=comrade_id,
                            emoji=stamp,
                        ))
                        active_reaction = stamp
                    await db.commit()
                    rows = (await db.execute(
                        select(DispatchReaction.emoji, func.count())
                        .where(DispatchReaction.dispatch_id == target_id)
                        .group_by(DispatchReaction.emoji)
                    )).all()
                    reaction_summary = [
                        {"stamp": emoji, "count": count}
                        for emoji, count in sorted(rows, key=lambda row: (-row[1], row[0]))
                    ]
                await manager.publish_dispatch(soviet_id, {
                    "type": "reaction",
                    "dispatch_id": str(target_id),
                    "sender_id": str(comrade_id),
                    "stamp": active_reaction or "",
                    "my_reaction": active_reaction,
                    "reactions": reaction_summary,
                })
                continue
            if event_type != "dispatch":
                continue
            content = data.get("content")
            if not isinstance(content, str) or not 1 <= len(content.strip()) <= 4000:
                await websocket.send_json({"type": "error", "code": "invalid_content"})
                continue
            async with async_session_maker() as db:
                sender = await db.get(Comrade, comrade_id)
                if sender is None or not sender.is_active:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                try:
                    reply_id, reply = await require_reply(db, soviet_id, data.get("reply_to_id"))
                except HTTPException:
                    await websocket.send_json({"type": "error", "code": "reply_unavailable"})
                    continue
                message = Dispatch(soviet_id=soviet_id, sender_id=comrade_id,
                                   content=content.strip(), reply_to_id=reply_id)
                db.add(message)
                await db.commit()
                await db.refresh(message)
                event = {
                    "type": "dispatch", "id": str(message.id), "soviet_id": str(soviet_id),
                    "sender_id": str(comrade_id), "sender_username": sender_username,
                    "sender_callsign": sender_callsign, "bubble_color": sender.bubble_color,
                    "content": message.content, "created_at": message.created_at.isoformat(),
                    "reply_to_id": str(reply_id) if reply_id else None, "reply": reply,
                }
            await manager.publish_dispatch(soviet_id, event)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(soviet_id, websocket)
        if was_typing:
            try:
                await manager.publish_dispatch(soviet_id, {
                    "type": "typing", "sender_id": str(comrade_id),
                    "sender_callsign": sender_callsign, "active": False,
                })
            except Exception:
                pass  # Clients also expire stale typing indicators after six seconds.
