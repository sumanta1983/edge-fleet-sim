"""Demo tenants, sites, devices and users. Safe to run repeatedly.

DEV SEED DATA ONLY: the plaintext password below exists solely so you can
log in to the local demo. It is hashed with bcrypt before being stored.
"""
import asyncio
import os

import bcrypt
from sqlalchemy.dialects.postgresql import insert

from app.db import make_engine, tenant_transaction
from app.models import Device, Site, Tenant, User

DEV_PASSWORD = "dev-password"  # DEV ONLY

TENANTS = [
    {
        "id": "tenant-a",
        "name": "Demo Logistics A",
        "sites": [
            {"id": "site-a1", "name": "A1 North Warehouse", "timezone": "Europe/Berlin",
             "devices": ["edge-a1-01", "edge-a1-02"]},
            {"id": "site-a2", "name": "A2 South Warehouse", "timezone": "Europe/Berlin",
             "devices": ["edge-a2-01"]},
        ],
        "users": [("operator@tenant-a.example", "operator"), ("viewer@tenant-a.example", "viewer")],
    },
    {
        "id": "tenant-b",
        "name": "Demo Cold Storage B",
        "sites": [
            {"id": "site-b1", "name": "B1 Cold Store", "timezone": "Asia/Kolkata",
             "devices": ["edge-b1-01"]},
        ],
        "users": [("operator@tenant-b.example", "operator")],
    },
]


async def main() -> None:
    engine = make_engine(os.environ["MIGRATIONS_DATABASE_URL"])
    password_hash = bcrypt.hashpw(DEV_PASSWORD.encode(), bcrypt.gensalt()).decode()

    for t in TENANTS:
        # FORCE ROW LEVEL SECURITY applies to the owner too, so even seeding
        # has to say which tenant it is writing for.
        async with tenant_transaction(engine, t["id"]) as conn:
            await conn.execute(insert(Tenant).values(id=t["id"], name=t["name"]).on_conflict_do_nothing())
            for s in t["sites"]:
                await conn.execute(
                    insert(Site)
                    .values(id=s["id"], tenant_id=t["id"], name=s["name"], timezone=s["timezone"])
                    .on_conflict_do_nothing()
                )
                for device_id in s["devices"]:
                    await conn.execute(
                        insert(Device)
                        .values(id=device_id, tenant_id=t["id"], site_id=s["id"], name=device_id)
                        .on_conflict_do_nothing()
                    )
            for email, role in t["users"]:
                await conn.execute(
                    insert(User)
                    .values(tenant_id=t["id"], email=email, password_hash=password_hash, role=role)
                    .on_conflict_do_nothing()
                )
        print(f"seeded {t['id']}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
