I want to build a portfolio prototype called **edgefleet-sim**: a small but realistic simulation of a cloud platform that manages edge devices at multiple customer sites. I will use it to show a hiring manager that I understand cloud-to-edge fleet management, and I must be able to explain every part of it in a technical interview. So build it in phases, keep it simple and readable, and teach me as you go.

Do NOT use any real company's name, logo or branding anywhere. This is a generic, original project.

## Context (what the system simulates)

Industrial safety company. Each customer site (a warehouse) has an on-site **edge server** that runs AI on camera feeds locally and makes safety decisions without the cloud. The edge sends **anonymised events** (never video) up to the cloud. The cloud platform lets operators manage all sites: view events and KPIs, push **config** (zones, speed limits, alert thresholds) down to sites, and roll out **software/model releases** safely. Sites can go **offline** at any time and must resync when they come back. Events are used as legal/insurance **proof**, so they must be an immutable audit trail.

## Stack (must use)

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic
- Database: PostgreSQL 16 with **row-level security (RLS)** for tenant isolation
- Messaging: MQTT via Eclipse Mosquitto (QoS 1, retained messages where appropriate)
- Frontend: React + TypeScript + Vite, TanStack Query; TypeScript API types generated from the FastAPI OpenAPI schema (openapi-typescript)
- Everything runs with Docker Compose on a single Ubuntu laptop (~8 GB RAM). No cloud accounts, no paid services.

## Services in docker-compose

1. `mosquitto` – MQTT broker, with per-device username/password and an ACL so each device can only publish/subscribe to its own topics
2. `postgres`
3. `api` – FastAPI REST API for the dashboard
4. `ingestor` – separate Python service that subscribes to MQTT and writes events/state into Postgres
5. `web` – React dashboard
6. `edge-sim` – one image, run as 4 containers via env vars (2 tenants, e.g. tenant A with 2 sites, tenant B with 1 site, plus 1 extra unit), each simulating an edge server

## MQTT topic design

- `tenants/{tenant_id}/sites/{site_id}/devices/{device_id}/events` – device → cloud
- `.../state` – device → cloud: heartbeat + reported state (software version, config version, health: cpu, gpu, cameras_online)
- `.../config` – cloud → device: desired config (retained, versioned)
- `.../release` – cloud → device: release command (target version, artifact checksum)
- `.../ack` – device → cloud: acknowledgement that config/release was applied or failed

Explain in comments why the device initiates the connection (customer firewalls) and why desired config is retained.

## Edge simulator behaviour

- Every few seconds generates realistic event types: `near_miss`, `ppe_violation`, `speed_violation`, `gate_triggered`, `label_read`, `loading_session_completed`, each with zone, timestamp, severity and a monotonically increasing per-device `seq` number
- Every event records the `software_version` and `config_version` that were active when it happened (audit requirement)
- Local SQLite **outbox buffer**: events are written locally first, then published; on reconnect, unsent events are flushed in order
- Heartbeat every 10s on `/state`
- Applies incoming config, reports applied `config_version` back, sends ack
- Release handling: "installs" the new version, runs a simulated health check, and **automatically rolls back** to the previous version if the health check fails. A release can be marked as intentionally bad (e.g. version `2.0.0-bad`) for demos.

## Database & security

- Tables (at minimum): tenants, sites, devices, users, events, device_state, desired_configs, config_acks, releases, release_targets
- All tenant-owned tables have `tenant_id` and an RLS policy based on `current_setting('app.tenant_id')`
- The API connects as a dedicated DB role **without BYPASSRLS** and not the table owner
- Per request, inside the transaction: `SET LOCAL app.tenant_id = ...` derived from the authenticated user. Add a comment explaining why plain `SET` is dangerous with connection pooling
- The ingestor writes with tenant context from the topic, also under RLS
- **Events are append-only**: block UPDATE/DELETE (trigger or revoked privileges). Idempotent inserts with a unique constraint on (device_id, seq), so duplicate MQTT deliveries are harmless
- Simple JWT login with seeded users per tenant (no real passwords in the repo except clearly marked dev seed data in a seed file)

## API features

- Fleet overview: devices with online/offline (heartbeat timeout), software version, config version (desired vs reported), last seen
- Events: filter by site, type, time range; paginated
- KPIs: e.g. near misses per site per day, PPE compliance per site/shift
- Config: update desired config for a site; see per-device ack status
- Releases: create a release; **staged rollout** (canary device first, then the rest); automatically **halt** the rollout if the canary fails; show rollout progress
- Audit: for any event, show which software and config version were running

## Dashboard pages

Fleet overview, device detail (events + desired vs reported config), config editor, releases with rollout progress, simple KPI charts. Clean and functional; no branding.

## Demo scripts

- `scripts/demo-offline.sh` – disconnects one edge container from the network (`docker network disconnect`), waits, reconnects; events should backfill with no duplicates
- `scripts/demo-bad-release.sh` – rolls out `2.0.0-bad`; canary fails, rolls back, rollout halts
- `scripts/demo-rls.sh` – shows tenant A cannot see tenant B's data, even with a direct query under the app role

## Tests (pytest)

- RLS isolation between tenants
- Idempotent event ingestion (same seq twice → one row)
- Events cannot be updated or deleted
- Rollout halts when the canary fails

## Build in phases — STOP after each phase

After each phase: make sure it runs with `docker compose up`, show me how to verify it, and wait for me to say "continue".

1. Compose skeleton, Mosquitto with auth/ACL, one edge-sim publishing events, ingestor printing them
2. Postgres schema, Alembic migrations, RLS, seed data, ingestor storing events idempotently, append-only protection
3. FastAPI with JWT auth, tenant context via `SET LOCAL`, fleet + events + KPI endpoints
4. Desired/reported config flow with acks
5. Releases: staged rollout, health check, automatic rollback, rollout halt
6. React dashboard
7. Offline buffering demo, tests, demo scripts, README

## Documentation

- `README.md`: what this is, a Mermaid architecture diagram, how to run, what each demo shows, **what it does not do** (no real computer vision, no real hardware, no vehicle control, single-node, not production-hardened), and how I would take it to production (managed Postgres, mTLS device certificates, signed artifacts, A/B partitions on real devices, monitoring/alerting, horizontal scaling)
- `docs/LEARNING.md`: after each phase, add a short section in simple English explaining the concepts and design decisions of that phase (MQTT QoS and retained messages, outbox pattern, idempotency, desired vs reported state, RLS and SET LOCAL, staged rollout and rollback, append-only audit logs), plus 3–5 likely interview questions on it with short model answers

Keep the code small and idiomatic, with comments on the *why*, not the obvious *what*. Prefer clarity over cleverness. Start with phase 1.