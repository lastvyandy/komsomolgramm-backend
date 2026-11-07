from datetime import datetime
from typing import Literal, Annotated
from pydantic import BaseModel, Field, ConfigDict, model_validator

class LocationData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["location"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = Field(default="", max_length=160)

class ContactData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["contact"]
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=64)

class PollData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["poll"]
    question: str = Field(min_length=1, max_length=240)
    options: list[str] = Field(min_length=2, max_length=12)
    multiple: bool = True

    @model_validator(mode="after")
    def valid_options(self):
        self.question = self.question.strip()
        self.options = [value.strip() for value in self.options]
        if not self.question or any(not value or len(value) > 100 for value in self.options):
            raise ValueError("Empty question or invalid option")
        if len({value.casefold() for value in self.options}) != len(self.options):
            raise ValueError("Duplicate poll options")
        return self

class EventData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["event"]
    title: str = Field(min_length=1, max_length=120)
    starts_at: datetime
    ends_at: datetime | None = None
    description: str = Field(default="", max_length=1000)
    place: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_times(self):
        if self.starts_at.tzinfo is None or (self.ends_at and self.ends_at.tzinfo is None):
            raise ValueError("An explicit timezone is required")
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("Event must end after it starts")
        return self

RichData = Annotated[LocationData | ContactData | PollData | EventData, Field(discriminator="kind")]

class RichCreate(BaseModel):
    rich: RichData
    reply_to_id: str | None = None
    client_id: str

class ChoiceIn(BaseModel):
    choices: list[int] = Field(max_length=12)
