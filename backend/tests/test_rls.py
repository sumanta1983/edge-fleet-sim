"""Tenant isolation is enforced by Postgres itself, for the API's own role."""
import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import tenant_transaction
from app.models import DesiredConfig
from tests.conftest import API_URL

TENANT_TABLES = ["sites", "devices", "users", "desired_configs"]


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_tenant_sees_only_its_own_rows(api_engine, table):
    async with tenant_transaction(api_engine, "tenant-a") as conn:
        tenants = set(await conn.scalars(text(f"SELECT DISTINCT tenant_id FROM {table}")))
    assert tenants == {"tenant-a"}


async def test_explicit_filter_for_other_tenant_returns_nothing(api_engine):
    # Even asking for tenant B by name, from tenant A's context, finds nothing.
    async with tenant_transaction(api_engine, "tenant-a") as conn:
        assert await conn.scalar(text("SELECT count(*) FROM devices WHERE tenant_id = 'tenant-b'")) == 0
    async with tenant_transaction(api_engine, "tenant-b") as conn:
        assert await conn.scalar(text("SELECT count(*) FROM devices WHERE tenant_id = 'tenant-b'")) == 1


async def test_no_tenant_set_means_no_rows(api_engine):
    # Fail closed: forgetting SET LOCAL shows nothing rather than everything.
    async with api_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM devices")) == 0


async def test_tenant_setting_does_not_leak_into_the_next_transaction():
    # One pooled connection, so the second transaction reuses the first one's
    # connection. SET LOCAL must be gone by then.
    engine = create_async_engine(API_URL, pool_size=1, max_overflow=0)
    try:
        async with tenant_transaction(engine, "tenant-a") as conn:
            assert await conn.scalar(text("SELECT count(*) FROM devices")) == 3
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT current_setting('app.tenant_id', true)")) in (None, "")
            assert await conn.scalar(text("SELECT count(*) FROM devices")) == 0
    finally:
        await engine.dispose()


async def test_cannot_write_a_row_for_another_tenant(api_engine):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_transaction(api_engine, "tenant-a") as conn:
            await conn.execute(
                insert(DesiredConfig).values(tenant_id="tenant-b", site_id="site-b1", version=99, config={})
            )


async def test_api_role_cannot_switch_rls_off(api_engine):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api_engine.connect() as conn:
            await conn.execute(text("SET row_security = off"))
            await conn.execute(text("SELECT * FROM devices"))
