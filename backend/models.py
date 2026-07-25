import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite's DateTime storage silently drops tzinfo on read, which then
    raises "can't subtract offset-naive and offset-aware datetimes" the
    moment a DB-loaded timestamp (e.g. after restart recovery, or an
    incident's opened_at in the priority calc) is compared against a
    freshly created UTC-aware `now`. Normalizing at the ORM boundary - UTC
    in, UTC-aware out - fixes it once instead of at every call site.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    api_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="zone")


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # fire | gas | water | pir
    status: Mapped[str] = mapped_column(String, nullable=False, default="offline")  # online | offline

    zone: Mapped["Zone"] = relationship(back_populates="sensors")


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("zone_id", "seq", name="uq_readings_zone_seq"),
        Index("idx_readings_zone_ts", "zone_id", "ts_server"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    fire: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gas_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    water_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    occupancy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts_device: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    ts_server: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Additive column beyond the literal locked schema block: flags readings whose
    # ts_device arrived out of order relative to the zone's last applied reading (TC18c).
    # Such readings are stored for audit but never rewrite current zone state.
    anomaly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ZoneTransition(Base):
    __tablename__ = "zone_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False)
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    cause: Mapped[str] = mapped_column(String, nullable=False)  # 'sensor' | 'manual'
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Additive column beyond the literal locked schema block: required to
    # satisfy "manual state set with reason" (POST /api/admin/override).
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False)
    opened_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    peak_risk: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")  # open | acked | resolved
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("idx_incidents_status_created", "status", "opened_at"),)


class Acknowledgment(Base):
    __tablename__ = "acknowledgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # staff | admin
    token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
