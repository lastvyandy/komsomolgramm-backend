import uuid
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator, model_serializer, PrivateAttr
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
    _privacy: dict = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def capture_privacy(cls,value,handler):
        if isinstance(value,cls):return handler(value)
        instance=handler(value)
        instance._privacy=dict(getattr(value,"privacy",None) or {})
        return instance

    @model_serializer(mode="wrap")
    def hide_private_fields(self,handler):
        result=handler(self)
        from app.services.privacy import viewer_context
        context=viewer_context.get()
        if context is None or context[0]==self.id:return result
        viewer,contacts=context
        fields={"photo":["avatar_revision"],"bio":["bio"],"birthday":["birth_date","zodiac_sign"],
            "country":["country_code"],"nationality":["nationality"],"relationship":["relationship_status"],
            "song":["favorite_song"],"app_usage":["most_used_app","most_used_app_icon_revision"]}
        for key,names in fields.items():
            rule=self._privacy.get(key,"all")
            hidden=rule=="nobody" or (rule=="contacts" and self.id not in contacts)
            if key=="photo":result["avatar_visible"]=not hidden
            if hidden:
                for name in names:result[name]="" if name=="bio" else None
        return result

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
