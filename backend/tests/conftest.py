"""Integration tests against a real Postgres, in a throwaway database.

Row-level security, grants and triggers are Postgres features: SQLite or
mocks would happily pass tests that the real database fails. So each test
session creates `edgefleet_test` on the compose Postgres, migrates and seeds
it exactly like production, and drops it at the end. (Not the demo database:
events are append-only, so test rows could never be cleaned up there.)

Run with: docker compose run --rm tests
"""
import asyncio
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from itertools import count

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import make_engine

HOST = os.getenv("DB_HOST", "postgres")
TEST_DB = "edgefleet_test"
SUPERUSER_URL = f"postgresql+asyncpg://postgres:{os.environ['POSTGRES_PASSWORD']}@{HOST}/postgres"
OWNER_URL = f"postgresql+asyncpg://edgefleet_owner:dev-owner-pw@{HOST}/{TEST_DB}"
API_URL = f"postgresql+asyncpg://edgefleet_api:dev-api-pw@{HOST}/{TEST_DB}"
INGESTOR_URL = f"postgresql+asyncpg://edgefleet_ingestor:dev-ingestor-pw@{HOST}/{TEST_DB}"

# Fresh database per session, so sequence numbers only need to be unique
# within one run.
_seq = count(1)


async def _as_superuser(*statements: str) -> None:
    # CREATE/DROP DATABASE can't run inside a transaction.
    engine = create_async_engine(SUPERUSER_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    # Same setup as postgres/init/01-roles.sh does for the real database.
    asyncio.run(
        _as_superuser(
            f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)",
            f"CREATE DATABASE {TEST_DB} OWNER edgefleet_owner",
            f"REVOKE ALL ON DATABASE {TEST_DB} FROM PUBLIC",
            f"GRANT CONNECT ON DATABASE {TEST_DB} TO edgefleet_api, edgefleet_ingestor",
        )
    )
    env = {**os.environ, "MIGRATIONS_DATABASE_URL": OWNER_URL}
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)
    subprocess.run(["python", "-m", "app.seed"], check=True, env=env)
    yield
    asyncio.run(_as_superuser(f"DROP DATABASE {TEST_DB} WITH (FORCE)"))


@pytest.fixture
async def owner_engine():
    engine = make_engine(OWNER_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def api_engine():
    engine = make_engine(API_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def ingestor_engine():
    engine = make_engine(INGESTOR_URL)
    yield engine
    await engine.dispose()


def next_seq() -> int:
    return next(_seq)


def event_payload(seq: int, **overrides) -> bytes:
    event = {
        "seq": seq,
        "event_id": str(uuid.uuid4()),
        "type": "near_miss",
        "zone": "dock-1",
        "severity": "high",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "software_version": "1.0.0",
        "config_version": 0,
        "details": {},
    }
    return json.dumps({**event, **overrides}).encode()
