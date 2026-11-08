"""HTTP regression checks for optional relationship status and server-side privacy."""
import unittest
from check_rich_messages import RichMessageChecks
from app.api.v1.endpoints import auth
from app.models.models import Comrade
from app.schemas.comrade import ComradeOut

class RelationshipChecks(RichMessageChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp()
  self.app.include_router(auth.router,prefix="/auth")

 async def update(self, **body):
  return await self.client.patch("/auth/me",headers=self.headers(),json=body)

 async def test_default_hidden_and_private_settings_require_authentication(self):
  public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  self.assertIsNone(public.json()["relationship_status"])
  self.assertEqual((await self.client.get("/auth/me/relationship")).status_code,401)
  own=await self.client.get("/auth/me/relationship",headers=self.headers())
  self.assertEqual(own.json(),{"relationship_status":None,"show_relationship":False})

 async def test_all_choices_can_be_published(self):
  for value in ["single","married","open_relationship","living_life","dating","polyamorous",
                "studying_communism","free_to_use","depends","everything_has_a_price"]:
   result=await self.update(relationship_status=value,show_relationship=True)
   self.assertEqual(result.status_code,200,result.text)
   public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
   self.assertEqual(public.json()["relationship_status"],value)

 async def test_hide_retains_private_choice_and_removes_public_value(self):
  await self.update(relationship_status="polyamorous",show_relationship=True)
  self.assertEqual((await self.update(show_relationship=False)).status_code,200)
  public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  self.assertIsNone(public.json()["relationship_status"])
  own=await self.client.get("/auth/me/relationship",headers=self.headers())
  self.assertEqual(own.json(),{"relationship_status":"polyamorous","show_relationship":False})
  # All nested ComradeOut serialization uses the same privacy property.
  async with self.sessions() as db:
   person=await db.get(Comrade,self.ids[0])
   self.assertIsNone(ComradeOut.model_validate(person).model_dump()["relationship_status"])
  await self.update(show_relationship=True)
  public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  self.assertEqual(public.json()["relationship_status"],"polyamorous")

 async def test_remove_validate_and_preserve_when_old_client_updates_profile(self):
  await self.update(relationship_status="single",show_relationship=True)
  self.assertEqual((await self.update(relationship_status="not-a-status")).status_code,422)
  self.assertEqual((await self.update(show_relationship=None)).status_code,422)
  await self.update(bio="A short bio")
  public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  self.assertEqual(public.json()["relationship_status"],"single")
  await self.update(relationship_status=None)
  public=await self.client.get(f"/auth/profile/{self.ids[0]}",headers=self.headers(1))
  self.assertIsNone(public.json()["relationship_status"])

if __name__=="__main__":
 suite=unittest.TestSuite(RelationshipChecks(name) for name in [
  "test_default_hidden_and_private_settings_require_authentication","test_all_choices_can_be_published",
  "test_hide_retains_private_choice_and_removes_public_value","test_remove_validate_and_preserve_when_old_client_updates_profile"])
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
