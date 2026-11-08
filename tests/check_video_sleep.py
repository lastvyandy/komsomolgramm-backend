"""Round-video delivery and opt-in scheduled sleep status."""
import unittest
import uuid
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from check_relationship import RelationshipChecks
from app.api.v1.endpoints import dispatches
from app.models.models import ComradePresence, DispatchAttachment
from app.services.sleep_schedule import in_sleep_window
from app.services import media_relay

class VideoSleepChecks(RelationshipChecks):
 async def sleep_settings(self, **values):
  return await self.client.put("/auth/me/sleep",headers=self.headers(),
   json={"enabled":True,"start":1380,"end":420,"timezone":"Asia/Yekaterinburg",**values})

 async def presence(self, online=0):
  class Redis:
   async def zcount(self,*args):return online
   async def get(self,*args):return None
  with patch("app.core.redis.get_redis_client",return_value=Redis()):
   return await self.client.get(f"/auth/presence/{self.ids[0]}",headers=self.headers(1))

 async def test_sleep_default_validation_and_private_settings(self):
  result=await self.client.get("/auth/me/sleep",headers=self.headers())
  self.assertFalse(result.json()["enabled"])
  self.assertEqual((await self.client.get("/auth/me/sleep")).status_code,401)
  for values in [{"start":-1},{"end":1440},{"start":0,"end":0},{"timezone":"Not/AZone"}]:
   self.assertEqual((await self.sleep_settings(**values)).status_code,422)
  self.assertEqual((await self.sleep_settings()).status_code,200)

 async def test_sleep_only_offline_visible_and_opted_in(self):
  now=datetime.now(timezone.utc)
  minute=now.hour*60+now.minute
  await self.sleep_settings(start=(minute-1)%1440,end=(minute+2)%1440,timezone="UTC")
  self.assertTrue((await self.presence()).json()["sleeping"])
  self.assertFalse((await self.presence(online=1)).json()["sleeping"])
  async with self.sessions() as db:
   db.add(ComradePresence(comrade_id=self.ids[0],show_last_seen=False))
   await db.commit()
  self.assertFalse((await self.presence()).json()["sleeping"])
  await self.sleep_settings(enabled=False)
  self.assertFalse((await self.presence()).json()["sleeping"])

 async def test_sleep_respects_viewer_privacy(self):
  now=datetime.now(timezone.utc);minute=now.hour*60+now.minute
  await self.sleep_settings(start=(minute-1)%1440,end=(minute+2)%1440,timezone="UTC")
  async with self.sessions() as db:
   db.add(ComradePresence(comrade_id=self.ids[1],show_last_seen=False));await db.commit()
  self.assertFalse((await self.presence()).json()["sleeping"])

 async def video(self, content=b"\x00\x00\x00\x18ftypisomvideo", mime="video/mp4", id=None):
  async def quota(*args):pass
  with patch.object(dispatches,"admit_media",new=quota):
   return await self.client.post(f"/dispatches/{self.chat}/attachments",
    params={"name":"video-message.mp4","is_round_video":"true","client_id":str(id or uuid.uuid4())},
    headers={**self.headers(),"Content-Type":mime,"X-Media-Storage":"device-v1"},content=content)

 async def test_round_video_flag_idempotency_and_delete_after_delivery(self):
  id=uuid.uuid4()
  response=await self.video(id=id)
  self.assertEqual(response.status_code,201,response.text)
  self.assertTrue(response.json()["attachment"]["is_round_video"])
  self.assertEqual((await self.video(id=id)).json()["id"],str(id))
  url=f"/dispatches/{self.chat}/attachments/{id}"
  self.assertEqual((await self.client.get(url,headers=self.headers(1))).status_code,200)
  self.assertEqual((await self.client.post(url+"/received",headers=self.headers(1))).status_code,204)
  async with self.sessions() as db:self.assertIsNone(await db.scalar(select(DispatchAttachment.data).where(DispatchAttachment.dispatch_id==id)))
  self.assertEqual((await self.client.get(url,headers=self.headers(1))).status_code,410)

 async def test_round_video_pending_expiration_and_validation(self):
  for data,mime in [(b"fake","video/mp4"),(b"fake","image/jpeg")]:
   self.assertEqual((await self.video(data,mime)).status_code,422)
  id=uuid.uuid4();await self.video(id=id)
  async with self.sessions() as db:
   attachment=await db.get(DispatchAttachment,id)
   attachment.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);await db.commit()
  with patch.object(media_relay,"async_session_maker",self.sessions):
   await media_relay.cleanup_once()
  async with self.sessions() as db:self.assertIsNone(await db.scalar(select(DispatchAttachment.data).where(DispatchAttachment.dispatch_id==id)))

class SleepWindowChecks(unittest.TestCase):
 def test_overnight_boundaries_timezone_daytime_and_dst(self):
  def check(expected,hour,minute=0,**kwargs):
   now=datetime(2026,11,8,hour,minute,tzinfo=timezone.utc)
   self.assertEqual(in_sleep_window(kwargs.get("enabled",True),kwargs.get("start",1380),
    kwargs.get("end",420),kwargs.get("zone","UTC"),now),expected)
  check(True,23);check(True,0);check(False,7);check(False,22,59)
  check(False,23,enabled=False);check(False,23,start=0,end=0)
  check(True,18,zone="Asia/Yekaterinburg");check(False,2,zone="Asia/Yekaterinburg")
  check(True,14,start=780,end=900);check(False,15,start=780,end=900)
  check(False,23,zone="Invalid/Zone")
  self.assertTrue(in_sleep_window(True,60,180,"Europe/Berlin",
   datetime(2026,10,25,1,30,tzinfo=timezone.utc)))

if __name__=="__main__":
 suite=unittest.TestSuite(VideoSleepChecks(n) for n in VideoSleepChecks.__dict__ if n.startswith("test_"))
 suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SleepWindowChecks))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
