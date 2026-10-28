"""Fleet overview: which devices exist, and are they alive?"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.auth import TenantConn
from app.models import Device, DeviceState, Site

# Heartbeats arrive every 10 s. Allowing three missed ones avoids flapping
# to "offline" on a single dropped QoS 0 heartbeat or a short network blip.
HEARTBEAT_TIMEOUT = timedelta(seconds=30)

router = APIRouter(tags=["fleet"])


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    timezone: str
    device_count: int


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    site_id: str
    site_name: str
    online: bool
    # Everything below is the device's REPORTED state; None until its first
    # heartbeat. Desired config (what the cloud wants) arrives in phase 4.
    last_seen_at: datetime | None
    software_version: str | None
    config_version: int | None
    health: dict[str, Any] | None
    outbox_backlog: int | None


def _device_query():
    # Outer join: a device that has never sent a heartbeat still shows up
    # (as offline) instead of silently disappearing from the fleet.
    return (
        select(
            Device.id,
            Device.name,
            Device.site_id,
            Site.name.label("site_name"),
            DeviceState.last_seen_at,
            DeviceState.software_version,
            DeviceState.config_version,
            DeviceState.health,
            DeviceState.outbox_backlog,
        )
        .join(Site, Site.id == Device.site_id)
        .outerjoin(DeviceState, DeviceState.device_id == Device.id)
        .order_by(Device.site_id, Device.id)
    )


def _to_device(row) -> DeviceOut:
    # Same clock as the ingestor that wrote last_seen_at (the cloud's clock).
    cutoff = datetime.now(timezone.utc) - HEARTBEAT_TIMEOUT
    online = row.last_seen_at is not None and row.last_seen_at > cutoff
    return DeviceOut.model_validate({**row._mapping, "online": online})


@router.get("/sites")
async def list_sites(conn: TenantConn) -> list[SiteOut]:
    rows = await conn.execute(
        select(Site.id, Site.name, Site.timezone, func.count(Device.id).label("device_count"))
        .outerjoin(Device, Device.site_id == Site.id)
        .group_by(Site.id)
        .order_by(Site.id)
    )
    return [SiteOut.model_validate(r) for r in rows]


@router.get("/fleet/devices")
async def list_devices(conn: TenantConn, site_id: str | None = None) -> list[DeviceOut]:
    query = _device_query()
    if site_id:
        query = query.where(Device.site_id == site_id)
    return [_to_device(r) for r in await conn.execute(query)]


@router.get("/fleet/devices/{device_id}")
async def get_device(conn: TenantConn, device_id: str) -> DeviceOut:
    row = (await conn.execute(_device_query().where(Device.id == device_id))).one_or_none()
    # Another tenant's device is invisible under RLS, so it is a plain 404:
    # we don't even confirm that it exists.
    if row is None:
        raise HTTPException(404, "device not found")
    return _to_device(row)
