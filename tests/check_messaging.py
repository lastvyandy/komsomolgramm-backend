import os, sys, types, asyncio, uuid, tempfile
from datetime import datetime, timezone, timedelta
os.environ['SECRET_KEY']='local-messaging-check-only-not-a-production-key'
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
# Push transport is unrelated to these endpoints and intentionally disabled in this isolated check.
push=types.ModuleType('app.services.push_notifications')
async def no_push(*args): pass
push.send_push=no_push
sys.modules['app.services.push_notifications']=push
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
import fakeredis.aioredis
from app.models.models import Base,Comrade,Soviet,SovietMember,Dispatch,SovietReadCursor
from app.db.session import get_async_db
from app.api.v1.endpoints import auth,dispatches,soviets,ws
from app.api.deps import get_current_comrade
from app.core.security import create_access_token
from app.services.replies import require_reply
import app.core.redis

db_path=tempfile.mktemp(suffix='.db')
engine=create_async_engine('sqlite+aiosqlite:///'+db_path,poolclass=NullPool)
sessions=async_sessionmaker(engine,expire_on_commit=False)
redis=fakeredis.aioredis.FakeRedis()
app.core.redis.get_redis_client=lambda: redis
ws.async_session_maker=sessions
class Manager:
 def __init__(self):self.clients=set();self.events=[]
 async def connect(self,room,socket): await socket.accept();self.clients.add(socket)
 def disconnect(self,room,socket):self.clients.discard(socket)
 async def publish_dispatch(self,room,event):
  self.events.append(event)
  for socket in list(self.clients):await socket.send_json(event)
manager=Manager();ws.socket_manager=manager
app=FastAPI();app.include_router(auth.router,prefix='/auth');app.include_router(soviets.router,prefix='/soviets');app.include_router(dispatches.router,prefix='/dispatches');app.include_router(ws.router,prefix='/ws')
async def db_override():
 async with sessions() as db:yield db
app.dependency_overrides[get_async_db]=db_override
ids=[uuid.uuid4() for _ in range(3)];room=uuid.uuid4();other_room=uuid.uuid4();target=uuid.uuid4();foreign=uuid.uuid4();reply_id=uuid.uuid4()
def headers(index):return {'Authorization':'Bearer '+create_access_token(ids[index])}
async def setup():
 async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
 async with sessions() as db:
  db.add_all([Comrade(id=i,username='user'+str(n),callsign='User '+str(n),hashed_password='unused') for n,i in enumerate(ids)])
  db.add_all([Soviet(id=room,name='Group'),Soviet(id=other_room,name='Other')]);await db.flush()
  db.add_all([SovietMember(soviet_id=room,comrade_id=i) for i in ids[:2]])
  db.add_all([Dispatch(id=target,soviet_id=room,sender_id=ids[0],content='Original'),Dispatch(id=foreign,soviet_id=other_room,sender_id=ids[2],content='Private'),Dispatch(id=reply_id,soviet_id=room,sender_id=ids[1],content='Reply',reply_to_id=target)])
  await db.commit()
async def http_checks():
 async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
  # Contacts remain per account and save is idempotent.
  for _ in range(2):assert (await c.put('/auth/contacts/'+str(ids[1]),headers=headers(0))).status_code==204
  assert len((await c.get('/auth/contacts',headers=headers(0))).json())==1
  assert (await c.get('/auth/contacts',headers=headers(1))).json()==[]
  # Replies survive history and never leak another room's contents.
  history=await c.get(f'/dispatches/{room}',headers=headers(0));assert history.status_code==200,history.text
  quote=next(m for m in history.json() if m['id']==str(reply_id))['reply'];assert quote['content']=='Original'
  assert (await c.get(f'/dispatches/{room}/context/{target}',headers=headers(1))).status_code==200
  assert (await c.get(f'/dispatches/{room}',headers=headers(2))).status_code==403
  page=await c.get(f'/dispatches/{room}?limit=1',headers=headers(0));assert page.status_code==200
  cursor=page.json()[0]['id']
  older=await c.get(f'/dispatches/{room}?limit=5&before_id={cursor}',headers=headers(0));assert older.status_code==200
  assert all(m['id']!=cursor for m in older.json())
  bad=await c.post(f'/dispatches/{room}/attachments?name=a.jpg&reply_to_id={foreign}',headers={**headers(0),'Content-Type':'image/jpeg'},content=b'abc');assert bad.status_code==404,bad.text
  uploaded=await c.post(f'/dispatches/{room}/attachments?name=a.jpg&reply_to_id={target}',headers={**headers(1),'Content-Type':'image/jpeg'},content=b'abc');assert uploaded.status_code==201,uploaded.text
  assert uploaded.json()['reply']['id']==str(target)
  # Read cursor advances, emits an event, and never regresses.
  assert (await c.put(f'/dispatches/{room}/read/{reply_id}',headers=headers(1))).status_code==204
  readers=(await c.get(f'/dispatches/{room}/readers',headers=headers(0))).json();assert readers[0]['message_id']==str(reply_id)
  assert (await c.get(f'/dispatches/{room}/readers',headers=headers(2))).status_code==403
  # Existing group members can add recent contacts; strangers and direct rooms cannot.
  added=await c.post(f'/soviets/{room}/members',headers=headers(0),json={'member_ids':[str(ids[2])]})
  assert added.status_code==200,added.text
  assert sum(m['comrade']['id']==str(ids[2]) for m in added.json()['members'])==1
  repeated=await c.post(f'/soviets/{room}/members',headers=headers(0),json={'member_ids':[str(ids[2])]})
  assert repeated.status_code==200 and sum(m['comrade']['id']==str(ids[2]) for m in repeated.json()['members'])==1
  assert (await c.post(f'/soviets/{other_room}/members',headers=headers(0),json={'member_ids':[str(ids[1])]})).status_code==404
  assert manager.events[-1]['type']=='read'
  assert (await c.put(f'/dispatches/{room}/read/{target}',headers=headers(1))).status_code==204
  after_older=(await c.get(f'/dispatches/{room}/readers',headers=headers(0))).json()
  assert after_older[0]['message_id']==str(reply_id),after_older
  # Multiple foreground sessions: stopping one must not hide the other.
  s1,s2=str(uuid.uuid4()),str(uuid.uuid4())
  for sid in [s1,s2]:
   beat=await c.post('/auth/presence',headers=headers(1),json={'session_id':sid,'active':True});assert beat.status_code==204,beat.text
  assert (await c.get('/auth/presence/'+str(ids[1]),headers=headers(0))).json()['online'] is True
  await c.post('/auth/presence',headers=headers(1),json={'session_id':s1,'active':False})
  assert (await c.get('/auth/presence/'+str(ids[1]),headers=headers(0))).json()['online'] is True
  # Privacy is enforced server-side in both directions and survives heartbeats.
  assert (await c.put('/auth/me/presence-privacy',headers=headers(1),json={'show_last_seen':False})).status_code==200
  await c.post('/auth/presence',headers=headers(1),json={'session_id':s2,'active':True})
  hidden=(await c.get('/auth/presence/'+str(ids[1]),headers=headers(0))).json();assert hidden=={'online':None,'last_seen':None,'hidden':True},hidden
  assert (await c.get('/auth/presence/'+str(ids[0]),headers=headers(1))).json()['hidden'] is True
  await c.put('/auth/me/presence-privacy',headers=headers(1),json={'show_last_seen':True})
  await c.post('/auth/presence',headers=headers(1),json={'session_id':s2,'active':False})
  offline=(await c.get('/auth/presence/'+str(ids[1]),headers=headers(0))).json();assert offline['online'] is False and offline['last_seen']
  edited=await c.patch(f'/dispatches/{room}/messages/{target}',headers=headers(0),json={'content':'Updated original'})
  assert edited.status_code==200,edited.text
  history=(await c.get(f'/dispatches/{room}',headers=headers(1))).json()
  assert next(m for m in history if m['id']==str(reply_id))['reply']['content']=='Updated original'
  # Purging originals redacts quotes on subsequent loads.
  deleted=await c.delete(f'/dispatches/{room}/messages/{target}',headers=headers(0));assert deleted.status_code==204,deleted.text
  history=(await c.get(f'/dispatches/{room}',headers=headers(1))).json();assert next(m for m in history if m['id']==str(reply_id))['reply'] is None
  print('PASS contacts, cursor history, add members, reply permissions, media replies, read receipts and presence privacy')
asyncio.run(setup());asyncio.run(http_checks())
# Real websocket event loop with the same persisted database.
with TestClient(app) as client:
 with client.websocket_connect(f'/ws/soviets/{room}?token='+create_access_token(ids[0])) as socket:
  socket.send_json({'type':'typing','active':True});assert socket.receive_json()['type']=='typing'
  socket.send_json({'type':'typing','active':False});assert socket.receive_json()['active'] is False
  socket.send_json({'type':'buzz'});assert socket.receive_json()['type']=='buzz'
  socket.send_json({'type':'dispatch','content':'Invalid quote','reply_to_id':str(foreign)});assert socket.receive_json()['type']=='error'
  socket.send_json({'type':'dispatch','content':'Socket reply','reply_to_id':str(reply_id)})
  message=socket.receive_json();assert message['reply']['id']==str(reply_id),message
 with client.websocket_connect(f'/ws/soviets/{room}?token='+create_access_token(ids[0])) as socket:
  socket.send_json({'type':'typing','active':True})
  assert socket.receive_json()['active'] is True
 assert manager.events[-1]['type']=='typing' and manager.events[-1]['active'] is False
assert not manager.clients
print('PASS websocket typing/stop, buzz event, foreign reply rejection, persisted reply and connection cleanup')
os.unlink(db_path)
