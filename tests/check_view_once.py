import unittest,uuid
from unittest.mock import patch
from datetime import timedelta
from sqlalchemy import select
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import dispatches
from app.models.models import DispatchAttachment,AttachmentRecipient,Comrade,utc_now
DATA=b'\x89PNG\r\n\x1a\nphoto-data'
class ViewOnceChecks(RichMessageChecks):
 async def send(self,kind='image/png',once=True):
  async def quota(*args):pass
  with patch.object(dispatches,'admit_media',new=quota):
   return await self.client.post(f'/dispatches/{self.chat}/{"view-once-photo" if once else "attachments"}?name=photo.png',
    headers={**self.headers(),'Content-Type':kind,'X-Media-Storage':'device-v1'},content=DATA)
 async def open(self,id,i=1):return await self.client.post(f'/dispatches/{self.chat}/attachments/{id}/open-once',headers=self.headers(i))
 async def test_no_regular_download_no_gallery_no_auto_download(self):
  r=await self.send();self.assertEqual(r.status_code,201,r.text);id=r.json()['id'];self.assertTrue(r.json()['attachment']['view_once'])
  self.assertEqual((await self.client.get(f'/dispatches/{self.chat}/attachments/{id}',headers=self.headers(1))).status_code,404)
  self.assertEqual((await self.client.get(f'/dispatches/{self.chat}/attachments',headers=self.headers(1))).json(),[])
  self.assertEqual((await self.client.get('/dispatches/pending-media',headers=self.headers(1))).json(),[])
  self.assertEqual((await self.client.post(f'/dispatches/{self.chat}/attachments/{id}/received',headers=self.headers(1))).status_code,409)
 async def test_one_open_only_for_recipient_and_bytes_are_removed(self):
  id=(await self.send()).json()['id']
  self.assertEqual((await self.open(id,0)).status_code,403)
  self.assertEqual((await self.open(id,2)).status_code,403)
  r=await self.open(id);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.content,DATA)
  self.assertIn('no-store',r.headers['cache-control']);self.assertEqual((await self.open(id)).status_code,410)
  async with self.sessions() as db:self.assertIsNone(await db.scalar(select(DispatchAttachment.data).where(DispatchAttachment.dispatch_id==uuid.UUID(id))))
  history=await self.client.get(f'/dispatches/{self.chat}',headers=self.headers(1))
  self.assertTrue(history.json()[0]['attachment']['opened'])
 async def test_expiry_and_audio_validation(self):
  self.assertEqual((await self.send('audio/mp4')).status_code,422)
  id=(await self.send()).json()['id']
  async with self.sessions() as db:
   (await db.get(DispatchAttachment,uuid.UUID(id))).expires_at=utc_now()-timedelta(seconds=1);await db.commit()
  self.assertEqual((await self.open(id)).status_code,410)
 async def test_normal_media_is_unchanged(self):
  id=(await self.send(once=False)).json()['id']
  self.assertEqual((await self.open(id)).status_code,422)
  self.assertEqual((await self.client.get(f'/dispatches/{self.chat}/attachments/{id}',headers=self.headers(1))).content,DATA)
 async def test_sender_open_receipt_respects_privacy(self):
  async with self.sessions() as db:
   (await db.get(Comrade,self.ids[1])).privacy={'read_receipts':'nobody'};await db.commit()
  id=(await self.send()).json()['id'];self.assertEqual((await self.open(id)).status_code,200)
  history=await self.client.get(f'/dispatches/{self.chat}',headers=self.headers())
  self.assertFalse(history.json()[0]['attachment']['opened'])
 async def test_each_group_recipient_has_one_open(self):
  from app.models.models import SovietMember
  async with self.sessions() as db:db.add(SovietMember(soviet_id=self.chat,comrade_id=self.ids[2]));await db.commit()
  id=(await self.send()).json()['id'];self.assertEqual((await self.open(id,1)).status_code,200)
  async with self.sessions() as db:self.assertIsNotNone(await db.scalar(select(DispatchAttachment.data).where(DispatchAttachment.dispatch_id==uuid.UUID(id))))
  self.assertEqual((await self.open(id,2)).status_code,200);self.assertEqual((await self.open(id,1)).status_code,410)
 async def test_message_forward_is_rejected(self):
  id=(await self.send()).json()['id']
  r=await self.client.post(f'/dispatches/{self.chat}/forward',headers=self.headers(),json={'message_ids':[id],'target_chat_id':str(self.chat)})
  self.assertEqual(r.status_code,409,r.text)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ViewOnceChecks(n) for n in ViewOnceChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
