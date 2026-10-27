"""Cloud-side ingestor: MQTT -> Postgres.

Delivery guarantee, end to end: we turn off paho's automatic PUBACK and
acknowledge a message only AFTER its database transaction has committed.
If the ingestor crashes in between, the broker still holds the message
(persistent session) and redelivers it; the (device_id, seq) unique
constraint turns that redelivery into a harmless no-op.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import NamedTuple

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import make_engine, tenant_transaction
from app.models import DeviceState, Event
from app.schemas import EventIn, StateIn

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
SUBSCRIPTIONS = [
    ("tenants/+/sites/+/devices/+/events", 1),
    ("tenants/+/sites/+/devices/+/state", 1),
    ("tenants/+/sites/+/devices/+/ack", 1),
]

log = logging.getLogger("ingestor")


class DeviceTopic(NamedTuple):
    tenant_id: str
    site_id: str
    device_id: str
    kind: str


def parse_topic(topic: str) -> DeviceTopic | None:
    """Identity comes from the topic, never from the payload: the broker ACL
    guarantees a device can only publish on its own topic path, whereas the
    payload is whatever the device chose to write."""
    parts = topic.split("/")
    if len(parts) != 7 or parts[0] != "tenants" or parts[2] != "sites" or parts[4] != "devices":
        return None
    return DeviceTopic(parts[1], parts[3], parts[5], parts[6])


async def store_event(engine: AsyncEngine, t: DeviceTopic, payload: bytes) -> None:
    event = EventIn.model_validate_json(payload)
    async with tenant_transaction(engine, t.tenant_id) as conn:
        result = await conn.execute(
            insert(Event)
            .values(tenant_id=t.tenant_id, site_id=t.site_id, device_id=t.device_id, **event.model_dump())
            .on_conflict_do_nothing(index_elements=["device_id", "seq"])
        )
    if result.rowcount:
        log.info("stored event %s seq=%d %s", t.device_id, event.seq, event.type)
    else:
        log.info("duplicate event %s seq=%d ignored", t.device_id, event.seq)


async def store_state(engine: AsyncEngine, t: DeviceTopic, payload: bytes) -> None:
    state = StateIn.model_validate_json(payload)
    values = dict(
        device_id=t.device_id,
        tenant_id=t.tenant_id,
        site_id=t.site_id,
        software_version=state.software_version,
        config_version=state.config_version,
        health=state.health.model_dump(),
        outbox_backlog=state.outbox_backlog,
        reported_at=state.sent_at,
        # Online/offline is judged on the cloud's clock; device clocks drift.
        last_seen_at=datetime.now(timezone.utc),
    )
    stmt = insert(DeviceState).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["device_id"],
        set_={k: stmt.excluded[k] for k in values if k != "device_id"},
        # A delayed old heartbeat must not overwrite a newer one.
        where=DeviceState.reported_at < stmt.excluded.reported_at,
    )
    async with tenant_transaction(engine, t.tenant_id) as conn:
        await conn.execute(stmt)


class Ingestor:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.loop = asyncio.get_running_loop()
        # Bounded: if the database is slow, paho's thread blocks on put(),
        # stops reading from the socket, and the broker holds messages back.
        self.queue: asyncio.Queue[tuple[int, mqtt.MQTTMessage]] = asyncio.Queue(maxsize=500)
        # Bumped on every reconnect. A packet id (mid) only means something on
        # the connection it arrived on, so we never ack a message from an
        # older connection; the broker will redeliver it instead.
        self.connection = 0

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor", clean_session=False)
        self.client.username_pw_set("ingestor", os.environ["MQTT_PASSWORD"])
        self.client.manual_ack_set(True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # --- paho network thread --------------------------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        log.info("connected to broker (%s, session_present=%s)", reason_code, flags.session_present)
        if not reason_code.is_failure:
            self.connection += 1
            client.subscribe(SUBSCRIPTIONS)

    def on_message(self, client, userdata, msg):
        asyncio.run_coroutine_threadsafe(self.queue.put((self.connection, msg)), self.loop).result()

    # --- asyncio side ---------------------------------------------------

    async def run(self) -> None:
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.client.loop_start()
        while True:
            connection, msg = await self.queue.get()
            await self.handle(msg)
            if connection == self.connection:
                self.client.ack(msg.mid, msg.qos)

    async def handle(self, msg: mqtt.MQTTMessage) -> None:
        """Returns once the message is stored or deliberately dropped.

        Bad data (fails validation, unknown device, policy violation) will
        never succeed, so we log it and drop it instead of blocking the queue.
        Anything else (database down, network blip) is retried forever:
        we'd rather stall than lose an event. Messages are handled one at a
        time, so order is preserved.
        """
        t = parse_topic(msg.topic)
        if t is None:
            log.warning("ignoring unexpected topic %s", msg.topic)
            return
        delay = 1
        while True:
            try:
                if t.kind == "events":
                    await store_event(self.engine, t, msg.payload)
                elif t.kind == "state":
                    await store_state(self.engine, t, msg.payload)
                else:
                    log.info("[%s] %s %s (handled in a later phase)", t.kind, t.device_id, msg.payload[:200])
                return
            except (ValidationError, IntegrityError, ProgrammingError) as e:
                log.warning("dropping bad message on %s: %s", msg.topic, str(e).splitlines()[0])
                return
            except Exception as e:
                log.error("cannot store message (%s: %s); retrying in %ds", type(e).__name__, str(e).splitlines()[0], delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    await Ingestor(make_engine(os.environ["DATABASE_URL"])).run()


if __name__ == "__main__":
    asyncio.run(main())
