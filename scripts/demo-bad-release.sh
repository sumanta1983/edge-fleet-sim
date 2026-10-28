#!/usr/bin/env bash
# Bad release demo.
#
# Rolls out a version whose health check fails. Expected: the canary installs
# it, fails the check, rolls itself back, and the rollout halts before any
# other device gets it.
#
# Usage: scripts/demo-bad-release.sh [version]   (needs curl and jq)
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
VERSION="${1:-2.0.0-bad}"
EMAIL="operator@tenant-a.example"

token=$(curl -sf -X POST "$API/auth/login" -d "username=$EMAIL&password=dev-password" | jq -r .access_token)
get() { curl -sf -H "Authorization: Bearer $token" "$API$1"; }
versions() { get /fleet/devices | jq -r '.[] | "   \(.id)\t\(.software_version)"'; }

echo "== Software versions before:"
versions

echo "== Starting rollout of $VERSION to every device of tenant-a"
response=$(curl -s -w '\n%{http_code}' -X POST -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  "$API/releases" -d "{\"version\": \"$VERSION\"}")
code=$(tail -n1 <<<"$response")
body=$(sed '$d' <<<"$response")
if [ "$code" != 201 ]; then
  echo "   could not start: $(jq -r .detail <<<"$body")"
  exit 1
fi
id=$(jq -r .id <<<"$body")
echo "   release #$id, canary: $(jq -r '.targets[] | select(.wave == 0) | .device_id' <<<"$body")"

for _ in $(seq 1 60); do
  sleep 2
  release=$(get "/releases/$id")
  status=$(jq -r .status <<<"$release")
  echo "   $status  $(jq -c .progress <<<"$release")"
  [ "$status" != rolling_out ] && break
done

echo
echo "== Result: release #$id is $status"
jq -r '"   reason: \(.halt_reason // "-")"' <<<"$release"
jq -r '.targets[] | "   \(if .wave == 0 then "canary" else "wave 1" end)\t\(.device_id)\t\(.status)\t\(.error // "")"' <<<"$release"

echo "== Software versions after (unchanged if the canary rolled back):"
versions
