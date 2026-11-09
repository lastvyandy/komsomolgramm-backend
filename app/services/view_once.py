from sqlalchemy import select
from app.models.models import AttachmentRecipient
from app.services.privacy import allows

async def annotate_opened(db,messages,viewer):
    attachments={m.id:m for m in messages if m.attachment and m.attachment.view_once}
    if not attachments:return
    receipts=(await db.scalars(select(AttachmentRecipient).where(
        AttachmentRecipient.dispatch_id.in_(attachments),AttachmentRecipient.downloaded.is_(True)))).all()
    for receipt in receipts:
        message=attachments[receipt.dispatch_id]
        # Recipients always know their own state; sender receipts respect read privacy.
        if receipt.comrade_id==viewer or (message.sender_id==viewer and
                await allows(db,receipt.comrade_id,"read_receipts",viewer)):
            message.attachment._opened_for_viewer=True
