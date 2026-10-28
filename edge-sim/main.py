"""Simulated on-site edge server.

Connection direction: the DEVICE dials out to the cloud broker, never the
other way round. Customer sites sit behind firewalls and NAT that block
inbound connections, but almost always allow outbound TCP. Once the device
holds that connection open, the cloud can push config and release commands
back down the same connection without any port being opened on site.
"""
import json
import logging
import os
import queue
import random
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from events import DEFAULT_CONFIG, make_event
from store import LocalStore

TENANT_ID = os.environ["TENANT_ID"]
SITE_ID = os.environ["SITE_ID"]
DEVICE_ID = os.environ["DEVICE_ID"]
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
EVENT_INTERVAL_S = float(os.getenv("EVENT_INTERVAL_S", "3"))
HEARTBEAT_INTERVAL_S = 10
CAMERAS_TOTAL = 8
# A hardware limit the cloud doesn't know about: this unit's speed radar
# can't enforce limits above 25 km/h. The cloud allows up to 40, so setting
# e.g. 30 shows the "device rejects config" path in the demo.
MAX_SPEED_LIMIT_KMH = 25

TOPIC_BASE = f"tenants/{TENANT_ID}/sites/{SITE_ID}/devices/{DEVICE_ID}"

log = logging.getLogger(DEVICE_ID)


def connect(inbox: queue.Queue) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    client.username_pw_set(DEVICE_ID, MQTT_PASSWORD)

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("connected to broker (%s)", reason_code)
        if not reason_code.is_failure:
            # (Re)subscribing on every connect makes the broker send us the
            # retained desired config, so we catch up on anything we missed.
            c.subscribe(f"{TOPIC_BASE}/config", qos=1)

    client.on_connect = on_connect
    # paho calls this on its network thread; SQLite and our state belong to
    # the main loop, so just hand the payload over.
    client.on_message = lambda c, userdata, msg: inbox.put(msg.payload)
    client.on_disconnect = lambda c, u, f, rc, p: log.warning("disconnected from broker (%s)", rc)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    # connect_async + loop_start: paho keeps retrying in a background thread,
    # so the device keeps working (and buffering) while the cloud is away.
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


class OutboxSender:
    """Publishes buffered events one at a time, oldest first, so the cloud
    receives them in seq order."""

    def __init__(self, client: mqtt.Client, store: LocalStore):
        self.client = client
        self.store = store
        self.inflight: tuple[int, mqtt.MQTTMessageInfo] | None = None

    def flush(self) -> None:
        while True:
            if self.inflight:
                seq, info = self.inflight
                if not info.is_published():
                    # No PUBACK yet. paho keeps the message and retransmits it
                    # after a reconnect, so we must NOT publish it again here:
                    # a half-dead connection can look "connected" for up to a
                    # keepalive period, and every re-publish would queue
                    # another copy.
                    return
                self.store.mark_sent(seq)
                self.inflight = None

            pending = self.store.pending(limit=1)
            if not pending or not self.client.is_connected():
                return
            seq, event = pending[0]
            info = self.client.publish(f"{TOPIC_BASE}/events", json.dumps({"seq": seq, **event}), qos=1)
            self.inflight = (seq, info)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                return
            # Duplicates are still possible (e.g. PUBACK lost, then a crash
            # before mark_sent). That's fine: the cloud de-duplicates on
            # (device_id, seq). At-least-once delivery + idempotent consumer
            # = effectively exactly-once.


def publish_state(client: mqtt.Client, store: LocalStore) -> None:
    # Heartbeats are neither buffered nor QoS 1: a stale "I'm alive" is
    # worthless and the next one replaces it, so QoS 0 (fire and forget) is
    # the right trade-off. Events are the data that must not be lost.
    state = {
        "software_version": store.get("software_version"),
        "config_version": int(store.get("config_version")),
        "health": {
            "cpu_pct": round(random.uniform(15, 60), 1),
            "gpu_pct": round(random.uniform(30, 85), 1),
            "cameras_online": random.choices([CAMERAS_TOTAL, CAMERAS_TOTAL - 1], weights=[9, 1])[0],
            "cameras_total": CAMERAS_TOTAL,
        },
        "outbox_backlog": store.backlog(),
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    client.publish(f"{TOPIC_BASE}/state", json.dumps(state), qos=0)


def validate_config(config: dict) -> str | None:
    missing = {"zones", "speed_limit_kmh", "near_miss_critical_m"} - config.keys()
    if missing:
        return f"missing keys: {sorted(missing)}"
    if not config["zones"]:
        return "config has no zones"
    if config["speed_limit_kmh"] > MAX_SPEED_LIMIT_KMH:
        return f"speed limit {config['speed_limit_kmh']} km/h exceeds this unit's radar maximum ({MAX_SPEED_LIMIT_KMH} km/h)"
    return None


def send_ack(client: mqtt.Client, version: int, status: str, error: str | None = None) -> None:
    # Not buffered in the outbox: if we're offline this ack is lost, but on
    # reconnect the retained config arrives again and we ack again.
    ack = {"kind": "config", "version": version, "status": status, "error": error,
           "acked_at": datetime.now(timezone.utc).isoformat()}
    client.publish(f"{TOPIC_BASE}/ack", json.dumps(ack), qos=1)


def handle_config(client: mqtt.Client, store: LocalStore, payload: bytes) -> bool:
    """Returns True if a new config was applied.

    Runs for every delivery, including the retained copy we get after each
    reconnect, so handling the same message twice must be harmless.
    """
    try:
        msg = json.loads(payload)
        version, config = int(msg["version"]), msg["config"]
    except (ValueError, KeyError, TypeError):
        log.warning("ignoring malformed config message")
        return False

    running = int(store.get("config_version"))
    if version < running:
        log.info("ignoring config v%d: already running v%d", version, running)
        return False
    applied = False
    if version > running:
        error = validate_config(config)
        if error:
            log.warning("rejecting config v%d: %s", version, error)
            send_ack(client, version, "failed", error)
            return False
        store.set_many(config=json.dumps(config), config_version=str(version))
        log.info("applied config v%d: %s", version, config)
        applied = True
    # Ack both a fresh apply and a redelivery of the version we already
    # run: the earlier ack may have been lost.
    send_ack(client, version, "applied")
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Python as PID 1 ignores SIGTERM unless we handle it, which would make
    # `docker stop` hang for 10 s before killing us.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    store = LocalStore(os.getenv("DATA_DIR", "/data") + "/edge.db")
    if store.get("software_version") is None:
        store.set("software_version", os.getenv("SOFTWARE_VERSION", "1.0.0"))
        store.set("config_version", "0")
    if store.get("config") is None:
        store.set("config", json.dumps(DEFAULT_CONFIG))

    inbox: queue.Queue[bytes] = queue.Queue()
    client = connect(inbox)
    sender = OutboxSender(client, store)
    next_event = next_heartbeat = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            while not inbox.empty():
                if handle_config(client, store, inbox.get_nowait()):
                    next_heartbeat = now  # report the new config_version right away
            if now >= next_event:
                config = json.loads(store.get("config"))
                event = make_event(store.get("software_version"), int(store.get("config_version")), config)
                seq = store.add_event(event)
                log.info("event seq=%d %s zone=%s (backlog=%d)", seq, event["type"], event["zone"], store.backlog())
                next_event = now + EVENT_INTERVAL_S * random.uniform(0.5, 1.5)
            if now >= next_heartbeat and client.is_connected():
                publish_state(client, store)
                next_heartbeat = now + HEARTBEAT_INTERVAL_S
            sender.flush()
            time.sleep(0.5)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
