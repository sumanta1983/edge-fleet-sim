"""Releases and staged rollouts.

A release is one rollout of a software version to a set of devices (one
site, or the whole tenant). release_targets has one row per device, saying
which wave it is in and how far it got.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def execute_all(*statements: str) -> None:
    for statement in statements:
        op.execute(statement)


def enable_rls(table: str) -> None:
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
        CREATE TABLE releases (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES tenants (id),
            site_id     text,                 -- NULL: every device of the tenant
            version     text NOT NULL,
            checksum    text NOT NULL,        -- sha256 of the artifact; devices verify it
            status      text NOT NULL DEFAULT 'rolling_out'
                            CHECK (status IN ('rolling_out', 'completed', 'halted')),
            halt_reason text,
            created_by  uuid REFERENCES users (id),
            created_at  timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            FOREIGN KEY (tenant_id, site_id) REFERENCES sites (tenant_id, id),
            UNIQUE (tenant_id, id)
        )
        """,
        """
        -- At most one rollout in progress per tenant. Two overlapping rollouts
        -- could send one device two different versions and make "which
        -- release broke it?" unanswerable.
        CREATE UNIQUE INDEX releases_one_active ON releases (tenant_id) WHERE status = 'rolling_out'
        """,
        """
        CREATE TABLE release_targets (
            tenant_id    text NOT NULL,
            release_id   bigint NOT NULL,
            site_id      text NOT NULL,
            device_id    text NOT NULL,
            wave         smallint NOT NULL CHECK (wave IN (0, 1)),  -- 0 = canary
            status       text NOT NULL DEFAULT 'pending' CHECK (status IN (
                             'pending',      -- waiting for the canary
                             'sent',         -- command published, no answer yet
                             'succeeded',    -- installed and passed its health check
                             'rolled_back',  -- installed, failed health check, back on from_version
                             'failed',       -- not installed (e.g. checksum mismatch)
                             'timed_out',    -- canary never answered
                             'skipped')),    -- rollout halted before this device's turn
            from_version text,                -- what it ran when the rollout started
            error        text,
            sent_at      timestamptz,
            finished_at  timestamptz,
            PRIMARY KEY (release_id, device_id),
            FOREIGN KEY (tenant_id, release_id) REFERENCES releases (tenant_id, id),
            FOREIGN KEY (tenant_id, site_id, device_id) REFERENCES devices (tenant_id, site_id, id)
        )
        """,
    )
    enable_rls("releases")
    enable_rls("release_targets")

    execute_all(
        "GRANT SELECT, INSERT, UPDATE ON releases, release_targets TO edgefleet_api",
        # The ingestor drives the rollout forward as acks and heartbeats arrive.
        "GRANT SELECT, UPDATE ON releases, release_targets TO edgefleet_ingestor",
    )


def downgrade() -> None:
    op.execute("DROP TABLE release_targets, releases")
