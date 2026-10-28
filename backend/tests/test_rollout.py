"""Staged rollout: the canary's outcome decides what happens to the rest.

Acks go through the ingestor's real handler (store_release_ack), the same
path an MQTT ack takes.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app import rollout
from app.db import tenant_transaction
from app.ingestor import DeviceTopic, rollout_on_heartbeat, store_release_ack
from app.models import Release, ReleaseTarget
from app.schemas import ReleaseAckIn

TENANT = "tenant-a"
CANARY = ("site-a1", "edge-a1-01")
WAVE_1 = [("site-a1", "edge-a1-02"), ("site-a2", "edge-a2-01")]


@pytest.fixture
async def release_id(api_engine, owner_engine):
    """A tenant-wide rollout whose command has just been sent to the canary."""
    async with tenant_transaction(api_engine, TENANT) as conn:
        release = (
            await conn.execute(
                insert(Release)
                .values(tenant_id=TENANT, version="9.9.9", checksum=rollout.artifact_checksum("9.9.9"))
                .returning(Release.id)
            )
        ).one()
        targets = [(CANARY, 0, "sent")] + [(t, 1, "pending") for t in WAVE_1]
        await conn.execute(
            insert(ReleaseTarget).values(
                [
                    dict(tenant_id=TENANT, release_id=release.id, site_id=site, device_id=device, wave=wave,
                         status=status, sent_at=datetime.now(timezone.utc) if status == "sent" else None)
                    for (site, device), wave, status in targets
                ]
            )
        )
    yield release.id
    # Only one rollout may be active per tenant; don't block the next test.
    async with tenant_transaction(owner_engine, TENANT) as conn:
        await conn.execute(update(Release).where(Release.status == "rolling_out").values(status="halted"))


async def ack(engine, release_id: int, device: tuple[str, str], status: str, error: str | None = None):
    site, device_id = device
    return await store_release_ack(
        engine,
        DeviceTopic(TENANT, site, device_id, "ack"),
        ReleaseAckIn(kind="release", release_id=release_id, version="9.9.9", status=status, error=error,
                     acked_at=datetime.now(timezone.utc)),
    )


async def state(engine, release_id: int) -> tuple[str, dict[str, str]]:
    async with tenant_transaction(engine, TENANT) as conn:
        status = await conn.scalar(select(Release.status).where(Release.id == release_id))
        targets = await conn.execute(select(ReleaseTarget).where(ReleaseTarget.release_id == release_id))
        return status, {t.device_id: t.status for t in targets}


@pytest.mark.parametrize("canary_result", ["rolled_back", "failed"])
async def test_rollout_halts_when_the_canary_fails(ingestor_engine, release_id, canary_result):
    publishes = await ack(ingestor_engine, release_id, CANARY, canary_result, "health check failed")

    status, targets = await state(ingestor_engine, release_id)
    assert status == "halted"
    assert targets == {"edge-a1-01": canary_result, "edge-a1-02": "skipped", "edge-a2-01": "skipped"}
    # Nothing is sent to wave 1; every retained command is deleted instead.
    assert publishes and all(p.payload == "" for p in publishes)


async def test_rollout_halts_when_the_canary_goes_silent(ingestor_engine, api_engine, release_id):
    async with tenant_transaction(api_engine, TENANT) as conn:
        await conn.execute(
            update(ReleaseTarget)
            .where(ReleaseTarget.release_id == release_id, ReleaseTarget.wave == 0)
            .values(sent_at=datetime.now(timezone.utc) - rollout.CANARY_TIMEOUT - timedelta(seconds=1))
        )
    # A heartbeat from ANOTHER device of the tenant is enough to notice.
    site, device_id = WAVE_1[0]
    await rollout_on_heartbeat(ingestor_engine, DeviceTopic(TENANT, site, device_id, "state"))

    status, targets = await state(ingestor_engine, release_id)
    assert status == "halted"
    assert targets["edge-a1-01"] == "timed_out"


async def test_canary_success_releases_wave_1_then_completes(ingestor_engine, release_id):
    publishes = await ack(ingestor_engine, release_id, CANARY, "succeeded")
    assert sorted(p.topic.split("/")[5] for p in publishes) == ["edge-a1-02", "edge-a2-01"]
    assert all(f'"release_id": {release_id}' in p.payload for p in publishes)
    assert (await state(ingestor_engine, release_id))[0] == "rolling_out"

    for device in WAVE_1:
        await ack(ingestor_engine, release_id, device, "succeeded")
    assert (await state(ingestor_engine, release_id))[0] == "completed"


async def test_wave_1_is_not_sent_before_the_canary_answers(ingestor_engine, release_id):
    site, device_id = WAVE_1[0]
    publishes = await rollout_on_heartbeat(ingestor_engine, DeviceTopic(TENANT, site, device_id, "state"))
    assert publishes == []
    assert (await state(ingestor_engine, release_id))[1]["edge-a1-02"] == "pending"
