"""Alembic runs as the schema owner (MIGRATIONS_DATABASE_URL), never as an
app role. Migrations are hand-written SQL, so no autogenerate metadata."""
import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine


def run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(os.environ["MIGRATIONS_DATABASE_URL"])
    async with engine.connect() as conn:
        await conn.run_sync(run_migrations)
    await engine.dispose()


asyncio.run(main())
