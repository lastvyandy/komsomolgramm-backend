"""Automatic contacts are directional, transactional and do not undo manual removal."""
import unittest,uuid
from check_rich_messages import RichMessageChecks
from sqlalchemy import select,delete
from app.models.models import Contact,AutoContactEnrollment,Dispatch,Soviet,SovietMember,SovietType

class AutoContactChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp()
  async with self.sessions() as db:
   room=await db.get(Soviet,self.chat);room.soviet_type=SovietType.DIRECT;await db.commit()
 async def count_contacts(self):
  async with self.sessions() as db:return set((r.owner_id,r.person_id) for r in (await db.scalars(select(Contact))).all())
 async def send(self,index=0,identifier=None):
  id=identifier or uuid.uuid4()
  return await self.client.post(f'/dispatches/{self.chat}/queued-messages',headers=self.headers(index),json={'client_id':str(id),'content':'Hello'})
 async def test_threshold_sender_only_and_manual_removal(self):
  for n in range(2):
   r=await self.send();self.assertEqual(r.status_code,201,r.text);self.assertEqual(await self.count_contacts(),set())
  r=await self.send();self.assertEqual(r.status_code,201,r.text)
  pair=(self.ids[0],self.ids[1]);self.assertEqual(await self.count_contacts(),{pair})
  async with self.sessions() as db:
   await db.execute(delete(Contact).where(Contact.owner_id==self.ids[0]));await db.commit()
  await self.send();self.assertEqual(await self.count_contacts(),set())
 async def test_retries_and_received_messages_do_not_count(self):
  id=uuid.uuid4()
  for _ in range(4):self.assertIn((await self.send(identifier=id)).status_code,(200,201))
  await self.send(1);await self.send(1)
  self.assertEqual(await self.count_contacts(),set())
  await self.send(1);self.assertEqual(await self.count_contacts(),{(self.ids[1],self.ids[0])})
 async def test_groups_system_results_and_rollback(self):
  async with self.sessions() as db:
   room=await db.get(Soviet,self.chat);room.soviet_type=SovietType.SOVIET;await db.commit()
  for _ in range(4):await self.send()
  self.assertEqual(await self.count_contacts(),set())
  async with self.sessions() as db:
   room=await db.get(Soviet,self.chat);room.soviet_type=SovietType.DIRECT;await db.commit()
   db.add(Dispatch(id=uuid.uuid4(),soviet_id=self.chat,sender_id=self.ids[0],content='🏀',rich={'kind':'basketball_result'}))
   await db.commit()
  self.assertEqual(await self.count_contacts(),set())
  # The next actual message can enroll a pre-existing conversation with >3 sends.
  async with self.sessions() as db:
   db.add(Dispatch(id=uuid.uuid4(),soviet_id=self.chat,sender_id=self.ids[0],content='rollback'))
   await db.flush();self.assertIsNotNone(await db.get(Contact,(self.ids[0],self.ids[1])))
   await db.rollback()
  self.assertEqual(await self.count_contacts(),set())
  await self.send();self.assertEqual(await self.count_contacts(),{(self.ids[0],self.ids[1])})
 async def test_multiple_new_messages_in_one_transaction_and_existing_contact(self):
  async with self.sessions() as db:
   db.add(Contact(owner_id=self.ids[0],person_id=self.ids[1]));await db.commit()
   for _ in range(3):db.add(Dispatch(soviet_id=self.chat,sender_id=self.ids[0],content='Batch'))
   await db.commit()
   self.assertIsNotNone(await db.get(AutoContactEnrollment,(self.ids[0],self.ids[1])))
  self.assertEqual(await self.count_contacts(),{(self.ids[0],self.ids[1])})
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(AutoContactChecks(n) for n in AutoContactChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
