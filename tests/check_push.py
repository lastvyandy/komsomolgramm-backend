import os, sys, types, asyncio, uuid, tempfile
from datetime import datetime, timezone, timedelta
os.environ['SECRET_KEY']='local-messaging-check-only-not-a-production-key'
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
import fakeredis.aioredis
from app.models.models import Base,Comrade,Soviet,SovietType,SovietMember,Dispatch,SovietReadCursor
from app.db.session import get_async_db
from app.api.v1.endpoints import auth,dispatches,soviets,ws
from app.api.deps import get_current_comrade
from app.core.security import create_access_token
from app.services.replies import require_reply
import app.core.redis

db_path=tempfile.mktemp(suffix='.db')
engine=create_async_engine('sqlite+aiosqlite:///'+db_path,poolclass=NullPool)
@event.listens_for(engine.sync_engine, "connect")
def sqlite_lock(connection, _):
 connection.create_function("pg_advisory_xact_lock", 1, lambda _: 1)
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
ids=[uuid.uuid4() for _ in range(3)];room=uuid.uuid4();other_room=uuid.uuid4();direct_room=uuid.uuid4();target=uuid.uuid4();foreign=uuid.uuid4();reply_id=uuid.uuid4()
def headers(index):return {'Authorization':'Bearer '+create_access_token(ids[index])}
async def setup():
 async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
 async with sessions() as db:
  db.add_all([Comrade(id=i,username='user'+str(n),callsign='User '+str(n),hashed_password='unused') for n,i in enumerate(ids)])
  db.add_all([Soviet(id=room,name='Group'),Soviet(id=other_room,name='Other'),Soviet(id=direct_room,soviet_type=SovietType.DIRECT)]);await db.flush()
  db.add_all([SovietMember(soviet_id=room,comrade_id=i) for i in ids[:2]])
  db.add_all([SovietMember(soviet_id=direct_room,comrade_id=i) for i in ids[:2]])
  db.add_all([Dispatch(id=target,soviet_id=room,sender_id=ids[0],content='Original'),Dispatch(id=foreign,soviet_id=other_room,sender_id=ids[2],content='Private'),Dispatch(id=reply_id,soviet_id=room,sender_id=ids[1],content='Reply',reply_to_id=target)])
  await db.commit()


from app.services import push_notifications as push
from types import SimpleNamespace

sent=[]
def fake_send(message, app=None):
 sent.append(message)
 return 'fake-id'
def fake_batch(messages, app=None):
 sent.extend(messages)
 return SimpleNamespace(responses=[SimpleNamespace(success=True,exception=None) for _ in messages])
push.messaging.send=fake_send
push.messaging.send_each=fake_batch
push.async_session_maker=sessions

token0='test-token-account-zero-00000000'
token1='test-token-account-one-11111111'
async def checks():
 async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
  push._app=None
  result=await c.get('/auth/me/push-status',headers=headers(0))
  assert result.status_code==200 and result.json()=={'configured':False,'registered_devices':0},result.text
  assert (await c.get('/auth/me/push-status')).status_code==401
  result=await c.post('/auth/me/push-test',headers=headers(0),json={'token':token0})
  assert result.status_code==503,result.text
  for who,token in enumerate([token0,token1]):
   registered=await c.put('/auth/me/push-token',headers=headers(who),json={'token':token})
   assert registered.status_code==204,registered.text
  push._app=object()
  status=(await c.get('/auth/me/push-status',headers=headers(0))).json()
  assert status=={'configured':True,'registered_devices':1},status
  forbidden=await c.post('/auth/me/push-test',headers=headers(0),json={'token':token1})
  assert forbidden.status_code==404,forbidden.text
  accepted=await c.post('/auth/me/push-test',headers=headers(0),json={'token':token0})
  assert accepted.status_code==202,accepted.text
  assert sent[-1].token==token0 and sent[-1].data['event_type']=='test'
  limited=await c.post('/auth/me/push-test',headers=headers(0),json={'token':token0})
  assert limited.status_code==429,limited.text
  sent.clear()
  await push.send_push(room,{'type':'dispatch','sender_id':str(ids[0]),'id':str(target),'sender_callsign':'User 0','content':'private-text'})
  assert len(sent)==1 and sent[0].token==token1
  assert sent[0].data['recipient_id']==str(ids[1])
  assert sent[0].android.priority=='high' and 'content' not in sent[0].data
  def invalid_batch(messages,app=None):
   return SimpleNamespace(responses=[SimpleNamespace(success=False,exception=push.messaging.UnregisteredError('invalid')) for _ in messages])
  push.messaging.send_each=invalid_batch
  await push.send_push(room,{'type':'buzz','sender_id':str(ids[0]),'id':str(target)})
  status=(await c.get('/auth/me/push-status',headers=headers(1))).json()
  assert status['registered_devices']==0,status
  push._app=None
  before=len(sent)
  await push.send_push(room,{'type':'dispatch','sender_id':str(ids[0]),'id':str(target)})
  assert len(sent)==before
  print('PASS push configuration status, token ownership, server test, rate limit, receiver routing, high priority, private payloads and invalid token cleanup')

asyncio.run(setup())
asyncio.run(checks())
os.unlink(db_path)
