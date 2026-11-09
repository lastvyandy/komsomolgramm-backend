import uuid
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, or_
from app.api.deps import get_current_comrade
from app.db.session import get_async_db
from app.models.models import AutoDeletePolicy, AutoDeleteDeadline, SovietMember
from app.api.v1.endpoints.dispatches import require_membership
from app.services.councils import require_council_publisher

router = APIRouter()

class Policy(BaseModel):
    seconds: int = Field(default=0, strict=True, ge=0, le=315360000)
    for_everyone: bool = False

    @model_validator(mode="after")
    def minimum(self):
        if 0 < self.seconds < 600:
            raise ValueError("Minimum duration is 10 minutes")
        return self

@router.get("/{chat_id}", response_model=Policy)
async def get_policy(chat_id: uuid.UUID, current=Depends(get_current_comrade), db=Depends(get_async_db)):
    await require_membership(db, chat_id, current)
    value = await db.get(AutoDeletePolicy, (chat_id, current.id))
    return Policy(seconds=value.seconds, for_everyone=value.for_everyone) if value else Policy()

@router.put("/{chat_id}", response_model=Policy)
async def set_policy(chat_id: uuid.UUID, payload: Policy, current=Depends(get_current_comrade), db=Depends(get_async_db)):
    await require_membership(db, chat_id, current)
    if payload.seconds and payload.for_everyone:
        await require_council_publisher(db, chat_id, current.id)
    value = await db.get(AutoDeletePolicy, (chat_id, current.id))
    if value is None:
        value = AutoDeletePolicy(soviet_id=chat_id, comrade_id=current.id)
        db.add(value)
    value.seconds = payload.seconds
    value.for_everyone = payload.for_everyone
    await db.commit()
    return payload

@router.get("/{chat_id}/deadlines")
async def deadlines(chat_id: uuid.UUID, after: uuid.UUID | None = None,
                    limit: int = Query(200, ge=1, le=200),
                    current=Depends(get_current_comrade), db=Depends(get_async_db)):
    await require_membership(db, chat_id, current)
    conditions = [AutoDeleteDeadline.soviet_id == chat_id,
        or_(AutoDeleteDeadline.for_everyone.is_(True), AutoDeleteDeadline.comrade_id == current.id)]
    if after is not None:
        conditions.append(AutoDeleteDeadline.dispatch_id > after)
    rows = (await db.scalars(select(AutoDeleteDeadline).where(*conditions)
        .order_by(AutoDeleteDeadline.dispatch_id, AutoDeleteDeadline.expires_at).limit(limit))).all()
    # A message can have a global deadline and the reader's own local deadline.
    # Pagination groups by message id so neither deadline is skipped at a boundary.
    if rows:
        last = rows[-1].dispatch_id
        rows = [r for r in rows if r.dispatch_id != last] + list((await db.scalars(
            select(AutoDeleteDeadline).where(*conditions, AutoDeleteDeadline.dispatch_id == last))).all())
    return [{"id": str(r.dispatch_id), "expires_at": r.expires_at.isoformat()} for r in rows]
