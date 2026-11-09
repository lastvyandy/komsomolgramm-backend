import os, unittest, uuid
from datetime import timedelta
from unittest.mock import patch, AsyncMock
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import calls
from app.models.models import Soviet, SovietType, VoiceCall, utc_now

class CallChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(calls.router,prefix='/calls')
  import app.services.push_notifications as push
  push.send_call_push=AsyncMock(return_value=True)
  self.env=patch.dict(os.environ,{'CALL_TURN_HOST':'turn.example.org','CALL_TURN_SECRET':'test-turn-secret'});self.env.start()
  async with self.sessions() as db:
   (await db.get(Soviet,self.chat)).soviet_type=SovietType.DIRECT;await db.commit()
 async def asyncTearDown(self):
  self.env.stop();await super().asyncTearDown()
 async def start(self,i=0):
  return await self.client.post('/calls',headers=self.headers(i),json={'chat_id':str(self.chat)})
 async def action(self,id,action,i=0):return await self.client.post(f'/calls/{id}/action',headers=self.headers(i),json={'action':action})
 async def signal(self,id,i=0,**body):return await self.client.post(f'/calls/{id}/signal',headers=self.headers(i),json=body)
 async def test_auth_membership_and_turn(self):
  self.assertEqual((await self.client.get('/calls/ice')).status_code,401)
  ice=await self.client.get('/calls/ice',headers=self.headers());self.assertEqual(ice.status_code,200)
  self.assertNotIn('test-turn-secret',ice.text)
  self.assertEqual((await self.start(2)).status_code,403)
  with patch.dict(os.environ,{'CALL_TURN_SECRET':''}):self.assertEqual((await self.start()).status_code,503)
 async def test_call_accept_signal_and_end(self):
  r=await self.start();self.assertEqual(r.status_code,201,r.text);id=r.json()['id']
  self.assertEqual((await self.client.get('/calls/active',headers=self.headers(1))).json()['id'],id)
  self.assertEqual((await self.action(id,'accept')).status_code,403)
  self.assertEqual((await self.action(id,'accept',1)).json()['status'],'active')
  self.assertEqual((await self.signal(id,sdp='v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111')).status_code,200)
  answer=await self.signal(id,1);self.assertTrue(answer.json()['sdp'].startswith('v=0'))
  self.assertEqual((await self.action(id,'end')).json()['status'],'ended')
  self.assertEqual((await self.signal(id)).status_code,409)
 async def test_outsider_and_busy(self):
  r=await self.start();id=r.json()['id'];self.assertEqual((await self.start(1)).status_code,409)
  self.assertEqual((await self.signal(id,2)).status_code,404)
  self.assertEqual((await self.action(id,'end',2)).status_code,404)
 async def test_timeout_and_no_old_sdp(self):
  id=(await self.start()).json()['id']
  async with self.sessions() as db:
   call=await db.get(VoiceCall,uuid.UUID(id));call.created_at=utc_now()-timedelta(seconds=61);call.offer='private SDP';await db.commit()
  self.assertIsNone((await self.client.get('/calls/active',headers=self.headers(1))).json())
  async with self.sessions() as db:
   call=await db.get(VoiceCall,uuid.UUID(id));self.assertEqual(call.status,'missed');self.assertIsNone(call.offer)
 async def test_signal_cursor_reduces_repeated_payload(self):
  id=(await self.start()).json()['id'];await self.action(id,'accept',1)
  candidate={'sdpMid':'0','sdpMLineIndex':0,'sdp':'candidate:first'}
  await self.signal(id,candidates=[candidate])
  self.assertEqual(len((await self.signal(id,1)).json()['candidates']),1)
  self.assertEqual((await self.signal(id,1,candidate_offset=1)).json()['candidates'],[])
  self.assertEqual((await self.signal(id,1,candidate_offset=-1)).status_code,422)
 async def test_signals_bounded_and_audio_only(self):
  id=(await self.start()).json()['id']
  self.assertEqual((await self.signal(id,sdp='v=0\r\nm=video 9')).status_code,422)
  self.assertEqual((await self.signal(id,1,sdp='v=0')).status_code,403)
  candidate={'sdpMid':'0','sdpMLineIndex':0,'sdp':'candidate:test'}
  for _ in range(2):self.assertEqual((await self.signal(id,candidates=[candidate])).status_code,200)
  await self.action(id,'accept',1)
  self.assertEqual(len((await self.signal(id,1)).json()['candidates']),1)
  self.assertEqual((await self.signal(id,candidates=[candidate]*33)).status_code,422)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CallChecks(n) for n in CallChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
