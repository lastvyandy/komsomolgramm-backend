"""App icon sharing: authenticated delivery, bounded uploads and public privacy."""
import base64
import struct
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from check_relationship import RelationshipChecks
from app.models.models import Comrade

def png(size=1, color=0):
 def chunk(kind, data):
  return struct.pack(">I", len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
 return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",size,size,8,6,0,0,0))+
  chunk(b"IDAT",zlib.compress((b"\0"+bytes([color,0,0,255])*size)*size))+chunk(b"IEND",b""))

class AppIconChecks(RelationshipChecks):
 async def publish(self, icon=None, enabled=True, name="App"):
  return await self.client.put("/auth/me/app-activity",headers=self.headers(),
   json={"enabled":enabled,"app_name":name,"app_icon":base64.b64encode(icon).decode() if icon else None})

 async def revision(self):
  result=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  return result.json()["most_used_app_icon_revision"]

 async def test_delivery_and_no_embedded_image_in_profiles(self):
  image=png(48)
  result=await self.publish(image)
  self.assertEqual(result.status_code,200,result.text)
  revision=await self.revision()
  self.assertEqual(len(revision),64)
  url=f"/auth/app-icon/{self.ids[0]}"
  response=await self.client.get(url,headers=self.headers(1))
  self.assertEqual(response.content,image)
  self.assertEqual(response.headers["content-type"],"image/png")
  self.assertEqual(response.headers["etag"],'"'+revision+'"')
  self.assertEqual((await self.client.get(url)).status_code,401)
  profile=(await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))).json()
  self.assertNotIn("most_used_app_icon",profile)

 async def test_disable_and_expiry_hide_icon(self):
  await self.publish(png())
  url=f"/auth/app-icon/{self.ids[0]}"
  async with self.sessions() as db:
   person=await db.get(Comrade,self.ids[0])
   person.app_usage_updated_at=datetime.now(timezone.utc)-timedelta(days=9)
   await db.commit()
  self.assertIsNone(await self.revision())
  self.assertEqual((await self.client.get(url,headers=self.headers(1))).status_code,404)
  await self.publish(png(),enabled=False)
  async with self.sessions() as db:
   self.assertIsNone((await db.get(Comrade,self.ids[0])).most_used_app_icon)

 async def test_replacement_and_legacy_update_clear_stale_icon(self):
  await self.publish(png())
  old=await self.revision()
  await self.publish(png(color=255))
  self.assertNotEqual(await self.revision(),old)
  response=await self.client.get(f"/auth/app-icon/{self.ids[0]}",
   headers={**self.headers(1),"If-Match":'"'+old+'"'})
  self.assertEqual(response.status_code,412)
  await self.publish(name="Other app")
  self.assertIsNone(await self.revision())

 async def test_reject_bad_or_oversized_icons(self):
  for data in [b"not a PNG",png(49),png()+b"x"*17000]:
   result=await self.publish(data)
   self.assertEqual(result.status_code,422,result.text)
  response=await self.client.put("/auth/me/app-activity",headers=self.headers(),
   json={"enabled":True,"app_name":"App","app_icon":"%%%invalid"})
  self.assertEqual(response.status_code,422)

if __name__=="__main__":
 names=[name for name in AppIconChecks.__dict__ if name.startswith("test_")]
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(AppIconChecks(n) for n in names))
 raise SystemExit(not result.wasSuccessful())
