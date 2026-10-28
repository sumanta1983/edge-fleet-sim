import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, orNull, unwrap, type Schemas } from "../api/client";
import { useIsOperator } from "../App";
import { Badge, ErrorBox, formatTime } from "../ui";

type SiteConfig = Schemas["SiteConfig"];
type ZoneKind = Schemas["Zone"]["kind"];
const ZONE_KINDS: ZoneKind[] = ["dock", "aisle", "forklift_lane", "gate", "walkway"];

const EMPTY: SiteConfig = { zones: [{ id: "zone-1", kind: "aisle" }], speed_limit_kmh: 10, near_miss_critical_m: 0.5 };

export default function ConfigPage() {
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => unwrap(api.GET("/sites")) });
  const [params, setParams] = useSearchParams();
  const siteId = params.get("site") ?? sites.data?.[0]?.id;

  return (
    <>
      <h1>Site config</h1>
      <ErrorBox error={sites.error} />
      <div className="row">
        <label className="inline">
          Site{" "}
          <select value={siteId ?? ""} onChange={(e) => setParams({ site: e.target.value })}>
            {sites.data?.map((s) => (
              <option key={s.id} value={s.id}>{s.name} ({s.device_count} devices)</option>
            ))}
          </select>
        </label>
      </div>
      {/* key: a fresh editor (and draft) per site */}
      {siteId && <SiteConfigEditor key={siteId} siteId={siteId} />}
    </>
  );
}

function SiteConfigEditor({ siteId }: { siteId: string }) {
  const queryClient = useQueryClient();
  const isOperator = useIsOperator();
  const sitePath = { params: { path: { site_id: siteId } } };

  // Loaded once, NOT polled: polling would silently replace the draft the
  // operator is editing. Conflicts are caught on save instead (409).
  const current = useQuery({
    queryKey: ["config", siteId],
    queryFn: () => orNull(unwrap(api.GET("/sites/{site_id}/config", sitePath))),
    refetchOnWindowFocus: false,
  });
  const history = useQuery({
    queryKey: ["config-history", siteId],
    queryFn: () => unwrap(api.GET("/sites/{site_id}/config/history", sitePath)),
  });
  const status = useQuery({
    queryKey: ["config-status", siteId],
    queryFn: () => unwrap(api.GET("/sites/{site_id}/config/status", sitePath)),
    refetchInterval: 2000,
  });

  const [draft, setDraft] = useState<SiteConfig | null>(null);
  const [baseVersion, setBaseVersion] = useState(0);

  function resetTo(loaded: Schemas["DesiredConfigOut"] | null) {
    setDraft(structuredClone(loaded?.config ?? EMPTY));
    setBaseVersion(loaded?.version ?? 0);
  }
  useEffect(() => {
    if (current.data !== undefined && draft === null) resetTo(current.data);
  }, [current.data, draft]);

  const save = useMutation({
    mutationFn: (config: SiteConfig) =>
      unwrap(api.PUT("/sites/{site_id}/config", { ...sitePath, body: { base_version: baseVersion, config } })),
    onSuccess: (result) => {
      setBaseVersion(result.desired.version);
      queryClient.setQueryData(["config", siteId], result.desired);
      for (const key of ["config-history", "config-status", "devices"]) queryClient.invalidateQueries({ queryKey: [key] });
    },
  });

  async function reloadLatest() {
    const latest = await queryClient.fetchQuery({
      queryKey: ["config", siteId],
      queryFn: () => orNull(unwrap(api.GET("/sites/{site_id}/config", sitePath))),
    });
    resetTo(latest);
    save.reset();
  }

  if (!draft) return <ErrorBox error={current.error} />;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const readOnly = !isOperator;

  function update(patch: Partial<SiteConfig>) {
    setDraft({ ...draft!, ...patch });
  }
  function updateZone(i: number, patch: Partial<Schemas["Zone"]>) {
    update({ zones: draft!.zones.map((z, j) => (j === i ? { ...z, ...patch } : z)) });
  }

  return (
    <div className="grid-2">
      <section className="card">
        <h2>Edit <span className="muted">based on v{baseVersion}</span></h2>
        {readOnly && <p className="hint">You are a viewer: config is read-only.</p>}
        <fieldset disabled={readOnly || save.isPending}>
          <label>
            Speed limit (km/h)
            <input type="number" step="0.5" value={draft.speed_limit_kmh} onChange={(e) => update({ speed_limit_kmh: Number(e.target.value) })} />
          </label>
          <label>
            Near miss is critical below (m)
            <input type="number" step="0.1" value={draft.near_miss_critical_m} onChange={(e) => update({ near_miss_critical_m: Number(e.target.value) })} />
          </label>
          <h3>Zones</h3>
          {draft.zones.map((z, i) => (
            <div className="row" key={i}>
              <input value={z.id} onChange={(e) => updateZone(i, { id: e.target.value })} aria-label="zone id" />
              <select value={z.kind} onChange={(e) => updateZone(i, { kind: e.target.value as ZoneKind })} aria-label="zone kind">
                {ZONE_KINDS.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
              <button type="button" className="link" onClick={() => update({ zones: draft.zones.filter((_, j) => j !== i) })}>Remove</button>
            </div>
          ))}
          <button type="button" className="secondary" onClick={() => update({ zones: [...draft.zones, { id: `zone-${draft.zones.length + 1}`, kind: "aisle" }] })}>
            Add zone
          </button>
          <div className="row actions">
            <button onClick={() => save.mutate(draft)}>{save.isPending ? "Saving…" : `Save as v${baseVersion + 1}`}</button>
          </div>
        </fieldset>
        {conflict ? (
          <div className="error">
            {save.error?.message} <button className="link" onClick={reloadLatest}>Load latest (discards your edits)</button>
          </div>
        ) : (
          <ErrorBox error={save.error} />
        )}
        {save.data && (
          <p className="ok">
            Saved v{save.data.desired.version} and pushed to {save.data.devices.length} device(s)
            {save.data.broker_accepted ? "." : " (broker didn't confirm yet; it will be re-sent on the next heartbeat)."}
          </p>
        )}
        <p className="hint">Devices reject a speed limit above 25 km/h (their radar can't enforce it), to demo a failed ack.</p>
      </section>

      <section className="card">
        <h2>Devices at this site</h2>
        <ErrorBox error={status.error} />
        <table>
          <thead>
            <tr><th>Device</th><th>Reported</th><th>Desired</th><th>State</th></tr>
          </thead>
          <tbody>
            {status.data?.map((s) => (
              <tr key={s.device_id}>
                <td><Link to={`/devices/${s.device_id}`}>{s.device_id}</Link></td>
                <td>{s.reported_version != null ? `v${s.reported_version}` : "–"}</td>
                <td>{s.desired_version != null ? `v${s.desired_version}` : "–"}</td>
                <td>
                  <Badge value={s.state} />
                  {s.ack_error && <div className="small bad-text">{s.ack_error}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2>History</h2>
        <ul className="history">
          {history.data?.map((h) => (
            <li key={h.version}>
              <strong>v{h.version}</strong> <span className="muted">{formatTime(h.created_at)}</span>{" "}
              {!readOnly && (
                <button className="link" onClick={() => setDraft(structuredClone(h.config))} title="Copies this version into the editor; saving creates a new version">
                  Edit from this
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
