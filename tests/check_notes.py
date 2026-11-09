import unittest,uuid
from datetime import timedelta
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import notes
from app.models.models import Contact,StatusNote,utc_now
class NoteChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(notes.router,prefix='/notes')
  async with self.sessions() as db:
   db.add(Contact(owner_id=self.ids[0],person_id=self.ids[1]));await db.commit()
 async def publish(self,text='A note',i=0):return await self.client.put('/notes/me',headers=self.headers(i),json={'content':text})
 async def feed(self,i=0):return await self.client.get('/notes',headers=self.headers(i))
 async def test_visibility_not_public_and_one_way_contacts(self):
  self.assertEqual((await self.publish()).status_code,200)
  self.assertEqual(len((await self.feed(0)).json()),1);self.assertEqual(len((await self.feed(1)).json()),1)
  self.assertEqual((await self.feed(2)).json(),[])
  self.assertEqual((await self.client.get('/notes')).status_code,401)
  await self.publish('Reader note',1);self.assertEqual(len((await self.feed(0)).json()),1)
 async def test_conditional_feed_changes_and_viewer_isolation(self):
  await self.publish()
  first=await self.feed(1);tag=first.headers['etag']
  unchanged=await self.client.get('/notes',headers={**self.headers(1),'If-None-Match':tag})
  self.assertEqual(unchanged.status_code,304);self.assertEqual(unchanged.content,b'')
  hidden=await self.client.get('/notes',headers={**self.headers(2),'If-None-Match':tag})
  self.assertEqual(hidden.status_code,200);self.assertEqual(hidden.json(),[])
  await self.publish('Changed')
  changed=await self.client.get('/notes',headers={**self.headers(1),'If-None-Match':tag})
  self.assertEqual(changed.status_code,200);self.assertNotEqual(changed.headers['etag'],tag)
  self.assertEqual(changed.json()[0]['content'],'Changed')
  await self.client.delete('/notes/me',headers=self.headers())
  deleted=await self.client.get('/notes',headers={**self.headers(1),'If-None-Match':changed.headers['etag']})
  self.assertEqual(deleted.status_code,200);self.assertEqual(deleted.json(),[])
 async def test_replacement_delete_and_validation(self):
  first=(await self.publish()).json()['id'];second=(await self.publish('🔥'*60)).json()['id'];self.assertNotEqual(first,second)
  self.assertEqual(len((await self.feed()).json()),1)
  for text in ['   ','x'*61]:self.assertEqual((await self.publish(text)).status_code,422)
  self.assertEqual((await self.client.delete('/notes/me',headers=self.headers(1))).status_code,204)
  self.assertEqual(len((await self.feed()).json()),1)
  await self.client.delete('/notes/me',headers=self.headers());self.assertEqual((await self.feed(1)).json(),[])
 async def test_expiry_enforced_before_cleanup(self):
  id=(await self.publish()).json()['id']
  async with self.sessions() as db:
   (await db.get(StatusNote,self.ids[0])).expires_at=utc_now()-timedelta(seconds=1);await db.commit()
  self.assertEqual((await self.feed(1)).json(),[])
  r=await self.client.post(f'/notes/{id}/reply',headers=self.headers(1),json={'client_id':str(uuid.uuid4()),'content':'Hi'})
  self.assertEqual(r.status_code,404)
 async def test_private_reply_reaction_idempotency_and_scope(self):
  id=(await self.publish('My mood')).json()['id'];client=str(uuid.uuid4())
  for _ in range(2):
   r=await self.client.post(f'/notes/{id}/reply',headers=self.headers(1),json={'client_id':client,'content':'❤️','reaction':True})
   self.assertEqual(r.status_code,200,r.text)
  history=await self.client.get(f'/dispatches/{r.json()["chat_id"]}',headers=self.headers())
  self.assertEqual(len(history.json()),1);self.assertIn('My mood',history.json()[0]['content']);self.assertIn('❤️',history.json()[0]['content'])
  r=await self.client.post(f'/notes/{id}/reply',headers=self.headers(2),json={'client_id':str(uuid.uuid4()),'content':'Hi'})
  self.assertEqual(r.status_code,404)
 async def test_removed_contact_cannot_reply(self):
  id=(await self.publish()).json()['id']
  async with self.sessions() as db:
   await db.delete(await db.get(Contact,(self.ids[0],self.ids[1])));await db.commit()
  self.assertEqual((await self.feed(1)).json(),[])
  r=await self.client.post(f'/notes/{id}/reply',headers=self.headers(1),json={'client_id':str(uuid.uuid4()),'content':'Hi'})
  self.assertEqual(r.status_code,404)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(NoteChecks(n) for n in NoteChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
