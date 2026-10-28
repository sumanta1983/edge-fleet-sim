import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { Badge, ErrorBox, Stat, timeAgo } from "../ui";

export function ConfigSync({ device }: { device: Schemas["DeviceOut"] }) {
  const { desired_config_version: desired, config_version: reported } = device;
  if (desired == null) return <span className="muted">–</span>;
  if (reported === desired) return <Badge value="in_sync" label={`v${reported}`} />;
  return <Badge value="pending" label={`v${reported ?? "?"} → v${desired}`} />;
}

export default function Fleet() {
  // Heartbeats arrive every 10 s, so polling every 5 s keeps the page at
  // most one heartbeat behind without hammering the API.
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => unwrap(api.GET("/fleet/devices")),
    refetchInterval: 5000,
  });
  const list = devices.data ?? [];

  return (
    <>
      <h1>Fleet</h1>
      <ErrorBox error={devices.error} />
      <div className="stats">
        <Stat label="devices" value={list.length} />
        <Stat label="online" value={list.filter((d) => d.online).length} tone="good" />
        <Stat label="offline" value={list.filter((d) => !d.online).length} tone={list.some((d) => !d.online) ? "bad" : undefined} />
        <Stat
          label="config not in sync"
          value={list.filter((d) => d.desired_config_version != null && d.config_version !== d.desired_config_version).length}
        />
        <Stat label="events buffered on devices" value={list.reduce((n, d) => n + (d.outbox_backlog ?? 0), 0)} />
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Device</th>
              <th>Site</th>
              <th>Status</th>
              <th>Last seen</th>
              <th>Software</th>
              <th>Config (reported → desired)</th>
              <th>Cameras</th>
              <th>Outbox</th>
            </tr>
          </thead>
          <tbody>
            {list.map((d) => (
              <tr key={d.id}>
                <td><Link to={`/devices/${d.id}`}>{d.id}</Link></td>
                <td>{d.site_name}</td>
                <td><Badge value={d.online ? "online" : "offline"} /></td>
                <td title={d.last_seen_at ?? ""}>{timeAgo(d.last_seen_at)}</td>
                <td><code>{d.software_version ?? "–"}</code></td>
                <td><ConfigSync device={d} /></td>
                <td>{d.health ? `${d.health.cameras_online}/${d.health.cameras_total}` : "–"}</td>
                <td>{d.outbox_backlog ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Offline = no heartbeat for 30 s (three missed heartbeats). Refreshes every 5 s.</p>
    </>
  );
}
