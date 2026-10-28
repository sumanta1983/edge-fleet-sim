#!/usr/bin/env bash
# Offline buffering demo.
#
# Cuts one edge server off the network (as if the site's internet went down),
# waits, reconnects it, and then checks that every event it produced while
# offline reached the cloud, with no gaps and no duplicates.
#
# Usage: scripts/demo-offline.sh [device] [seconds offline]
set -euo pipefail
cd "$(dirname "$0")/.."

DEVICE="${1:-edge-a1-02}"
OFFLINE_S="${2:-60}"

container=$(docker compose ps -q "$DEVICE")
[ -n "$container" ] || { echo "device container '$DEVICE' is not running (docker compose up -d first)"; exit 1; }
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$container")

sql() { docker compose exec -T postgres psql -U postgres -d edgefleet -tAc "$1"; }
backlog() {
  docker compose exec -T "$DEVICE" python -c \
    "import sqlite3; print(sqlite3.connect('/data/edge.db').execute('SELECT count(*) FROM outbox').fetchone()[0])"
}
cloud_max_seq() { sql "SELECT coalesce(max(seq), 0) FROM events WHERE device_id = '$DEVICE'"; }

reconnect() { docker network connect "$network" "$container" 2>/dev/null || true; }
trap reconnect EXIT   # never leave the device cut off, even on Ctrl+C

start_seq=$(cloud_max_seq)
echo "== $DEVICE: latest event in the cloud has seq $start_seq; device outbox backlog $(backlog)"

echo "== Disconnecting $DEVICE from '$network' for ${OFFLINE_S}s"
docker network disconnect "$network" "$container"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for ((t = 10; t <= OFFLINE_S; t += 10)); do
  sleep 10
  echo "   ${t}s offline: device outbox backlog = $(backlog), cloud max seq = $(cloud_max_seq)"
done

echo "== Reconnecting"
reconnect
for _ in $(seq 1 60); do
  sleep 2
  b=$(backlog)
  echo "   backlog = $b, cloud max seq = $(cloud_max_seq)"
  [ "$b" -eq 0 ] && break
done
end_seq=$(cloud_max_seq)

echo
echo "== Checks for seq $((start_seq + 1))..$end_seq"
read -r stored distinct missing late <<<"$(sql "
  SELECT count(*),
         count(DISTINCT seq),
         (SELECT count(*) FROM generate_series($start_seq + 1, $end_seq) s
           WHERE NOT EXISTS (SELECT 1 FROM events WHERE device_id = '$DEVICE' AND seq = s)),
         count(*) FILTER (WHERE received_at - occurred_at > interval '10 seconds')
  FROM events WHERE device_id = '$DEVICE' AND seq > $start_seq" | tr '|' ' ')"
echo "   events stored:                 $stored"
echo "   distinct seq:                  $distinct   (equal to stored -> no duplicates)"
echo "   missing seq in range:          $missing   (0 -> no gaps, nothing lost)"
echo "   arrived >10s after they happened (backfilled): $late"
dups=$(docker compose logs --since "$started" ingestor | grep -c "duplicate event $DEVICE" || true)
echo "   redeliveries the ingestor ignored: $dups   (QoS 1 retries; harmless thanks to (device_id, seq))"

if [ "$missing" -eq 0 ] && [ "$stored" -eq "$distinct" ] && [ "$stored" -gt 0 ]; then
  echo "== OK: every event made it to the cloud exactly once"
else
  echo "== UNEXPECTED: see numbers above"; exit 1
fi
