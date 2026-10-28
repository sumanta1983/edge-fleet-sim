// Small shared UI pieces and formatting helpers.
import type { ReactNode } from "react";

type Tone = "good" | "bad" | "warn" | "info" | "muted";

const TONES: Record<string, Tone> = {
  online: "good",
  offline: "bad",
  in_sync: "good",
  pending: "warn",
  failed: "bad",
  no_config: "muted",
  applied: "good",
  rolling_out: "info",
  completed: "good",
  halted: "bad",
  sent: "info",
  succeeded: "good",
  rolled_back: "bad",
  timed_out: "bad",
  skipped: "muted",
  info: "muted",
  medium: "warn",
  high: "bad",
  critical: "bad",
};

export function Badge({ value, label }: { value: string; label?: ReactNode }) {
  return <span className={`badge ${TONES[value] ?? "muted"}`}>{label ?? value.replace(/_/g, " ")}</span>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: Tone }) {
  return (
    <div className="stat">
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function formatTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "–";
}

/** Grouped vertical bar chart: one group per category, one bar per series. */
export function BarChart({
  categories,
  series,
  height = 220,
}: {
  categories: string[];
  series: { name: string; values: number[] }[];
  height?: number;
}) {
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const bar = 22;
  const gap = 24;
  const group = series.length * bar + gap;
  const top = 16;
  const bottom = 28;
  const left = 36;
  const plot = height - top - bottom;
  const width = left + categories.length * group;
  const ticks = [0, Math.round(max / 2), max];

  return (
    <div className="chart">
      {/* Drawn at its natural pixel size: stretching a 1-day chart to full
          width blows the bars and labels up to giant size. Many days scroll
          sideways instead of being squashed. */}
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img">
        {ticks.map((t) => {
          const y = top + plot - (t / max) * plot;
          return (
            <g key={t}>
              <line x1={left} x2={width} y1={y} y2={y} className="grid" />
              <text x={left - 6} y={y + 4} textAnchor="end" className="axis">{t}</text>
            </g>
          );
        })}
        {categories.map((c, ci) => (
          <g key={c} transform={`translate(${left + ci * group + gap / 2},0)`}>
            {series.map((s, si) => {
              const v = s.values[ci] ?? 0;
              const h = (v / max) * plot;
              return (
                <rect key={s.name} x={si * bar} y={top + plot - h} width={bar - 2} height={h} rx={2} className={`series-${si % 4}`}>
                  <title>{`${s.name} · ${c}: ${v}`}</title>
                </rect>
              );
            })}
            <text x={(series.length * bar) / 2} y={height - 10} textAnchor="middle" className="axis">{c}</text>
          </g>
        ))}
      </svg>
      <div className="legend">
        {series.map((s, si) => (
          <span key={s.name}>
            <i className={`swatch series-${si % 4}`} /> {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
