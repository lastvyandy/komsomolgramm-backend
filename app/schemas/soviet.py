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

class SovietMemberOut(BaseModel):
    id: uuid.UUID
    role: ComradeRole
    comrade: ComradeOut
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SovietOut(SovietBase):
    id: uuid.UUID
    created_at: datetime
    members: list[SovietMemberOut] = []

    model_config = ConfigDict(from_attributes=True)