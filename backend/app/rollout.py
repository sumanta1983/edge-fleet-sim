"""The staged-rollout state machine, shared by the API and the ingestor.

    wave 0 (canary) ──succeeded──▶ wave 1 (everyone else) ──all answered──▶ completed
         │
         └─rolled_back / failed / no answer in time──▶ halted (wave 1 skipped)

Nothing here runs on a timer. advance() is called whenever something might
have changed (an ack arrived, a heartbeat arrived) and moves the rollout as
far as the current facts allow. Calling it twice is harmless.

Like config, a release command is published as a RETAINED message, so a
device that is offline when its wave starts gets the command on reconnect.
Every function returns the MQTT messages to publish instead of publishing
them itself: the caller publishes only after its transaction has committed.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models import Release, ReleaseTarget

# Install + health check take a few seconds; a canary that is quiet for a
# minute is treated as a failure. Silence must never count as success.
CANARY_TIMEOUT = timedelta(seconds=60)
# Re-send a command that got no answer, in case the publish was lost
# (e.g. we crashed between commit and publish). Devices ignore duplicates.
RESEND_AFTER = timedelta(seconds=30)
TERMINAL = {"succeeded", "rolled_back", "failed", "timed_out", "skipped"}


class Publish(NamedTuple):
    topic: str
    payload: str  # "" deletes the retained message


def artifact_checksum(version: str) -> str:
    # Simulation: the "artifact" is just a string. A real build pipeline
    # would hash the real image and also sign it, so a device can check
    # WHO built it, not only that it arrived intact.
    return hashlib.sha256(f"edgefleet-edge-firmware {version}".encode()).hexdigest()


def release_topic(tenant_id: str, site_id: str, device_id: str) -> str:
    return f"tenants/{tenant_id}/sites/{site_id}/devices/{device_id}/release"


def release_command(release, target) -> Publish:
    message = {
        "release_id": release.id,
        "version": release.version,
        "checksum": release.checksum,
        "artifact_url": f"https://artifacts.invalid/edge/{release.version}.img",
    }
    return Publish(release_topic(target.tenant_id, target.site_id, target.device_id), json.dumps(message, sort_keys=True))


async def halt(conn: AsyncConnection, release, reason: str) -> list[Publish]:
    """Stop a rollout: devices that haven't answered are skipped, and every
    target's retained command is deleted so nobody installs it later."""
    now = datetime.now(timezone.utc)
    await conn.execute(
        update(ReleaseTarget)
        .where(ReleaseTarget.release_id == release.id, ReleaseTarget.status.in_(["pending", "sent"]))
        # A device that already got the command may still answer; its ack
        # then overwrites "skipped" with what really happened.
        .values(status="skipped", finished_at=now)
    )
    await conn.execute(
        update(Release).where(Release.id == release.id).values(status="halted", halt_reason=reason, finished_at=now)
    )
    targets = await conn.execute(select(ReleaseTarget).where(ReleaseTarget.release_id == release.id))
    return [Publish(release_topic(t.tenant_id, t.site_id, t.device_id), "") for t in targets]


async def advance(conn: AsyncConnection) -> list[Publish]:
    """Move the tenant's active rollout (if any) as far as the facts allow.
    The connection must already be scoped to one tenant (SET LOCAL)."""
    # FOR UPDATE: the API (halt) and the ingestor (acks) may both try to
    # move the same rollout at once; the row lock makes them take turns.
    release = (
        await conn.execute(select(Release).where(Release.status == "rolling_out").with_for_update())
    ).one_or_none()
    if release is None:
        return []
    targets = (await conn.execute(select(ReleaseTarget).where(ReleaseTarget.release_id == release.id))).all()
    canary = next(t for t in targets if t.wave == 0)
    now = datetime.now(timezone.utc)

    if canary.status in ("rolled_back", "failed"):
        return await halt(conn, release, f"canary {canary.device_id} {canary.status}: {canary.error}")
    if canary.status == "sent" and canary.sent_at < now - CANARY_TIMEOUT:
        await conn.execute(
            update(ReleaseTarget)
            .where(ReleaseTarget.release_id == release.id, ReleaseTarget.device_id == canary.device_id)
            .values(status="timed_out", finished_at=now)
        )
        return await halt(conn, release, f"canary {canary.device_id} did not answer within {CANARY_TIMEOUT.seconds}s")
    if canary.status != "succeeded":
        return []  # still waiting for the canary

    pending = [t for t in targets if t.status == "pending"]
    if pending:
        await conn.execute(
            update(ReleaseTarget)
            .where(ReleaseTarget.release_id == release.id, ReleaseTarget.status == "pending")
            .values(status="sent", sent_at=now)
        )
        return [release_command(release, t) for t in pending]

    if all(t.status in TERMINAL for t in targets):
        # Wave-1 failures don't halt anything (there's nothing left to stop);
        # they show up per device in the rollout progress.
        await conn.execute(update(Release).where(Release.id == release.id).values(status="completed", finished_at=now))
    return []


async def resend_unanswered(conn: AsyncConnection, device_id: str) -> list[Publish]:
    """Called on each heartbeat: the device is online, so if its command has
    gone unanswered for a while, send it again."""
    # Explicit columns: both tables have tenant_id and site_id, and the
    # release's site_id is NULL for a tenant-wide rollout.
    rows = await conn.execute(
        select(
            Release.id,
            Release.version,
            Release.checksum,
            ReleaseTarget.tenant_id,
            ReleaseTarget.site_id,
            ReleaseTarget.device_id,
        )
        .join(ReleaseTarget, ReleaseTarget.release_id == Release.id)
        .where(
            Release.status == "rolling_out",
            ReleaseTarget.device_id == device_id,
            ReleaseTarget.status == "sent",
            ReleaseTarget.sent_at < datetime.now(timezone.utc) - RESEND_AFTER,
        )
    )
    # Not updating sent_at on purpose: it also drives the canary timeout,
    # and a re-send must not give a hanging canary more time.
    return [release_command(r, r) for r in rows]
