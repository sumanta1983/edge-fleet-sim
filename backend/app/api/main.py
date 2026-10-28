"""REST API for the dashboard. Run with: uvicorn app.api.main:app"""
import os
import uuid
from contextlib import asynccontextmanager

import paho.mqtt.client as mqtt
from fastapi import FastAPI

from app.api import auth, configs, events, fleet, kpis
from app.db import make_engine


def connect_mqtt() -> mqtt.Client:
    # Random client id: two API replicas sharing an id would keep kicking
    # each other off the broker. The API only publishes, so it needs no
    # persistent session.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"api-{uuid.uuid4().hex[:8]}")
    client.username_pw_set("api", os.environ["MQTT_PASSWORD"])
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(os.getenv("MQTT_HOST", "mosquitto"), int(os.getenv("MQTT_PORT", "1883")), keepalive=30)
    client.loop_start()
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connects as edgefleet_api: not the table owner, no BYPASSRLS.
    app.state.engine = make_engine(os.environ["DATABASE_URL"])
    app.state.mqtt = connect_mqtt()
    yield
    app.state.mqtt.loop_stop()
    app.state.mqtt.disconnect()
    await app.state.engine.dispose()


app = FastAPI(title="edgefleet-sim API", version="0.4.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(fleet.router)
app.include_router(events.router)
app.include_router(kpis.router)
app.include_router(configs.router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
