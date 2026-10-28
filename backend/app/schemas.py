"""Pydantic models for what travels over MQTT. Anything a device sends that
doesn't validate is rejected before it gets anywhere near the database."""
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

EventType = Literal[
    "near_miss", "ppe_violation", "speed_violation", "gate_triggered", "label_read", "loading_session_completed"
]
Severity = Literal["info", "medium", "high", "critical"]
ZoneKind = Literal["dock", "aisle", "forklift_lane", "gate", "walkway"]


class EventIn(BaseModel):
    seq: int = Field(ge=1)
    event_id: uuid.UUID
    type: EventType
    zone: str
    severity: Severity
    occurred_at: AwareDatetime
    software_version: str
    config_version: int = Field(ge=0)
    details: dict[str, Any] = {}


class Health(BaseModel):
    cpu_pct: float
    gpu_pct: float
    cameras_online: int
    cameras_total: int


class StateIn(BaseModel):
    software_version: str
    config_version: int = Field(ge=0)
    health: Health
    outbox_backlog: int = Field(ge=0)
    sent_at: AwareDatetime


class Zone(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    kind: ZoneKind


class SiteConfig(BaseModel):
    """Desired configuration for every edge server at one site."""

    model_config = ConfigDict(extra="forbid")  # a typo'd key is an error, not silently ignored
    zones: list[Zone] = Field(min_length=1, max_length=20)
    speed_limit_kmh: float = Field(gt=0, le=40)
    # A near miss closer than this is "critical" and raises an alert.
    near_miss_critical_m: float = Field(gt=0, le=5)

    @field_validator("zones")
    @classmethod
    def unique_zone_ids(cls, zones: list[Zone]) -> list[Zone]:
        ids = [z.id for z in zones]
        if len(ids) != len(set(ids)):
            raise ValueError("zone ids must be unique")
        return zones


class ConfigAckIn(BaseModel):
    kind: Literal["config"]
    version: int = Field(ge=1)
    status: Literal["applied", "failed"]
    error: str | None = None
    acked_at: AwareDatetime


class ReleaseAckIn(BaseModel):
    kind: Literal["release"]
    release_id: int = Field(ge=1)
    version: str
    status: Literal["succeeded", "rolled_back", "failed"]
    error: str | None = None
    acked_at: AwareDatetime


# Both kinds of ack share the .../ack topic; "kind" says which model applies.
AckIn = TypeAdapter(Annotated[Union[ConfigAckIn, ReleaseAckIn], Field(discriminator="kind")])
