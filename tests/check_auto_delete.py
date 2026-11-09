import unittest, uuid
from datetime import timedelta
from sqlalchemy import select
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import auto_delete
from app.models.models import AutoDeleteDeadline, Dispatch, utc_now
from app.services.auto_delete import expire_due
class AutoDeleteChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(auto_delete.router,prefix='/auto-delete')
 async def policy(self,seconds=600,all=False,i=0):
  return await self.client.put(f'/auto-delete/{self.chat}',headers=self.headers(i),json={'seconds':seconds,'for_everyone':all})
 async def message(self,i=0):
  r=await self.client.post(f'/dispatches/{self.chat}/queued-messages',headers=self.headers(i),json={'client_id':str(uuid.uuid4()),'content':'New'})
  self.assertEqual(r.status_code,201,r.text);return uuid.UUID(r.json()['id'])
 async def expire(self):
  async with self.sessions() as db:return await expire_due(db,utc_now()+timedelta(minutes=11))
 async def history(self,i=0):return (await self.client.get(f'/dispatches/{self.chat}',headers=self.headers(i))).json()
 async def test_validation_and_isolation(self):
  for value in [599,-1,600.5]:self.assertEqual((await self.policy(value)).status_code,422)
  self.assertEqual((await self.policy()).status_code,200)
  self.assertEqual((await self.client.get(f'/auto-delete/{self.chat}',headers=self.headers(1))).json()['seconds'],0)
  self.assertEqual((await self.policy(i=2)).status_code,403)
  self.assertEqual((await self.client.get(f'/auto-delete/{self.chat}')).status_code,401)
 async def test_private_keeps_other_history_and_old_messages(self):
  old=await self.message();await self.policy();await self.message();await self.message(1)
  events=await self.expire();self.assertEqual({m['id'] for m in await self.history()},{str(old)})
  self.assertEqual(len(await self.history(1)),3);self.assertEqual(events,[]);self.assertEqual(await self.expire(),[])
 async def test_global_only_own_and_snapshot_survives_disable(self):
  await self.policy(all=True);mine=await self.message();theirs=await self.message(1);await self.policy(0);await self.expire()
  self.assertEqual({m['id'] for m in await self.history(1)},{str(theirs)})
  async with self.sessions() as db:
   m=await db.get(Dispatch,mine);self.assertTrue(m.is_purged);self.assertEqual(m.content,'')
 async def test_deadlines_private(self):
  await self.policy();a=await self.message();r=await self.client.get(f'/auto-delete/{self.chat}/deadlines',headers=self.headers())
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()[0]['id'],str(a))
  r=await self.client.get(f'/auto-delete/{self.chat}/deadlines',headers=self.headers(1));self.assertEqual(r.json(),[])
 async def test_rich_retry_has_one_deadline(self):
  await self.policy();identifier=uuid.uuid4()
  for _ in range(2):self.assertEqual((await self.create({'kind':'contact','name':'Friend','phone':'123'},id=identifier)).status_code,201)
  async with self.sessions() as db:self.assertEqual(len((await db.scalars(select(AutoDeleteDeadline))).all()),1)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(AutoDeleteChecks(n) for n in AutoDeleteChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
