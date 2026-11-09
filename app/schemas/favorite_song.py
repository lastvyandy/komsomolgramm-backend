"""A profile song is a validated public link, never an uploaded audio file."""
import re
from urllib.parse import urlsplit, parse_qs
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ARTWORK_HOSTS = {"i.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com",
    "i.ytimg.com", "img.youtube.com", "lh3.googleusercontent.com",
    "avatars.yandex.net", "avatars.mds.yandex.net", "avatars.mds.yandex.com"}

def safe_artwork_url(value):
    if not isinstance(value, str) or len(value) > 1024: return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname in ARTWORK_HOSTS and not parsed.username and not parsed.password and parsed.port in (None,443):
            return value
    except ValueError: pass
    return None

def canonical_song_link(value: str) -> tuple[str, str]:
    value = value.strip()
    if re.search(r"[\x00-\x20\x7f]", value):
        raise ValueError("Invalid song link")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Use a shared song link from Yandex Music, Spotify or YouTube Music")
    host = (parsed.hostname or "").lower()
    if host in {"music.yandex.ru", "music.yandex.com", "music.yandex.by", "music.yandex.kz", "music.yandex.uz"}:
        if re.fullmatch(r"/(?:album/\d+/)?track/\d+/?", parsed.path):
            return "yandex", f"https://{host}{parsed.path.rstrip('/')}"
    if host == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})/?", parsed.path)
        if match:
            return "spotify", "https://open.spotify.com/track/" + match[1]
    if host in {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}:
        identifier = parsed.path.strip("/") if host == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
            return "youtube", "https://music.youtube.com/watch?v=" + identifier
    raise ValueError("Use a shared song link from Yandex Music, Spotify or YouTube Music")

class FavoriteSongInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    artist: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=1024)
    artwork_url: str | None = None

    @field_validator("artwork_url")
    @classmethod
    def validate_artwork(cls, value):
        if value is not None and safe_artwork_url(value) is None:
            raise ValueError("Invalid artwork URL")
        return value

    @field_validator("title", "artist", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        return canonical_song_link(value)[1]

class FavoriteSongOut(FavoriteSongInput):
    provider: Literal["yandex", "spotify", "youtube"]

class FavoriteSongSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song: FavoriteSongInput | None = None
    visible: bool = True

class FavoriteSongSettingsOut(BaseModel):
    song: FavoriteSongOut | None = None
    visible: bool = False
