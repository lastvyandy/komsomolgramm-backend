"""HTTP checks for durable-client retry ids and repeated-label polls."""
import uuid, unittest
from check_rich_messages import RichMessageChecks, SovietMember

class OutboxChecks(RichMessageChecks):
 async def queued(self, content="Offline message", id=None, i=0, reply=None, chat=None):
  return await self.client.post(f"/dispatches/{chat or self.chat}/queued-messages", headers=self.headers(i),
   json={"content":content,"client_id":str(id or uuid.uuid4()),"reply_to_id":str(reply) if reply else None})

 async def test_queued_acknowledgement_loss_does_not_duplicate(self):
  key=uuid.uuid4()
  first=await self.queued(id=key)
  self.assertEqual(first.status_code,201,first.text)
  for _ in range(3):
   retry=await self.queued(id=key)
   self.assertEqual(retry.status_code,201,retry.text)
   self.assertEqual(retry.json()["id"],str(key))
  history=await self.client.get(f"/dispatches/{self.chat}",headers=self.headers())
  self.assertEqual([m["id"] for m in history.json()],[str(key)])
  self.assertEqual(len([e for e in self.events if e.get("id")==str(key)]),1)

 async def test_queued_preserves_reply(self):
  parent=await self.queued("Original")
  key=uuid.uuid4()
  reply=await self.queued("Reply",id=key,reply=parent.json()["id"])
  self.assertEqual(reply.status_code,201,reply.text)
  self.assertEqual(reply.json()["reply"]["content"],"Original")
  repeated=await self.queued("Reply",id=key,reply=parent.json()["id"])
  self.assertEqual(repeated.json()["reply"]["id"],parent.json()["id"])

 async def test_queued_id_cannot_be_reused_by_other_sender(self):
  key=uuid.uuid4()
  self.assertEqual((await self.queued(id=key)).status_code,201)
  rejected=await self.queued("Different sender",id=key,i=1)
  self.assertEqual(rejected.status_code,409,rejected.text)

 async def test_queued_id_cannot_be_reused_in_other_chat(self):
  key=uuid.uuid4()
  self.assertEqual((await self.queued(id=key)).status_code,201)
  async with self.sessions() as db:
   db.add(SovietMember(soviet_id=self.other,comrade_id=self.ids[0]));await db.commit()
  rejected=await self.queued("Different chat",id=key,chat=self.other)
  self.assertEqual(rejected.status_code,409,rejected.text)

 async def test_queued_purged_message_is_not_recreated(self):
  key=uuid.uuid4()
  self.assertEqual((await self.queued(id=key)).status_code,201)
  deleted=await self.client.post(f"/dispatches/{self.chat}/delete-messages",headers=self.headers(),
   json={"message_ids":[str(key)],"for_everyone":True})
  self.assertEqual(deleted.status_code,204,deleted.text)
  retried=await self.queued(id=key)
  self.assertEqual(retried.status_code,201,retried.text)
  self.assertTrue(retried.json()["is_purged"])
  history=await self.client.get(f"/dispatches/{self.chat}",headers=self.headers())
  self.assertEqual(history.json(),[])

 async def test_queued_rejects_outsider_and_blank_body(self):
  self.assertEqual((await self.queued(i=2)).status_code,403)
  self.assertEqual((await self.queued("   ")).status_code,422)

 async def test_identical_poll_labels_have_independent_votes(self):
  poll=await self.create({"kind":"poll","question":"Курок vs Курок","options":["курок","курок"],"multiple":False})
  self.assertEqual(poll.status_code,201,poll.text)
  result=await self.vote(poll.json()["id"],[1])
  self.assertEqual(result.status_code,200,result.text)
  self.assertEqual(result.json()["counts"],[0,1])
  self.assertEqual(result.json()["my_choices"],[1])

 async def test_old_quick_reply_route_still_returns_no_content(self):
  result=await self.client.post(f"/dispatches/{self.chat}/messages",headers=self.headers(),json={"content":"Quick reply"})
  self.assertEqual(result.status_code,204,result.text)

if __name__=="__main__":
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(RichMessageChecks)
 suite.addTests(OutboxChecks(n) for n in [
  "test_queued_acknowledgement_loss_does_not_duplicate","test_queued_preserves_reply",
  "test_queued_id_cannot_be_reused_by_other_sender","test_queued_id_cannot_be_reused_in_other_chat",
  "test_queued_purged_message_is_not_recreated","test_queued_rejects_outsider_and_blank_body",
  "test_identical_poll_labels_have_independent_votes","test_old_quick_reply_route_still_returns_no_content"])
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
