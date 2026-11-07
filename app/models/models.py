import enum
import uuid
from datetime import datetime, timezone
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
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_purged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    soviet: Mapped["Soviet"] = relationship(back_populates="dispatches")
    sender: Mapped["Comrade"] = relationship()
    attachment: Mapped["DispatchAttachment | None"] = relationship()


class DispatchAttachment(Base):
    __tablename__ = "dispatch_attachments"

    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dispatches.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(180))
    mime_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
