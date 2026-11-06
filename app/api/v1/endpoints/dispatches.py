import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, SovietMember, Dispatch
from app.schemas.dispatch import DispatchOut

router = APIRouter()

@router.get("/{soviet_id}", response_model=list[DispatchOut])
async def get_soviet_dispatches(
    soviet_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Obtiene el historial de despachos de un Soviet asegurando que el solicitante sea miembro."""
    # 1. Validar membresía
    membership_query = select(SovietMember).where(
        SovietMember.soviet_id == soviet_id,
        SovietMember.comrade_id == current_comrade.id
    )
    mem_res = await db.execute(membership_query)
    if not mem_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. No posee credenciales para inspeccionar este archivo."
        )

    # 2. Consultar despachos ordenados por fecha ascendente para el cliente móvil
    stmt = (
        select(Dispatch)
        .options(selectinload(Dispatch.sender))
        .where(
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged == False  # noqa: E712
        )
        .order_by(Dispatch.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    dispatches = result.scalars().all()

    # Revertir para entregar en orden cronológico (los más antiguos primero)
    return list(reversed(dispatches))