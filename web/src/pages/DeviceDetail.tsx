import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { Badge, ErrorBox, formatTime, Stat, timeAgo } from "../ui";
import { ConfigSync } from "./Fleet";

type EventType = Schemas["EventOut"]["type"];
const EVENT_TYPES: EventType[] = [
  "near_miss",
  "ppe_violation",
  "speed_violation",
  "gate_triggered",
  "label_read",
  "loading_session_completed",
];

export function ConfigView({ config, compareTo }: { config: Schemas["SiteConfig"]; compareTo?: Schemas["SiteConfig"] | null }) {
  // Highlight what differs from the other side, so drift is visible at a glance.
  const differs = (a: unknown, b: unknown) => compareTo != null && JSON.stringify(a) !== JSON.stringify(b);
  return (
    <dl className="config">
      <dt>Speed limit</dt>
      <dd className={differs(config.speed_limit_kmh, compareTo?.speed_limit_kmh) ? "diff" : ""}>{config.speed_limit_kmh} km/h</dd>
      <dt>Near miss critical below</dt>
      <dd className={differs(config.near_miss_critical_m, compareTo?.near_miss_critical_m) ? "diff" : ""}>{config.near_miss_critical_m} m</dd>
      <dt>Zones</dt>
      <dd className={differs(config.zones, compareTo?.zones) ? "diff" : ""}>
        {config.zones.map((z) => `${z.id} (${z.kind})`).join(", ")}
      </dd>
    </dl>
  );
}

function EventAudit({ eventId, onClose }: { eventId: number; onClose: () => void }) {
  const event = useQuery({
    queryKey: ["event", eventId],
    queryFn: () => unwrap(api.GET("/events/{event_id}", { params: { path: { event_id: eventId } } })),
  });
  const e = event.data;
  return (
    <section className="card audit">
      <header className="row">
        <h2>Audit: event #{eventId}</h2>
        <button className="link" onClick={onClose}>Close</button>
      </header>
      <ErrorBox error={event.error} />
      {e && (
        <>
          <dl className="config">
            <dt>Type</dt>
            <dd>{e.type} <Badge value={e.severity} /></dd>
            <dt>Occurred (device clock)</dt>
            <dd>{formatTime(e.occurred_at)}</dd>
            <dt>Received (cloud clock)</dt>
            <dd>{formatTime(e.received_at)}</dd>
            <dt>Device / seq</dt>
            <dd>{e.device_id} / {e.seq}</dd>
            <dt>Software running</dt>
            <dd><code>{e.software_version}</code></dd>
            <dt>Config running</dt>
            <dd>v{e.config_version}{e.config_version === 0 && " (factory default)"}</dd>
            <dt>Details</dt>
            <dd><code>{JSON.stringify(e.details)}</code></dd>
          </dl>
          {e.config && (
            <>
              <h3>Config v{e.config_version} as it was</h3>
              <ConfigView config={e.config} />
            </>
          )}
        </>
      )}
    </section>
  );
}

export default function DeviceDetail() {
  const deviceId = useParams().deviceId!;
  const [eventType, setEventType] = useState<EventType | "">("");
  const [auditId, setAuditId] = useState<number | null>(null);

  const device = useQuery({
    queryKey: ["device", deviceId],
    queryFn: () => unwrap(api.GET("/fleet/devices/{device_id}", { params: { path: { device_id: deviceId } } })),
    refetchInterval: 5000,
  });
  const siteId = device.data?.site_id;
  const sitePath = { params: { path: { site_id: siteId ?? "" } } };

  // The device reports only a version number; the config contents for any
  // version come from the cloud's history (every version is kept).
  const history = useQuery({
    queryKey: ["config-history", siteId],
    queryFn: () => unwrap(api.GET("/sites/{site_id}/config/history", sitePath)),
    enabled: !!siteId,
  });
  const status = useQuery({
    queryKey: ["config-status", siteId],
    queryFn: () => unwrap(api.GET("/sites/{site_id}/config/status", sitePath)),
    enabled: !!siteId,
    refetchInterval: 5000,
  });

  const events = useInfiniteQuery({
    queryKey: ["events", deviceId, eventType],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/events", {
          params: { query: { device_id: deviceId, type: eventType || undefined, cursor: pageParam, limit: 25 } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  const d = device.data;
  const desired = history.data?.[0];
  const reported = history.data?.find((h) => h.version === d?.config_version);
  const myStatus = status.data?.find((s) => s.device_id === deviceId);

  return (
    <>
      <p><Link to="/">← Fleet</Link></p>
      <h1>{deviceId}</h1>
      <ErrorBox error={device.error} />
      {d && (
        <div className="stats">
          <Stat label={`last seen ${timeAgo(d.last_seen_at)}`} value={<Badge value={d.online ? "online" : "offline"} />} />
          <Stat label="site" value={d.site_name} />
          <Stat label="software" value={<code>{d.software_version ?? "–"}</code>} />
          <Stat label="config" value={<ConfigSync device={d} />} />
          <Stat label="cpu / gpu" value={d.health ? `${d.health.cpu_pct}% / ${d.health.gpu_pct}%` : "–"} />
          <Stat label="cameras" value={d.health ? `${d.health.cameras_online}/${d.health.cameras_total}` : "–"} />
          <Stat label="outbox backlog" value={d.outbox_backlog ?? "–"} />
        </div>
      )}

      <div className="grid-2">
        <section className="card">
          <h2>Desired config {desired && <span className="muted">v{desired.version}</span>}</h2>
          {desired ? <ConfigView config={desired.config} compareTo={reported?.config} /> : <p className="muted">No desired config for this site.</p>}
          {siteId && <p><Link to={`/config?site=${siteId}`}>Edit site config →</Link></p>}
        </section>
        <section className="card">
          <h2>Reported config {d?.config_version != null && <span className="muted">v{d.config_version}</span>}</h2>
          {reported ? (
            <ConfigView config={reported.config} compareTo={desired?.config} />
          ) : (
            <p className="muted">{d?.config_version === 0 ? "Factory default (v0)." : "Unknown."}</p>
          )}
          {myStatus?.ack_status === "failed" && (
            <div className="error">Device rejected v{myStatus.desired_version}: {myStatus.ack_error}</div>
          )}
        </section>
      </div>

      {auditId != null && <EventAudit eventId={auditId} onClose={() => setAuditId(null)} />}

      <section>
        <header className="row">
          <h2>Events</h2>
          <select value={eventType} onChange={(e) => setEventType(e.target.value as EventType | "")}>
            <option value="">All types</option>
            {EVENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button className="secondary" onClick={() => events.refetch()}>Refresh</button>
        </header>
        <ErrorBox error={events.error} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Seq</th>
                <th>Occurred</th>
                <th>Type</th>
                <th>Severity</th>
                <th>Zone</th>
                <th>Software</th>
                <th>Config</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {events.data?.pages.flatMap((p) => p.items).map((e) => (
                <tr key={e.id}>
                  <td>{e.seq}</td>
                  <td>{formatTime(e.occurred_at)}</td>
                  <td>{e.type}</td>
                  <td><Badge value={e.severity} /></td>
                  <td>{e.zone}</td>
                  <td><code>{e.software_version}</code></td>
                  <td>v{e.config_version}</td>
                  <td><button className="link" onClick={() => setAuditId(e.id)}>Audit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {events.hasNextPage && (
          <button className="secondary" onClick={() => events.fetchNextPage()} disabled={events.isFetchingNextPage}>
            {events.isFetchingNextPage ? "Loading…" : "Load older events"}
          </button>
        )}
      </section>
    </>
  );
}
