import enum
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import String, DateTime, ForeignKey, Enum, Text, LargeBinary
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class ComradeRole(str, enum.Enum):
    SECRETARY_GENERAL = "secretary_general"
    COMMISSAR = "commissar"
    COMRADE = "comrade"

class SovietType(str, enum.Enum):
    DIRECT = "direct"
    SOVIET = "soviet"
    AGITPROP = "agitprop"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Comrade(Base):
    __tablename__ = "comrades"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    callsign: Mapped[str] = mapped_column(String(100), nullable=False)
    bubble_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bio: Mapped[str] = mapped_column(String(280), default="", server_default="", nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    memberships: Mapped[list["SovietMember"]] = relationship(back_populates="comrade")

class PushDevice(Base):
    __tablename__ = "push_devices"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    comrade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comrades.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False)


class ComradeAvatar(Base):
    __tablename__ = "comrade_avatars"

    comrade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True
    )
    image: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Soviet(Base):
    __tablename__ = "soviets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    soviet_type: Mapped[SovietType] = mapped_column(Enum(SovietType), default=SovietType.SOVIET, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    members: Mapped[list["SovietMember"]] = relationship(back_populates="soviet", cascade="all, delete-orphan")
    dispatches: Mapped[list["Dispatch"]] = relationship(back_populates="soviet", cascade="all, delete-orphan")

class SovietMember(Base):
    __tablename__ = "soviet_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    soviet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("soviets.id", ondelete="CASCADE"), index=True, nullable=False)
    comrade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[ComradeRole] = mapped_column(Enum(ComradeRole), default=ComradeRole.COMRADE, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    soviet: Mapped["Soviet"] = relationship(back_populates="members")
    comrade: Mapped["Comrade"] = relationship(back_populates="memberships")

class SovietReadCursor(Base):
    __tablename__ = "soviet_read_cursors"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("soviet_members.id", ondelete="CASCADE"), primary_key=True
    )
    message_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class Dispatch(Base):
    __tablename__ = "dispatches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    soviet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("soviets.id", ondelete="CASCADE"), index=True, nullable=False)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comrades.id", ondelete="SET NULL"), index=True, nullable=True)
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dispatches.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_purged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    soviet: Mapped["Soviet"] = relationship(back_populates="dispatches")
    sender: Mapped["Comrade"] = relationship()
    attachment: Mapped["DispatchAttachment | None"] = relationship()
    forwarded: Mapped["DispatchForward | None"] = relationship(lazy="selectin")


class DispatchReaction(Base):
    __tablename__ = "dispatch_reactions"

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dispatches.id", ondelete="CASCADE"), primary_key=True
    )
    comrade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True
    )
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DispatchAttachment(Base):
    __tablename__ = "dispatch_attachments"

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dispatches.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(180))
    mime_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: utc_now() + timedelta(hours=24), nullable=False)
    is_sticker: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)


class Contact(Base):
    __tablename__ = "contacts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)



class ComradePresence(Base):
    __tablename__ = "comrade_presence"
    comrade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True)
    show_last_seen: Mapped[bool] = mapped_column(default=True)
    show_battery: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProfilePhoto(Base):
    __tablename__ = "profile_photos"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    comrade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), index=True)
    image: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)



class DispatchForward(Base):
    __tablename__ = "dispatch_forwards"
    dispatch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispatches.id", ondelete="CASCADE"), primary_key=True)
    sender_name: Mapped[str] = mapped_column(String(100), nullable=False)


class DispatchHidden(Base):
    __tablename__ = "dispatch_hidden"
    dispatch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispatches.id", ondelete="CASCADE"), primary_key=True)
    comrade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True)


class AttachmentRecipient(Base):
    __tablename__ = "attachment_recipients"
    dispatch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispatch_attachments.dispatch_id", ondelete="CASCADE"), primary_key=True)
    comrade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comrades.id", ondelete="CASCADE"), primary_key=True)
    downloaded: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
