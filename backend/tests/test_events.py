"""Event ingestion is idempotent, and stored events can never change."""
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import tenant_transaction
from app.ingestor import DeviceTopic, store_event
from app.models import Event
from tests.conftest import event_payload, next_seq

TOPIC = DeviceTopic("tenant-a", "site-a1", "edge-a1-01", "events")


async def _rows(engine, seq: int) -> list:
    async with tenant_transaction(engine, "tenant-a") as conn:
        return (await conn.execute(select(Event).where(Event.device_id == TOPIC.device_id, Event.seq == seq))).all()


async def test_same_seq_twice_is_stored_once(ingestor_engine):
    seq = next_seq()
    payload = event_payload(seq)
    await store_event(ingestor_engine, TOPIC, payload)
    await store_event(ingestor_engine, TOPIC, payload)  # MQTT QoS 1 redelivery
    assert len(await _rows(ingestor_engine, seq)) == 1


async def test_first_delivery_wins(ingestor_engine):
    seq = next_seq()
    await store_event(ingestor_engine, TOPIC, event_payload(seq, type="near_miss"))
    await store_event(ingestor_engine, TOPIC, event_payload(seq, type="label_read"))
    [row] = await _rows(ingestor_engine, seq)
    assert row.type == "near_miss"


async def test_identity_comes_from_the_topic_not_the_payload(ingestor_engine):
    seq = next_seq()
    await store_event(ingestor_engine, TOPIC, event_payload(seq, tenant_id="tenant-b", device_id="edge-b1-01"))
    [row] = await _rows(ingestor_engine, seq)
    assert (row.tenant_id, row.device_id) == ("tenant-a", "edge-a1-01")


async def test_invalid_event_is_rejected(ingestor_engine):
    with pytest.raises(ValidationError):
        await store_event(ingestor_engine, TOPIC, event_payload(next_seq(), type="not_a_real_type"))


@pytest.fixture
async def event_seq(ingestor_engine) -> int:
    seq = next_seq()
    await store_event(ingestor_engine, TOPIC, event_payload(seq))
    return seq


@pytest.mark.parametrize("role", ["api", "ingestor"])
@pytest.mark.parametrize("statement", ["UPDATE events SET zone = 'x' WHERE seq = :seq", "DELETE FROM events WHERE seq = :seq"])
async def test_app_roles_have_no_update_or_delete_privilege(api_engine, ingestor_engine, event_seq, role, statement):
    engine = {"api": api_engine, "ingestor": ingestor_engine}[role]
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_transaction(engine, "tenant-a") as conn:
            await conn.execute(text(statement), {"seq": event_seq})


@pytest.mark.parametrize(
    "statement",
    ["UPDATE events SET zone = 'x' WHERE seq = :seq", "DELETE FROM events WHERE seq = :seq", "TRUNCATE events"],
)
async def test_trigger_blocks_even_the_table_owner(owner_engine, event_seq, statement):
    with pytest.raises(DBAPIError, match="append-only"):
        async with tenant_transaction(owner_engine, "tenant-a") as conn:
            await conn.execute(text(statement), {"seq": event_seq})
