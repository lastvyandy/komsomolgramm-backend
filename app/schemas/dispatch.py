import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.comrade import ComradeOut

class DispatchCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)

class AttachmentOut(BaseModel):
    dispatch_id: uuid.UUID
    name: str
    mime_type: str

    model_config = ConfigDict(from_attributes=True)


class DispatchOut(BaseModel):
    id: uuid.UUID
    soviet_id: uuid.UUID
    content: str
    is_purged: bool
    created_at: datetime
    sender: ComradeOut | None = None
    attachment: AttachmentOut | None = None

    model_config = ConfigDict(from_attributes=True)
