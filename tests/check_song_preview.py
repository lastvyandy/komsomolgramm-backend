"""Public provider fixtures, privacy and bounded fetching for link-only songs."""
import json
import unittest
from unittest.mock import patch
import httpx
from check_relationship import RelationshipChecks
from app.services import song_preview as preview

SPOTIFY='https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC'
YOUTUBE='https://music.youtube.com/watch?v=dQw4w9WgXcQ'
YANDEX='https://music.yandex.ru/album/24410941/track/110107044'

class PreviewChecks(RelationshipChecks):
 async def asyncSetUp(self):
  await super().asyncSetUp();preview._cache.clear()
 async def request(self,url,handler):
  original=httpx.AsyncClient
  with patch.object(preview.httpx,'AsyncClient',side_effect=lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs)):
   return await self.client.post('/auth/me/favorite-song/preview',headers=self.headers(),json={'url':url})

 async def test_spotify_title_artist_artwork_and_no_metadata_changes_before_save(self):
  calls=[]
  def handler(request):
   calls.append(str(request.url));self.assertNotIn('authorization',request.headers)
   if request.url.path=='/oembed':return httpx.Response(200,json={'title':'Never Gonna Give You Up','thumbnail_url':'https://i.scdn.co/image/cover'})
   return httpx.Response(200,text='<meta content="Never Gonna Give You Up" property="og:title"><meta property="og:description" content="Rick Astley · Album · Song · 1987"><meta property="og:image" content="https://i.scdn.co/image/cover">')
  result=await self.request(SPOTIFY,handler)
  self.assertEqual(result.status_code,200,result.text)
  self.assertEqual(result.json()['artist'],'Rick Astley');self.assertEqual(result.json()['provider'],'spotify')
  self.assertEqual(result.json()['artwork_url'],'https://i.scdn.co/image/cover')
  self.assertIsNone((await self.client.get('/auth/me/favorite-song',headers=self.headers())).json()['song'])
  again=await self.request(SPOTIFY,lambda r:(_ for _ in ()).throw(AssertionError('cache not used')))
  self.assertEqual(again.json(),result.json());self.assertEqual(len(calls),2)
  song={k:v for k,v in result.json().items() if k!='provider'}
  saved=await self.client.put('/auth/me/favorite-song',headers=self.headers(),json={'song':song,'visible':True})
  self.assertEqual(saved.status_code,200,saved.text)
  self.assertEqual(saved.json()['favorite_song']['artwork_url'],song['artwork_url'])

 async def test_youtube_metadata_and_topic_artist(self):
  def handler(request):
   self.assertEqual(request.url.host,'www.youtube.com')
   return httpx.Response(200,json={'title':'A song','author_name':'An artist - Topic','thumbnail_url':'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'})
  response=await self.request(YOUTUBE,handler)
  self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['artist'],'An artist')

 async def test_shared_spotify_short_link_uses_provider_oembed(self):
  def handler(request):
   self.assertEqual(request.url.host,'open.spotify.com')
   if request.url.path=='/oembed':return httpx.Response(200,json={'title':'Song','iframe_url':'https://open.spotify.com/embed/track/4uLU6hMCjMI75M1A2tKUQC?utm_source=oembed'})
   return httpx.Response(200,text='<meta property="og:title" content="Song"><meta property="og:description" content="Artist · Album · Song">')
  response=await self.request('https://spotify.link/Abc123',handler)
  self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['url'],SPOTIFY)

 async def test_yandex_public_resource_and_artwork_template(self):
  def handler(request):
   self.assertEqual(str(request.url),'https://api.music.yandex.ru/tracks');self.assertEqual(request.method,'POST')
   self.assertIn(b'name="trackIds"',request.content);self.assertIn(b'110107044',request.content)
   return httpx.Response(200,json={'result':[{'id':110107044,'title':'Кукушка','artists':[{'name':'КИНО'}],
    'coverUri':'avatars.yandex.net/get-music-content/123/cover/%%'}]})
  result=await self.request(YANDEX,handler)
  self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['artist'],'КИНО')
  self.assertTrue(result.json()['artwork_url'].endswith('/300x300'))

 async def test_arbitrary_links_and_artwork_are_rejected(self):
  result=await self.request('https://127.0.0.1/private',lambda r:(_ for _ in ()).throw(AssertionError('SSRF')))
  self.assertEqual(result.status_code,422)
  payload={'song':{'title':'A','artist':'B','url':SPOTIFY,'artwork_url':'https://localhost/private'},'visible':True}
  result=await self.client.put('/auth/me/favorite-song',headers=self.headers(),json=payload)
  self.assertEqual(result.status_code,422)
  self.assertEqual((await self.client.post('/auth/me/favorite-song/preview',json={'url':SPOTIFY})).status_code,401)

 async def test_missing_or_blocked_metadata_is_an_error_not_a_fake_song(self):
  result=await self.request(YANDEX,lambda request:httpx.Response(451))
  self.assertEqual(result.status_code,502)
  result=await self.request(SPOTIFY,lambda request:httpx.Response(200,text='<html>Sign in</html>'))
  self.assertEqual(result.status_code,502)
  self.assertNotIn(SPOTIFY,preview._cache)

 async def test_large_response_and_external_redirect_are_not_followed(self):
  for handler in [lambda r:httpx.Response(200,content=b'x'*(preview._MAX_BYTES+1)),
   lambda r:httpx.Response(302,headers={'Location':'http://127.0.0.1/internal'})]:
   result=await self.request(YOUTUBE,handler);self.assertEqual(result.status_code,502)

if __name__=='__main__':
 suite=unittest.TestSuite(PreviewChecks(n) for n in PreviewChecks.__dict__ if n.startswith('test_'))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
