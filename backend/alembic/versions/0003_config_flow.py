"""Desired config per site, and per-device acks.

desired_configs is a history: every change is a new row with the next
version number, never an UPDATE. That gives a free audit trail ("what
config was active when event X happened?") and lets devices compare plain
integers to decide whether a config is newer than the one they run.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def execute_all(*statements: str) -> None:
    for statement in statements:
        op.execute(statement)


def enable_rls(table: str) -> None:
    # Same policy as in 0001: rows are visible only to the tenant set with SET LOCAL.
    execute_all(
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"""
        CREATE POLICY tenant_isolation ON {table}
            USING      (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """,
    )


def upgrade() -> None:
    execute_all(
        """
        CREATE TABLE desired_configs (
            tenant_id  text NOT NULL,
            site_id    text NOT NULL,
            version    integer NOT NULL CHECK (version >= 1),  -- 0 = device factory default
            config     jsonb NOT NULL,
            created_by uuid REFERENCES users (id),             -- NULL for seed data
            created_at timestamptz NOT NULL DEFAULT now(),
            -- Two operators saving at the same moment both try version N+1;
            -- the primary key lets exactly one of them win.
            PRIMARY KEY (site_id, version),
            FOREIGN KEY (tenant_id, site_id) REFERENCES sites (tenant_id, id),
            UNIQUE (tenant_id, site_id, version)
        )
        """,
        """
        CREATE TABLE config_acks (
            tenant_id      text NOT NULL,
            site_id        text NOT NULL,
            device_id      text NOT NULL,
            config_version integer NOT NULL,
            status         text NOT NULL CHECK (status IN ('applied', 'failed')),
            error          text,
            acked_at       timestamptz NOT NULL,              -- device clock
            received_at    timestamptz NOT NULL DEFAULT now(), -- cloud clock
            -- One row per device and version: a re-sent ack (the device acks
            -- again after every reconnect) updates the row, not a new one.
            PRIMARY KEY (device_id, config_version),
            FOREIGN KEY (tenant_id, site_id, device_id) REFERENCES devices (tenant_id, site_id, id),
            -- A device can only ack a version that was really issued to its site.
            FOREIGN KEY (tenant_id, site_id, config_version)
                REFERENCES desired_configs (tenant_id, site_id, version)
        )
        """,
    )
    enable_rls("desired_configs")
    enable_rls("config_acks")

    execute_all(
        # No UPDATE/DELETE for the API: config history is append-only.
        "GRANT SELECT, INSERT ON desired_configs TO edgefleet_api",
        "GRANT SELECT ON config_acks TO edgefleet_api",
        # The ingestor reads desired config to re-send it when a device lags.
        "GRANT SELECT ON desired_configs TO edgefleet_ingestor",
        "GRANT SELECT, INSERT, UPDATE ON config_acks TO edgefleet_ingestor",
    )


def downgrade() -> None:
    op.execute("DROP TABLE config_acks, desired_configs")
