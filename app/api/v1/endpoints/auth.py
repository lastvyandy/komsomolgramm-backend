import uuid
import re
import json
import hashlib

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_password_hash, verify_password, create_access_token
from app.db.session import get_async_db
from app.models.models import Comrade, ComradeAvatar, ProfilePhoto, PushDevice, Contact, ComradePresence
from app.schemas.comrade import ComradeCreate, ComradeOut, Token
from app.api.deps import get_current_comrade

router = APIRouter()

@router.post("/register", response_model=ComradeOut, status_code=status.HTTP_201_CREATED)
async def register_comrade(
    comrade_in: ComradeCreate,
    db: AsyncSession = Depends(get_async_db)
):
    username = comrade_in.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,50}", username):
        raise HTTPException(422, "Username must contain 3-50 letters, digits or underscores")
    query = select(Comrade).where(func.lower(Comrade.username) == username)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El nombre de camarada ya se encuentra registrado en el censo estatal."
        )

    comrade = Comrade(
        username=username,
        callsign=comrade_in.callsign,
        hashed_password=get_password_hash(comrade_in.password),
        is_active=True
    )
    db.add(comrade)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Username already in use")
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
    callsign: str | None = Field(default=None, min_length=2, max_length=100)
    username: str | None = Field(default=None, min_length=3, max_length=50)
    bio: str | None = Field(default=None, max_length=280)


@router.patch("/me", response_model=ComradeOut)
async def update_profile(
    profile: ProfileUpdate,
    current: Comrade = Depends(get_current_comrade),
    db: AsyncSession = Depends(get_async_db),
):
    if not profile.model_fields_set or any(getattr(profile, key) is None for key in profile.model_fields_set):
        raise HTTPException(422, "Provide non-null profile fields")
    if profile.callsign is not None:
        name = profile.callsign.strip()
        if len(name) < 2:
            raise HTTPException(422, "Display name must contain at least two characters")
        current.callsign = name
    if profile.username is not None and profile.username != current.username:
        username = profile.username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{3,50}", username):
            raise HTTPException(422, "Username must contain 3-50 letters, digits or underscores")
        other = await db.scalar(select(Comrade.id).where(
            func.lower(Comrade.username) == username, Comrade.id != current.id))
        if other is not None:
            raise HTTPException(409, "Username already in use")
        current.username = username
    if profile.bio is not None:
        current.bio = profile.bio.strip()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Username already in use")
    await db.refresh(current)
    return current


@router.get("/profile/{person_id}", response_model=ComradeOut)
async def person_profile(person_id: uuid.UUID, current: Comrade = Depends(get_current_comrade),
                         db: AsyncSession = Depends(get_async_db)):
    person = await db.get(Comrade, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Person not found")
    return person


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


@router.get("/me/push-status")
async def push_status(current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    from app.services.push_notifications import push_ready
    devices = (await db.scalars(select(PushDevice.token_hash).where(PushDevice.comrade_id == current.id))).all()
    return {"configured": push_ready(), "registered_devices": len(devices)}


@router.post("/me/push-test", status_code=202)
async def test_push_device(payload: PushTokenUpdate,
                           current: Comrade = Depends(get_current_comrade),
                           db: AsyncSession = Depends(get_async_db)):
    from app.services.push_notifications import push_ready, send_test_push
    from app.core.redis import get_redis_client
    if not push_ready():
        raise HTTPException(503, "Firebase is not configured on the server")
    device = await db.scalar(select(PushDevice).where(
        PushDevice.token_hash == hashlib.sha256(payload.token.encode()).hexdigest(),
        PushDevice.comrade_id == current.id))
    if device is None:
        raise HTTPException(404, "Register this device first")
    if not await get_redis_client().set(f"push-test:{current.id}", "1", nx=True, ex=30):
        raise HTTPException(429, "Wait 30 seconds before another test")
    if not await send_test_push(device.token, current.id):
        raise HTTPException(502, "Firebase did not accept the test message")
    return {"accepted": True}


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
    existing_photos = (await db.scalars(select(ProfilePhoto.id).where(ProfilePhoto.comrade_id == current.id))).all()
    if len(existing_photos) >= 20:
        raise HTTPException(409, "At most 20 profile photos")
    if not existing_photos:
        old = await db.scalar(select(ComradeAvatar.image).where(ComradeAvatar.comrade_id == current.id))
        if old:
            db.add(ProfilePhoto(comrade_id=current.id, image=old))
            await db.flush()
    db.add(ProfilePhoto(comrade_id=current.id, image=bytes(image)))
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
    primary = await db.scalar(select(ComradeAvatar.image).where(ComradeAvatar.comrade_id == current.id))
    photo_id = await db.scalar(select(ProfilePhoto.id).where(ProfilePhoto.comrade_id == current.id, ProfilePhoto.image == primary).order_by(ProfilePhoto.created_at.desc()).limit(1))
    if photo_id:
        await db.execute(delete(ProfilePhoto).where(ProfilePhoto.id == photo_id))
    next_image = await db.scalar(select(ProfilePhoto.image).where(ProfilePhoto.comrade_id == current.id).order_by(ProfilePhoto.created_at.desc(), ProfilePhoto.id.desc()).limit(1))
    await db.execute(delete(ComradeAvatar).where(ComradeAvatar.comrade_id == current.id))
    if next_image:
        db.add(ComradeAvatar(comrade_id=current.id, image=next_image))
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



class PresenceHeartbeat(BaseModel):
    session_id: uuid.UUID
    active: bool
    battery_level: int | None = Field(default=None, ge=0, le=100)
    battery_charging: bool | None = None


class PresencePrivacy(BaseModel):
    show_last_seen: bool


@router.post("/presence", status_code=204)
async def heartbeat_presence(payload: PresenceHeartbeat, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    from app.core.redis import get_redis_client
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    redis = get_redis_client()
    key = f"presence:{current.id}"
    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.zremrangebyscore(key, "-inf", now.timestamp())
        if payload.active:
            pipeline.zadd(key, {str(payload.session_id): now.timestamp() + 45})
            pipeline.expire(key, 90)
        else:
            pipeline.zrem(key, str(payload.session_id))
        await pipeline.execute()
    privacy = await db.get(ComradePresence, current.id)
    if payload.active and payload.battery_level is not None and (privacy is None or privacy.show_battery):
        await redis.set(f"battery:{current.id}", json.dumps({
            "level": payload.battery_level, "charging": payload.battery_charging,
        }), ex=90)
    statement = insert(ComradePresence).values(comrade_id=current.id, last_seen=now)
    await db.execute(statement.on_conflict_do_update(index_elements=[ComradePresence.comrade_id], set_={"last_seen": now}))
    await db.commit()
    return Response(status_code=204)


@router.get("/me/presence-privacy", response_model=PresencePrivacy)
async def presence_privacy(current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    row = await db.get(ComradePresence, current.id)
    return {"show_last_seen": row.show_last_seen if row else True}


@router.put("/me/presence-privacy", response_model=PresencePrivacy)
async def set_presence_privacy(payload: PresencePrivacy, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    statement = insert(ComradePresence).values(comrade_id=current.id, show_last_seen=payload.show_last_seen)
    await db.execute(statement.on_conflict_do_update(index_elements=[ComradePresence.comrade_id], set_={"show_last_seen": payload.show_last_seen}))
    await db.commit()
    return payload


class BatteryPrivacy(BaseModel):
    show_battery: bool


@router.get("/me/battery-privacy", response_model=BatteryPrivacy)
async def battery_privacy(current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    row = await db.get(ComradePresence, current.id)
    return {"show_battery": row.show_battery if row else True}


@router.put("/me/battery-privacy", response_model=BatteryPrivacy)
async def set_battery_privacy(payload: BatteryPrivacy, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    from app.core.redis import get_redis_client
    statement = insert(ComradePresence).values(comrade_id=current.id, show_battery=payload.show_battery)
    await db.execute(statement.on_conflict_do_update(
        index_elements=[ComradePresence.comrade_id], set_={"show_battery": payload.show_battery}))
    await db.commit()
    if not payload.show_battery:
        await get_redis_client().delete(f"battery:{current.id}")
    return payload


@router.get("/presence/{person_id}")
async def get_presence(person_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    from app.core.redis import get_redis_client
    from datetime import datetime, timezone
    target = await db.get(Comrade, person_id)
    if target is None or not target.is_active:
        raise HTTPException(404, "Person not found")
    # Exact activity is reciprocal: hiding your own activity hides others' exact activity too.
    viewer = await db.get(ComradePresence, current.id)
    row = await db.get(ComradePresence, person_id)
    visible = (viewer is None or viewer.show_last_seen) and (row is None or row.show_last_seen)
    if not visible:
        return {"online": None, "last_seen": None, "hidden": True}
    count = await get_redis_client().zcount(f"presence:{person_id}", datetime.now(timezone.utc).timestamp(), "+inf")
    battery = None
    if count and (row is None or row.show_battery):
        raw = await get_redis_client().get(f"battery:{person_id}")
        if raw:
            try:
                battery = json.loads(raw)
            except (ValueError, TypeError):
                pass
    return {"online": bool(count), "last_seen": row.last_seen if row else None, "hidden": False,
            "battery_level": battery.get("level") if battery else None,
            "battery_charging": battery.get("charging") if battery else None}


@router.get("/photos/{person_id}")
async def profile_photos(person_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    if not await db.scalar(select(Comrade.id).where(Comrade.id == person_id, Comrade.is_active.is_(True))):
        raise HTTPException(404, "User not found")
    rows = (await db.execute(select(ProfilePhoto.id, ProfilePhoto.created_at).where(ProfilePhoto.comrade_id == person_id).order_by(ProfilePhoto.created_at.desc(), ProfilePhoto.id.desc()))).all()
    return [{"id": str(row.id)} for row in rows]


@router.get("/photos/{person_id}/{photo_id}")
async def profile_photo(person_id: uuid.UUID, photo_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    image = await db.scalar(select(ProfilePhoto.image).join(Comrade, Comrade.id == ProfilePhoto.comrade_id).where(ProfilePhoto.comrade_id == person_id, ProfilePhoto.id == photo_id, Comrade.is_active.is_(True)))
    if image is None:
        raise HTTPException(404, "Photo not found")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.put("/me/photos/{photo_id}/primary", status_code=204)
async def primary_photo(photo_id: uuid.UUID, current: Comrade = Depends(get_current_comrade), db: AsyncSession = Depends(get_async_db)):
    image = await db.scalar(select(ProfilePhoto.image).where(ProfilePhoto.id == photo_id, ProfilePhoto.comrade_id == current.id))
    if image is None:
        raise HTTPException(404, "Photo not found")
    statement = insert(ComradeAvatar).values(comrade_id=current.id, image=image)
    await db.execute(statement.on_conflict_do_update(index_elements=[ComradeAvatar.comrade_id], set_={"image": statement.excluded.image}))
    await db.commit()
    return Response(status_code=204)

