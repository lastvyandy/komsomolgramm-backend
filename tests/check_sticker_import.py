import os
import time
import unittest
from unittest.mock import patch
import httpx
from check_relationship import RelationshipChecks
from app.api.v1.endpoints import stickers

class StickerImportChecks(RelationshipChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp()
  self.app.include_router(stickers.router,prefix="/stickers")
  self.real_client=httpx.AsyncClient
  self.requests=[]
  def handler(request):
   self.requests.append(str(request.url))
   if request.url.path.endswith("/getStickerSet"):
    return httpx.Response(200,json={"ok":True,"result":{"title":"Public pack","stickers":[
     {"file_id":"static","is_animated":False,"is_video":False},
     {"file_id":"animation","is_animated":True,"thumbnail":{"file_id":"poster"}}]}})
   if request.url.path.endswith("/getFile"):
    return httpx.Response(200,json={"ok":True,"result":{"file_path":"stickers/file.webp"}})
   return httpx.Response(200,content=b"RIFFxxxxWEBPtest")
  self.transport=httpx.MockTransport(handler)
  def factory(**kwargs):return self.real_client(transport=self.transport,**kwargs)
  self.network=patch.object(stickers.httpx,"AsyncClient",side_effect=factory);self.network.start()
  self.token=patch.dict(os.environ,{"TELEGRAM_STICKER_BOT_TOKEN":"test-token"});self.token.start()

 async def asyncTearDown(self):
  self.network.stop();self.token.stop()
  await super().asyncTearDown()

 async def manifest(self,link="https://t.me/addstickers/PublicPack"):
  return await self.client.post("/stickers/telegram",headers=self.headers(),json={"link":link})

 async def test_public_pack_manifest_and_owner_bound_download(self):
  response=await self.manifest()
  self.assertEqual(response.status_code,200,response.text)
  value=response.json()
  self.assertEqual(value["name"],"Public pack")
  self.assertEqual([f["poster"] for f in value["files"]],[False,True])
  self.assertNotIn("test-token",response.text)
  ticket=value["files"][0]["ticket"]
  result=await self.client.get("/stickers/telegram/file",params={"ticket":ticket},headers=self.headers())
  self.assertEqual(result.status_code,200,result.text)
  self.assertEqual(result.content,b"RIFFxxxxWEBPtest")
  self.assertEqual(result.headers["cache-control"],"private, no-store")
  self.assertEqual((await self.client.get("/stickers/telegram/file",params={"ticket":ticket},headers=self.headers(1))).status_code,403)

 async def test_invalid_links_are_not_remote_requests(self):
  for link in ["https://evil.example/pack","https://t.me/other/Pack","http://t.me/addstickers/Pack","../../bad"]:
   result=await self.manifest(link)
   self.assertEqual(result.status_code,422,result.text)
  self.assertEqual(self.requests,[])
  self.assertEqual((await self.manifest("t.me/addstickers/Pack")).status_code,200)

 async def test_configuration_and_authentication_errors(self):
  with patch.dict(os.environ,{"TELEGRAM_STICKER_BOT_TOKEN":""}):
   self.assertEqual((await self.manifest()).status_code,503)
  self.assertEqual((await self.client.post("/stickers/telegram",json={"link":"Pack"})).status_code,401)

 async def test_tickets_expire_and_cannot_be_modified(self):
  with patch.object(stickers.time,"time",return_value=1000):
   ticket=stickers.make_ticket("static",self.ids[0])
  self.assertEqual((await self.client.get("/stickers/telegram/file",params={"ticket":ticket},headers=self.headers())).status_code,403)
  ticket=(await self.manifest()).json()["files"][0]["ticket"]
  broken=ticket[:-1]+("0" if ticket[-1]!="0" else "1")
  self.assertEqual((await self.client.get("/stickers/telegram/file",params={"ticket":broken},headers=self.headers())).status_code,403)

if __name__=="__main__":
 suite=unittest.TestSuite(StickerImportChecks(n) for n in StickerImportChecks.__dict__ if n.startswith("test_"))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
