from contextvars import ContextVar
from sqlalchemy import select
from app.models.models import Comrade, Contact
viewer_context = ContextVar("profile_privacy_viewer",default=None)
KEYS = ("photo","bio","birthday","country","nationality","relationship","song","app_usage",
        "last_seen","read_receipts","battery","sleep","notes","calls","group_invites")

def audience(owner,key):
    return (owner.privacy or {}).get(key,"contacts" if key=="notes" else "all")

async def allows(db,owner_id,key,viewer):
    if owner_id==viewer:return True
    owner=await db.get(Comrade,owner_id)
    if owner is None:return False
    rule=audience(owner,key)
    if rule=="all":return True
    if rule=="nobody":return False
    return await db.get(Contact,(owner_id,viewer)) is not None

async def load_viewer(db,viewer):
    contacts=set((await db.scalars(select(Contact.owner_id).where(Contact.person_id==viewer))).all())
    viewer_context.set((viewer,contacts))
