import Form from "next/form";
import Link from "next/link";

import {
  DEFAULT_LINE, DEFAULT_N, getMatch, num,
  type MatchDetail, type RateSummary, type SearchParams,
} from "@/lib/api";
import { kickoff, pct, SET_STAT_LABELS } from "@/lib/format";

export default async function MatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: SearchParams;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const line = num(sp.line, DEFAULT_LINE);
  const n = num(sp.n, DEFAULT_N);

  const d = await getMatch(id, { line, n });
  const { match: m } = d;

  return (
    <>
      <p><Link href={`/?line=${line}&n=${n}`}>← Matches</Link></p>
      <h1>
        {m.home} vs {m.away}
      </h1>
      <p className="muted">
        {m.league} · {kickoff(m.scheduled_at)} · {m.status}
        {m.status === "finished" && (
          <>
            {" "}· {m.home_sets_won}-{m.away_sets_won} (
            {m.sets.map((s) => `${s.home_points}-${s.away_points}`).join(", ")}) · {m.total_points} points
          </>
        )}
      </p>

      <Form action={`/matches/${m.id}`} className="filters">
        <label>
          Total points line
          <input type="number" name="line" step="1" min="30.5" max="150.5" defaultValue={line} />
        </label>
        <label>
          Form window (matches)
          <input type="number" name="n" min="1" max="200" defaultValue={n} />
        </label>
        <button type="submit">Apply</button>
      </Form>

      <div className="cards">
        <Card label={`P(${m.home} wins), Elo`} value={pct(m.model.p_home_win_elo, 1)}
              sub={`${m.model.home_elo.toFixed(0)} vs ${m.model.away_elo.toFixed(0)}`} />
        <Card label="P(home wins), points model" value={pct(m.model.p_home_win_points, 1)} />
        <Card label={`P(total > ${line}), points model`} value={pct(m.model.p_over, 1)}
              sub={`expected ${m.model.expected_total.toFixed(1)}`} />
        <Card label={`League over ${line}`} value={pct(m.model.league_over_rate, 1)}
              sub={`n=${m.model.league_over_n} before kickoff`} />
        <Card label={`H2H (last ${n})`} value={`${d.h2h.home_wins}-${d.h2h.n - d.h2h.home_wins}`}
              sub={d.h2h.n ? `${m.home} first` : "no meetings"} />
      </div>

      <h2>Total points distribution (points model)</h2>
      <Distribution d={d} line={line} />

      <h2>Player stats, last {n} finished matches before kickoff</h2>
      <p className="muted small">
        rate (hits/n) · 95% Wilson CI · shrunk toward league rate. Highlighted rows have under 10 qualifying matches.
      </p>
      <StatsTable d={d} line={line} />
    </>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="muted small">{sub}</div>}
    </div>
  );
}

function Distribution({ d, line }: { d: MatchDetail; line: number }) {
  const points = d.distribution;
  if (points.length === 0) return null;
  const W = 900, H = 180, pad = 24;
  const lo = points[0].total, hi = points[points.length - 1].total;
  const maxP = Math.max(...points.map((p) => p.prob));
  const x = (t: number) => pad + ((t - lo) / (hi - lo + 1)) * (W - 2 * pad);
  const bw = (W - 2 * pad) / (hi - lo + 1);
  const ticks = points.filter((p) => p.total % 10 === 0).map((p) => p.total);
  const firstOver = Math.floor(line) + 1; // smallest total that counts as over (total > line)

  return (
    <div className="card">
      <svg className="chart" viewBox={`0 0 ${W} ${H + 20}`} role="img"
           aria-label={`Distribution of total points; line at ${line}`}>
        {points.map((p) => {
          const h = (p.prob / maxP) * H;
          return (
            <rect key={p.total} className={`bar${p.total > line ? " over" : ""}`}
                  x={x(p.total)} y={H - h} width={Math.max(bw - 1, 1)} height={h}>
              <title>{`${p.total} points: ${(p.prob * 100).toFixed(2)}%`}</title>
            </rect>
          );
        })}
        <line className="line" x1={x(firstOver)} x2={x(firstOver)} y1={0} y2={H} />
        {ticks.map((t) => (
          <text key={t} x={x(t) + bw / 2} y={H + 14} textAnchor="middle">{t}</text>
        ))}
      </svg>
      <div className="muted small">Green bars are over {line}. Hover a bar for its probability.</div>
    </div>
  );
}

function RateCells({ s }: { s: RateSummary }) {
  return (
    <>
      <td className="num">{s.rate === null ? "–" : `${pct(s.rate)} (${s.hits}/${s.n})`}</td>
      <td className="num muted small">{s.n ? `${pct(s.ci_95[0])}–${pct(s.ci_95[1])}` : ""}</td>
      <td className="num">{pct(s.shrunk_rate)}</td>
    </>
  );
}

function StatsTable({ d, line }: { d: MatchDetail; line: number }) {
  const { home_stats: h, away_stats: a } = d;
  const rows: [string, RateSummary, RateSummary][] = [
    [`Match total over ${line}`, h.over, a.over],
    ...SET_STAT_LABELS.map(([key, label]) =>
      [label, h.set_stats[key], a.set_stats[key]] as [string, RateSummary, RateSummary]),
  ];

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th rowSpan={2}>Stat</th>
            <th colSpan={3}>{h.name}</th>
            <th colSpan={3}>{a.name}</th>
            <th rowSpan={2} className="num">League</th>
          </tr>
          <tr>
            <th className="num">rate</th><th className="num">95% CI</th><th className="num">shrunk</th>
            <th className="num">rate</th><th className="num">95% CI</th><th className="num">shrunk</th>
          </tr>
        </thead>
        <tbody>
          <tr className={h.avg_total.small_sample || a.avg_total.small_sample ? "small-sample" : undefined}>
            <td>Average total points</td>
            <td className="num">{h.avg_total.mean?.toFixed(1) ?? "–"} <span className="muted small">n={h.avg_total.n}</span></td>
            <td />
            <td className="num">{h.avg_total.shrunk_mean.toFixed(1)}</td>
            <td className="num">{a.avg_total.mean?.toFixed(1) ?? "–"} <span className="muted small">n={a.avg_total.n}</span></td>
            <td />
            <td className="num">{a.avg_total.shrunk_mean.toFixed(1)}</td>
            <td className="num">{h.avg_total.league_mean.toFixed(1)}</td>
          </tr>
          {rows.map(([label, hs, as]) => (
            <tr key={label} className={hs.small_sample || as.small_sample ? "small-sample" : undefined}>
              <td>{label}</td>
              <RateCells s={hs} />
              <RateCells s={as} />
              <td className="num">{pct(hs.league_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
