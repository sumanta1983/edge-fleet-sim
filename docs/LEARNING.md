# Learning notes

One section per build phase: the concepts, the design decisions, and the
interview questions you should be ready for.

---

## Phase 1 – MQTT, broker security, and the edge outbox

### The big picture

Each warehouse has an **edge server**. It watches cameras, makes safety
decisions locally (it must keep working even if the internet is down), and
sends small **anonymised events** to the cloud. Never video.

Devices and cloud talk through an **MQTT broker** (Mosquitto). MQTT is
publish/subscribe: a sender publishes a message to a *topic*, the broker
forwards it to everyone subscribed to that topic. Sender and receiver never
talk directly.

```
edge-a1-01 --publish--> [ mosquitto ] --deliver--> ingestor
```

### Why the device opens the connection

Customer sites sit behind firewalls and NAT. Inbound connections from the
internet are blocked, but outbound connections are almost always allowed.
So the **device dials out** to the cloud broker and keeps the connection
open. The cloud can then send config and release commands *back down the
same connection*. Nobody has to open a port at the customer site.

### Topics are identity

```
tenants/{tenant}/sites/{site}/devices/{device}/{events|state|ack|config|release}
```

The ingestor reads tenant, site and device **from the topic, not from the
message body**. That is only safe because of the broker's **ACL**: each
device login may publish and subscribe *only* to its own full topic path. If
edge-a1-01 publishes on a tenant-b topic, the broker silently drops the
message. We use exact paths, not a wildcard like `tenants/+/.../%u/...`,
because a wildcard on the tenant segment would let a device post under
another tenant's prefix.

Every client must log in (`allow_anonymous false`). Dev passwords live in
`mosquitto/dev-passwords.txt` and are hashed at broker start-up. In
production each device would have its own **X.509 certificate (mTLS)**.

### QoS levels

| QoS | Meaning | Used for |
|---|---|---|
| 0 | at most once: fire and forget | heartbeats (a stale heartbeat is useless, the next one replaces it) |
| 1 | at least once: resend until the receiver sends PUBACK; **may duplicate** | events, acks, config, releases |
| 2 | exactly once: 4-step handshake, slower | not used. We get the same effect more cheaply (see below) |

### Outbox pattern + seq = no lost events, no double counting

1. A new event is first **written to local SQLite** (`outbox` table). Its
   primary key is the per-device **`seq`**, which only ever increases.
   `AUTOINCREMENT` guarantees a seq is never reused.
2. A sender publishes the oldest unsent event with QoS 1.
3. Only after the broker's **PUBACK** is the row deleted.

If the network drops, events pile up locally and are sent in order when it
comes back. If the device crashes after sending but before deleting, the
event is sent twice. That's why the cloud will de-duplicate on
`(device_id, seq)` (phase 2). **At-least-once delivery + an idempotent
consumer = effectively exactly-once.**

A bug we found while building this: a dead TCP connection can still look
"connected" until the keepalive timeout (~45 s). Our first version re-published
the same event every loop during that window, and paho queued every copy.
The fix: keep exactly one message in flight and let the MQTT library
retransmit it. Lesson: the network can lie about being up.

### Persistent session for the ingestor

The ingestor connects with a fixed client id and `clean_session=False`. The
broker remembers its subscriptions and **queues QoS 1 messages while it is
down**, then delivers them when it reconnects. Broker `persistence true`
keeps that queue on disk across broker restarts too.

### Audit fields

Every event carries `software_version` and `config_version`: which model and
which rules produced this decision. Events can become legal or insurance
evidence, so we must be able to answer "what was running when this happened?"

### Interview questions

**Q: Why MQTT instead of HTTP for devices?**
A: One long-lived outbound connection that works through firewalls, lets
the cloud push to the device, has a tiny overhead on bad networks, and gives
delivery guarantees (QoS) and offline queueing out of the box.

**Q: How do you guarantee events aren't lost when a site goes offline?**
A: Outbox: write to local durable storage first, delete only after the
broker acknowledges (QoS 1 PUBACK). On reconnect the backlog is flushed
oldest-first.

**Q: QoS 1 can deliver duplicates. How do you handle that?**
A: Every event has a monotonically increasing per-device seq. The database
has a unique constraint on (device_id, seq) and inserts with
`ON CONFLICT DO NOTHING`, so a duplicate is a no-op.

**Q: How do you stop one customer's device from sending data as another customer?**
A: Each device authenticates with its own credentials, and the broker ACL
pins it to its own topic path. The cloud derives tenant identity from the
topic, never from the payload. In production: per-device certificates (mTLS).

**Q: Why is the heartbeat QoS 0 but events QoS 1?**
A: Heartbeats are only useful when fresh, and the next one supersedes the
last. Events are records that must survive, so they get acknowledgement,
retries and local buffering.

---

## Phase 2 – Postgres, row-level security, idempotent and append-only events

### Three database roles (least privilege)

| Role | Used by | Can do |
|---|---|---|
| `edgefleet_owner` | migrations + seed | owns the tables (DDL) |
| `edgefleet_api` | REST API (phase 3) | SELECT only so far |
| `edgefleet_ingestor` | MQTT ingestor | INSERT events, upsert device_state |

The app roles are **not superusers, have no `BYPASSRLS`, and don't own the
tables**. All three matter: superusers and roles with `BYPASSRLS` skip RLS
entirely, and table owners skip it unless the table has `FORCE ROW LEVEL
SECURITY` (we set FORCE anyway, as an extra safety net).

### Row-level security (RLS)

Every tenant-owned table has a `tenant_id` column and this policy:

```sql
USING      (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
```

- `USING` filters what you can **read** (and which rows UPDATE/DELETE can touch).
- `WITH CHECK` filters what you can **write**: tenant A can't insert a tenant-B row.
- If `app.tenant_id` isn't set, nothing matches → you see **zero rows**.
  Forgetting the tenant **fails closed**.

Why RLS instead of just `WHERE tenant_id = ?` in every query? Because one
forgotten WHERE clause is a data leak. With RLS the database enforces
isolation, even for a buggy query or a hand-typed psql session.

### SET LOCAL and connection pooling

The application sets the tenant **inside each transaction**:

```sql
SELECT set_config('app.tenant_id', 'tenant-a', true);  -- true = LOCAL
```

Connections are pooled: after a request finishes, the *same* connection
serves the next request, possibly for another tenant. A plain `SET` stays
on the connection, so the next request would inherit the wrong tenant.
`SET LOCAL` is reset automatically at COMMIT/ROLLBACK. We use
`set_config(...)` because it accepts a bind parameter (no SQL injection);
`SET LOCAL` can't.

### Composite foreign keys

`events (tenant_id, site_id, device_id) REFERENCES devices (tenant_id, site_id, id)`.
Including `tenant_id` in the key means the database itself guarantees that
an event's device belongs to the same tenant and site. A bug can't create a
cross-tenant link.

### Idempotent ingestion

`UNIQUE (device_id, seq)` + `INSERT ... ON CONFLICT (device_id, seq) DO NOTHING`.
A duplicate MQTT delivery inserts 0 rows instead of failing or double-counting.

### End-to-end at-least-once: ack after commit

paho normally sends PUBACK as soon as a message arrives. If the ingestor
crashed before writing to the DB, the message would be gone. So we turn on
**manual ack** and send PUBACK only **after the DB transaction commits**.
Combined with the persistent session, a crash means "redeliver", never "lose".

Errors are split into two kinds:
- **Permanent** (fails validation, unknown device, RLS violation): log and
  drop. Retrying will never help, and it would block every message behind it.
  (Production: send it to a dead-letter queue.)
- **Transient** (database down): retry with exponential backoff, without
  acking. We'd rather stall than lose an event.

We saw this work during the build: a datetime-mapping bug made every insert
fail. The ingestor kept retrying, and after the fix it stored everything
with no gaps.

### Append-only audit trail

Two layers:
1. App roles are never granted UPDATE, DELETE or TRUNCATE on `events` → `permission denied`.
2. A trigger raises an error for UPDATE/DELETE/TRUNCATE, **even for the table owner**.

A superuser could still disable the trigger, so in production you'd also
copy events to write-once storage (e.g. object storage with object lock)
or hash-chain them.

### Heartbeats: upsert with an ordering guard

`device_state` keeps one row per device. The upsert only overwrites when
the incoming `reported_at` is newer, so a delayed heartbeat can't roll back
fresher state. `last_seen_at` uses the **cloud's** clock (device clocks drift).

### Interview questions

**Q: How do you isolate tenants in a shared database?**
A: Every tenant-owned table has a tenant_id and a Postgres RLS policy
comparing it to a per-transaction setting. The app connects as a role without
BYPASSRLS that doesn't own the tables, and sets the tenant with SET LOCAL
from the authenticated user. If the tenant isn't set, queries return nothing.

**Q: Why SET LOCAL and not SET?**
A: Pooled connections are reused across requests. SET persists on the
connection and would leak one tenant's context into the next request.
SET LOCAL is scoped to the transaction and resets on commit or rollback.

**Q: How do you make sure events are never lost between broker and database?**
A: Manual MQTT ack after the DB commit, a persistent broker session, and
idempotent inserts. A crash leads to redelivery; redelivery leads to a no-op.

**Q: How do you make the audit log tamper-resistant?**
A: Least-privilege grants (INSERT/SELECT only) plus a trigger that rejects
UPDATE, DELETE and TRUNCATE for everyone. For stronger guarantees: write-once
storage or hash chaining, because a DB superuser can bypass triggers.

**Q: What happens if a device's clock is wrong?**
A: We store both the device timestamp (occurred_at, when it happened) and
the cloud timestamp (received_at / last_seen_at). Online/offline uses the
cloud clock, and per-device ordering uses seq, not timestamps.

## Phase 3 – REST API, JWT auth, tenant context per request

### Request flow

```
client ──Bearer JWT──▶ FastAPI
                        1. current_user: verify JWT signature + expiry → (user id, tenant_id, role)
                        2. tenant_conn:  BEGIN; set_config('app.tenant_id', <tenant from JWT>, true)
                        3. route handler runs plain queries; RLS filters every row
                        4. COMMIT → the tenant setting disappears with the transaction
```

The tenant comes **only** from the signed token. There is no `?tenant_id=`
parameter anywhere, so a client can't ask for someone else's data, and even a
query that forgets a `WHERE tenant_id = …` is still filtered by Postgres.

### JWT in one paragraph

At login the API checks the password (bcrypt) and returns a token signed with
a server secret (HS256). The token holds `sub` (user id), `tenant_id`, `role`
and `exp`. On every request we verify the signature and expiry; no database
lookup is needed. Trade-off: a stateless token **can't be revoked**, so we keep
its lifetime short (8 h here; real systems use minutes plus refresh tokens).

### Login before the tenant is known

`users` is protected by RLS, but at login we don't know the tenant yet. Rather
than give the API role BYPASSRLS, we added **one narrow door**: a
`SECURITY DEFINER` function `auth_user_by_email(email)` that runs as the schema
owner and returns just the matching row. The API role can EXECUTE it, but still
sees 0 rows if it runs `SELECT * FROM users` without a tenant. The function has
a pinned `search_path` so nobody can trick it into reading a fake `users`
table, and `EXECUTE` is revoked from everyone else (the ingestor gets
"permission denied").

Two small login details:
- bcrypt is deliberately slow, so it runs in a thread (`asyncio.to_thread`)
  and doesn't block the event loop.
- An unknown email is still checked against a dummy hash, so attackers can't
  tell from response times which emails exist.

### Online / offline

A device is online if its last heartbeat (cloud clock) is under 30 s old:
heartbeats every 10 s, so we tolerate **three** missed ones before flagging it.
One threshold → no flapping on a single lost QoS 0 heartbeat. Devices that have
never reported still appear (outer join), as offline.

### Keyset pagination

`/events` uses a cursor `(occurred_at, id)` instead of `OFFSET`.
- `OFFSET 10000` reads and throws away 10 000 rows; a cursor jumps straight
  there using the `(tenant_id, occurred_at)` index.
- New events arrive every second; with OFFSET, page 2 shifts and you see
  duplicates or miss rows. "Give me rows older than this one" stays stable.
- `id` breaks ties between events with the same timestamp.

### KPIs in the site's timezone

"Per day" and "per shift" depend on where the warehouse is: 23:00 UTC is
already tomorrow in Kolkata. Queries use `occurred_at AT TIME ZONE
site.timezone`. Night shift (22:00–06:00) crosses midnight, so we shift the
clock back 6 h: all three shifts then fall inside one calendar day, and the
02:00 part of a night shift belongs to the day the shift started.

PPE compliance needs a **denominator**: violations alone don't say whether 5
violations is good (out of 1000 people) or bad (out of 6). The gate camera now
records `ppe_ok` for every person passing, so
compliance = passed checks / all checks. In-site `ppe_violation` detections
are reported next to it.

At this scale a live `GROUP BY` is fine. At scale: a materialized view or a
rollup table the ingestor updates.

### Interview questions

**Q: How does a request end up seeing only its tenant's data?**
A: The JWT carries the tenant id and is signed by the server. A FastAPI
dependency opens a transaction and runs `SET LOCAL app.tenant_id` from that
claim; Postgres RLS policies filter every table on it. The API's DB role has
no BYPASSRLS and doesn't own the tables, so there's no way around it.

**Q: How do you log a user in when RLS hides the users table?**
A: A SECURITY DEFINER function that returns only the row for one email,
with a pinned search_path and EXECUTE granted only to the API role. That's
a narrow, auditable exception instead of giving the API role BYPASSRLS.

**Q: What are the downsides of JWTs?**
A: They can't be revoked before they expire, and their claims (role, tenant)
can go stale. Mitigations: short expiry plus refresh tokens, a deny-list for
emergencies, or opaque session tokens checked against a store.

**Q: Why cursor pagination instead of page numbers?**
A: OFFSET gets slower the deeper you go and is unstable on a table that's
constantly growing. A cursor on (occurred_at, id) is index-friendly and
stable while new events arrive.

**Q: How do you decide a device is offline?**
A: Compare the last heartbeat time, measured on the cloud's clock, to a
timeout of a few heartbeat intervals (here 3 × 10 s). Long enough to ride out
a lost packet, short enough that operators notice quickly.

## Phase 4 – Desired vs reported config, with acks

### The flow

```
operator ──PUT /sites/{id}/config──▶ API
   API: INSERT desired_configs (version N+1) → COMMIT
   API: publish {version, config} RETAINED, QoS 1 → .../devices/{d}/config  (each device at the site)
device: version > running? validate → apply (SQLite) → ack {applied|failed} → heartbeat with new config_version
ingestor: store ack in config_acks; store heartbeat in device_state
dashboard: desired_version vs reported_version + ack → in_sync / pending / failed
```

### Desired vs reported state

The cloud never "sets" a device's config. It writes down what it **wants**
(desired) and the device says what it **has** (reported). They can differ for
hours: the device is offline, or it rejected the config. The UI shows both
instead of pretending a change is live the moment you click Save. Kubernetes,
AWS IoT device shadows and Azure device twins all use this same idea.

### Versions, not timestamps

Each change is a new row with the next integer version, never an UPDATE.
- Devices compare plain integers: older than what I run → ignore; same → just
  ack again; newer → validate and apply.
- History for free: the event audit view (`GET /events/{id}`) joins on
  `(site_id, config_version)` and shows the exact config that was active.

### Why retained messages

The broker keeps the last retained message per topic and delivers it as soon
as a device subscribes. The device subscribes on every (re)connect, so a device
that was offline during the change gets the latest config the moment it comes
back. The cloud doesn't track who missed what. Tested: stop a device, change
config, start it → it applies the new version within a second.

### Commit, then publish, then reconcile

Writing to the DB and publishing to MQTT can't be one atomic step.
- Publish first, then the commit fails → devices run a config that doesn't
  exist anywhere. Bad.
- Commit first, then crash before publishing → the config is saved, but the
  retained message is stale. This gap can be fixed.

We commit first, and close the gap with a **reconciliation loop** in the
ingestor: on every heartbeat it checks "reported < desired, and the device
hasn't answered for that version" and re-sends the config. This is
**level-triggered** (keep comparing state and fix any difference) rather than
**edge-triggered** (trust that one "config changed" message arrived). It also
pushed v1 to every device right after seeding, with no extra code.

A failed ack stops the re-sending: the same config would just fail again.
The operator has to issue a new version.

### Acks are idempotent too

The device acks again for every redelivery (the first ack may have been lost
while offline). `config_acks` has primary key `(device_id, config_version)`,
and an upsert keeps the newest ack. A composite foreign key makes sure a device
can only ack a version that was really issued to **its** site.

### Validation happens in two places

- **Cloud** (Pydantic, `extra="forbid"`): shape and sane ranges; a typo'd key
  is a 422, not a silently ignored field.
- **Device**: what only the hardware knows. The simulated radar maxes out at
  25 km/h, so a 30 km/h limit passes the cloud but is rejected by the device
  with a reason. That's the `failed` state in the UI.

### Optimistic concurrency (lost updates)

`PUT` carries `base_version`, the version the operator started editing. If
someone saved in the meantime, the API returns **409** instead of overwriting
their change. Two saves at exactly the same moment both try version N+1; the
primary key lets only one win.

### Interview questions

**Q: How do you push config to devices that may be offline?**
A: Store it as versioned desired state in the cloud and publish it as a
retained MQTT message per device. Devices subscribe on every connect, get the
latest retained config, apply it if the version is newer, and ack. A
reconciliation loop re-sends to any device whose reported version lags.

**Q: What's the difference between desired and reported state?**
A: Desired is what the operator asked for; reported is what the device says
it actually runs. Keeping both lets you show drift honestly (pending, failed,
offline) and drive retries from the difference.

**Q: You write to the DB and to MQTT. What if one fails?**
A: Commit first, then publish. The remaining gap (committed but not published)
is closed by level-triggered reconciliation on heartbeats. The alternative is a
transactional outbox table with a separate publisher.

**Q: How do you avoid two operators overwriting each other's changes?**
A: Optimistic concurrency: the client sends the version it edited, and the
server rejects with 409 if that's no longer the latest. A unique version per
site catches exact races.

**Q: Why should the device validate config if the cloud already did?**
A: The device knows things the cloud doesn't (hardware limits, firmware
capabilities). It must never apply a config it can't honour, and it reports
the reason back so the operator can fix it.
