"""ORM mappings for the tables created in alembic/versions/. The migrations
are the source of truth for the schema; these classes just let Python code
query and insert without hand-written SQL."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str]
    name: Mapped[str]
    timezone: Mapped[str]


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str]
    site_id: Mapped[str]
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[str]
    email: Mapped[str]
    password_hash: Mapped[str]
    role: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[str]
    site_id: Mapped[str]
    device_id: Mapped[str]
    seq: Mapped[int] = mapped_column(BigInteger)
    event_id: Mapped[uuid.UUID]
    type: Mapped[str]
    zone: Mapped[str]
    severity: Mapped[str]
    occurred_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default="now()")
    software_version: Mapped[str]
    config_version: Mapped[int]
    details: Mapped[dict[str, Any]]


class DeviceState(Base):
    __tablename__ = "device_state"
    device_id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str]
    site_id: Mapped[str]
    software_version: Mapped[str]
    config_version: Mapped[int]
    health: Mapped[dict[str, Any]]
    outbox_backlog: Mapped[int]
    reported_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class DesiredConfig(Base):
    __tablename__ = "desired_configs"
    site_id: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str]
    config: Mapped[dict[str, Any]]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class ConfigAck(Base):
    __tablename__ = "config_acks"
    device_id: Mapped[str] = mapped_column(primary_key=True)
    config_version: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str]
    site_id: Mapped[str]
    status: Mapped[str]
    error: Mapped[str | None]
    acked_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default="now()")
