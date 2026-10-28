"""Event search and per-event audit view."""
import base64
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, tuple_

from app.api.auth import TenantConn
from app.models import Event
from app.schemas import EventType, Severity

router = APIRouter(prefix="/events", tags=["events"])


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: str
    device_id: str
    seq: int
    event_id: uuid.UUID
    type: EventType
    zone: str
    severity: Severity
    occurred_at: datetime
    received_at: datetime
    # Audit: the software and config that produced this event.
    software_version: str
    config_version: int
    details: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    # Pass back as ?cursor= to get the next (older) page; None on the last page.
    next_cursor: str | None


# Keyset ("cursor") pagination instead of OFFSET: OFFSET 10000 makes Postgres
# read and throw away 10000 rows, and on a table that grows every second the
# pages shift between requests (you see duplicates or miss events). A cursor
# says "continue after this exact row", which is stable and uses the index.
# id breaks ties between events with the same timestamp.
def _encode_cursor(e: EventOut) -> str:
    return base64.urlsafe_b64encode(f"{e.occurred_at.isoformat()}|{e.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        occurred_at, event_id = base64.urlsafe_b64decode(cursor).decode().split("|")
        return datetime.fromisoformat(occurred_at), int(event_id)
    except ValueError:
        raise HTTPException(400, "invalid cursor")


@router.get("")
async def list_events(
    conn: TenantConn,
    site_id: str | None = None,
    device_id: str | None = None,
    event_type: Annotated[EventType | None, Query(alias="type")] = None,
    since: Annotated[datetime | None, Query(description="occurred_at >= since (ISO 8601)")] = None,
    until: Annotated[datetime | None, Query(description="occurred_at < until (ISO 8601)")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> EventPage:
    query = select(Event).order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit + 1)
    if site_id:
        query = query.where(Event.site_id == site_id)
    if device_id:
        query = query.where(Event.device_id == device_id)
    if event_type:
        query = query.where(Event.type == event_type)
    if since:
        query = query.where(Event.occurred_at >= since)
    if until:
        query = query.where(Event.occurred_at < until)
    if cursor:
        query = query.where(tuple_(Event.occurred_at, Event.id) < _decode_cursor(cursor))

    items = [EventOut.model_validate(r) for r in await conn.execute(query)]
    # We fetched one extra row only to learn whether another page exists.
    has_more = len(items) > limit
    items = items[:limit]
    return EventPage(items=items, next_cursor=_encode_cursor(items[-1]) if has_more else None)


@router.get("/{event_id}")
async def get_event(conn: TenantConn, event_id: int) -> EventOut:
    row = (await conn.execute(select(Event).where(Event.id == event_id))).one_or_none()
    if row is None:
        raise HTTPException(404, "event not found")
    return EventOut.model_validate(row)
