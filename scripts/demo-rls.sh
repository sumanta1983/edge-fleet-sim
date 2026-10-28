#!/usr/bin/env bash
# Tenant isolation demo.
#
# Connects to Postgres as edgefleet_api, the role the REST API uses, and
# shows that row-level security keeps tenants apart even for hand-written
# SQL, then shows the same through the API.
#
# Usage: scripts/demo-rls.sh   (needs curl and jq)
set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://127.0.0.1:8000}"

echo "################ 1. Directly in Postgres, as the API's role ################"
# -e echoes each statement; ON_ERROR_STOP off so the expected errors are shown.
docker compose exec -T postgres psql -e -v ON_ERROR_STOP=0 \
  "postgresql://edgefleet_api:dev-api-pw@127.0.0.1/edgefleet" <<'SQL'
\echo '--- The role is not a superuser, cannot bypass RLS, and owns no tables'
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
SELECT tablename, tableowner FROM pg_tables WHERE tablename IN ('events', 'devices');

\echo '--- No tenant set: every query sees nothing (fails closed)'
SELECT count(*) AS events_visible FROM events;

\echo '--- As tenant-a: only tenant-a rows, even when asking for tenant-b by name'
BEGIN;
SELECT set_config('app.tenant_id', 'tenant-a', true);   -- = SET LOCAL
SELECT tenant_id, count(*) AS events FROM events GROUP BY tenant_id;
SELECT count(*) AS tenant_b_devices_visible FROM devices WHERE tenant_id = 'tenant-b';
\echo '--- ...and cannot write a row for tenant-b'
INSERT INTO desired_configs (tenant_id, site_id, version, config) VALUES ('tenant-b', 'site-b1', 999, '{}');
ROLLBACK;

\echo '--- After the transaction the tenant is gone again (SET LOCAL, not SET)'
SELECT count(*) AS events_visible FROM events;

\echo '--- Switching RLS off is refused for this role'
SET row_security = off;
SELECT count(*) FROM events;
SQL

echo
echo "################ 2. Through the REST API, logged in as tenant-a ################"
token=$(curl -sf -X POST "$API/auth/login" -d "username=operator@tenant-a.example&password=dev-password" | jq -r .access_token)
call() {
  echo "GET $1"
  curl -s -w '   -> HTTP %{http_code}\n' -H "Authorization: Bearer $token" "$API$1" | sed 's/^/   /'
}
echo "GET /fleet/devices"
echo "   devices visible: $(curl -sf -H "Authorization: Bearer $token" "$API/fleet/devices" | jq -c 'map(.id)')"
call "/fleet/devices/edge-b1-01"
call "/events?site_id=site-b1&limit=5"
token_b=$(curl -sf -X POST "$API/auth/login" -d "username=operator@tenant-b.example&password=dev-password" | jq -r .access_token)
release_b=$(curl -sf -H "Authorization: Bearer $token_b" "$API/releases" | jq -r '.[0].id // empty')
if [ -n "$release_b" ]; then
  echo "(release #$release_b belongs to tenant-b)"
  call "/releases/$release_b"
fi
echo
echo "Tenant B's device and events don't exist as far as tenant A can tell: 404 / empty, never 403."
