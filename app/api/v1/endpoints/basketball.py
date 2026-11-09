"""Two-player chat challenges. Scores are computed from shots, never client totals."""
import copy, hashlib, math, uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.api.deps import get_current_comrade
from app.db.session import get_async_db
from app.models.models import Comrade, Dispatch, Soviet, SovietMember, utc_now
from app.api.v1.endpoints.dispatches import require_membership, visible_to
from app.api.v1.endpoints.ws import get_socket_manager
router=APIRouter()

class Challenge(BaseModel):
    model_config=ConfigDict(extra='forbid')
    client_id:uuid.UUID
    opponent_id:uuid.UUID
class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:str=Field(pattern='^(accept|decline|cancel|start|finish)$')
class Shot(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:uuid.UUID
    sequence:int=Field(ge=0,le=100)
    dx:float=Field(ge=-1,le=1,allow_inf_nan=False)
    dy:float=Field(ge=-1,le=-.035,allow_inf_nan=False)
    phase:float=Field(ge=0,le=180,allow_inf_nan=False)

def instant(value):return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
def ball_x(game,sequence):
    n=int(hashlib.sha256((str(game)+':'+str(sequence)).encode()).hexdigest()[:8],16)
    return .2+(n%6001)/10000

def hoop_x(score,phase):
    return .5 if score<10 else .5+.24*math.sin(phase*(1.6 if score<20 else 2.4))

def trajectory(game,score,dx,dy,phase):
    # Identical normalized court and projectile constants in Android's renderer.
    target=hoop_x(score,phase)
    end=(1.9+math.sqrt(1.9**2-4*1.4*(.82-.28)))/(2*1.4)
    vx=max(-2.,min(2.,dx/-dy*1.9))
    hit=abs(ball_x(game,score)+vx*end-target)<=.055
    return hit,target,end,vx

def public(message):
    return dict(message.rich, id=str(message.id), chat_id=str(message.soviet_id), server_now=utc_now().isoformat())

async def message(db,chat,identifier,current):
    await require_membership(db,chat,current)
    row=await db.scalar(select(Dispatch).where(Dispatch.id==identifier,Dispatch.soviet_id==chat,
        Dispatch.is_purged.is_(False),visible_to(current.id)).with_for_update())
    if row is None or not row.rich or row.rich.get('kind')!='basketball':raise HTTPException(404,'Challenge unavailable')
    # Always replace JSON rather than mutating it in place (SQLAlchemy tracks assignment).
    row.rich=copy.deepcopy(row.rich)
    return row

async def publish(row,sender):
    try:
        await get_socket_manager().publish_dispatch(row.soviet_id,{'type':'dispatch','id':str(row.id),
            'soviet_id':str(row.soviet_id),'content':row.content,'rich':row.rich,
            'sender_id':str(sender.id),'sender_callsign':sender.callsign,'sender_username':sender.username,
            'bubble_color':sender.bubble_color,'created_at':row.created_at.isoformat()})
    except Exception:pass # Durable state is also fetched when opening/polling the card.

async def settle(db,row):
    data=row.rich
    if data['status']!='active':return None
    now=utc_now()
    for p in data['players']:
        if p['started'] and (now-instant(p['started'])).total_seconds()>=180:p['done']=True
    if now>=instant(data['expires_at']):
        for p in data['players']:p['done']=True
    if not all(p['done'] for p in data['players']):return None
    a,b=data['players'];data['status']='finished'
    data['winner_id']=None if a['score']==b['score'] else max((a,b),key=lambda p:p['score'])['id']
    identifier=uuid.uuid5(row.id,'basketball-result')
    result=await db.get(Dispatch,identifier)
    if result is None:
        summary={'kind':'basketball_result','players':[{'id':p['id'],'name':p['name'],'score':p['score']} for p in data['players']],
            'winner_id':data['winner_id']}
        winner=next((p['name'] for p in data['players'] if p['id']==data['winner_id']), '=')
        result=Dispatch(id=identifier,soviet_id=row.soviet_id,sender_id=row.sender_id,
            content=f"🏀 {winner} · {a['name']} {a['score']} — {b['score']} {b['name']}",rich=summary)
        db.add(result);await db.flush()
    return result

async def save(db,row):
    result=await settle(db,row)
    # Reassign after nested writes so the JSON column is marked dirty.
    row.rich=copy.deepcopy(row.rich)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row,'rich')
    await db.commit()
    if result:
        sender=await db.get(Comrade,row.sender_id)
        if sender:await publish(result,sender)
    return public(row)

@router.get('/status')
async def availability(current=Depends(get_current_comrade)):
    return {'available':True,'version':1}

@router.post('/{chat}',status_code=201)
async def create(chat:uuid.UUID,payload:Challenge,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    await require_membership(db,chat,current)
    room=await db.get(Soviet,chat)
    if room.council_key is not None:raise HTTPException(403,'Challenges are for chats and groups')
    existing=await db.get(Dispatch,payload.client_id)
    if existing:
        if existing.soviet_id!=chat or existing.sender_id!=current.id or not existing.rich or existing.rich.get('kind')!='basketball' or existing.rich['players'][1]['id']!=str(payload.opponent_id):raise HTTPException(409,'Identifier already used')
        return public(existing)
    if payload.opponent_id==current.id:raise HTTPException(422,'Choose another player')
    await require_membership(db,chat,type('Member',(),{'id':payload.opponent_id})())
    opponent=await db.get(Comrade,payload.opponent_id)
    if not opponent or not opponent.is_active:raise HTTPException(404,'Player unavailable')
    players=[{'id':str(p.id),'name':p.callsign,'score':0,'sequence':0,'done':False,'started':None,'last_phase':-1.,'last_request':None,'last_shot':None} for p in (current,opponent)]
    row=Dispatch(id=payload.client_id,soviet_id=chat,sender_id=current.id,content=f'🏀 {current.callsign} → {opponent.callsign}',
        rich={'kind':'basketball','game_id':str(payload.client_id),'status':'invited','players':players,
            'expires_at':(utc_now()+timedelta(hours=24)).isoformat(),'winner_id':None})
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback()
        row=await db.get(Dispatch,payload.client_id)
        if row is None or row.soviet_id!=chat or row.sender_id!=current.id or not row.rich or row.rich.get('kind')!='basketball' or row.rich['players'][1]['id']!=str(payload.opponent_id):
            raise HTTPException(409,'Identifier already used')
        return public(row)
    await publish(row,current)
    return public(row)

@router.get('/{chat}/{identifier}')
async def read(chat:uuid.UUID,identifier:uuid.UUID,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    row=await message(db,chat,identifier,current)
    if row.rich['status']=='invited' and utc_now()>=instant(row.rich['expires_at']):row.rich['status']='expired'
    return await save(db,row)

@router.post('/{chat}/{identifier}/action')
async def action(chat:uuid.UUID,identifier:uuid.UUID,payload:Action,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    row=await message(db,chat,identifier,current);data=row.rich
    player=next((p for p in data['players'] if p['id']==str(current.id)),None)
    if player is None:raise HTTPException(403,'Only the two players may act')
    if utc_now()>=instant(data['expires_at']):
        if data['status']=='invited':data['status']='expired'
        return await save(db,row)
    act=payload.action
    if act in ('accept','decline'):
        if player is not data['players'][1]:raise HTTPException(403,'Only the invited player may answer')
        if data['status']=='invited':data['status']='active' if act=='accept' else 'declined'
        elif act=='accept' and data['status']=='active':pass
        else:raise HTTPException(409,'Invitation already answered')
    elif act=='cancel':
        if str(current.id)!=data['players'][0]['id'] or data['status']!='invited':raise HTTPException(409,'Cannot cancel this invitation')
        data['status']='cancelled'
    else:
        if data['status']!='active':raise HTTPException(409,'Challenge is not active')
        if act=='start':
            if player['done']:raise HTTPException(409,'Turn already completed')
            if player['started'] is None:player['started']=utc_now().isoformat()
        else:player['done']=True
    return await save(db,row)

@router.post('/{chat}/{identifier}/shot')
async def shot(chat:uuid.UUID,identifier:uuid.UUID,payload:Shot,current=Depends(get_current_comrade),db=Depends(get_async_db)):
    row=await message(db,chat,identifier,current);data=row.rich
    player=next((p for p in data['players'] if p['id']==str(current.id)),None)
    if player is None:raise HTTPException(403,'Not a player')
    if player['last_request']==str(payload.request_id):return public(row)
    if data['status']!='active' or player['done'] or player['started'] is None:raise HTTPException(409,'Turn unavailable')
    elapsed=(utc_now()-instant(player['started'])).total_seconds()
    if elapsed>=180 or utc_now()>=instant(data['expires_at']):
        player['done']=True;return await save(db,row)
    if payload.sequence!=player['sequence']:raise HTTPException(409,'Shot sequence mismatch')
    if payload.phase>elapsed+2 or payload.phase<player['last_phase']+.65:raise HTTPException(422,'Invalid shot timing')
    made,target,end,vx=trajectory(row.id,player['score'],payload.dx,payload.dy,payload.phase)
    player['last_shot']={'made':made,'target':target,'duration':end,'vx':vx}
    player['last_request']=str(payload.request_id);player['last_phase']=payload.phase;player['sequence']+=1
    if made:player['score']+=1
    else:player['done']=True
    if player['score']>=100:player['done']=True
    return await save(db,row)
