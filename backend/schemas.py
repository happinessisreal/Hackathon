import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_binary(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if value not in (0, 1):
        raise ValueError(f"{field_name} must be 0 or 1 (or omitted/null if the sensor is offline)")
    return value


class IngestPayload(BaseModel):
    seq: int = Field(ge=0)
    fire: int | None = Field(default=None, description="0/1 digital flame reading, null if sensor offline")
    gas_norm: float | None = Field(default=None, ge=0.0, le=1.0, description="MQ-2 normalized 0.0-1.0")
    water_norm: float | None = Field(default=None, ge=0.0, le=1.0)
    occupancy: int | None = Field(default=None, description="0/1 PIR reading, null if sensor offline")
    ts_device: dt.datetime
    uptime_ms: int | None = Field(default=None, ge=0, description="millis() since node boot, for gas warm-up")

    @field_validator("fire")
    @classmethod
    def _check_fire(cls, v):
        return _validate_binary(v, "fire")

    @field_validator("occupancy")
    @classmethod
    def _check_occupancy(cls, v):
        return _validate_binary(v, "occupancy")

    @field_validator("ts_device")
    @classmethod
    def _ensure_tz(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=dt.timezone.utc)
        return v


class IngestResponse(BaseModel):
    duplicate: bool
    anomaly: bool = False
    state: str
    risk_score: float


class SensorStatusOut(BaseModel):
    type: str
    status: str


class ZoneStatusOut(BaseModel):
    zone_id: int
    name: str
    state: str
    risk_score: float
    offline: bool
    sensors: list[SensorStatusOut]
    last_reading_at: dt.datetime | None


class ZoneTransitionOut(BaseModel):
    id: int
    from_state: str
    to_state: str
    risk_score: float
    cause: str
    reason: str | None
    ts: dt.datetime

    model_config = ConfigDict(from_attributes=True)


class AcknowledgmentOut(BaseModel):
    user_id: int
    ts: dt.datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentOut(BaseModel):
    id: int
    zone_id: int
    zone_name: str
    opened_at: dt.datetime
    peak_risk: float
    status: str
    resolved_at: dt.datetime | None
    ack: AcknowledgmentOut | None = None


class IncidentTimelineOut(BaseModel):
    incident: IncidentOut
    transitions: list[ZoneTransitionOut]


class AckResponse(BaseModel):
    incident_id: int
    acked_by: int
    ts: dt.datetime


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str
    username: str


class OverrideRequest(BaseModel):
    zone_id: int
    target_state: str = Field(pattern="^(SAFE|WARNING|CRITICAL)$")
    reason: str = Field(min_length=1, max_length=500)


class OverrideResponse(BaseModel):
    zone_id: int
    state: str
    transitioned: bool


class CommandOut(BaseModel):
    zone_id: int
    state: str
    buzzer: bool
    relay: bool
    led: str  # green | yellow | red
    ts: dt.datetime
    cause: str


class PriorityEntryOut(BaseModel):
    zone_id: int
    zone_name: str
    risk_score: float
    occupied: bool
    unacked_seconds: float
    priority: float
    justification: str


class TrendOut(BaseModel):
    zone_id: int
    scores: list[float]
    slope: float
    rising: bool
