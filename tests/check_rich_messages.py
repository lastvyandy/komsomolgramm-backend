"""HTTP regression checks for attachment messages, polls, events and documents."""
import asyncio, os, sys, types, uuid, unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("SECRET_KEY", "local-rich-test-only-not-production")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
push = types.ModuleType("app.services.push_notifications")
async def no_push(*args): pass
push.send_push = no_push
sys.modules["app.services.push_notifications"] = push
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.models import Base, Comrade, Soviet, SovietMember, Dispatch
from app.api.v1.endpoints import dispatches
from app.db.session import get_async_db
from app.core.security import create_access_token

class RichMessageChecks(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
  self.ids=[uuid.uuid4() for _ in range(3)];self.chat=uuid.uuid4();self.other=uuid.uuid4()
  async with self.sessions() as db:
   db.add_all([Comrade(id=id,username="user"+str(i),callsign="User"+str(i),hashed_password="unused") for i,id in enumerate(self.ids)])
   db.add_all([Soviet(id=self.chat,name="Test"),Soviet(id=self.other,name="Other")]);await db.flush()
   db.add_all([SovietMember(soviet_id=self.chat,comrade_id=id) for id in self.ids[:2]])
   db.add(SovietMember(soviet_id=self.other,comrade_id=self.ids[2]));await db.commit()
  async def database():
   async with self.sessions() as db:yield db
  self.app=FastAPI();self.app.include_router(dispatches.router,prefix="/dispatches");self.app.dependency_overrides[get_async_db]=database
  self.events=[]
  class Manager:
   async def publish_dispatch(_,chat,event):self.events.append(event)
  self.patch=patch.object(dispatches,"get_socket_manager",return_value=Manager());self.patch.start()
  self.client=AsyncClient(transport=ASGITransport(app=self.app),base_url="http://test")

 async def asyncTearDown(self):
  self.patch.stop();await self.client.aclose();await self.engine.dispose()
 def headers(self,i=0):return {"Authorization":"Bearer "+create_access_token(self.ids[i])}
 async def create(self,data,i=0,id=None,reply=None,chat=None):
  return await self.client.post(f"/dispatches/{chat or self.chat}/rich",headers=self.headers(i),
   json={"rich":data,"client_id":str(id or uuid.uuid4()),"reply_to_id":str(reply) if reply else None})
 async def vote(self,id,choices,i=0):
  return await self.client.put(f"/dispatches/{self.chat}/rich/{id}/responses",headers=self.headers(i),json={"choices":choices})
 async def poll(self,multiple=True):
  r=await self.create({"kind":"poll","question":"When?","options":["Today","Tomorrow"],"multiple":multiple})
  self.assertEqual(r.status_code,201,r.text);return r.json()["id"]

 async def test_location_survives_history_and_socket(self):
  r=await self.create({"kind":"location","latitude":56.8389,"longitude":60.6057,"label":"Ekaterinburg"})
  self.assertEqual(r.status_code,201,r.text);id=r.json()["id"]
  history=await self.client.get(f"/dispatches/{self.chat}",headers=self.headers(1))
  self.assertEqual(history.status_code,200,history.text)
  self.assertEqual(history.json()[0]["rich"]["latitude"],56.8389)
  self.assertIn("pt=60.6057,56.8389",history.json()[0]["content"])
  self.assertEqual(self.events[0]["rich"]["kind"],"location")

 async def test_coordinate_validation(self):
  for lat,lon in [(91,0),(0,181),(-91,0)]:
   self.assertEqual((await self.create({"kind":"location","latitude":lat,"longitude":lon})).status_code,422)

 async def test_outsider_cannot_send_or_vote(self):
  self.assertEqual((await self.create({"kind":"contact","name":"Test","phone":"+123"},i=2)).status_code,403)
  id=await self.poll()
  self.assertEqual((await self.vote(id,[0],2)).status_code,403)
  self.assertEqual((await self.client.get(f"/dispatches/{self.chat}/rich/{id}/responses",headers=self.headers(2))).status_code,403)

 async def test_multiple_votes_idempotent_and_removable(self):
  id=await self.poll()
  for _ in range(2):
   r=await self.vote(id,[0,1]);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()["counts"],[1,1])
  r=await self.vote(id,[1],1);self.assertEqual(r.json()["counts"],[1,2])
  r=await self.vote(id,[]);self.assertEqual(r.json()["counts"],[0,1]);self.assertEqual(r.json()["my_choices"],[])

 async def test_single_vote_rejects_multiple(self):
  id=await self.poll(False)
  self.assertEqual((await self.vote(id,[0,1])).status_code,422)
  self.assertEqual((await self.vote(id,[0])).json()["counts"],[1,0])
  self.assertEqual((await self.vote(id,[1])).json()["counts"],[0,1])

 async def test_invalid_options_and_duplicate_answers_rejected(self):
  self.assertEqual((await self.create({"kind":"poll","question":"Test","options":["One","one"]})).status_code,422)
  self.assertEqual((await self.create({"kind":"poll","question":"Test","options":["Only one"]})).status_code,422)
  id=await self.poll()
  self.assertEqual((await self.vote(id,[2])).status_code,422)
  self.assertEqual((await self.vote(id,[-1])).status_code,422)

 async def test_events_rsvp_and_timezone(self):
  r=await self.create({"kind":"event","title":"Meeting","starts_at":"2026-12-01T18:00:00+05:00","place":"UrFU"})
  self.assertEqual(r.status_code,201,r.text);id=r.json()["id"]
  self.assertEqual((await self.vote(id,[0])).json()["counts"],[1,0,0])
  self.assertEqual((await self.vote(id,[1],1)).json()["counts"],[1,1,0])
  self.assertEqual((await self.vote(id,[2])).json()["counts"],[0,1,1])
  self.assertEqual((await self.create({"kind":"event","title":"Test","starts_at":"2026-12-01T18:00:00"})).status_code,422)

 async def test_retry_does_not_duplicate_message(self):
  id=uuid.uuid4();data={"kind":"contact","name":"Friend","phone":"+56912345678"}
  first=await self.create(data,id=id);second=await self.create(data,id=id)
  self.assertEqual(first.status_code,201,first.text);self.assertEqual(second.status_code,201,second.text)
  self.assertEqual(first.json()["id"],second.json()["id"]);self.assertEqual(len(self.events),1)

 async def test_hidden_message_does_not_accept_votes(self):
  id=await self.poll()
  r=await self.client.post(f"/dispatches/{self.chat}/delete-messages",headers=self.headers(1),
   json={"message_ids":[id],"for_everyone":False})
  self.assertEqual(r.status_code,204,r.text)
  self.assertEqual((await self.vote(id,[0],1)).status_code,404)

 async def test_structured_messages_cannot_be_edited_as_text(self):
  id=await self.poll()
  r=await self.client.patch(f"/dispatches/{self.chat}/messages/{id}",headers=self.headers(),json={"content":"Replace poll"})
  self.assertEqual(r.status_code,422,r.text)

 async def test_pdf_delivery_uses_existing_temporary_relay(self):
  # Quota lock is PostgreSQL-specific and irrelevant to MIME/persistence validation here.
  async def quota(db,size):pass
  with patch.object(dispatches,"admit_media",new=quota):
   r=await self.client.post(f"/dispatches/{self.chat}/attachments?name=test.pdf",
    headers={**self.headers(),"Content-Type":"application/pdf","X-Media-Storage":"device-v1"},content=b"%PDF-1.4 test")
  self.assertEqual(r.status_code,201,r.text)
  self.assertEqual(r.json()["attachment"]["mime_type"],"application/pdf")

if __name__=="__main__":unittest.main()
