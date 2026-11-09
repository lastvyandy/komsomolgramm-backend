import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_async_db
from app.models.models import Comrade

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

async def get_current_comrade(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db)
) -> Comrade:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Salvoconducto inválido o expirado. Identifíquese ante el comisariado.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
        
    subject: str = payload.get("sub")
    if subject is None:
        raise credentials_exception
        
    try:
        comrade_id = uuid.UUID(subject)
    except ValueError:
        raise credentials_exception

    query = select(Comrade).where(Comrade.id == comrade_id)
    result = await db.execute(query)
    comrade = result.scalar_one_or_none()

    if comrade is None:
        raise credentials_exception
    if not comrade.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este camarada ha sido suspendido de sus funciones."
        )

    from app.services.privacy import load_viewer
    await load_viewer(db, comrade.id)
    return comrade