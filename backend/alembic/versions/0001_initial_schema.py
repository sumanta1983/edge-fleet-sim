"""Initial schema: tenants, sites, devices, users, events, device_state.

Written as plain SQL on purpose: row-level security, triggers and grants are
the important part of this schema, and they read best as SQL.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ["sites", "devices", "users", "events", "device_state"]


def execute_all(*statements: str) -> None:
    # asyncpg runs each statement as a prepared statement, which allows only
    # one command at a time.
    for statement in statements:
        op.execute(statement)


def enable_rls(table: str, column: str = "tenant_id") -> None:
    # current_setting(..., true) returns NULL/'' when app.tenant_id is unset,
    # which matches no rows: forgetting to set the tenant fails closed (you
    # see nothing) rather than open (you see everything).
    #
    # FORCE makes the policy apply to the table owner too, so even the
    # migrations/seed role can't accidentally read across tenants.
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
            USING      ({column} = current_setting('app.tenant_id', true))
            WITH CHECK ({column} = current_setting('app.tenant_id', true))
        """
    )


def upgrade() -> None:
    execute_all(
        """
        CREATE TABLE tenants (
            id         text PRIMARY KEY,           -- same id as in MQTT topics
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE sites (
            id        text PRIMARY KEY,
            tenant_id text NOT NULL REFERENCES tenants (id),
            name      text NOT NULL,
            timezone  text NOT NULL DEFAULT 'UTC',  -- for per-shift KPIs
            UNIQUE (tenant_id, id)
        )
        """,
        """
        -- Composite foreign keys that include tenant_id let the database
        -- itself guarantee that a device belongs to a site of the SAME
        -- tenant, and that an event belongs to a device of the same tenant
        -- and site. A bug can't create a cross-tenant reference.
        CREATE TABLE devices (
            id         text PRIMARY KEY,
            tenant_id  text NOT NULL,
            site_id    text NOT NULL,
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, site_id) REFERENCES sites (tenant_id, id),
            UNIQUE (tenant_id, site_id, id)
        )
        """,
        """
        CREATE TABLE users (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     text NOT NULL REFERENCES tenants (id),
            email         text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            role          text NOT NULL DEFAULT 'operator' CHECK (role IN ('operator', 'viewer')),
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE events (
            id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id        text NOT NULL,
            site_id          text NOT NULL,
            device_id        text NOT NULL,
            seq              bigint NOT NULL,
            event_id         uuid NOT NULL,
            type             text NOT NULL CHECK (type IN (
                                 'near_miss', 'ppe_violation', 'speed_violation', 'gate_triggered',
                                 'label_read', 'loading_session_completed')),
            zone             text NOT NULL,
            severity         text NOT NULL CHECK (severity IN ('info', 'medium', 'high', 'critical')),
            occurred_at      timestamptz NOT NULL,              -- device clock
            received_at      timestamptz NOT NULL DEFAULT now(), -- cloud clock
            -- Audit: what was running on the device when this happened.
            software_version text NOT NULL,
            config_version   integer NOT NULL,
            details          jsonb NOT NULL DEFAULT '{}',
            FOREIGN KEY (tenant_id, site_id, device_id) REFERENCES devices (tenant_id, site_id, id),
            -- Idempotency key: MQTT QoS 1 may deliver the same event twice;
            -- the second insert hits this constraint and is ignored.
            UNIQUE (device_id, seq)
        )
        """,
        "CREATE INDEX events_tenant_time ON events (tenant_id, occurred_at DESC)",
        "CREATE INDEX events_site_time ON events (site_id, occurred_at DESC)",
        """
        -- Latest reported state per device (one row, overwritten by heartbeats).
        CREATE TABLE device_state (
            device_id        text PRIMARY KEY,
            tenant_id        text NOT NULL,
            site_id          text NOT NULL,
            software_version text NOT NULL,
            config_version   integer NOT NULL,
            health           jsonb NOT NULL,
            outbox_backlog   integer NOT NULL DEFAULT 0,
            reported_at      timestamptz NOT NULL,  -- device clock, for ordering heartbeats
            last_seen_at     timestamptz NOT NULL,  -- cloud clock, for online/offline
            FOREIGN KEY (tenant_id, site_id, device_id) REFERENCES devices (tenant_id, site_id, id)
        )
        """,
    )

    enable_rls("tenants", column="id")
    for table in TENANT_TABLES:
        enable_rls(table)

    # Append-only audit trail, enforced twice:
    #  1. the app roles are never granted UPDATE/DELETE/TRUNCATE on events;
    #  2. this trigger blocks those operations for every role, including the
    #     table owner. (A superuser could still disable the trigger; in
    #     production you'd also stream events to write-once storage.)
    execute_all(
        """
        CREATE FUNCTION reject_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'events are append-only: % is not allowed', TG_OP;
        END
        $$
        """,
        """
        CREATE TRIGGER events_no_update_delete BEFORE UPDATE OR DELETE ON events
            FOR EACH ROW EXECUTE FUNCTION reject_event_mutation()
        """,
        """
        CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
            FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation()
        """,
    )

    # Least privilege per service.
    execute_all(
        "GRANT SELECT ON tenants, sites, devices, users, events, device_state TO edgefleet_api",
        "GRANT SELECT ON tenants, sites, devices TO edgefleet_ingestor",
        "GRANT SELECT, INSERT ON events TO edgefleet_ingestor",
        "GRANT SELECT, INSERT, UPDATE ON device_state TO edgefleet_ingestor",
    )


def downgrade() -> None:
    execute_all(
        "DROP TABLE device_state, events, users, devices, sites, tenants",
        "DROP FUNCTION reject_event_mutation()",
    )
