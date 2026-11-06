import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.redis import get_redis_client
from app.db.session import engine
from app.models.models import Base
from app.api.v1.router import api_router
from app.api.v1.endpoints import ws
from app.services.socket_manager import SovietSocketManager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Crear tablas en PostgreSQL
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS bubble_color VARCHAR(16)")

    # 2. Inicializar SovietSocketManager y tarea de Redis Pub/Sub
    redis_client = get_redis_client()
    manager = SovietSocketManager(redis_client)
    ws.socket_manager = manager

    listener_task = asyncio.create_task(manager.start_listener())

    yield

    # 3. Limpieza al apagar el servidor
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    await redis_client.aclose()
    await engine.dispose()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Red de comunicaciones del Comité Central",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health", tags=["Comité"])
async def health_check():
    return {
        "status": "operational",
        "apparatus": "Comité Central en línea",
        "quota_fulfilled": True
    }