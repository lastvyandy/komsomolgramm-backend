import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class ComradeBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    callsign: str = Field(..., min_length=2, max_length=100)

class ComradeCreate(ComradeBase):
    password: str = Field(..., min_length=6, max_length=128)

class ComradeOut(ComradeBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    comrade: ComradeOut