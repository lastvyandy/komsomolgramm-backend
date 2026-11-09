import unittest,json
from unittest.mock import patch
import httpx,fakeredis.aioredis
from check_relationship import RelationshipChecks
from app.api.v1.endpoints import locations
class LocationChecks(RelationshipChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();self.app.include_router(locations.router,prefix='/locations')
  self.redis=fakeredis.aioredis.FakeRedis();self.redis_patch=patch.object(locations,'get_redis_client',return_value=self.redis);self.redis_patch.start()
  self.network_calls=[];original=httpx.AsyncClient
  def handler(request):
   self.network_calls.append(request)
   return httpx.Response(200,json=[{'lat':'56.8389','lon':'60.6057','name':'Ekaterinburg','display_name':'Ekaterinburg, Russia'},
    {'lat':'nan','lon':'0','display_name':'Invalid'}])
  self.transport=httpx.MockTransport(handler)
  self.network=patch.object(locations.httpx,'AsyncClient',side_effect=lambda **kwargs:original(transport=self.transport,**kwargs));self.network.start()
 async def asyncTearDown(self):
  self.network.stop();self.redis_patch.stop();await self.redis.aclose();await super().asyncTearDown()
 async def search(self,body=None):
  return await self.client.post('/locations/search',headers=self.headers(),json=body or {'query':'Ekaterinburg','language':'ru','latitude':56.8389,'longitude':60.6057})
 async def test_search_bounds_cache_and_coarse_region(self):
  r=await self.search();self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()),1)
  request=self.network_calls[0];self.assertEqual(request.url.host,'nominatim.openstreetmap.org');self.assertIn('Komsomolgram',request.headers['user-agent'])
  self.assertNotIn('56.8389',str(request.url));self.assertNotIn('authorization',request.headers)
  again=await self.search();self.assertEqual(again.json(),r.json());self.assertEqual(len(self.network_calls),1)
 async def test_input_validation_and_authentication(self):
  for body in [{'query':'x'},{'query':'  '},{'query':'City','latitude':91},{'query':'City','language':'ru/evil'}]:
   self.assertEqual((await self.search(body)).status_code,422)
  self.assertEqual((await self.client.post('/locations/search',json={'query':'City'})).status_code,401)
 async def test_global_rate_limit_does_not_fetch_when_busy(self):
  await self.redis.set('location-search:upstream','1',ex=60)
  with patch.object(locations.asyncio,'sleep',return_value=None):r=await self.search()
  self.assertEqual(r.status_code,429,r.text);self.assertEqual(self.network_calls,[])
 async def test_bad_upstream_not_reported_as_places(self):
  self.transport.handler=lambda request:httpx.Response(200,text='<html>blocked</html>')
  r=await self.search();self.assertEqual(r.status_code,502)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(LocationChecks(n) for n in LocationChecks.__dict__ if n.startswith('test_')));raise SystemExit(not result.wasSuccessful())
