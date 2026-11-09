import hashlib
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, true, or_, tuple_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.deps import get_current_comrade
from app.services.councils import ensure_councils, COUNCILS
from app.models.models import Comrade, Dispatch, Soviet, SovietMember, SovietReadCursor, SovietType, ComradeRole, DispatchHidden
from app.schemas.soviet import ChatPreview, SovietCreate, SovietMembersAdd, SovietOut

router = APIRouter()

@router.post("/", response_model=SovietOut, status_code=status.HTTP_201_CREATED)
async def create_soviet(
    soviet_in: SovietCreate,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Crea un Soviet (grupo o canal) y asigna al creador como Secretario General."""
    if soviet_in.soviet_type == SovietType.DIRECT:
        raise HTTPException(400, "Use the direct chat endpoint")
    member_ids = set(soviet_in.member_ids) - {current_comrade.id}
    from app.services.privacy import allows
    for person in member_ids:
        if not await allows(db,person,"group_invites",current_comrade.id):
            raise HTTPException(403,"Group invitations restricted by recipient")
    if len(member_ids) > 50:
        raise HTTPException(422, "At most 50 members can be added at creation")
    if member_ids:
        active_ids = set((await db.scalars(
            select(Comrade.id).where(Comrade.id.in_(member_ids), Comrade.is_active.is_(True))
        )).all())
        if active_ids != member_ids:
            raise HTTPException(422, "One or more participants are unavailable")
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
    for member_id in member_ids:
        membership = SovietMember(
            soviet_id=new_soviet.id,
            comrade_id=member_id,
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
    await ensure_councils(db, current_comrade.id)
    latest = (
        select(
            func.left(Dispatch.content, 160).label("content"),
            Dispatch.sender_id,
            Comrade.callsign.label("sender_name"),
            Dispatch.created_at,
        )
        .select_from(Dispatch)
        .outerjoin(Comrade, Dispatch.sender_id == Comrade.id)
        .where(Dispatch.soviet_id == Soviet.id, Dispatch.is_purged.is_(False),
            ~select(DispatchHidden.dispatch_id).where(DispatchHidden.dispatch_id == Dispatch.id,
                DispatchHidden.comrade_id == current_comrade.id).exists())
        .order_by(Dispatch.created_at.desc(), Dispatch.id.desc())
        .limit(1)
        .correlate(Soviet)
        .lateral("latest_dispatch")
    )
    unread_count = (
        select(func.count())
        .select_from(Dispatch)
        .where(
            Dispatch.soviet_id == Soviet.id,
            Dispatch.is_purged.is_(False),
            ~select(DispatchHidden.dispatch_id).where(DispatchHidden.dispatch_id == Dispatch.id,
                DispatchHidden.comrade_id == current_comrade.id).exists(),
            Dispatch.sender_id.is_distinct_from(current_comrade.id),
            Dispatch.created_at >= SovietMember.joined_at,
            or_(
                SovietReadCursor.message_id.is_(None),
                tuple_(Dispatch.created_at, Dispatch.id) >
                tuple_(SovietReadCursor.message_created_at, SovietReadCursor.message_id),
            ),
        )
        .correlate(Soviet, SovietMember, SovietReadCursor)
        .scalar_subquery()
    )
    stmt = (
        select(
            Soviet, latest.c.content, latest.c.sender_id, latest.c.sender_name,
            latest.c.created_at, unread_count,
        )
        .join(Soviet.members)
        .outerjoin(latest, true())
        .outerjoin(SovietReadCursor, SovietReadCursor.membership_id == SovietMember.id)
        .where(SovietMember.comrade_id == current_comrade.id, Soviet.council_key.is_(None))
        .options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
        .order_by(func.coalesce(latest.c.created_at, Soviet.created_at).desc(), Soviet.id)
    )
    result = await db.execute(stmt)
    return [
        SovietOut.model_validate(soviet).model_copy(update={
            "last_message": ChatPreview(
                content=content, sender_name=sender_name, sender_id=sender_id, created_at=created_at
            ) if created_at is not None else None,
            "unread_count": unread,
        })
        for soviet, content, sender_id, sender_name, created_at, unread in result.unique().all()
    ]

@router.get("/councils", response_model=list[SovietOut])
async def list_councils(current_comrade: Comrade = Depends(get_current_comrade),
        db: AsyncSession = Depends(get_async_db)):
    await ensure_councils(db, current_comrade.id)
    results = []
    for identifier, key, name in COUNCILS:
        council = await db.get(Soviet, identifier)
        latest = await db.scalar(select(Dispatch).where(Dispatch.soviet_id == identifier,
            Dispatch.is_purged.is_(False)).options(selectinload(Dispatch.sender))
            .order_by(Dispatch.created_at.desc(), Dispatch.id.desc()).limit(1))
        membership = await db.scalar(select(SovietMember).where(SovietMember.soviet_id == identifier,
            SovietMember.comrade_id == current_comrade.id))
        cursor = await db.get(SovietReadCursor, membership.id)
        unseen = select(func.count()).select_from(Dispatch).where(Dispatch.soviet_id == identifier,
            Dispatch.is_purged.is_(False), Dispatch.sender_id.is_distinct_from(current_comrade.id),
            Dispatch.created_at >= membership.joined_at)
        if cursor:
            unseen = unseen.where(tuple_(Dispatch.created_at, Dispatch.id) >
                tuple_(cursor.message_created_at, cursor.message_id))
        results.append(SovietOut(id=council.id, name=council.name, soviet_type=council.soviet_type,
            created_at=council.created_at, council_key=key, council_publisher_id=council.council_publisher_id,
            members=[], unread_count=await db.scalar(unseen) or 0,
            last_message=ChatPreview(content=latest.content[:160], sender_name=latest.sender.callsign if latest.sender else None,
                sender_id=latest.sender_id, created_at=latest.created_at) if latest else None))
    return results

@router.get("/{soviet_id}", response_model=SovietOut)
async def read_soviet(
    soviet_id: uuid.UUID,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = (
        select(Soviet)
        .join(Soviet.members)
        .where(Soviet.id == soviet_id, SovietMember.comrade_id == current_comrade.id)
        .options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
    )
    soviet = (await db.execute(stmt)).scalars().first()
    if soviet is None:
        raise HTTPException(404, "Chat not found")
    return soviet


@router.post("/{soviet_id}/members", response_model=SovietOut)
async def add_soviet_members(
    soviet_id: uuid.UUID,
    payload: SovietMembersAdd,
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    """Adds active comrades to a group. Existing members may invite people."""
    group = (await db.execute(
        select(Soviet).join(Soviet.members).where(
            Soviet.id == soviet_id,
            SovietMember.comrade_id == current_comrade.id,
        )
    )).scalar_one_or_none()
    if group is None:
        raise HTTPException(404, "Group not found")
    if group.council_key is not None:
        raise HTTPException(403, "Council membership is automatic")
    if group.soviet_type == SovietType.DIRECT:
        raise HTTPException(403, "Direct chats cannot have additional members")

    requested = set(payload.member_ids) - {current_comrade.id}
    from app.services.privacy import allows
    for person in requested:
        if not await allows(db,person,"group_invites",current_comrade.id):
            raise HTTPException(403,"Group invitations restricted by recipient")
    if not requested:
        raise HTTPException(422, "Select at least one other member")
    active_ids = set((await db.scalars(
        select(Comrade.id).where(Comrade.id.in_(requested), Comrade.is_active.is_(True))
    )).all())
    if active_ids != requested:
        raise HTTPException(422, "One or more participants are unavailable")
    existing = set((await db.scalars(select(SovietMember.comrade_id).where(
        SovietMember.soviet_id == soviet_id,
        SovietMember.comrade_id.in_(requested),
    ))).all())
    db.add_all(SovietMember(soviet_id=soviet_id, comrade_id=person_id, role=ComradeRole.COMRADE)
               for person_id in requested - existing)
    await db.commit()
    return (await db.execute(
        select(Soviet).options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
        .where(Soviet.id == soviet_id)
    )).scalar_one()


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

    if target_soviet.soviet_type == SovietType.DIRECT:
        raise HTTPException(403, "Direct chats are private")

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

    # Serialize concurrent requests for the same unordered pair until commit.
    pair = ":".join(sorted((str(current_comrade.id), str(target_comrade_id))))
    lock_id = int.from_bytes(hashlib.sha256(pair.encode()).digest()[:8], "big", signed=True)
    if db.bind.dialect.name == "postgresql":
        await db.execute(select(func.pg_advisory_xact_lock(lock_id)))

    # 2. Buscar si ya existe un Soviet de tipo DIRECT compartido entre ambos
    find_stmt = (
        select(SovietMember.soviet_id)
        .join(Soviet, Soviet.id == SovietMember.soviet_id)
        .where(
            Soviet.soviet_type == SovietType.DIRECT,
        )
        .group_by(SovietMember.soviet_id)
        .having(
            func.count(SovietMember.comrade_id) == (1 if target_comrade_id == current_comrade.id else 2),
            func.count(func.distinct(SovietMember.comrade_id)).filter(
                SovietMember.comrade_id.in_([current_comrade.id, target_comrade_id])
            ) == (1 if target_comrade_id == current_comrade.id else 2),
        )
    )
    existing_soviet_id = (await db.execute(find_stmt.order_by(SovietMember.soviet_id))).scalars().first()

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
    db.add_all([member_self] if target_comrade_id == current_comrade.id else [member_self, member_target])
    await db.commit()

    # Recargar con relaciones
    stmt_reload = (
        select(Soviet)
        .options(selectinload(Soviet.members).selectinload(SovietMember.comrade))
        .where(Soviet.id == new_direct.id)
    )
    return (await db.execute(stmt_reload)).scalar_one()


