"""Safety KPIs, computed on the fly from the events table.

Days and shifts are in each SITE's local timezone: a night shift in Berlin
and one in Kolkata are different UTC hours. Plain SQL here because the
date/timezone arithmetic reads better as SQL than as SQLAlchemy expressions.

At this data volume a live GROUP BY is fine. At scale you'd pre-aggregate
(a materialized view or a rollup table updated by the ingestor).
"""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.auth import TenantConn

router = APIRouter(prefix="/kpis", tags=["kpis"])

Days = Annotated[int, Query(ge=1, le=90)]


class NearMissesPerDay(BaseModel):
    site_id: str
    day: date
    near_misses: int


class PpeCompliance(BaseModel):
    site_id: str
    day: date
    shift: Literal["early", "late", "night"]
    checks: int        # people checked for PPE at gates
    passed: int
    compliance_pct: float | None  # None when there were no checks
    violations: int    # ppe_violation detections inside the site


@router.get("/near-misses")
async def near_misses_per_day(conn: TenantConn, days: Days = 14) -> list[NearMissesPerDay]:
    rows = await conn.execute(
        text(
            """
            SELECT e.site_id,
                   (e.occurred_at AT TIME ZONE s.timezone)::date AS day,
                   count(*) AS near_misses
            FROM events e
            JOIN sites s ON s.id = e.site_id
            WHERE e.type = 'near_miss'
              AND e.occurred_at >= now() - make_interval(days => :days)
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ),
        {"days": days},
    )
    return [NearMissesPerDay.model_validate(r._mapping) for r in rows]


@router.get("/ppe-compliance")
async def ppe_compliance(conn: TenantConn, days: Days = 7) -> list[PpeCompliance]:
    # Shifts: early 06-14, late 14-22, night 22-06 (local time). Shifting the
    # clock back 6 h makes every shift fall inside one calendar day, so the
    # 02:00 part of a night shift counts towards the day the shift STARTED.
    rows = await conn.execute(
        text(
            """
            WITH shifted AS (
                SELECT e.site_id, e.type, e.details,
                       (e.occurred_at AT TIME ZONE s.timezone) - interval '6 hours' AS t
                FROM events e
                JOIN sites s ON s.id = e.site_id
                WHERE e.type IN ('gate_triggered', 'ppe_violation')
                  AND e.occurred_at >= now() - make_interval(days => :days)
            )
            SELECT site_id,
                   t::date AS day,
                   CASE WHEN extract(hour FROM t) < 8  THEN 'early'
                        WHEN extract(hour FROM t) < 16 THEN 'late'
                        ELSE 'night' END AS shift,
                   count(*) FILTER (WHERE details->'ppe_ok' IS NOT NULL) AS checks,
                   count(*) FILTER (WHERE (details->>'ppe_ok')::boolean) AS passed,
                   count(*) FILTER (WHERE type = 'ppe_violation') AS violations
            FROM shifted
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
            """
        ),
        {"days": days},
    )
    return [
        PpeCompliance(
            **r._mapping,
            compliance_pct=round(100 * r.passed / r.checks, 1) if r.checks else None,
        )
        for r in rows
    ]
