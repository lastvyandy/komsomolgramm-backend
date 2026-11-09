"""Authenticated audio-only call signaling; audio is transported by WebRTC."""
import asyncio, base64, hashlib, hmac, logging, os, time, uuid
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, and_
from app.api.deps import get_current_comrade
from app.db.session import get_async_db
from app.models.models import VoiceCall, Soviet, SovietType, SovietMember, Comrade, utc_now
from app.api.v1.endpoints.dispatches import require_membership

router = APIRouter()

def aware(value):
    from datetime import timezone
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def expire(call):
    now = utc_now()
    if call.status == "ringing" and now - aware(call.created_at) > timedelta(seconds=60):
        call.status = "missed"; call.ended_at = now
    elif call.status == "active" and (now - min(aware(call.caller_seen), aware(call.callee_seen)) > timedelta(seconds=60)
            or now - aware(call.created_at) > timedelta(hours=4)):
        call.status = "ended"; call.ended_at = now
    if call.ended_at:
        call.offer = call.answer = None; call.caller_ice = []; call.callee_ice = []

def public(call, viewer, peer):
    return {"id":str(call.id), "chat_id":str(call.soviet_id), "incoming":call.callee_id == viewer,
        "peer_id":str(peer.id), "peer_name":peer.callsign, "status":call.status,
        "created_at":call.created_at.isoformat(), "answered_at":call.answered_at.isoformat() if call.answered_at else None,
        "ended_at":call.ended_at.isoformat() if call.ended_at else None}

async def authorized(db, identifier, viewer):
    call = await db.scalar(select(VoiceCall).where(VoiceCall.id == identifier).with_for_update())
    if not call or viewer not in (call.caller_id, call.callee_id):
        raise HTTPException(404, "Call unavailable")
    await require_membership(db,call.soviet_id,await db.get(Comrade,viewer))
    expire(call)
    return call

@router.get("/ice")
async def ice(current=Depends(get_current_comrade)):
    host = os.environ.get("CALL_TURN_HOST", "").strip()
    secret = os.environ.get("CALL_TURN_SECRET", "")
    if not host or not secret:
        raise HTTPException(503,"Voice calls require TURN configuration")
    username = f"{int(time.time())+18000}:{current.id}"
    credential = base64.b64encode(hmac.new(secret.encode(),username.encode(),hashlib.sha1).digest()).decode()
    return {"servers":[{"urls":[f"stun:{host}:3478"]},
        {"urls":[f"turn:{host}:3478?transport=udp",f"turn:{host}:3478?transport=tcp"],
         "username":username,"credential":credential}]}

@router.get("/active")
async def active(current=Depends(get_current_comrade),db=Depends(get_async_db)):
    calls = (await db.scalars(select(VoiceCall).where(or_(VoiceCall.caller_id==current.id,
        VoiceCall.callee_id==current.id),VoiceCall.status.in_(["ringing","active"]))
        .order_by(VoiceCall.created_at.desc()).with_for_update())).all()
    result = None
    for call in calls:
        expire(call)
        if result is None and call.status in ("ringing","active"):
            peer = await db.get(Comrade,call.caller_id if call.callee_id==current.id else call.callee_id)
            result = public(call,current.id,peer)
    await db.commit()
    return result

@router.get("/history")
async def history(current=Depends(get_current_comrade),db=Depends(get_async_db)):
    calls = (await db.scalars(select(VoiceCall).where(or_(VoiceCall.caller_id==current.id,
        VoiceCall.callee_id==current.id)).order_by(VoiceCall.created_at.desc()).limit(100))).all()
    result=[]
    for call in calls:
        expire(call)
        peer=await db.get(Comrade,call.caller_id if call.callee_id==current.id else call.callee_id)
        result.append(public(call,current.id,peer))
    await db.commit();return result

class Start(BaseModel):
    chat_id: uuid.UUID

@router.post("",status_code=201)
async def start(payload:Start,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    await ice(current)
    await require_membership(db,payload.chat_id,current)
    chat=await db.get(Soviet,payload.chat_id)
    if chat.soviet_type != SovietType.DIRECT or chat.council_key:
        raise HTTPException(422,"Only direct voice calls are supported")
    members=(await db.scalars(select(Comrade).join(SovietMember,SovietMember.comrade_id==Comrade.id)
        .where(SovietMember.soviet_id==chat.id,Comrade.is_active.is_(True)).order_by(Comrade.id).with_for_update())).all()
    if len(members)!=2: raise HTTPException(422,"A call requires two participants")
    peer=next(p for p in members if p.id != current.id)
    existing=(await db.scalars(select(VoiceCall).where(VoiceCall.status.in_(["ringing","active"]),
        or_(VoiceCall.caller_id.in_([current.id,peer.id]),VoiceCall.callee_id.in_([current.id,peer.id]))))).all()
    for call in existing:
        expire(call)
        if call.status in ("ringing","active"):raise HTTPException(409,"Participant is busy")
    call=VoiceCall(soviet_id=chat.id,caller_id=current.id,callee_id=peer.id)
    db.add(call);await db.flush();result=public(call,current.id,peer);await db.commit()
    try:
        from app.services.push_notifications import send_call_push
        await send_call_push(peer.id,call.id,current.callsign)
    except Exception:logging.getLogger(__name__).exception("Call saved but push failed")
    return result

class Action(BaseModel):
    action: Literal["accept","decline","end"]

@router.post("/{identifier}/action")
async def action(identifier:uuid.UUID,payload:Action,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    call=await authorized(db,identifier,current.id)
    if payload.action=="accept":
        if call.callee_id!=current.id:raise HTTPException(403,"Only recipient can accept")
        if call.status=="ringing":
            call.status="active";call.answered_at=utc_now();call.caller_seen=call.callee_seen=utc_now()
        elif call.status!="active":raise HTTPException(409,"Call already ended")
    elif call.status in ("ringing","active"):
        call.status="declined" if payload.action=="decline" and call.callee_id==current.id else "ended"
        call.ended_at=utc_now();expire(call)
    peer=await db.get(Comrade,call.caller_id if call.callee_id==current.id else call.callee_id)
    result=public(call,current.id,peer);await db.commit()
    if call.ended_at:
        try:
            from app.services.push_notifications import send_call_push
            await send_call_push(peer.id,call.id,current.callsign,"call_cancel")
        except Exception:logging.getLogger(__name__).exception("Call ended but cancellation push failed")
    return result

class Candidate(BaseModel):
    sdpMid: str | None = Field(default=None,max_length=64)
    sdpMLineIndex: int = Field(ge=0,le=10)
    sdp: str = Field(min_length=1,max_length=2048)
class Signal(BaseModel):
    sdp: str | None = Field(default=None,max_length=65536)
    candidates: list[Candidate] = Field(default_factory=list,max_length=32)

@router.post("/{identifier}/signal")
async def signal(identifier:uuid.UUID,payload:Signal,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    call=await authorized(db,identifier,current.id)
    if call.status not in ("ringing","active"):raise HTTPException(409,"Call ended")
    caller=call.caller_id==current.id
    if not caller and call.status!="active":raise HTTPException(403,"Accept first")
    if payload.sdp is not None:
        # An audio-only offer must not request camera/data tracks.
        if not payload.sdp.startswith("v=0") or "m=video" in payload.sdp or "m=application" in payload.sdp:
            raise HTTPException(422,"Audio SDP required")
        key="offer" if caller else "answer"
        old=getattr(call,key)
        if old and old!=payload.sdp:raise HTTPException(409,"SDP already set")
        setattr(call,key,payload.sdp)
    key="caller_ice" if caller else "callee_ice"
    candidates=list(getattr(call,key) or [])
    for candidate in payload.candidates:
        value=candidate.model_dump()
        if value not in candidates:candidates.append(value)
    if len(candidates)>256:raise HTTPException(422,"Too many candidates")
    setattr(call,key,candidates)
    if caller:call.caller_seen=utc_now()
    else:call.callee_seen=utc_now()
    peer=await db.get(Comrade,call.callee_id if caller else call.caller_id)
    result=public(call,current.id,peer)
    result.update(sdp=call.answer if caller else call.offer,candidates=call.callee_ice if caller else call.caller_ice)
    await db.commit();return result
