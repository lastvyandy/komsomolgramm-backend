import unittest
import uuid
from sqlalchemy import select,func
from check_relationship import RelationshipChecks
from app.api.v1.endpoints import soviets
from app.models.models import Comrade, Soviet, SovietMember
from app.services.councils import ensure_councils, COUNCILS

class CouncilContactChecks(RelationshipChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp()
  self.app.include_router(soviets.router,prefix="/soviets")
  async with self.sessions() as db:
   (await db.get(Comrade,self.ids[0])).username="Andy"
   await db.commit()
   await ensure_councils(db)
  self.council=COUNCILS[0][0]

 async def test_only_two_councils_readable_by_everyone(self):
  for _ in range(2):
   response=await self.client.get("/soviets/councils",headers=self.headers(1))
   self.assertEqual(response.status_code,200,response.text)
   self.assertEqual({c["council_key"] for c in response.json()},{"RU","ES"})
   self.assertTrue(all(c["council_publisher_id"]==str(self.ids[0]) for c in response.json()))
  async with self.sessions() as db:
   self.assertEqual(await db.scalar(select(func.count()).select_from(Soviet).where(Soviet.council_key.is_not(None))),2)
   self.assertEqual(await db.scalar(select(func.count()).select_from(SovietMember).where(SovietMember.soviet_id==self.council)),3)

 async def test_subscribers_cannot_publish_any_message_type(self):
  base=f"/dispatches/{self.council}"
  operations=[
   ("post",base+"/messages",{"content":"Forbidden"}),
   ("post",base+"/queued-messages",{"content":"Forbidden","client_id":str(uuid.uuid4())}),
   ("post",base+"/rich",{"client_id":str(uuid.uuid4()),"rich":{"kind":"contact","name":"A","phone":"1"}}),
  ]
  for method,url,body in operations:
   response=await self.client.request(method,url,headers=self.headers(1),json=body)
   self.assertEqual(response.status_code,403,response.text)
  response=await self.client.post(base+"/attachments?name=v.mp4&is_round_video=true",
   headers={**self.headers(1),"X-Media-Storage":"device-v1","Content-Type":"video/mp4"},content=b"file")
  self.assertEqual(response.status_code,403,response.text)
  response=await self.client.post(base+"/queued-messages",headers=self.headers(),
   json={"client_id":str(uuid.uuid4()),"content":"Announcement"})
  self.assertEqual(response.status_code,201,response.text)
  history=await self.client.get(base,headers=self.headers(1))
  self.assertEqual(history.status_code,200,history.text)
  self.assertEqual(history.json()[0]["content"],"Announcement")

 async def test_publisher_identity_is_pinned_after_rename(self):
  async with self.sessions() as db:
   (await db.get(Comrade,self.ids[0])).username="renamed_andy"
   (await db.get(Comrade,self.ids[1])).username="andy"
   await db.commit();await ensure_councils(db)
   self.assertEqual((await db.get(Soviet,self.council)).council_publisher_id,self.ids[0])
  result=await self.client.post(f"/dispatches/{self.council}/messages",headers=self.headers(1),json={"content":"Spoof"})
  self.assertEqual(result.status_code,403)

 async def test_forward_to_council_is_publisher_only(self):
  result=await self.client.post(f"/dispatches/{self.chat}/queued-messages",headers=self.headers(1),
   json={"client_id":str(uuid.uuid4()),"content":"Original"})
  self.assertEqual(result.status_code,201,result.text)
  result=await self.client.post(f"/dispatches/{self.chat}/forward",headers=self.headers(1),
   json={"message_ids":[result.json()["id"]],"target_chat_id":str(self.council)})
  self.assertEqual(result.status_code,403,result.text)

 async def test_app_contact_card_resolves_real_profile(self):
  result=await self.client.post(f"/dispatches/{self.chat}/rich",headers=self.headers(),
   json={"client_id":str(uuid.uuid4()),"rich":{"kind":"app_contact","user_id":str(self.ids[2])}})
  self.assertEqual(result.status_code,201,result.text)
  contact=result.json()["rich"]
  self.assertEqual(contact["user_id"],str(self.ids[2]))
  self.assertEqual(contact["username"],"user2")
  self.assertEqual(contact["name"],"User2")
  profile=await self.client.get(f"/auth/profile/{contact['user_id']}",headers=self.headers(1))
  self.assertEqual(profile.status_code,200)
  self.assertEqual(profile.json()["id"],contact["user_id"])

 async def test_forged_and_missing_contacts_rejected(self):
  for payload,status in [({"kind":"app_contact","user_id":str(uuid.uuid4())},404),
    ({"kind":"app_contact","user_id":str(self.ids[2]),"name":"Fake"},422),
    ({"kind":"app_contact","user_id":"not-an-id"},422)]:
   result=await self.client.post(f"/dispatches/{self.chat}/rich",headers=self.headers(),
    json={"client_id":str(uuid.uuid4()),"rich":payload})
   self.assertEqual(result.status_code,status,result.text)

 async def test_new_registration_joins_both_councils(self):
  result=await self.client.post("/auth/register",json={
   "username":"new_reader","callsign":"New Reader","password":"testing-password-123"})
  self.assertEqual(result.status_code,201,result.text)
  identifier=uuid.UUID(result.json()["id"])
  async with self.sessions() as db:
   count=await db.scalar(select(func.count()).select_from(SovietMember).where(
    SovietMember.comrade_id==identifier,SovietMember.soviet_id.in_([c[0] for c in COUNCILS])))
   self.assertEqual(count,2)

 async def test_websocket_rejects_reader_writes_typing_and_buzz(self):
  from unittest.mock import patch
  from fastapi import WebSocketDisconnect
  from app.api.v1.endpoints import ws
  from app.core.security import create_access_token
  class Socket:
   def __init__(self):
    self.messages=iter([{"type":"dispatch","content":"Forbidden"},{"type":"typing","active":True},{"type":"buzz"}])
    self.sent=[]
   async def receive_json(self):
    try:return next(self.messages)
    except StopIteration:raise WebSocketDisconnect()
   async def send_json(self,value):self.sent.append(value)
   async def close(self,**kwargs):pass
  class Manager:
   async def connect(self,*args):pass
   def disconnect(self,*args):pass
   async def publish_dispatch(self,*args):raise AssertionError("A reader published a channel event")
  socket=Socket()
  with patch.object(ws,"async_session_maker",self.sessions),patch.object(ws,"get_socket_manager",return_value=Manager()):
   await ws.soviet_websocket_endpoint(socket,self.council,token=create_access_token(subject=self.ids[1]))
  self.assertEqual([x["code"] for x in socket.sent],["council_read_only"]*3)

if __name__=="__main__":
 suite=unittest.TestSuite(CouncilContactChecks(n) for n in CouncilContactChecks.__dict__ if n.startswith("test_"))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
