from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.api.deps import get_current_comrade
from app.db.session import get_async_db
from app.models.models import ComradePresence
from app.services.privacy import KEYS
router=APIRouter()

async def settings(current,db):
    row=await db.get(ComradePresence,current.id)
    values={key:"all" for key in KEYS}
    values.update(notes="contacts",relationship="all" if current.show_relationship else "nobody",
        song="all" if current.show_favorite_song else "nobody",app_usage="all" if current.share_app_usage else "nobody",
        sleep="all" if current.sleep_enabled else "nobody",last_seen="all" if row is None or row.show_last_seen else "nobody",
        battery="all" if row is None or row.show_battery else "nobody")
    values.update(current.privacy or {})
    return values

@router.get("")
async def get_settings(current=Depends(get_current_comrade),db=Depends(get_async_db)):
    return await settings(current,db)

class Change(BaseModel):
    key:str
    audience:Literal["all","contacts","nobody"]

@router.put("")
async def change(payload:Change,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    if payload.key not in KEYS:raise HTTPException(422,"Unknown privacy setting")
    value=payload.audience!="nobody"
    current.privacy={**(current.privacy or {}),payload.key:payload.audience}
    for key,field in {"song":"show_favorite_song","relationship":"show_relationship","app_usage":"share_app_usage"}.items():
        if payload.key==key:setattr(current,field,value)
    if payload.key in ("last_seen","battery"):
        row=await db.get(ComradePresence,current.id)
        if row is None:row=ComradePresence(comrade_id=current.id);db.add(row)
        setattr(row,"show_last_seen" if payload.key=="last_seen" else "show_battery",value)
    await db.commit()
    return await settings(current,db)
