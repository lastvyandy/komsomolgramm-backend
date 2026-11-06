from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_password_hash, verify_password, create_access_token
from app.db.session import get_async_db
from app.models.models import Comrade
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
            (Comrade.username.ilike(f"%{clean_query}%") | Comrade.callsign.ilike(f"%{clean_query}%"))
        )
        .limit(20)
    )
    result = await db.execute(stmt)
    return result.scalars().all()