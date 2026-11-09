"""Explicit geocoding fallback, cached and globally limited across API workers."""
import asyncio, hashlib, json, math
import httpx
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.api.deps import get_current_comrade
from app.core.redis import get_redis_client
router=APIRouter()

class Search(BaseModel):
    query:str=Field(min_length=2,max_length=160)
    language:str=Field(default="en",pattern="^[a-z]{2,3}$")
    latitude:float|None=Field(default=None,ge=-90,le=90,allow_inf_nan=False)
    longitude:float|None=Field(default=None,ge=-180,le=180,allow_inf_nan=False)

@router.post("/search")
async def search(payload:Search,current=Depends(get_current_comrade)):
    query=payload.query.strip()
    if len(query)<2:raise HTTPException(422,"Enter a place or address")
    lat=round(payload.latitude,1) if payload.latitude is not None else None
    lon=round(payload.longitude,1) if payload.longitude is not None else None
    key="location-search:"+hashlib.sha256(json.dumps([query.lower(),payload.language,lat,lon]).encode()).hexdigest()
    redis=get_redis_client()
    try:
        cached=await redis.get(key)
        if cached:return json.loads(cached)
        # All users and workers share this gate: at most one upstream request/1.1s.
        for _ in range(4):
            if await redis.set("location-search:upstream", "1",nx=True,px=1100):break
            await asyncio.sleep(.4)
        else:raise HTTPException(429,"Location search busy; try again shortly")
    except HTTPException:raise
    except Exception:raise HTTPException(503,"Location search temporarily unavailable")
    params={"q":query,"format":"jsonv2","limit":"8","accept-language":payload.language}
    if lat is not None and lon is not None:
        params['viewbox']=f"{max(-180,lon-.3)},{min(90,lat+.3)},{min(180,lon+.3)},{max(-90,lat-.3)}"
    try:
        async with httpx.AsyncClient(timeout=8,follow_redirects=False,headers={
            "User-Agent":"Komsomolgram/1.9 (https://github.com/lastvyandy/Komsomolgram-updates)"}) as client:
            async with client.stream("GET","https://nominatim.openstreetmap.org/search",params=params) as response:
                response.raise_for_status();data=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data)+len(chunk)>512*1024:raise ValueError("Too large")
                    data.extend(chunk)
        values=json.loads(data)
        if not isinstance(values,list):raise ValueError("Invalid result")
        results=[]
        for value in values[:8]:
            try:
                a=float(value['lat']);o=float(value['lon'])
                if not math.isfinite(a) or not math.isfinite(o) or not -90<=a<=90 or not -180<=o<=180:continue
                address=str(value.get('display_name',''))[:300]
                name=str(value.get('name') or address.split(',')[0])[:160]
                results.append({'name':name,'address':address,'lat':a,'lon':o})
            except (KeyError,TypeError,ValueError):continue
    except (httpx.HTTPError,ValueError,TypeError):raise HTTPException(502,"Could not search places")
    try:await redis.set(key,json.dumps(results),ex=3600)
    except Exception:pass
    return results
