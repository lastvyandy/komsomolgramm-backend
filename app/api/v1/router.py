from fastapi import APIRouter
from app.api.v1.endpoints import auth, soviets, dispatches, ws, stickers

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Comisariado de Identificación"])
api_router.include_router(soviets.router, prefix="/soviets", tags=["Asambleas y Soviets"])
api_router.include_router(dispatches.router, prefix="/dispatches", tags=["Archivos de Despachos"])
api_router.include_router(ws.router, prefix="/ws", tags=["Transmisiones en Vivo"])
api_router.include_router(stickers.router, prefix="/stickers", tags=["Stickers"])

