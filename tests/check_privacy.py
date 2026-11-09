import unittest,uuid
from datetime import date
from unittest.mock import patch
from check_relationship import RelationshipChecks
from app.api.v1.endpoints import privacy,soviets,notes
from app.models.models import Comrade,Contact,ComradePresence,Dispatch,StatusNote,utc_now
class PrivacyChecks(RelationshipChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(privacy.router,prefix='/privacy');self.app.include_router(soviets.router,prefix='/soviets');self.app.include_router(notes.router,prefix='/notes')
  async with self.sessions() as db:
   person=await db.get(Comrade,self.ids[0]);person.bio='Secret';person.birth_date=date(2001,2,7);person.country_code='CL'
   db.add(Contact(owner_id=self.ids[0],person_id=self.ids[1]));await db.commit()
 async def setting(self,key,audience):return await self.client.put('/privacy',headers=self.headers(),json={'key':key,'audience':audience})
 async def profile(self,i):return await self.client.get(f'/auth/profile/{self.ids[0]}',headers=self.headers(i))
 async def test_fields_owner_contacts_strangers_and_nested_profiles(self):
  for key in ['bio','birthday','country']:self.assertEqual((await self.setting(key,'contacts')).status_code,200)
  self.assertEqual((await self.profile(0)).json()['bio'],'Secret');self.assertEqual((await self.profile(1)).json()['bio'],'Secret')
  public=(await self.profile(2)).json();self.assertEqual(public['bio'],'');self.assertIsNone(public['birth_date']);self.assertIsNone(public['zodiac_sign']);self.assertIsNone(public['country_code'])
  await self.setting('bio','nobody');self.assertEqual((await self.profile(1)).json()['bio'],'')
 async def test_independent_read_receipts_preserve_unread_cursor(self):
  await self.setting('read_receipts','nobody')
  r=await self.client.get('/privacy',headers=self.headers());self.assertEqual(r.json()['last_seen'],'all')
  id=uuid.uuid4()
  async with self.sessions() as db:db.add(Dispatch(id=id,soviet_id=self.chat,sender_id=self.ids[1],content='Hi'));await db.commit()
  r=await self.client.put(f'/dispatches/{self.chat}/read/{id}',headers=self.headers());self.assertEqual(r.status_code,204,r.text)
  r=await self.client.get(f'/dispatches/{self.chat}/readers',headers=self.headers(1));self.assertEqual(r.json(),[])
  self.assertEqual(self.events[-1]['allowed_viewers'],[str(self.ids[0])])
  await self.setting('read_receipts','contacts')
  r=await self.client.get(f'/dispatches/{self.chat}/readers',headers=self.headers(1));self.assertEqual(len(r.json()),1)
 async def test_photo_endpoint_and_nested_user_are_protected(self):
  await self.setting('photo','nobody')
  self.assertFalse((await self.profile(1)).json()['avatar_visible'])
  r=await self.client.get(f'/auth/avatar/{self.ids[0]}',headers=self.headers(1));self.assertEqual(r.status_code,404)
  r=await self.client.get(f'/auth/photos/{self.ids[0]}',headers=self.headers(1));self.assertEqual(r.status_code,404)
 async def test_notes_audience_is_checked_by_server(self):
  await self.client.put('/notes/me',headers=self.headers(),json={'content':'Mood'})
  self.assertEqual(len((await self.client.get('/notes',headers=self.headers(1))).json()),1)
  await self.setting('notes','nobody')
  self.assertEqual((await self.client.get('/notes',headers=self.headers(1))).json(),[])
  await self.setting('notes','all')
  self.assertEqual(len((await self.client.get('/notes',headers=self.headers(2))).json()),1)
 async def test_validation_and_group_invites(self):
  self.assertEqual((await self.setting('unknown','all')).status_code,422)
  self.assertEqual((await self.setting('bio','wrong')).status_code,422)
  self.assertEqual((await self.client.get('/privacy')).status_code,401)
  await self.setting('group_invites','nobody')
  r=await self.client.post('/soviets/',headers=self.headers(1),json={'name':'Group','member_ids':[str(self.ids[0])]})
  self.assertEqual(r.status_code,403,r.text)
 async def test_private_read_events_are_not_broadcast_to_other_sockets(self):
  from app.services.socket_manager import SovietSocketManager
  class Socket:
   def __init__(self,id):self._comrade_id=str(id);self.received=[]
   async def send_text(self,text):self.received.append(text)
  a,b=Socket(self.ids[0]),Socket(self.ids[1])
  manager=object.__new__(SovietSocketManager);manager.local_connections={self.chat:{a,b}}
  await manager.broadcast_locally(self.chat,{'type':'read','allowed_viewers':[str(self.ids[0])]})
  self.assertEqual(len(a.received),1);self.assertEqual(b.received,[])
 async def test_nested_profiles_are_filtered_for_group_members(self):
  from app.models.models import SovietMember
  await self.setting('birthday','contacts');await self.setting('bio','contacts')
  async with self.sessions() as db:db.add(SovietMember(soviet_id=self.chat,comrade_id=self.ids[2]));await db.commit()
  r=await self.client.get(f'/soviets/{self.chat}',headers=self.headers(2))
  self.assertEqual(r.status_code,200,r.text)
  user=next(m['comrade'] for m in r.json()['members'] if m['comrade']['id']==str(self.ids[0]))
  self.assertEqual(user['bio'],'');self.assertIsNone(user['birth_date']);self.assertIsNone(user['zodiac_sign'])
 async def test_call_permission_is_enforced(self):
  import os
  from app.api.v1.endpoints import calls
  from app.models.models import Soviet,SovietType
  self.app.include_router(calls.router,prefix='/calls')
  async with self.sessions() as db:(await db.get(Soviet,self.chat)).soviet_type=SovietType.DIRECT;await db.commit()
  await self.setting('calls','nobody')
  with patch.dict(os.environ,{'CALL_TURN_HOST':'turn.example.org','CALL_TURN_SECRET':'only-test'}):
   r=await self.client.post('/calls',headers=self.headers(1),json={'chat_id':str(self.chat)})
  self.assertEqual(r.status_code,403,r.text)

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(PrivacyChecks(n) for n in PrivacyChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
