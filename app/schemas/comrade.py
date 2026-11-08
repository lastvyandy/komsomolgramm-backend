import uuid
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.schemas.favorite_song import FavoriteSongOut

def zodiac_for_date(value: date | None) -> str | None:
    if value is None:
        return None
    # Conventional tropical zodiac, based only on calendar day/month.
    boundaries = (
        (20, "capricorn", "aquarius"), (19, "aquarius", "pisces"),
        (21, "pisces", "aries"), (20, "aries", "taurus"),
        (21, "taurus", "gemini"), (21, "gemini", "cancer"),
        (23, "cancer", "leo"), (23, "leo", "virgo"),
        (23, "virgo", "libra"), (23, "libra", "scorpio"),
        (22, "scorpio", "sagittarius"), (22, "sagittarius", "capricorn"),
    )
    boundary, before, after = boundaries[value.month - 1]
    return after if value.day >= boundary else before

class ComradeBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    callsign: str = Field(..., min_length=2, max_length=100)

class ComradeCreate(ComradeBase):
    password: str = Field(..., min_length=6, max_length=128)

class ComradeOut(ComradeBase):
    id: uuid.UUID
    bubble_color: str | None = None
    avatar_revision: str | None = None
    bio: str = ""
    country_code: str | None = None
    nationality: str | None = None
    birth_date: date | None = None
    favorite_song: FavoriteSongOut | None = Field(default=None, validation_alias="public_favorite_song")
    relationship_status: str | None = Field(default=None, validation_alias="public_relationship_status")
    most_used_app: str | None = Field(default=None, validation_alias="public_most_used_app")
    most_used_app_icon_revision: str | None = Field(default=None, validation_alias="public_most_used_app_icon_revision")
    is_active: bool
    created_at: datetime

    @computed_field
    @property
    def zodiac_sign(self) -> str | None:
        return zodiac_for_date(self.birth_date)

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    comrade: ComradeOut
