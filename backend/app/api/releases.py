"""Software releases with a staged rollout: one canary first, then the rest.

The rollout itself is driven by app/rollout.py (mostly from the ingestor,
as acks come in). The API only starts a rollout, shows its progress, and
lets an operator halt it.
"""
import uuid
from collections import Counter
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app import rollout
from app.api.auth import OperatorDep, TenantConn
from app.api.fleet import HEARTBEAT_TIMEOUT
from app.models import Device, DeviceState, Release, ReleaseTarget, Site

router = APIRouter(prefix="/releases", tags=["releases"])


class ReleaseCreate(BaseModel):
    # Anything containing "bad" fails the simulated health check, and
    # "corrupt" fails the checksum (see edge-sim), for demos.
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(-[a-z0-9.-]+)?$", examples=["2.0.0", "2.0.0-bad"])
    site_id: str | None = Field(None, description="Limit to one site; omit for every device of the tenant.")


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    device_id: str
    site_id: str
    wave: int
    status: str
    from_version: str | None
    error: str | None
    sent_at: datetime | None
    finished_at: datetime | None


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: str
    checksum: str
    site_id: str | None
    status: str
    halt_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    progress: dict[str, int]  # target status -> number of devices


class ReleaseDetail(ReleaseOut):
    targets: list[TargetOut]


def _publish(client: mqtt.Client, publishes: list[rollout.Publish]) -> None:
    # Fire and forget: anything lost here is re-sent from heartbeats
    # (rollout.resend_unanswered), so the API doesn't wait for the broker.
    for p in publishes:
        client.publish(p.topic, p.payload, qos=1, retain=True)


async def _detail(conn: AsyncConnection, release_id: int) -> ReleaseDetail:
    release = (await conn.execute(select(Release).where(Release.id == release_id))).one_or_none()
    if release is None:
        raise HTTPException(404, "release not found")
    targets = [
        TargetOut.model_validate(t)
        for t in await conn.execute(
            select(ReleaseTarget)
            .where(ReleaseTarget.release_id == release_id)
            .order_by(ReleaseTarget.wave, ReleaseTarget.device_id)
        )
    ]
    progress = dict(Counter(t.status for t in targets))
    return ReleaseDetail.model_validate({**release._mapping, "progress": progress, "targets": targets})


@router.get("")
async def list_releases(conn: TenantConn) -> list[ReleaseOut]:
    progress: dict[int, dict[str, int]] = {}
    for r in await conn.execute(
        select(ReleaseTarget.release_id, ReleaseTarget.status, func.count().label("n")).group_by(
            ReleaseTarget.release_id, ReleaseTarget.status
        )
    ):
        progress.setdefault(r.release_id, {})[r.status] = r.n
    releases = await conn.execute(select(Release).order_by(Release.id.desc()))
    return [ReleaseOut.model_validate({**r._mapping, "progress": progress.get(r.id, {})}) for r in releases]


@router.get("/{release_id}")
async def get_release(conn: TenantConn, release_id: int) -> ReleaseDetail:
    return await _detail(conn, release_id)


@router.post("", status_code=201)
async def create_release(request: Request, conn: TenantConn, user: OperatorDep, body: ReleaseCreate) -> ReleaseDetail:
    active = await conn.scalar(select(Release.id).where(Release.status == "rolling_out"))
    if active:
        raise HTTPException(409, f"release #{active} is still rolling out; wait for it or halt it")

    query = (
        select(Device.id, Device.site_id, DeviceState.software_version, DeviceState.last_seen_at)
        .outerjoin(DeviceState, DeviceState.device_id == Device.id)
        .order_by(Device.id)
    )
    if body.site_id:
        if await conn.scalar(select(Site.id).where(Site.id == body.site_id)) is None:
            raise HTTPException(404, "site not found")
        query = query.where(Device.site_id == body.site_id)
    devices = (await conn.execute(query)).all()

    # The canary must be online: its answer is what decides whether the rest
    # of the fleet gets the update, so we need it to answer quickly.
    cutoff = datetime.now(timezone.utc) - HEARTBEAT_TIMEOUT
    online = [d for d in devices if d.last_seen_at and d.last_seen_at > cutoff]
    if not online:
        raise HTTPException(409, "no online device to act as canary")
    canary = online[0]

    try:
        release = (
            await conn.execute(
                insert(Release)
                .values(
                    tenant_id=user.tenant_id,
                    site_id=body.site_id,
                    version=body.version,
                    checksum=rollout.artifact_checksum(body.version),
                    created_by=user.id,
                )
                .returning(Release)
            )
        ).one()
    except IntegrityError:  # lost a race with another create (releases_one_active)
        raise HTTPException(409, "another release is already rolling out")

    now = datetime.now(timezone.utc)
    targets = (
        await conn.execute(
            insert(ReleaseTarget)
            .values(
                [
                    dict(
                        tenant_id=user.tenant_id,
                        release_id=release.id,
                        site_id=d.site_id,
                        device_id=d.id,
                        wave=0 if d is canary else 1,
                        status="sent" if d is canary else "pending",
                        sent_at=now if d is canary else None,
                        from_version=d.software_version,
                    )
                    for d in devices
                ]
            )
            .returning(ReleaseTarget)
        )
    ).all()
    detail = await _detail(conn, release.id)
    await conn.commit()  # commit, then publish (same reasoning as config)

    canary_target = next(t for t in targets if t.wave == 0)
    _publish(request.app.state.mqtt, [rollout.release_command(release, canary_target)])
    return detail


@router.post("/{release_id}/halt")
async def halt_release(request: Request, conn: TenantConn, user: OperatorDep, release_id: int) -> ReleaseDetail:
    release = (
        await conn.execute(select(Release).where(Release.id == release_id).with_for_update())
    ).one_or_none()
    if release is None:
        raise HTTPException(404, "release not found")
    if release.status != "rolling_out":
        raise HTTPException(409, f"release is already {release.status}")
    publishes = await rollout.halt(conn, release, "halted by operator")
    detail = await _detail(conn, release_id)
    await conn.commit()
    _publish(request.app.state.mqtt, publishes)
    return detail
