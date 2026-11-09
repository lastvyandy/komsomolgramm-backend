"""Enroll only the sender's contact after three committed direct-chat messages."""
import hashlib
from sqlalchemy import select, func, text
from app.models.models import AutoContactEnrollment, Contact, Dispatch, Soviet, SovietMember, SovietType

def enroll_after_messages(session):
    pairs=set()
    for message in session.new:
        if not isinstance(message,Dispatch) or message.sender_id is None or (message.rich or {}).get('kind')=='basketball_result':
            continue
        # Some HTTP routes assign the sender relationship and the FK during flush.
        recipients=session.scalars(select(SovietMember.comrade_id).join(Soviet,Soviet.id==SovietMember.soviet_id).where(
            Soviet.id==message.soviet_id,Soviet.soviet_type==SovietType.DIRECT,
            SovietMember.comrade_id!=message.sender_id)).all()
        if len(recipients)==1:pairs.add((message.sender_id,recipients[0]))
    for owner,person in sorted(pairs,key=lambda pair:(str(pair[0]),str(pair[1]))):
        if session.bind.dialect.name=='postgresql':
            # Serialize sends without upgrading the FK row locks held by inserts.
            key=int.from_bytes(hashlib.sha256(f'auto-contact:{owner}:{person}'.encode()).digest()[:8],'big',signed=True)
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
        if session.get(AutoContactEnrollment,(owner,person)) is not None:continue
        count=session.scalar(select(func.count()).select_from(Dispatch).join(Soviet,Soviet.id==Dispatch.soviet_id)
            .join(SovietMember,SovietMember.soviet_id==Soviet.id).where(
                Soviet.soviet_type==SovietType.DIRECT,SovietMember.comrade_id==person,
                Dispatch.sender_id==owner,func.coalesce(Dispatch.rich['kind'].as_string(),'')!='basketball_result'))
        if count<3:continue
        # Completion survives manual removal, so later messages don't undo that choice.
        session.add(AutoContactEnrollment(owner_id=owner,person_id=person))
        if session.get(Contact,(owner,person)) is None:session.add(Contact(owner_id=owner,person_id=person))
