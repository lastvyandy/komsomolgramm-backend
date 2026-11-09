"""Profile-song privacy, canonical links and native council title regressions."""
import unittest
from pydantic import ValidationError
from check_relationship import RelationshipChecks
from app.schemas.favorite_song import FavoriteSongInput, canonical_song_link
from app.models.models import Comrade, Soviet
from app.schemas.comrade import ComradeOut
from app.services.councils import ensure_councils, COUNCILS

class SongChecks(RelationshipChecks):
 async def put_song(self, url='https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=tracking', visible=True):
  return await self.client.put('/auth/me/favorite-song',headers=self.headers(),json={
   'song':{'title':' My song ','artist':' My artist ','url':url},'visible':visible})

 async def test_save_public_song_and_normalize_link(self):
  result=await self.put_song()
  self.assertEqual(result.status_code,200,result.text)
  song=result.json()['favorite_song']
  self.assertEqual(song,dict(title='My song',artist='My artist',url='https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC',provider='spotify',artwork_url=None))
  public=await self.client.get(f'/auth/profile/{self.ids[0]}',headers=self.headers(1))
  self.assertEqual(public.json()['favorite_song'],song)

 async def test_hidden_song_is_private_and_deletion_is_durable(self):
  self.assertEqual((await self.put_song(visible=False)).status_code,200)
  public=await self.client.get(f'/auth/profile/{self.ids[0]}',headers=self.headers(1))
  self.assertIsNone(public.json()['favorite_song'])
  own=await self.client.get('/auth/me/favorite-song',headers=self.headers())
  self.assertFalse(own.json()['visible']);self.assertIsNotNone(own.json()['song'])
  async with self.sessions() as db:
   self.assertIsNone(ComradeOut.model_validate(await db.get(Comrade,self.ids[0])).favorite_song)
  other=await self.client.get('/auth/me/favorite-song',headers=self.headers(1))
  self.assertIsNone(other.json()['song'])
  deleted=await self.client.put('/auth/me/favorite-song',headers=self.headers(),json={'song':None,'visible':True})
  self.assertEqual(deleted.status_code,200,deleted.text)
  self.assertEqual((await self.client.get('/auth/me/favorite-song',headers=self.headers())).json(),{'song':None,'visible':False})

 async def test_authentication_and_invalid_input(self):
  self.assertEqual((await self.client.get('/auth/me/favorite-song')).status_code,401)
  self.assertEqual((await self.client.put('/auth/me/favorite-song',json={'song':None})).status_code,401)
  for url in ['https://open.spotify.com.evil.example/track/4uLU6hMCjMI75M1A2tKUQC','javascript:alert(1)',
    'https://user@music.yandex.ru/track/12','https://music.yandex.ru:8080/track/12',
    'https://open.spotify.com/artist/4uLU6hMCjMI75M1A2tKUQC','https://music.youtube.com/watch?v=bad']:
   result=await self.put_song(url)
   self.assertEqual(result.status_code,422,result.text)

 async def test_three_services_and_tracking_removed(self):
  links=[('https://music.yandex.ru/album/12/track/34?utm_source=app','yandex','https://music.yandex.ru/album/12/track/34'),
    ('https://youtu.be/abcdefghijk?si=tracking','youtube','https://music.youtube.com/watch?v=abcdefghijk'),
    ('https://music.youtube.com/watch?v=abcdefghijk&list=foo','youtube','https://music.youtube.com/watch?v=abcdefghijk'),
    ('https://open.spotify.com/intl-es/track/4uLU6hMCjMI75M1A2tKUQC?si=foo','spotify','https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC')]
  for link,provider,url in links:
   result=await self.put_song(link)
   self.assertEqual(result.status_code,200,result.text)
   self.assertEqual(result.json()['favorite_song']['provider'],provider)
   self.assertEqual(result.json()['favorite_song']['url'],url)
  with self.assertRaises(ValidationError):FavoriteSongInput(title='   ',artist='Artist',url=links[0][0])

 async def test_existing_council_names_are_repaired_without_changing_identity(self):
  async with self.sessions() as db:
   await ensure_councils(db)
   channel=await db.get(Soviet,COUNCILS[0][0]);publisher=channel.council_publisher_id
   channel.name='Avisos del soviet supremo RU';await db.commit()
   await ensure_councils(db)
   self.assertEqual(channel.id,COUNCILS[0][0]);self.assertEqual(channel.council_publisher_id,publisher)
   self.assertEqual(channel.name,'Объявления Верховного Совета RU')
   self.assertEqual((await db.get(Soviet,COUNCILS[1][0])).name,'Avisos del soviet supremo ES')

if __name__=='__main__':
 suite=unittest.TestSuite(SongChecks(n) for n in SongChecks.__dict__ if n.startswith('test_'))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
