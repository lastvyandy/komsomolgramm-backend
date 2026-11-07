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

async def checks():
 async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
  async def post(path, data=None, who=0):
   return await c.post(path,headers=headers(who),json=data)
  own = await post(f'/soviets/direct/{ids[0]}')
  assert own.status_code==200,own.text
  saved=own.json()['id']
  assert len(own.json()['members'])==1
  again=await post(f'/soviets/direct/{ids[0]}')
  assert again.json()['id']==saved
  assert (await c.get(f'/dispatches/{saved}',headers=headers(1))).status_code==403
  second=await post(f'/soviets/direct/{ids[1]}',who=1)
  assert second.json()['id']!=saved
  upload=await c.post(f'/dispatches/{room}/attachments?name=test.jpg',headers={**headers(0),'Content-Type':'image/jpeg'},content=b'image-bytes')
  assert upload.status_code==201,upload.text
  photo=upload.json()['id']
  payload={'message_ids':[str(reply_id),photo,str(target)],'target_chat_id':saved}
  result=await post(f'/dispatches/{room}/forward',payload)
  assert result.status_code==201,result.text
  copied=result.json()
  assert len(copied)==3 and copied[0]['content']=='Original'
  assert copied[0]['forwarded']['sender_name']=='User 0'
  assert copied[1]['forwarded']['sender_name']=='User 1'
  copied_photo=copied[-1]['id']
  assert (await c.get(f'/dispatches/{saved}/attachments/{copied_photo}',headers=headers(0))).content==b'image-bytes'
  assert (await c.get(f'/dispatches/{saved}/attachments/{copied_photo}',headers=headers(1))).status_code==403
  bad=await post(f'/dispatches/{room}/forward',{'message_ids':[str(target),str(foreign)],'target_chat_id':saved})
  assert bad.status_code==404,bad.text
  history=await c.get(f'/dispatches/{saved}',headers=headers(0))
  assert len(history.json())==3,history.text
  no_access=await post(f'/dispatches/{room}/forward',{'message_ids':[str(target)],'target_chat_id':str(other_room)})
  assert no_access.status_code==403,no_access.text
  forbidden=await post(f'/dispatches/{room}/delete-messages',{'message_ids':[str(target),str(reply_id)],'for_everyone':True})
  assert forbidden.status_code==403,forbidden.text
  history=await c.get(f'/dispatches/{room}',headers=headers(0))
  assert any(x['id']==str(target) for x in history.json())
  hidden=await post(f'/dispatches/{room}/delete-messages',{'message_ids':[str(reply_id)],'for_everyone':False})
  assert hidden.status_code==204,hidden.text
  mine=(await c.get(f'/dispatches/{room}',headers=headers(0))).json()
  peer=(await c.get(f'/dispatches/{room}',headers=headers(1))).json()
  assert all(x['id']!=str(reply_id) for x in mine)
  assert any(x['id']==str(reply_id) for x in peer)
  assert (await c.get(f'/dispatches/{room}/context/{reply_id}',headers=headers(0))).status_code==404
  gone=await post(f'/dispatches/{room}/delete-messages',{'message_ids':[str(target),photo],'for_everyone':True})
  assert gone.status_code==204,gone.text
  assert (await c.get(f'/dispatches/{room}/attachments/{photo}',headers=headers(1))).status_code==404
  assert (await c.get(f'/dispatches/{saved}/attachments/{copied_photo}',headers=headers(0))).content==b'image-bytes'
  history=(await c.get(f'/dispatches/{saved}',headers=headers(0))).json()
  assert len(history)==3 and history[-1]['forwarded']['sender_name']=='User 0'
  # Forwarding an existing forward preserves its original author.
  forwarded_again=await post(f'/dispatches/{saved}/forward',{'message_ids':[copied[1]['id']],'target_chat_id':str(direct_room)})
  assert forwarded_again.status_code==201,forwarded_again.text
  assert forwarded_again.json()[0]['forwarded']['sender_name']=='User 1'
  print('PASS private and idempotent Saved Messages, batch forwards and attachment copies, original authors, permission checks, atomic rejection, personal and shared bulk deletion')

asyncio.run(setup())
asyncio.run(checks())
os.unlink(db_path)
