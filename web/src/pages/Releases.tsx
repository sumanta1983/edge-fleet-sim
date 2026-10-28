import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useIsOperator } from "../App";
import { Badge, ErrorBox, formatTime } from "../ui";

type TargetStatus = Schemas["TargetOut"]["status"];
const ORDER: TargetStatus[] = ["succeeded", "sent", "pending", "rolled_back", "failed", "timed_out", "skipped"];

function ProgressBar({ progress }: { progress: Schemas["ReleaseOut"]["progress"] }) {
  const counts = progress as Partial<Record<TargetStatus, number>>;
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0) || 1;
  return (
    <div className="progress" title={ORDER.filter((s) => counts[s]).map((s) => `${s}: ${counts[s]}`).join(", ")}>
      {ORDER.map((s) =>
        counts[s] ? <span key={s} className={`seg ${s}`} style={{ width: `${(100 * counts[s]!) / total}%` }} /> : null,
      )}
    </div>
  );
}

function ReleaseCard({ release, open }: { release: Schemas["ReleaseOut"]; open: boolean }) {
  const queryClient = useQueryClient();
  const isOperator = useIsOperator();
  const [expanded, setExpanded] = useState(open);
  const active = release.status === "rolling_out";

  const detail = useQuery({
    queryKey: ["release", release.id],
    queryFn: () => unwrap(api.GET("/releases/{release_id}", { params: { path: { release_id: release.id } } })),
    enabled: expanded,
    refetchInterval: active ? 1500 : false,
  });
  const halt = useMutation({
    mutationFn: () => unwrap(api.POST("/releases/{release_id}/halt", { params: { path: { release_id: release.id } } })),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["releases"] }),
  });

  return (
    <section className="card">
      <header className="row">
        <h2>
          #{release.id} <code>{release.version}</code>
        </h2>
        <Badge value={release.status} />
        <span className="muted">{release.site_id ?? "all sites"} · {formatTime(release.created_at)}</span>
        <span className="spacer" />
        {active && isOperator && (
          <button className="danger" onClick={() => halt.mutate()} disabled={halt.isPending}>Halt rollout</button>
        )}
        <button className="link" onClick={() => setExpanded(!expanded)}>{expanded ? "Hide" : "Details"}</button>
      </header>
      <ProgressBar progress={release.progress} />
      {release.halt_reason && <div className="error">Halted: {release.halt_reason}</div>}
      <ErrorBox error={halt.error ?? detail.error} />
      {expanded && detail.data && (
        <table>
          <thead>
            <tr><th>Wave</th><th>Device</th><th>From</th><th>Status</th><th>Sent</th><th>Finished</th></tr>
          </thead>
          <tbody>
            {detail.data.targets.map((t) => (
              <tr key={t.device_id}>
                <td>{t.wave === 0 ? <strong>canary</strong> : "wave 1"}</td>
                <td><Link to={`/devices/${t.device_id}`}>{t.device_id}</Link></td>
                <td><code>{t.from_version ?? "–"}</code></td>
                <td>
                  <Badge value={t.status} />
                  {t.error && <div className="small bad-text">{t.error}</div>}
                </td>
                <td>{formatTime(t.sent_at)}</td>
                <td>{formatTime(t.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function NewRelease() {
  const queryClient = useQueryClient();
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => unwrap(api.GET("/sites")) });
  const [version, setVersion] = useState("");
  const [siteId, setSiteId] = useState("");

  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/releases", { body: { version, site_id: siteId || null } })),
    onSuccess: () => {
      setVersion("");
      queryClient.invalidateQueries({ queryKey: ["releases"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New release</h2>
      <div className="row">
        <input placeholder="version, e.g. 2.1.0" value={version} onChange={(e) => setVersion(e.target.value)} required />
        <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
          <option value="">All sites</option>
          {sites.data?.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <button type="submit" disabled={create.isPending}>Start rollout</button>
      </div>
      <ErrorBox error={create.error} />
      <p className="hint">
        One online device gets it first (canary); the rest only after the canary passes its health check. For demos:
        a version containing <code>bad</code> fails the health check, <code>corrupt</code> fails the checksum.
      </p>
    </form>
  );
}

export default function Releases() {
  const isOperator = useIsOperator();
  const releases = useQuery({
    queryKey: ["releases"],
    queryFn: () => unwrap(api.GET("/releases")),
    // Poll fast only while something is happening.
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "rolling_out") ? 1500 : 10000),
  });

  return (
    <>
      <h1>Releases</h1>
      {isOperator && <NewRelease />}
      <ErrorBox error={releases.error} />
      {releases.data?.length === 0 && <p className="muted">No releases yet.</p>}
      {releases.data?.map((r, i) => <ReleaseCard key={r.id} release={r} open={i === 0} />)}
    </>
  );
}
