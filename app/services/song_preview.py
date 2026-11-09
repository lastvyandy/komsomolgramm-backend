"""Bounded public music metadata lookups; no audio downloads or arbitrary URLs."""
import asyncio
import json
import re
import time
from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlencode
import httpx
from fastapi import HTTPException
from app.schemas.favorite_song import canonical_song_link, safe_artwork_url

_network = asyncio.Semaphore(4)
_cache = OrderedDict()
_MAX_BYTES = 1536 * 1024

class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.json_ld = []
        self._script = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property", attrs.get("name", ""))
            self.meta.setdefault(key, attrs.get("content", ""))
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = []
    def handle_data(self, data):
        if self._script is not None: self._script.append(data)
    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try: self.json_ld.append(json.loads("".join(self._script)))
            except (ValueError, TypeError): pass
            self._script = None

async def request_data(client, url, *, method="GET", data=None, files=None):
    # Every caller builds the URL from fixed provider origins and validated IDs.
    async with client.stream(method, url, data=data, files=files) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > _MAX_BYTES: raise ValueError("Metadata response too large")
        return bytes(body)

def page_song(document, provider):
    page = PageMetadata(); page.feed(document)
    title = page.meta.get("og:title", "")
    artist = page.meta.get("music:musician:name", "")
    artwork = page.meta.get("og:image", "")
    def walk(node):
        nonlocal title, artist, artwork
        if isinstance(node, list):
            for item in node: walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type", [])
            kinds = [kind] if isinstance(kind, str) else kind if isinstance(kind, list) else []
            if "MusicRecording" in kinds:
                title = node.get("name") or title
                people = node.get("byArtist", node.get("author", []))
                if isinstance(people, dict): people = [people]
                if isinstance(people, list): artist = ", ".join(p.get("name", "") for p in people if isinstance(p, dict)).strip(", ") or artist
                image = node.get("image")
                if isinstance(image, str): artwork = image
            for key, value in node.items():
                if key == "@graph": walk(value)
    for node in page.json_ld: walk(node)
    if not artist and provider == "spotify":
        # Spotify's own public Open Graph description starts with the artist.
        description = page.meta.get("og:description", "")
        if " · " in description: artist = description.split(" · ", 1)[0]
    if not artist and provider == "yandex":
        description = page.meta.get("og:description", "")
        match = re.search(r"(?:исполнител[ья]|artist)\s*[:：]\s*([^·.]+)", description, re.I)
        if match: artist = match[1]
    return title, artist, artwork

def clean(value, limit):
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()[:limit]

async def spotify_short_link(link):
    parsed = urlsplit(link.strip())
    if parsed.scheme != "https" or parsed.hostname != "spotify.link" or parsed.username or parsed.password or parsed.port not in (None,443) or not re.fullmatch(r"/[A-Za-z0-9_-]{1,120}", parsed.path):
        raise HTTPException(422, "Invalid Spotify link")
    try:
        if _network.locked(): raise HTTPException(429, "Try again shortly")
        async with _network, httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            data = json.loads(await request_data(client, "https://open.spotify.com/oembed?"+urlencode({"url":link.strip()})))
            embedded = data.get("iframe_url", "")
            if not embedded:
                match = re.search(r'src=["\x27](https://open\.spotify\.com/embed/track/[A-Za-z0-9]+)', data.get("html", ""))
                embedded = match[1] if match else ""
            page = urlsplit(embedded)
            match = re.fullmatch(r"/embed/track/([A-Za-z0-9]{22})", page.path)
            if page.hostname != "open.spotify.com" or match is None: raise ValueError("Not a song")
            return "https://open.spotify.com/track/"+match[1]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, "Song preview unavailable") from exc

async def resolve_song(link):
    try: host = urlsplit(link.strip()).hostname
    except ValueError as exc: raise HTTPException(422, "Invalid song link") from exc
    if host == "spotify.link": link = await spotify_short_link(link)
    try: provider, url = canonical_song_link(link)
    except ValueError as exc: raise HTTPException(422, "Invalid song link") from exc
    cached = _cache.get(url)
    if cached and cached[0] > time.monotonic():
        _cache.move_to_end(url); return dict(cached[1])
    try:
        if _network.locked(): raise HTTPException(429, "Try again shortly")
        async with _network, asyncio.timeout(22), httpx.AsyncClient(timeout=8, follow_redirects=False,
                headers={"User-Agent":"Komsomolgram/1.8 (public music link preview)",
                         "Accept-Language":"en,ru;q=0.9", "Accept":"text/html,application/json"}) as client:
            title = artist = artwork = ""
            if provider == "youtube":
                identifier = urlsplit(url).query.split("v=", 1)[1]
                endpoint = "https://www.youtube.com/oembed?" + urlencode({"url":"https://www.youtube.com/watch?v="+identifier,"format":"json"})
                data = json.loads(await request_data(client, endpoint))
                title, artist, artwork = data.get("title"), data.get("author_name"), data.get("thumbnail_url")
                # Music uploads frequently identify the artist as a Topic channel.
                if isinstance(artist, str): artist = re.sub(r" - Topic$", "", artist)
                if isinstance(title, str) and artist and title.casefold().startswith((artist+" - ").casefold()):
                    title = title[len(artist)+3:]
            elif provider == "spotify":
                try:
                    data = json.loads(await request_data(client, "https://open.spotify.com/oembed?"+urlencode({"url":url})))
                    title, artwork = data.get("title", ""), data.get("thumbnail_url", "")
                except (httpx.HTTPError, ValueError, KeyError): pass
                parsed_title, artist, parsed_art = page_song((await request_data(client, url)).decode("utf-8", "replace"), provider)
                title = parsed_title or title; artwork = parsed_art or artwork
            else:
                identifier = urlsplit(url).path.rstrip("/").split("/")[-1]
                try:
                    # This is the public tracks resource used by Yandex's own iframe.
                    payload = json.loads(await request_data(client, "https://api.music.yandex.ru/tracks", method="POST",
                        files={"trackIds":(None,identifier),"removeDuplicates":(None,"false")}))
                    tracks = payload.get("result", payload) if isinstance(payload, dict) else payload
                    track = tracks[0] if isinstance(tracks, list) and tracks else {}
                    if str(track.get("id")) == identifier:
                        title = track.get("title", "")
                        artist = ", ".join(p.get("name", "") for p in track.get("artists", []))
                        artwork = track.get("coverUri", "")
                        if artwork:
                            artwork = "https://" + artwork.removeprefix("https://").replace("%%", "300x300")
                except (httpx.HTTPError, ValueError, KeyError, TypeError): pass
                if not title or not artist:
                    title, artist, artwork = page_song((await request_data(client, url)).decode("utf-8", "replace"), provider)
            title, artist = clean(title, 120), clean(artist, 100)
            if not title or not artist: raise ValueError("Song metadata unavailable")
            result = dict(title=title, artist=artist, url=url, provider=provider,
                          artwork_url=safe_artwork_url(artwork))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, TimeoutError) as exc:
        raise HTTPException(502, "Song preview unavailable; try again later") from exc
    _cache[url] = (time.monotonic()+21600, result)
    _cache.move_to_end(url)
    while len(_cache) > 256: _cache.popitem(last=False)
    return dict(result)
