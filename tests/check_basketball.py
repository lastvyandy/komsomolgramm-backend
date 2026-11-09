"""Server scoring, membership, idempotency and durable challenge/result checks."""
import unittest,uuid
from datetime import timedelta
from sqlalchemy import select
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import basketball
from app.models.models import Dispatch,SovietMember,utc_now
from unittest.mock import patch
class BasketballChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(basketball.router,prefix='/basketball')
  class Manager:
   async def publish_dispatch(_,chat,event):self.events.append(event)
  self.game_patch=patch.object(basketball,'get_socket_manager',return_value=Manager());self.game_patch.start()
 async def asyncTearDown(self):self.game_patch.stop();await super().asyncTearDown()
 async def create_game(self):
  identifier=uuid.uuid4();r=await self.client.post(f'/basketball/{self.chat}',headers=self.headers(),json={'client_id':str(identifier),'opponent_id':str(self.ids[1])})
  self.assertEqual(r.status_code,201,r.text);return str(identifier)
 async def action(self,id,action,i=0):return await self.client.post(f'/basketball/{self.chat}/{id}/action',headers=self.headers(i),json={'action':action})
 async def get_game(self,id,i=0):return await self.client.get(f'/basketball/{self.chat}/{id}',headers=self.headers(i))
 async def throw(self,id,i=0,body=None):
  return await self.client.post(f'/basketball/{self.chat}/{id}/shot',headers=self.headers(i),json=body)
 async def test_availability_is_authenticated(self):
  r=await self.client.get('/basketball/status',headers=self.headers())
  self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['available'])
  self.assertEqual((await self.client.get('/basketball/status')).status_code,401)
 async def test_completed_local_turn_and_retry(self):
  id=await self.create_game();await self.action(id,'accept',1);await self.action(id,'start')
  end=basketball.trajectory(id,0,0,-.5,0)[2]
  dx=(.5-basketball.ball_x(id,0))/end/1.9*.5
  body={'request_id':str(uuid.uuid4()),'shots':[{'request_id':str(uuid.uuid4()),'sequence':0,'dx':dx,'dy':-.5,'phase':0}]}
  url=f'/basketball/{self.chat}/{id}/turn'
  r=await self.client.post(url,headers=self.headers(),json=body)
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['players'][0]['score'],1);self.assertTrue(r.json()['players'][0]['done'])
  again=await self.client.post(url,headers=self.headers(),json=body);self.assertEqual(again.json()['players'][0]['score'],1)
  await self.action(id,'start',1)
  r=await self.client.post(url,headers=self.headers(1),json={'request_id':str(uuid.uuid4()),'shots':[]})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'finished')
 async def test_invalid_batch_does_not_save_partial_score(self):
  id=await self.create_game();await self.action(id,'accept',1);await self.action(id,'start')
  shot={'request_id':str(uuid.uuid4()),'sequence':8,'dx':0,'dy':-.5,'phase':0}
  r=await self.client.post(f'/basketball/{self.chat}/{id}/turn',headers=self.headers(),json={'request_id':str(uuid.uuid4()),'shots':[shot]})
  self.assertEqual(r.status_code,422,r.text)
  r=await self.get_game(id);self.assertEqual(r.json()['players'][0]['score'],0);self.assertFalse(r.json()['players'][0]['done'])
 async def test_membership_target_and_authorization(self):
  for target,expected in [(self.ids[0],422),(self.ids[2],403)]:
   r=await self.client.post(f'/basketball/{self.chat}',headers=self.headers(),json={'client_id':str(uuid.uuid4()),'opponent_id':str(target)})
   self.assertEqual(r.status_code,expected,r.text)
  id=await self.create_game()
  self.assertEqual((await self.action(id,'accept')).status_code,403)
  self.assertEqual((await self.get_game(id,2)).status_code,403)
  self.assertEqual((await self.action(id,'start')).status_code,409)
  self.assertEqual((await self.action(id,'decline',1)).json()['status'],'declined')
  self.assertEqual((await self.action(id,'accept',1)).status_code,409)
 async def test_server_scoring_retry_finish_and_result_once(self):
  id=await self.create_game();await self.action(id,'accept',1);await self.action(id,'start')
  end=basketball.trajectory(id,0,0,-.5,0)[2]
  # Aim at the center with a valid upward gesture. No score is submitted by the client.
  dx=(.5-basketball.ball_x(id,0))/end/1.9*.5
  body={'request_id':str(uuid.uuid4()),'sequence':0,'dx':dx,'dy':-.5,'phase':0}
  r=await self.throw(id,body=body);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['players'][0]['score'],1)
  retry=await self.throw(id,body=body);self.assertEqual(retry.json()['players'][0]['score'],1)
  self.assertEqual((await self.throw(id,body=dict(body,request_id=str(uuid.uuid4())))).status_code,409)
  self.assertEqual((await self.throw(id,body=dict(body,score=999))).status_code,422)
  self.assertEqual((await self.action(id,'finish')).status_code,200)
  self.assertEqual((await self.action(id,'start')).status_code,409)
  await self.action(id,'finish',1)
  r=await self.get_game(id);self.assertEqual(r.json()['status'],'finished');self.assertEqual(r.json()['winner_id'],str(self.ids[0]))
  await self.get_game(id);await self.get_game(id,1)
  async with self.sessions() as db:
   rows=(await db.scalars(select(Dispatch))).all();results=[r for r in rows if r.rich and r.rich.get('kind')=='basketball_result']
   self.assertEqual(len(results),1);self.assertEqual(results[0].rich['players'][0]['score'],1)
  history=await self.client.get(f'/dispatches/{self.chat}',headers=self.headers())
  self.assertEqual(history.status_code,200,history.text);self.assertEqual(len(history.json()),2)
 async def test_miss_ends_turn_and_zero_zero_is_draw(self):
  id=await self.create_game();await self.action(id,'accept',1)
  for i in (0,1):
   await self.action(id,'start',i)
   r=await self.throw(id,i,{'request_id':str(uuid.uuid4()),'sequence':0,'dx':1,'dy':-.04,'phase':0})
   self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['players'][i]['done']);self.assertEqual(r.json()['players'][i]['score'],0)
  r=await self.get_game(id);self.assertEqual(r.json()['status'],'finished');self.assertIsNone(r.json()['winner_id'])
 async def test_expiry_and_deleted_challenge(self):
  id=await self.create_game()
  async with self.sessions() as db:
   row=await db.get(Dispatch,uuid.UUID(id));row.rich=dict(row.rich,expires_at=(utc_now()-timedelta(seconds=1)).isoformat());await db.commit()
  self.assertEqual((await self.get_game(id)).json()['status'],'expired')
  async with self.sessions() as db:
   row=await db.get(Dispatch,uuid.UUID(id));row.is_purged=True;row.rich=None;await db.commit()
  self.assertEqual((await self.get_game(id)).status_code,404)
 async def test_group_spectators_can_read_but_cannot_play(self):
  async with self.sessions() as db:
   db.add(SovietMember(soviet_id=self.chat,comrade_id=self.ids[2]));await db.commit()
  id=await self.create_game()
  self.assertEqual((await self.get_game(id,2)).status_code,200)
  self.assertEqual((await self.action(id,'accept',2)).status_code,403)
  self.assertEqual((await self.action(id,'start',2)).status_code,403)

 async def test_identity_collision_and_out_of_order_input(self):
  id=await self.create_game()
  r=await self.client.post(f'/basketball/{self.chat}',headers=self.headers(),json={'client_id':id,'opponent_id':str(self.ids[1])})
  self.assertEqual(r.status_code,201);self.assertEqual(r.json()['id'],id)
  await self.action(id,'accept',1);await self.action(id,'start')
  for body in [dict(sequence=5,phase=0),dict(sequence=0,phase=150)]:
   r=await self.throw(id,body={'request_id':str(uuid.uuid4()),'dx':0,'dy':-.5,**body});self.assertIn(r.status_code,(409,422))
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(BasketballChecks(n) for n in BasketballChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
