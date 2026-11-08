"""Two read-only announcement councils. The publishing account is pinned by UUID."""
import uuid
from sqlalchemy import select, text, func
from fastapi import HTTPException
from app.models.models import Comrade, Soviet, SovietMember, SovietType, ComradeRole

COUNCILS = [
 (uuid.UUID("4bb63d3e-041f-41fd-887a-000000000001"), "RU", "Объявления Верховного Совета RU"),
 (uuid.UUID("4bb63d3e-041f-41fd-887a-000000000002"), "ES", "Avisos del soviet supremo ES"),
]

async def ensure_councils(db, person_id=None):
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(87531027)"))
    pinned = await db.scalar(select(Soviet.council_publisher_id).where(Soviet.council_key.is_not(None),
        Soviet.council_publisher_id.is_not(None)).limit(1))
    publisher = pinned or await db.scalar(select(Comrade.id).where(func.lower(Comrade.username) == "andy"))
    people = [person_id] if person_id is not None else (await db.scalars(
        select(Comrade.id).where(Comrade.is_active.is_(True)))).all()
    for identifier, key, name in COUNCILS:
        council = await db.get(Soviet, identifier)
        if council is None:
            council = Soviet(id=identifier, name=name, soviet_type=SovietType.AGITPROP,
                council_key=key, council_publisher_id=publisher)
            db.add(council)
            await db.flush()
        elif council.council_publisher_id is None and publisher is not None:
            council.council_publisher_id = publisher
        # Repair channels created before native-language titles were introduced.
        council.name = name
        members = set((await db.scalars(select(SovietMember.comrade_id).where(
            SovietMember.soviet_id == identifier, SovietMember.comrade_id.in_(people)))).all())
        db.add_all([SovietMember(soviet_id=identifier,comrade_id=person,
            role=ComradeRole.SECRETARY_GENERAL if person == council.council_publisher_id else ComradeRole.COMRADE)
            for person in people if person not in members])
    await db.commit()

async def require_council_publisher(db, chat_id, person_id):
    council = await db.get(Soviet, chat_id)
    if council and council.council_key is not None and council.council_publisher_id != person_id:
        raise HTTPException(403, "Only the council publisher can post announcements")

