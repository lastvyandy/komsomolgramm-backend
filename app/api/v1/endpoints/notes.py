import uuid, hashlib, json
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, exists, or_, and_, func, delete
from app.api.deps import get_current_comrade
from app.db.session import get_async_db
from app.models.models import StatusNote, Contact, Comrade, utc_now
from app.api.v1.endpoints.soviets import get_or_create_direct_soviet
from app.api.v1.endpoints.dispatches import create_queued_dispatch
from app.schemas.dispatch import QueuedDispatchCreate

router = APIRouter()

async def can_see(db,note,viewer):
    from app.services.privacy import allows
    return await allows(db,note.owner_id,"notes",viewer)


class NoteIn(BaseModel):
    content: str = Field(min_length=1,max_length=60)
    @field_validator("content")
    @classmethod
    def clean(cls,value):
        value=value.strip()
        if not value:raise ValueError("Empty note")
        return value

@router.get("")
async def feed(request: Request, current=Depends(get_current_comrade),db=Depends(get_async_db)):
    rule=func.coalesce(Comrade.privacy["notes"].as_string(),"contacts")
    visibility=or_(StatusNote.owner_id==current.id,rule=="all",and_(rule=="contacts",exists(
        select(Contact.owner_id).where(Contact.owner_id==StatusNote.owner_id,Contact.person_id==current.id))))
    rows=(await db.execute(select(StatusNote,Comrade).join(Comrade,Comrade.id==StatusNote.owner_id).where(
        visibility,StatusNote.expires_at>utc_now(),Comrade.is_active.is_(True))
        .order_by((StatusNote.owner_id == current.id).desc(), StatusNote.created_at.desc()).limit(200))).all()
    result = [{"id":str(n.id),"owner_id":str(n.owner_id),"name":p.callsign,"content":n.content,
        "expires_at":n.expires_at.isoformat()} for n,p in rows if await can_see(db,n,current.id)]

    # Per-viewer content fingerprint includes privacy filtering, expiry and profile names.
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Authorization"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)

@router.put("/me")
async def publish(payload:NoteIn,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    # Lock owner to serialize replacements without affecting anyone else's note.
    await db.execute(select(Comrade.id).where(Comrade.id==current.id).with_for_update())
    await db.execute(delete(StatusNote).where(StatusNote.owner_id==current.id))
    note=StatusNote(owner_id=current.id,id=uuid.uuid4(),content=payload.content,
        created_at=utc_now(),expires_at=utc_now()+timedelta(hours=24))
    db.add(note);await db.commit()
    return {"id":str(note.id)}

@router.delete("/me",status_code=204)
async def remove(current=Depends(get_current_comrade),db=Depends(get_async_db)):
    await db.execute(delete(StatusNote).where(StatusNote.owner_id==current.id));await db.commit()
    return Response(status_code=204)

class NoteReply(BaseModel):
    client_id: uuid.UUID
    content: str = Field(min_length=1,max_length=1000)
    reaction: bool = False

@router.post("/{note_id}/reply")
async def reply(note_id:uuid.UUID,payload:NoteReply,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    note=await db.scalar(select(StatusNote).where(StatusNote.id==note_id,
        StatusNote.expires_at>utc_now()).with_for_update())
    if note is None or not await can_see(db,note,current.id):raise HTTPException(404,"Note expired or unavailable")
    if note.owner_id==current.id:raise HTTPException(422,"Cannot reply to your own note")
    content=payload.content.strip()
    if not content:raise HTTPException(422,"Empty reply")
    if payload.reaction and content not in ["❤️","😂","😮","😢","🔥","👏"]:
        raise HTTPException(422,"Invalid reaction")
    owner=await db.get(Comrade,note.owner_id)
    if not owner or not owner.is_active:raise HTTPException(404,"Author unavailable")
    quote=note.content
    chat=await get_or_create_direct_soviet(note.owner_id,current,db)
    result=await create_queued_dispatch(chat.id,QueuedDispatchCreate(client_id=payload.client_id,
        content=f"💭 «{quote}»\n{content}"),current,db)
    return {"chat_id":str(chat.id),"message_id":str(result.id)}
