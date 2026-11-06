import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, SovietMember, SovietReadCursor, Dispatch
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
        .order_by(Dispatch.created_at.desc(), Dispatch.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    dispatches = result.scalars().all()

    # Revertir para entregar en orden cronológico (los más antiguos primero)
    return list(reversed(dispatches))

@router.put("/{soviet_id}/read/{dispatch_id}", status_code=204)
async def mark_dispatch_read(
    soviet_id: uuid.UUID,
    dispatch_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    membership_id = await db.scalar(
        select(SovietMember.id).where(
            SovietMember.soviet_id == soviet_id,
            SovietMember.comrade_id == current_comrade.id,
        )
    )
    if membership_id is None:
        raise HTTPException(403, "Chat membership required")

    message_created_at = await db.scalar(
        select(Dispatch.created_at).where(
            Dispatch.id == dispatch_id,
            Dispatch.soviet_id == soviet_id,
            Dispatch.is_purged.is_(False),
        )
    )
    if message_created_at is None:
        raise HTTPException(404, "Message not found in this chat")

    statement = insert(SovietReadCursor).values(
        membership_id=membership_id,
        message_created_at=message_created_at,
        message_id=dispatch_id,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[SovietReadCursor.membership_id],
        set_={
            "message_created_at": statement.excluded.message_created_at,
            "message_id": statement.excluded.message_id,
        },
        where=tuple_(SovietReadCursor.message_created_at, SovietReadCursor.message_id) <
            tuple_(statement.excluded.message_created_at, statement.excluded.message_id),
    ))
    await db.commit()
    return Response(status_code=204)
