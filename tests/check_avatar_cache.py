"""HTTP checks for avatar revision and conditional image transfers."""
import unittest
from check_relationship import RelationshipChecks
from app.models.models import Comrade, ComradeAvatar, ProfilePhoto

JPEG_A=b"\xff\xd8\xffavatar-a\xff\xd9"
JPEG_B=b"\xff\xd8\xffavatar-b\xff\xd9"

class AvatarChecks(RelationshipChecks):
 async def upload(self, image=JPEG_A):
  return await self.client.put("/auth/me/avatar",headers={**self.headers(),"Content-Type":"image/jpeg"},content=image)
 async def avatar(self, etag=None):
  return await self.client.get(f"/auth/avatar/{self.ids[0]}",
   headers={**self.headers(1),**({"If-None-Match":etag} if etag else {})})
 async def revision(self):
  response=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  return response.json()["avatar_revision"]

 async def test_missing_photo_and_authentication(self):
  self.assertEqual((await self.avatar()).status_code,404)
  self.assertEqual((await self.client.get(f"/auth/avatar/{self.ids[0]}")).status_code,401)

 async def test_matching_etag_transfers_no_image_body(self):
  self.assertEqual((await self.upload()).status_code,204)
  first=await self.avatar()
  self.assertEqual(first.status_code,200,first.text)
  self.assertEqual(first.content,JPEG_A)
  self.assertEqual(first.headers["vary"],"Authorization")
  cached=await self.avatar(first.headers["etag"])
  self.assertEqual(cached.status_code,304)
  self.assertEqual(cached.content,b"")
  self.assertEqual(cached.headers["etag"],first.headers["etag"])

 async def test_changed_image_changes_revision_and_conditional_result(self):
  await self.upload()
  version=await self.revision()
  etag=(await self.avatar()).headers["etag"]
  self.assertIsNotNone(version)
  await self.upload(JPEG_B)
  self.assertNotEqual(await self.revision(),version)
  changed=await self.avatar(etag)
  self.assertEqual(changed.status_code,200)
  self.assertEqual(changed.content,JPEG_B)
  self.assertNotEqual(changed.headers["etag"],etag)

 async def test_primary_photo_and_removal_invalidate_version(self):
  await self.upload()
  await self.upload(JPEG_B)
  version=await self.revision()
  async with self.sessions() as db:
   from sqlalchemy import select
   photo=await db.scalar(select(ProfilePhoto).where(ProfilePhoto.comrade_id==self.ids[0],ProfilePhoto.image==JPEG_A))
   photo_id=photo.id
  changed=await self.client.put(f"/auth/me/photos/{photo_id}/primary",headers=self.headers())
  self.assertEqual(changed.status_code,204,changed.text)
  self.assertNotEqual(await self.revision(),version)
  self.assertEqual((await self.avatar()).content,JPEG_A)
  version=await self.revision()
  removed=await self.client.delete("/auth/me/avatar",headers=self.headers())
  self.assertEqual(removed.status_code,204,removed.text)
  self.assertNotEqual(await self.revision(),version)
  self.assertEqual((await self.avatar()).content,JPEG_B)
  await self.client.delete("/auth/me/avatar",headers=self.headers())
  self.assertEqual((await self.avatar()).status_code,404)

if __name__=="__main__":
 suite=unittest.TestSuite(AvatarChecks(n) for n in [
  "test_missing_photo_and_authentication","test_matching_etag_transfers_no_image_body",
  "test_changed_image_changes_revision_and_conditional_result","test_primary_photo_and_removal_invalidate_version"])
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
