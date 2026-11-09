import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.services.push_notifications import configure_push
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
        await conn.exec_driver_sql("ALTER TABLE dispatches ADD COLUMN IF NOT EXISTS rich JSON")
        await conn.exec_driver_sql("ALTER TABLE dispatches ADD COLUMN IF NOT EXISTS reply_to_id UUID REFERENCES dispatches(id) ON DELETE SET NULL")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS bubble_color VARCHAR(16)")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS avatar_revision VARCHAR(36)")

        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS bio VARCHAR(280) NOT NULL DEFAULT ''")
        await conn.exec_driver_sql("ALTER TABLE comrade_presence ADD COLUMN IF NOT EXISTS show_battery BOOLEAN NOT NULL DEFAULT true")

        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS country_code VARCHAR(2)")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS nationality VARCHAR(80)")
        await conn.exec_driver_sql("ALTER TABLE dispatch_attachments ALTER COLUMN data DROP NOT NULL")
        await conn.exec_driver_sql("ALTER TABLE dispatch_attachments ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
        await conn.exec_driver_sql("ALTER TABLE dispatch_attachments ADD COLUMN IF NOT EXISTS is_sticker BOOLEAN NOT NULL DEFAULT false")
        await conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS applied_data_actions (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        # Explicitly authorized test-media reset. Runs exactly once on deployment.
        claimed = await conn.exec_driver_sql("INSERT INTO applied_data_actions(name) VALUES ('test-media-reset-local-delivery-v1') ON CONFLICT DO NOTHING RETURNING name")
        if claimed.first():
            await conn.exec_driver_sql("UPDATE dispatch_attachments SET data = NULL, expires_at = now()")
        await conn.exec_driver_sql("UPDATE dispatch_attachments SET expires_at = now() WHERE expires_at IS NULL")
        await conn.exec_driver_sql("ALTER TABLE dispatch_attachments ALTER COLUMN expires_at SET NOT NULL")

        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS birth_date DATE")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS favorite_song JSON")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS show_favorite_song BOOLEAN NOT NULL DEFAULT false")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS relationship_status VARCHAR(40)")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS show_relationship BOOLEAN NOT NULL DEFAULT false")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS share_app_usage BOOLEAN NOT NULL DEFAULT false")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS most_used_app_name VARCHAR(100)")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS app_usage_updated_at TIMESTAMPTZ")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS most_used_app_icon BYTEA")
        await conn.exec_driver_sql("ALTER TABLE soviets ADD COLUMN IF NOT EXISTS council_key VARCHAR(2)")
        await conn.exec_driver_sql("ALTER TABLE soviets ADD COLUMN IF NOT EXISTS council_publisher_id UUID REFERENCES comrades(id)")
        await conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_soviets_council_key ON soviets(council_key)")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS sleep_enabled BOOLEAN NOT NULL DEFAULT false")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS sleep_start INTEGER NOT NULL DEFAULT 1380")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS sleep_end INTEGER NOT NULL DEFAULT 420")
        await conn.exec_driver_sql("ALTER TABLE comrades ADD COLUMN IF NOT EXISTS sleep_timezone VARCHAR(80) NOT NULL DEFAULT 'UTC'")
        await conn.exec_driver_sql("ALTER TABLE dispatch_attachments ADD COLUMN IF NOT EXISTS is_round_video BOOLEAN NOT NULL DEFAULT false")

    configure_push()
    from app.db.session import async_session_maker
    from app.services.councils import ensure_councils
    async with async_session_maker() as council_db:
        await ensure_councils(council_db)
    from app.services.media_relay import cleanup_loop
    media_cleanup = asyncio.create_task(cleanup_loop())

    # 2. Inicializar SovietSocketManager y tarea de Redis Pub/Sub
    redis_client = get_redis_client()
    manager = SovietSocketManager(redis_client)
    ws.socket_manager = manager

    listener_task = asyncio.create_task(manager.start_listener())
    from app.services.auto_delete import cleanup_loop as auto_delete_loop
    auto_delete_task = asyncio.create_task(auto_delete_loop())

    yield

    # 3. Limpieza al apagar el servidor
    auto_delete_task.cancel()
    await asyncio.gather(auto_delete_task, return_exceptions=True)
    media_cleanup.cancel()
    await asyncio.gather(media_cleanup, return_exceptions=True)
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    for task in manager.push_tasks:
        task.cancel()
    if manager.push_tasks:
        await asyncio.gather(*list(manager.push_tasks), return_exceptions=True)
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



