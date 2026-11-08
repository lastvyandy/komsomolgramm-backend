import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.models.models import SovietType, ComradeRole
from app.schemas.comrade import ComradeOut

class SovietBase(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    soviet_type: SovietType = SovietType.SOVIET

class SovietCreate(SovietBase):
    member_ids: list[uuid.UUID] = Field(default_factory=list)

class SovietMembersAdd(BaseModel):
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)

class SovietMemberOut(BaseModel):
    id: uuid.UUID
    role: ComradeRole
    comrade: ComradeOut
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ChatPreview(BaseModel):
    content: str
    sender_name: str | None
    sender_id: uuid.UUID | None
    created_at: datetime


class SovietOut(SovietBase):
    id: uuid.UUID
    created_at: datetime
    members: list[SovietMemberOut] = []
    last_message: ChatPreview | None = None
    unread_count: int = 0
    council_key: str | None = None
    council_publisher_id: uuid.UUID | None = None

    model_config = ConfigDict(from_attributes=True)

