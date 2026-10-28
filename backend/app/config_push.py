"""Desired config on the wire. Shared by the two publishers: the API (when an
operator saves a change) and the ingestor (when it reconciles a device that
is behind), so both send byte-for-byte the same message.

The config is published as a RETAINED message on each device's
.../config topic. The broker keeps the last retained message per topic and
delivers it the moment a device (re)subscribes. So a device that was offline
during the change, or was replaced with a fresh box, gets the current desired
config on its next connect; the cloud doesn't have to track who missed what.
"""
import json
from typing import Any

from sqlalchemy import Select, select

from app.models import DesiredConfig


def config_topic(tenant_id: str, site_id: str, device_id: str) -> str:
    return f"tenants/{tenant_id}/sites/{site_id}/devices/{device_id}/config"


def config_message(version: int, config: dict[str, Any]) -> str:
    return json.dumps({"version": version, "config": config}, sort_keys=True)


def latest_desired(site_id: str) -> Select:
    return (
        select(DesiredConfig.version, DesiredConfig.config)
        .where(DesiredConfig.site_id == site_id)
        .order_by(DesiredConfig.version.desc())
        .limit(1)
    )
