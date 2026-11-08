"""HTTP checks for voluntary most-used-app sharing and server-side privacy."""
import unittest
from datetime import datetime, timedelta, timezone
from check_relationship import RelationshipChecks
from app.models.models import Comrade
from app.schemas.comrade import ComradeOut

class AppUsageChecks(RelationshipChecks):
 async def update_usage(self, body, user=0):
  return await self.client.put("/auth/me/app-activity",headers=self.headers(user),json=body)

 async def public(self, user=0):
  response=await self.client.get(f"/auth/profile/{self.ids[user]}",headers=self.headers(1))
  self.assertEqual(response.status_code,200,response.text)
  return response.json()["most_used_app"]

 async def test_disabled_by_default_and_authentication_required(self):
  self.assertIsNone(await self.public())
  own=await self.client.get("/auth/me/app-activity",headers=self.headers())
  self.assertEqual(own.json(),{"enabled":False,"app_name":None,"updated_at":None})
  self.assertEqual((await self.client.get("/auth/me/app-activity")).status_code,401)
  self.assertEqual((await self.client.put("/auth/me/app-activity",json={"enabled":True})).status_code,401)

 async def test_opt_in_publishes_only_label_and_other_account_is_unchanged(self):
  result=await self.update_usage({"enabled":True,"app_name":"Telegram"})
  self.assertEqual(result.status_code,200,result.text)
  self.assertEqual(await self.public(),"Telegram")
  self.assertIsNotNone(result.json()["updated_at"])
  other=await self.client.get("/auth/me/app-activity",headers=self.headers(1))
  self.assertFalse(other.json()["enabled"])
  self.assertIsNone(await self.public(1))

 async def test_disable_erases_stored_name_and_timestamp(self):
  await self.update_usage({"enabled":True,"app_name":"YouTube"})
  result=await self.update_usage({"enabled":False,"app_name":"Ignored"})
  self.assertEqual(result.json(),{"enabled":False,"app_name":None,"updated_at":None})
  self.assertIsNone(await self.public())
  async with self.sessions() as db:
   person=await db.get(Comrade,self.ids[0])
   self.assertIsNone(person.most_used_app_name)
   self.assertIsNone(person.app_usage_updated_at)
   self.assertFalse(person.share_app_usage)
   self.assertIsNone(ComradeOut.model_validate(person).most_used_app)

 async def test_no_statistics_clears_previous_result_without_inventing_a_winner(self):
  await self.update_usage({"enabled":True,"app_name":"Telegram"})
  result=await self.update_usage({"enabled":True,"app_name":None})
  self.assertTrue(result.json()["enabled"])
  self.assertIsNone(await self.public())

 async def test_old_or_future_results_are_not_shown(self):
  await self.update_usage({"enabled":True,"app_name":"Browser"})
  for offset in [-9,1]:
   async with self.sessions() as db:
    person=await db.get(Comrade,self.ids[0])
    person.app_usage_updated_at=datetime.now(timezone.utc)+timedelta(days=offset)
    await db.commit()
   self.assertIsNone(await self.public())

 async def test_labels_are_validated_and_legacy_profile_updates_preserve_opt_in(self):
  for label in ["","  ","x"*101]:
   result=await self.update_usage({"enabled":True,"app_name":label})
   self.assertEqual(result.status_code,422,result.text)
  self.assertEqual((await self.update_usage({"app_name":"App"})).status_code,422)
  await self.update_usage({"enabled":True,"app_name":"  Yandex   Maps  "})
  self.assertEqual(await self.public(),"Yandex Maps")
  await self.client.patch("/auth/me",headers=self.headers(),json={"bio":"Updated"})
  self.assertEqual(await self.public(),"Yandex Maps")

if __name__=="__main__":
 suite=unittest.TestSuite(AppUsageChecks(n) for n in [
  "test_disabled_by_default_and_authentication_required",
  "test_opt_in_publishes_only_label_and_other_account_is_unchanged",
  "test_disable_erases_stored_name_and_timestamp",
  "test_no_statistics_clears_previous_result_without_inventing_a_winner",
  "test_old_or_future_results_are_not_shown",
  "test_labels_are_validated_and_legacy_profile_updates_preserve_opt_in"])
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
