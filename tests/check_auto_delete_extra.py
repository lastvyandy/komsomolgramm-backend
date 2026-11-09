import unittest, uuid
from unittest.mock import patch
from datetime import timedelta
from sqlalchemy import select
from check_auto_delete import AutoDeleteChecks
from app.models.models import Dispatch, DispatchAttachment, AutoDeleteDeadline, utc_now
from app.api.v1.endpoints import dispatches

class MediaAutoDeleteChecks(AutoDeleteChecks):
 async def test_attachment_deadline_and_expiry(self):
  await self.policy(all=True)
  async def quota(*args):pass
  with patch.object(dispatches,'admit_media',new=quota):
   r=await self.client.post(f'/dispatches/{self.chat}/attachments?name=file.pdf',
      headers={**self.headers(),'Content-Type':'application/pdf','X-Media-Storage':'device-v1'},content=b'%PDF')
  self.assertEqual(r.status_code,201,r.text);id=uuid.UUID(r.json()['id'])
  async with self.sessions() as db:self.assertIsNotNone(await db.get(AutoDeleteDeadline,(id,self.ids[0])))
  await self.expire()
  async with self.sessions() as db:self.assertIsNone(await db.get(DispatchAttachment,id))
 async def test_future_deadline_and_both_scopes_paginate(self):
  await self.policy(all=True);await self.policy(i=1)
  identifiers=[await self.message() for _ in range(3)]
  async with self.sessions() as db:
   from app.services.auto_delete import expire_due
   self.assertEqual(await expire_due(db),[])
  seen=[];after=''
  for _ in range(5):
   response=await self.client.get(f'/auto-delete/{self.chat}/deadlines?limit=1'+(f'&after={after}' if after else ''),headers=self.headers(1))
   self.assertEqual(response.status_code,200,response.text);page=response.json()
   if not page:break
   self.assertEqual(len(page),2);seen.extend(x['id'] for x in page);after=max(x['id'] for x in page)
  self.assertEqual(set(seen),set(map(str,identifiers)))

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(MediaAutoDeleteChecks(n) for n in MediaAutoDeleteChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
