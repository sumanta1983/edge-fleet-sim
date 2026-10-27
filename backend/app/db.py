from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_size=5, pool_pre_ping=True)


async def set_tenant(conn, tenant_id: str) -> None:
    """Scope every following query in this transaction to one tenant.

    This is SET LOCAL, not SET. Connections are pooled and reused: a plain
    SET would stay on the connection after we return it to the pool, and
    the next request (maybe for another tenant) would silently inherit this
    tenant's id. SET LOCAL is undone automatically at COMMIT/ROLLBACK.

    set_config(name, value, is_local => true) is the function form of
    SET LOCAL. We use it because SET can't take a bind parameter, and
    string-formatting a tenant id into SQL is an injection risk.
    """
    await conn.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})


@asynccontextmanager
async def tenant_transaction(engine: AsyncEngine, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    async with engine.begin() as conn:
        await set_tenant(conn, tenant_id)
        yield conn
