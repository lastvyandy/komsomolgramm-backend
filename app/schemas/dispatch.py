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


class ReplyOut(BaseModel):
    id: uuid.UUID
    sender_name: str
    content: str
    media_type: str | None = None


class ReactionCount(BaseModel):
    stamp: str
    count: int


class DispatchOut(BaseModel):
    id: uuid.UUID
    soviet_id: uuid.UUID
    content: str
    is_purged: bool
    created_at: datetime
    sender: ComradeOut | None = None
    attachment: AttachmentOut | None = None
    reply_to_id: uuid.UUID | None = None
    reply: ReplyOut | None = None
    reactions: list[ReactionCount] = Field(default_factory=list)
    my_reaction: str | None = None

    model_config = ConfigDict(from_attributes=True)

