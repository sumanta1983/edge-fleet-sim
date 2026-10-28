"""Login lookup: find a user by email before the tenant is known.

Every API query runs under RLS with the tenant taken from the caller's JWT.
Login is the one moment where there is no JWT yet, so we can't SET LOCAL
app.tenant_id, and RLS on users would (correctly) return nothing.

Instead of giving the API role a way around RLS, we expose exactly one
narrow capability: a SECURITY DEFINER function that returns the single user
row matching an email. The API role may EXECUTE it but still has no way to
list or read other tenants' users.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A SECURITY DEFINER function runs as its owner (edgefleet_owner). Because
    # users has FORCE ROW LEVEL SECURITY, the owner is filtered too, so it
    # needs its own read policy. Policies are OR-ed together, so this adds to
    # tenant_isolation for the owner only. That gives nothing away: the owner
    # could already ALTER the table and switch RLS off; FORCE is a guard
    # against accidents for that role, not a security boundary.
    op.execute("CREATE POLICY owner_login_lookup ON users FOR SELECT TO edgefleet_owner USING (true)")
    op.execute(
        """
        CREATE FUNCTION auth_user_by_email(p_email text)
            RETURNS TABLE (id uuid, tenant_id text, password_hash text, role text)
            LANGUAGE sql STABLE SECURITY DEFINER
            -- Pinned search_path: otherwise a caller could create their own
            -- "users" in another schema and have this function read it.
            SET search_path = public, pg_temp
        AS $$
            SELECT u.id, u.tenant_id, u.password_hash, u.role
            FROM users u
            WHERE u.email = lower(p_email)
        $$
        """
    )
    # New functions are executable by PUBLIC by default.
    op.execute("REVOKE ALL ON FUNCTION auth_user_by_email(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_user_by_email(text) TO edgefleet_api")


def downgrade() -> None:
    op.execute("DROP FUNCTION auth_user_by_email(text)")
    op.execute("DROP POLICY owner_login_lookup ON users")
