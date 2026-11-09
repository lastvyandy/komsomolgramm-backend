"""Authenticated, temporary transfer of public Telegram sticker packs to the phone."""
import base64
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit,parse_qs
import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from app.api.deps import get_current_comrade
from app.core.config import settings
from app.models.models import Comrade

router = APIRouter()
_network = asyncio.Semaphore(4)
logging.getLogger("httpx").setLevel(logging.WARNING)  # Bot tokens are part of Telegram URLs.
logging.getLogger("httpcore").setLevel(logging.WARNING)

class PackRequest(BaseModel):
    link: str = Field(min_length=1, max_length=240)

def pack_name(link):
    value=link.strip()
    if value.startswith(("t.me/","telegram.me/")):value="https://"+value
    if "://" in value:
        try:
            parsed=urlsplit(value)
            port=parsed.port
        except ValueError:raise HTTPException(422,"Invalid Telegram link")
        if parsed.username or parsed.password:raise HTTPException(422,"Invalid Telegram link")
        if parsed.scheme=="tg" and parsed.hostname=="addstickers":
            values=parse_qs(parsed.query).get("set",[])
            if len(values)!=1:raise HTTPException(422,"Expected a sticker pack")
            value=values[0]
        elif parsed.scheme=="https" and parsed.hostname in ("t.me","telegram.me") and port in (None,443):
            prefix="/addstickers/"
            if not parsed.path.startswith(prefix):raise HTTPException(422,"Expected an addstickers link")
            value=parsed.path[len(prefix):].rstrip("/")
        else:raise HTTPException(422,"Expected a Telegram sticker-pack link")
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}",value):raise HTTPException(422,"Invalid sticker pack name")
    return value

def token():
    value=os.environ.get("TELEGRAM_STICKER_BOT_TOKEN",settings.TELEGRAM_STICKER_BOT_TOKEN.get_secret_value()).strip()
    if not value:raise HTTPException(503,"Telegram sticker import is not configured")
    return value

@router.get("/telegram/status")
async def import_status(current:Comrade=Depends(get_current_comrade)):
    try:token();configured=True
    except HTTPException:configured=False
    return {"configured":configured}

async def telegram_json(method,params):
    secret=token()
    try:
        async with _network,httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
            async with client.stream("GET",f"https://api.telegram.org/bot{secret}/{method}",params=params) as result:
                if result.status_code==401:raise HTTPException(424,"Invalid Telegram bot configuration")
                if result.status_code==429:raise HTTPException(429,"Telegram rate limit; try again shortly")
                if result.status_code==400 and method=="getStickerSet":raise HTTPException(404,"Sticker pack not found")
                result.raise_for_status();data=bytearray()
                async for chunk in result.aiter_bytes():
                    if len(data)+len(chunk)>2*1024*1024:raise ValueError("Too large")
                    data.extend(chunk)
            body=json.loads(data)
            if not body.get("ok"):
                code=body.get("error_code")
                if code==401:raise HTTPException(424,"Invalid Telegram bot configuration")
                if code==429:raise HTTPException(429,"Telegram rate limit")
                if code==400 and method=="getStickerSet":raise HTTPException(404,"Sticker pack not found")
                raise ValueError("Telegram rejected request")
            return body["result"]
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise HTTPException(502,"Could not retrieve this Telegram pack")

def make_ticket(file_id, owner):
    body = base64.urlsafe_b64encode(json.dumps(
        {"file": file_id, "owner": str(owner), "expires": int(time.time())+900},
        separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(settings.SECRET_KEY.encode(), b"sticker-import:"+body.encode(), hashlib.sha256).hexdigest()
    return body+"."+signature

def read_ticket(ticket, owner):
    try:
        body, signature = ticket.rsplit(".", 1)
        expected = hmac.new(settings.SECRET_KEY.encode(), b"sticker-import:"+body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(body+"="*(-len(body)%4)))
        if value["owner"] != str(owner) or not time.time() < value["expires"] <= time.time()+901:
            raise ValueError()
        return value["file"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(403, "Invalid or expired sticker download")

@router.post("/telegram")
async def telegram_pack(payload: PackRequest, current: Comrade = Depends(get_current_comrade)):
    result = await telegram_json("getStickerSet", {"name": pack_name(payload.link)})
    files = []
    for sticker in result.get("stickers", [])[:120]:
        poster = sticker.get("is_animated") or sticker.get("is_video")
        file_id = (sticker.get("thumbnail") or sticker.get("thumb") or {}).get("file_id") if poster else sticker.get("file_id")
        if file_id:
            files.append({"ticket": make_ticket(file_id, current.id), "poster": bool(poster)})
    if not files:
        raise HTTPException(422, "This pack has no supported stickers")
    return {"name": str(result.get("title", "Telegram"))[:100], "files": files}

@router.get("/telegram/file")
async def telegram_file(ticket: str, current: Comrade = Depends(get_current_comrade)):
    if len(ticket) > 2000:
        raise HTTPException(422, "Invalid sticker download")
    file_id = read_ticket(ticket, current.id)
    metadata = await telegram_json("getFile", {"file_id": file_id})
    path = metadata.get("file_path", "")
    if not isinstance(path,str) or len(path)>512 or not re.fullmatch(r"(?:[a-zA-Z0-9_-]+/)+[a-zA-Z0-9_.-]+",path) or any(p in (".","..") for p in path.split("/")):
        raise HTTPException(502, "Invalid Telegram file")
    try:
        async with _network, httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            async with client.stream("GET", f"https://api.telegram.org/file/bot{token()}/{path}") as result:
                result.raise_for_status()
                data = bytearray()
                async for chunk in result.aiter_bytes():
                    if len(data)+len(chunk) > 512*1024:
                        raise HTTPException(413, "Sticker is too large")
                    data.extend(chunk)
        return Response(bytes(data), media_type="image/webp",
            headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
    except httpx.HTTPError:
        raise HTTPException(502, "Could not download sticker")
