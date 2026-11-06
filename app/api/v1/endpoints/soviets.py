import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.models.models import Comrade, Soviet, SovietMember, SovietType, ComradeRole
from app.schemas.soviet import SovietCreate, SovietOut

router = APIRouter()

@router.post("/", response_model=SovietOut, status_code=status.HTTP_201_CREATED)
async def create_soviet(
    soviet_in: SovietCreate,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Crea un Soviet (grupo o canal) y asigna al creador como Secretario General."""
    new_soviet = Soviet(
        name=soviet_in.name,
        soviet_type=soviet_in.soviet_type
    )
    db.add(new_soviet)
    await db.flush()

    # Añadir al creador como Secretario General
    creator_membership = SovietMember(
        soviet_id=new_soviet.id,
        comrade_id=current_comrade.id,
        role=ComradeRole.SECRETARY_GENERAL
    )
    db.add(creator_membership)

    # Añadir camaradas adicionales indicados en el cuerpo
    if soviet_in.member_ids:
        unique_ids = {m_id for m_id in soviet_in.member_ids if m_id != current_comrade.id}
        for m_id in unique_ids:
            membership = SovietMember(
                soviet_id=new_soviet.id,
                comrade_id=m_id,
                role=ComradeRole.COMRADE
            )
            db.add(membership)

    await db.commit()

    # Cargar relaciones completas para la respuesta Pydantic
    stmt = (
        select(Soviet)
        .options(
            selectinload(Soviet.members).selectinload(SovietMember.comrade)
        )
        .where(Soviet.id == new_soviet.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one()

@router.get("/", response_model=list[SovietOut])
async def list_my_soviets(
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Lista todos los Soviets donde el camarada participa activamente."""
    stmt = (
        select(Soviet)
        .join(Soviet.members)
        .where(SovietMember.comrade_id == current_comrade.id)
        .options(
            selectinload(Soviet.members).selectinload(SovietMember.comrade)
        )
        .order_by(Soviet.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/{soviet_id}/join", response_model=SovietOut)
async def join_soviet(
    soviet_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Permite a un camarada integrarse voluntariamente a una célula existente."""
    soviet_query = select(Soviet).where(Soviet.id == soviet_id)
    res = await db.execute(soviet_query)
    target_soviet = res.scalar_one_or_none()
    if not target_soviet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El Soviet especificado no consta en los registros estatales."
        )

    # Verificar si ya es miembro
    mem_query = select(SovietMember).where(
        SovietMember.soviet_id == soviet_id,
        SovietMember.comrade_id == current_comrade.id
    )
    mem_res = await db.execute(mem_query)
    if mem_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El camarada ya forma parte de esta asamblea."
        )

    new_member = SovietMember(
        soviet_id=soviet_id,
        comrade_id=current_comrade.id,
        role=ComradeRole.COMRADE
    )
    db.add(new_member)
    await db.commit()

    stmt = (
        select(Soviet)
        .options(
            selectinload(Soviet.members).selectinload(SovietMember.comrade)
        )
        .where(Soviet.id == soviet_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one()

@router.post("/direct/{target_comrade_id}", response_model=SovietOut)
async def get_or_create_direct_soviet(
    target_comrade_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Inicia o recupera un Encuentro Clandestino (chat 1 a 1) con otro camarada."""
    if target_comrade_id == current_comrade.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un camarada no puede mantener un encuentro clandestino consigo mismo."
        )

    # 1. Comprobar que el camarada destino exista y esté activo
    target_res = await db.execute(
        select(Comrade).where(Comrade.id == target_comrade_id, Comrade.is_active == True)
    )
    target_user = target_res.scalar_one_or_none()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El camarada destinatario no consta en el censo activo."
        )

    # 2. Buscar si ya existe un Soviet de tipo DIRECT compartido entre ambos
    find_stmt = (
        select(SovietMember.soviet_id)
        .join(Soviet, Soviet.id == SovietMember.soviet_id)
        .where(
            Soviet.soviet_type == SovietType.DIRECT,
            SovietMember.comrade_id.in_([current_comrade.id, target_comrade_id])
        )
        .group_by(SovietMember.soviet_id)
        .having(func.count(SovietMember.comrade_id) == 2)
    )
    existing_soviet_id = (await db.execute(find_stmt)).scalar_one_or_none()

    if existing_soviet_id:
        stmt_existing = (
            select(Soviet)
            .options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
            .where(Soviet.id == existing_soviet_id)
        )
        return (await db.execute(stmt_existing)).scalar_one()

    # 3. Si no existe, crear la sala directa y vincular a ambos
    new_direct = Soviet(
        name=None,
        soviet_type=SovietType.DIRECT
    )
    db.add(new_direct)
    await db.flush()

    member_self = SovietMember(
        soviet_id=new_direct.id,
        comrade_id=current_comrade.id,
        role=ComradeRole.COMRADE
    )
    member_target = SovietMember(
        soviet_id=new_direct.id,
        comrade_id=target_comrade_id,
        role=ComradeRole.COMRADE
    )
    db.add_all([member_self, member_target])
    await db.commit()

    # Recargar con relaciones
    stmt_reload = (
        select(Soviet)
        .options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
        .where(Soviet.id == new_direct.id)
    )
    return (await db.execute(stmt_reload)).scalar_one()