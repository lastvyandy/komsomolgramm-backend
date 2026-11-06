import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.security import decode_access_token
from app.db.session import async_session_maker
from app.models.models import Comrade, SovietMember, Dispatch
from app.services.socket_manager import SovietSocketManager

router = APIRouter()
socket_manager: SovietSocketManager | None = None

def get_socket_manager() -> SovietSocketManager:
    if socket_manager is None:
        raise RuntimeError("El SovietSocketManager no ha sido inicializado.")
    return socket_manager

@router.websocket("/soviets/{soviet_id}")
async def soviet_websocket_endpoint(
    websocket: WebSocket,
    soviet_id: uuid.UUID,
    token: str = Query(...)
):
    manager = get_socket_manager()

    # 1. Validar el token JWT
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        comrade_id = uuid.UUID(payload["sub"])
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. Verificar membresía
    async with async_session_maker() as db:
        query = (
            select(SovietMember)
            .options(selectinload(SovietMember.comrade))
            .where(
                SovietMember.soviet_id == soviet_id,
                SovietMember.comrade_id == comrade_id
            )
        )
        res = await db.execute(query)
        membership = res.scalar_one_or_none()

        if not membership or not membership.comrade.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        sender_callsign = membership.comrade.callsign
        sender_username = membership.comrade.username

    await manager.connect(soviet_id, websocket)

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type", "dispatch")

            # Caso A: Reacción / Sello partidario sobre un despacho
            if event_type == "reaction":
                dispatch_id = data.get("dispatch_id")
                stamp = data.get("stamp")
                if dispatch_id and stamp:
                    reaction_event = {
                        "type": "reaction",
                        "dispatch_id": dispatch_id,
                        "stamp": stamp,
                        "sender_callsign": sender_callsign
                    }
                    await manager.publish_dispatch(soviet_id, reaction_event)
                continue

            # Caso B: Nuevo despacho de texto estándar
            content = data.get("content", "").strip()
            if not content:
                continue

            async with async_session_maker() as db:
                new_dispatch = Dispatch(
                    soviet_id=soviet_id,
                    sender_id=comrade_id,
                    content=content
                )
                db.add(new_dispatch)
                await db.commit()
                await db.refresh(new_dispatch)

                dispatch_id = str(new_dispatch.id)
                created_at_iso = new_dispatch.created_at.isoformat()

            outbound_event = {
                "type": "dispatch",
                "id": dispatch_id,
                "soviet_id": str(soviet_id),
                "sender_id": str(comrade_id),
                "sender_username": sender_username,
                "sender_callsign": sender_callsign,
                "content": content,
                "created_at": created_at_iso
            }
            await manager.publish_dispatch(soviet_id, outbound_event)

    except WebSocketDisconnect:
        manager.disconnect(soviet_id, websocket)