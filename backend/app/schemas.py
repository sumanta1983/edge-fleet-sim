"""Pydantic models for what devices send over MQTT. Anything that doesn't
validate is rejected before it gets anywhere near the database."""
import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

EventType = Literal[
    "near_miss", "ppe_violation", "speed_violation", "gate_triggered", "label_read", "loading_session_completed"
]
Severity = Literal["info", "medium", "high", "critical"]


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
