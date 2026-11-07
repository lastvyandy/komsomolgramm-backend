import uuid
import hashlib

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_password_hash, verify_password, create_access_token
from app.db.session import get_async_db
from app.models.models import Comrade, ComradeAvatar, PushDevice, Contact
from app.schemas.comrade import ComradeCreate, ComradeOut, Token
from app.api.deps import get_current_comrade

router = APIRouter()

@router.post("/register", response_model=ComradeOut, status_code=status.HTTP_201_CREATED)
async def register_comrade(
    comrade_in: ComradeCreate,
    db: AsyncSession = Depends(get_async_db)
):
    # Verificar si el usuario ya existe
    query = select(Comrade).where(Comrade.username == comrade_in.username)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El nombre de camarada ya se encuentra registrado en el censo estatal."
        )

    comrade = Comrade(
        username=comrade_in.username,
        callsign=comrade_in.callsign,
        hashed_password=get_password_hash(comrade_in.password),
        is_active=True
    )
    db.add(comrade)
    await db.commit()
    await db.refresh(comrade)
    return comrade

@router.post("/login", response_model=Token)
async def issue_salvoconducto(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_async_db)
):
    query = select(Comrade).where(Comrade.username == form_data.username)
    result = await db.execute(query)
    comrade = result.scalar_one_or_none()

    if not comrade or not verify_password(form_data.password, comrade.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales erróneas. Inspección rechazada.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not comrade.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Camarada bajo investigación disciplinaria."
        )

    access_token = create_access_token(subject=comrade.id)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "comrade": comrade
    }

@router.get("/me", response_model=ComradeOut)
async def read_current_comrade_dossier(
    current_comrade: Comrade = Depends(get_current_comrade)
):
    """Devuelve el expediente del camarada autenticado."""
    return current_comrade

class ProfileUpdate(BaseModel):
    callsign: str = Field(min_length=2, max_length=100)


@router.patch("/me", response_model=ComradeOut)
async def update_profile(
    profile: ProfileUpdate,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    name = profile.callsign.strip()
    if len(name) < 2:
        raise HTTPException(422, "Display name must contain at least two characters")
    current.callsign = name
    await db.commit()
    await db.refresh(current)
    return current


class BubbleColorUpdate(BaseModel):
    color: Literal["red", "steel", "gold", "green"] | Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] | None


@router.put("/me/bubble-color", response_model=ComradeOut)
async def update_bubble_color(
    payload: BubbleColorUpdate,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    current.bubble_color = payload.color
    await db.commit()
    await db.refresh(current)
    return current


class PushTokenUpdate(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


@router.put("/me/push-token", status_code=204)
async def register_push_device(
    payload: PushTokenUpdate,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    statement = insert(PushDevice).values(
        token_hash=hashlib.sha256(payload.token.encode()).hexdigest(),
        comrade_id=current.id,
        token=payload.token,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[PushDevice.token_hash],
        set_={"comrade_id": current.id, "token": payload.token},
    ))
    await db.commit()
    return Response(status_code=204)


@router.delete("/me/push-token", status_code=204)
async def unregister_push_device(
    payload: PushTokenUpdate,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await db.execute(delete(PushDevice).where(
        PushDevice.token_hash == hashlib.sha256(payload.token.encode()).hexdigest(),
        PushDevice.comrade_id == current.id,
    ))
    await db.commit()
    return Response(status_code=204)


@router.get("/search", response_model=list[ComradeOut])
async def search_comrades(
    q: str = "",
    current_comrade: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db)
):
    """Busca camaradas en el padrón por nombre de usuario o callsign (excluyendo al solicitante)."""
    clean_query = q.strip()
    if not clean_query:
        return []

    stmt = (
        select(Comrade)
        .where(
            Comrade.id != current_comrade.id,
            Comrade.is_active == True,
            (Comrade.username.icontains(clean_query, autoescape=True) | Comrade.callsign.icontains(clean_query, autoescape=True))
        )
        .order_by(Comrade.username)
        .limit(20)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/me/avatar", status_code=204)
async def set_avatar(
    request: Request,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    if request.headers.get("content-type") != "image/jpeg":
        raise HTTPException(415, "Expected a JPEG image")
    image = bytearray()
    async for chunk in request.stream():
        if len(image) + len(chunk) > 512 * 1024:
            raise HTTPException(413, "Image exceeds 512 KiB")
        image.extend(chunk)
    if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise HTTPException(422, "Invalid JPEG image")
    statement = insert(ComradeAvatar).values(comrade_id=current.id, image=bytes(image))
    await db.execute(statement.on_conflict_do_update(
        index_elements=[ComradeAvatar.comrade_id], set_={"image": statement.excluded.image}
    ))
    await db.commit()
    return Response(status_code=204)


@router.delete("/me/avatar", status_code=204)
async def remove_avatar(
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    await db.execute(delete(ComradeAvatar).where(ComradeAvatar.comrade_id == current.id))
    await db.commit()
    return Response(status_code=204)


@router.get("/avatar/{comrade_id}", response_class=Response)
async def get_avatar(
    comrade_id: uuid.UUID,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(ComradeAvatar.image).join(Comrade).where(
            ComradeAvatar.comrade_id == comrade_id, Comrade.is_active.is_(True)
        )
    )
    image = result.scalar_one_or_none()
    if image is None:
        raise HTTPException(404, "Profile photo not found")
    return Response(image, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/contacts", response_model=list[ComradeOut])
async def list_contacts(current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    return (await db.scalars(select(Comrade).join(Contact, Contact.person_id == Comrade.id).where(
        Contact.owner_id == current.id, Comrade.is_active.is_(True)
    ).order_by(Comrade.callsign, Comrade.username))).all()


@router.put("/contacts/{person_id}", status_code=204)
async def save_contact(person_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    if person_id == current.id:
        raise HTTPException(422, "Cannot add yourself")
    person = await db.get(Comrade, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Person not found")
    await db.execute(insert(Contact).values(owner_id=current.id, person_id=person_id).on_conflict_do_nothing())
    await db.commit()
    return Response(status_code=204)


@router.delete("/contacts/{person_id}", status_code=204)
async def delete_contact(person_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    await db.execute(delete(Contact).where(Contact.owner_id == current.id, Contact.person_id == person_id))
    await db.commit()
    return Response(status_code=204)
