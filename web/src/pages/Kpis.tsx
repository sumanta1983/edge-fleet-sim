import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { BarChart, ErrorBox } from "../ui";

export default function Kpis() {
  const [days, setDays] = useState(7);
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => unwrap(api.GET("/sites")) });
  const nearMisses = useQuery({
    queryKey: ["kpi-near-misses", days],
    queryFn: () => unwrap(api.GET("/kpis/near-misses", { params: { query: { days } } })),
    refetchInterval: 30000,
  });
  const ppe = useQuery({
    queryKey: ["kpi-ppe", days],
    queryFn: () => unwrap(api.GET("/kpis/ppe-compliance", { params: { query: { days } } })),
    refetchInterval: 30000,
  });

  const siteName = (id: string) => sites.data?.find((s) => s.id === id)?.name ?? id;
  const nm = nearMisses.data ?? [];
  const nmDays = [...new Set(nm.map((r) => r.day))].sort();
  const nmSites = [...new Set(nm.map((r) => r.site_id))].sort();

  return (
    <>
      <header className="row">
        <h1>Safety KPIs</h1>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30].map((d) => (
            <option key={d} value={d}>Last {d} days</option>
          ))}
        </select>
      </header>
      <p className="hint">Days and shifts are in each site's local time zone.</p>

      <section className="card">
        <h2>Near misses per day</h2>
        <ErrorBox error={nearMisses.error} />
        {nm.length === 0 ? (
          <p className="muted">No near misses in this period.</p>
        ) : (
          <BarChart
            categories={nmDays.map((d) => d.slice(5))}
            series={nmSites.map((s) => ({
              name: siteName(s),
              values: nmDays.map((d) => nm.find((r) => r.site_id === s && r.day === d)?.near_misses ?? 0),
            }))}
          />
        )}
      </section>

      <section className="card">
        <h2>PPE compliance per shift</h2>
        <ErrorBox error={ppe.error} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Site</th><th>Day</th><th>Shift</th><th>Gate checks</th><th>Compliance</th><th>Violations in site</th></tr>
            </thead>
            <tbody>
              {ppe.data?.map((r) => (
                <tr key={`${r.site_id}-${r.day}-${r.shift}`}>
                  <td>{siteName(r.site_id)}</td>
                  <td>{r.day}</td>
                  <td>{r.shift}</td>
                  <td>{r.checks}</td>
                  <td>
                    {r.compliance_pct == null ? (
                      <span className="muted">no checks</span>
                    ) : (
                      <span className="meter">
                        <span className="track">
                          <span className={r.compliance_pct >= 90 ? "good" : "bad"} style={{ width: `${r.compliance_pct}%` }} />
                        </span>
                        {r.compliance_pct}%
                      </span>
                    )}
                  </td>
                  <td>{r.violations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">Compliance = people who passed the PPE check at a gate ÷ all gate checks. Shifts: early 06–14, late 14–22, night 22–06.</p>
      </section>
    </>
  );
}
