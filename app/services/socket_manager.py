import asyncio
from app.services.push_notifications import send_push
import json
import logging
import uuid
from typing import Dict, Set
from fastapi import WebSocket
import redis.asyncio as redis

logger = logging.getLogger("komsomol.sockets")

class SovietSocketManager:
    def __init__(self, redis_client: redis.Redis):
        # Mapeo en memoria local: soviet_id -> set de WebSockets locales
        self.local_connections: Dict[uuid.UUID, Set[WebSocket]] = {}
        self.redis = redis_client
        self.push_tasks: set[asyncio.Task] = set()
        self._listener_task: asyncio.Task | None = None

    async def connect(self, soviet_id: uuid.UUID, websocket: WebSocket):
        await websocket.accept()
        if soviet_id not in self.local_connections:
            self.local_connections[soviet_id] = set()
        self.local_connections[soviet_id].add(websocket)

    def disconnect(self, soviet_id: uuid.UUID, websocket: WebSocket):
        if soviet_id in self.local_connections:
            self.local_connections[soviet_id].discard(websocket)
            if not self.local_connections[soviet_id]:
                del self.local_connections[soviet_id]

    async def broadcast_locally(self, soviet_id: uuid.UUID, payload: dict):
        """Distribuye el despacho a todos los clientes conectados a este proceso worker."""
        connections = self.local_connections.get(soviet_id, set())
        if not connections:
            return

        dead_connections = set()
        message_text = json.dumps(payload)

        for ws in list(connections):
            try:
                await ws.send_text(message_text)
            except Exception:
                dead_connections.add(ws)

        for ws in dead_connections:
            connections.discard(ws)

    async def publish_dispatch(self, soviet_id: uuid.UUID, payload: dict):
        """Publica el despacho en el canal Redis del Soviet para todas las réplicas."""
        channel = f"soviet:{str(soviet_id)}"
        await self.redis.publish(channel, json.dumps(payload))
        if payload.get("type") == "dispatch" and payload.get("sender_id"):
            task = asyncio.create_task(send_push(soviet_id, payload))
            self.push_tasks.add(task)
            task.add_done_callback(self.push_tasks.discard)

    async def start_listener(self):
        """Escucha en segundo plano todos los canales de soviets en Redis."""
        pubsub = self.redis.pubsub()
        await pubsub.psubscribe("soviet:*")
        logger.info("📡 Escuchando transmisiones en el bus estatal de Redis...")
        try:
            async for msg in pubsub.listen():
                if msg["type"] == "pmessage":
                    raw_channel: str = msg["channel"].decode("utf-8")
                    soviet_id_str = raw_channel.split(":")[1]
                    try:
                        soviet_id = uuid.UUID(soviet_id_str)
                        payload = json.loads(msg["data"].decode("utf-8"))
                        await self.broadcast_locally(soviet_id, payload)
                    except Exception as e:
                        logger.error(f"Error procesando despacho del canal {raw_channel}: {e}")
        except asyncio.CancelledError:
            await pubsub.punsubscribe("soviet:*")
            await pubsub.close()