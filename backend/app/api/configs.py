"""Desired config per site, and how far each device has got with it.

Desired vs reported state: the cloud never "sets" a device's config
directly. It records what it WANTS (desired, in desired_configs) and the
device tells it what it HAS (reported, in heartbeats and acks). The two can
differ for a long time (device offline, config rejected), and showing that
difference honestly is the point of the status endpoint.
"""
import asyncio
import uuid
from datetime import datetime
from typing import Literal

import paho.mqtt.client as mqtt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.auth import OperatorDep, TenantConn
from app.config_push import config_message, config_topic, latest_desired
from app.models import ConfigAck, DesiredConfig, Device, DeviceState, Site
from app.schemas import SiteConfig

router = APIRouter(prefix="/sites/{site_id}/config", tags=["config"])


class DesiredConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    site_id: str
    version: int
    config: SiteConfig
    created_by: uuid.UUID | None
    created_at: datetime


class ConfigUpdate(BaseModel):
    base_version: int = Field(ge=0, description="The version you started editing from (0 if the site has none).")
    config: SiteConfig


class ConfigPushResult(BaseModel):
    desired: DesiredConfigOut
    devices: list[str]
    # False means the broker didn't confirm in time. The config is still
    # saved, and the ingestor's reconciliation delivers it on the next
    # heartbeat, so this is informational, not an error.
    broker_accepted: bool


class DeviceConfigStatus(BaseModel):
    device_id: str
    desired_version: int | None
    reported_version: int | None
    ack_status: Literal["applied", "failed"] | None  # the device's answer to desired_version
    ack_error: str | None
    acked_at: datetime | None
    state: Literal["in_sync", "pending", "failed", "no_config"]


async def _require_site(conn: AsyncConnection, site_id: str) -> None:
    # Another tenant's site is invisible under RLS, so it's a plain 404.
    if await conn.scalar(select(Site.id).where(Site.id == site_id)) is None:
        raise HTTPException(404, "site not found")


async def _publish(client: mqtt.Client, topics: list[str], message: str) -> bool:
    infos = [client.publish(topic, message, qos=1, retain=True) for topic in topics]

    def wait() -> bool:
        try:
            for info in infos:
                info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):  # not connected / queue full
            return False
        return all(info.is_published() for info in infos)

    return await asyncio.to_thread(wait)


@router.get("")
async def get_config(conn: TenantConn, site_id: str) -> DesiredConfigOut:
    await _require_site(conn, site_id)
    row = (
        await conn.execute(
            select(DesiredConfig).where(DesiredConfig.site_id == site_id).order_by(DesiredConfig.version.desc()).limit(1)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "site has no desired config yet")
    return DesiredConfigOut.model_validate(row)


@router.get("/history")
async def config_history(conn: TenantConn, site_id: str) -> list[DesiredConfigOut]:
    await _require_site(conn, site_id)
    rows = await conn.execute(
        select(DesiredConfig).where(DesiredConfig.site_id == site_id).order_by(DesiredConfig.version.desc())
    )
    return [DesiredConfigOut.model_validate(r) for r in rows]


@router.put("")
async def update_config(
    request: Request, conn: TenantConn, user: OperatorDep, site_id: str, body: ConfigUpdate
) -> ConfigPushResult:
    await _require_site(conn, site_id)
    latest = await conn.scalar(select(func.max(DesiredConfig.version)).where(DesiredConfig.site_id == site_id)) or 0

    # Optimistic concurrency: the client says which version it edited. If
    # someone saved in the meantime, refuse instead of silently overwriting
    # their change (the "lost update" problem). The client reloads and retries.
    if body.base_version != latest:
        raise HTTPException(409, f"config changed since you loaded it: latest is v{latest}, you edited v{body.base_version}")
    try:
        row = (
            await conn.execute(
                insert(DesiredConfig)
                .values(
                    tenant_id=user.tenant_id,
                    site_id=site_id,
                    version=latest + 1,
                    config=body.config.model_dump(),
                    created_by=user.id,
                )
                .returning(DesiredConfig)
            )
        ).one()
    except IntegrityError:
        # Lost the race against a concurrent save of the same version.
        raise HTTPException(409, "config changed since you loaded it, reload and try again")
    devices = list(await conn.scalars(select(Device.id).where(Device.site_id == site_id).order_by(Device.id)))

    # Commit BEFORE publishing. The other order could push a config to
    # devices that then disappears from the database if the commit fails.
    # This order has the opposite gap (committed, not yet published), which
    # the ingestor's reconciliation closes.
    await conn.commit()

    topics = [config_topic(user.tenant_id, site_id, d) for d in devices]
    accepted = await _publish(request.app.state.mqtt, topics, config_message(row.version, row.config))
    return ConfigPushResult(desired=DesiredConfigOut.model_validate(row), devices=devices, broker_accepted=accepted)


@router.get("/status")
async def config_status(conn: TenantConn, site_id: str) -> list[DeviceConfigStatus]:
    await _require_site(conn, site_id)
    desired = (await conn.execute(latest_desired(site_id))).one_or_none()
    desired_version = desired.version if desired else None
    rows = await conn.execute(
        select(
            Device.id,
            DeviceState.config_version.label("reported_version"),
            ConfigAck.status,
            ConfigAck.error,
            ConfigAck.acked_at,
        )
        .outerjoin(DeviceState, DeviceState.device_id == Device.id)
        .outerjoin(ConfigAck, and_(ConfigAck.device_id == Device.id, ConfigAck.config_version == desired_version))
        .where(Device.site_id == site_id)
        .order_by(Device.id)
    )

    result = []
    for r in rows:
        if desired_version is None:
            state = "no_config"
        elif r.reported_version == desired_version:
            state = "in_sync"
        elif r.status == "failed":
            state = "failed"
        else:
            state = "pending"
        result.append(
            DeviceConfigStatus(
                device_id=r.id,
                desired_version=desired_version,
                reported_version=r.reported_version,
                ack_status=r.status,
                ack_error=r.error,
                acked_at=r.acked_at,
                state=state,
            )
        )
    return result
