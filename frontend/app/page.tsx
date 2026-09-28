import Form from "next/form";
import Link from "next/link";

import { DEFAULT_LINE, DEFAULT_N, getLeagues, getMatches, num, one, type Match, type SearchParams } from "@/lib/api";
import { kickoff, pct } from "@/lib/format";

const STATUSES = [
  ["scheduled", "Upcoming"],
  ["finished", "Finished"],
  ["", "All"],
] as const;

export default async function MatchesPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const leagueId = one(sp.league_id) ?? "";
  const status = one(sp.status) ?? "scheduled";
  const line = num(sp.line, DEFAULT_LINE);
  const n = num(sp.n, DEFAULT_N);

  const [leagues, matches] = await Promise.all([
    getLeagues(),
    getMatches({ league_id: leagueId, status, line, n, limit: 100 }),
  ]);

  const finished = matches.filter((m) => m.went_over !== null);
  const overs = finished.filter((m) => m.went_over).length;
  const meanPOver = finished.reduce((s, m) => s + m.model.p_over, 0) / (finished.length || 1);
  const detailQs = `?line=${line}&n=${n}`;

  return (
    <>
      <h1>Matches</h1>

      <Form action="/" className="filters">
        <label>
          League
          <select name="league_id" defaultValue={leagueId}>
            <option value="">All leagues</option>
            {leagues.map((lg) => (
              <option key={lg.id} value={lg.id}>{lg.name}</option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select name="status" defaultValue={status}>
            {STATUSES.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>
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

      {finished.length > 0 && (
        <p className="muted">
          Finished matches shown: {overs}/{finished.length} went over {line} ({pct(overs / finished.length)}); the model
          averaged {pct(meanPOver)} P(over).
        </p>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Kickoff</th>
              <th>League</th>
              <th>Home</th>
              <th>Away</th>
              <th className="num" title="Elo: P(home wins)">P(home) Elo</th>
              <th className="num" title="Points model: P(home wins)">P(home) pts</th>
              <th className="num" title="Points model expected total">Exp. total</th>
              <th className="num" title="Points model P(total > line)">P(over {line})</th>
              <th className="num" title="League over rate at this line, before kickoff">League over</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {matches.map((m) => (
              <MatchRow key={m.id} m={m} href={`/matches/${m.id}${detailQs}`} />
            ))}
            {matches.length === 0 && (
              <tr>
                <td colSpan={10} className="muted">No matches. Run the ingest, or change the filters.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function MatchRow({ m, href }: { m: Match; href: string }) {
  const homeFav = m.model.p_home_win_elo >= 0.5;
  return (
    <tr>
      <td className="num"><Link href={href}>{kickoff(m.scheduled_at)}</Link></td>
      <td className="muted">{m.league}</td>
      <td className={homeFav ? "fav" : undefined}>
        {m.home} <span className="muted small">{m.model.home_elo.toFixed(0)}</span>
      </td>
      <td className={!homeFav ? "fav" : undefined}>
        {m.away} <span className="muted small">{m.model.away_elo.toFixed(0)}</span>
      </td>
      <td className="num">{pct(m.model.p_home_win_elo)}</td>
      <td className="num">{pct(m.model.p_home_win_points)}</td>
      <td className="num">{m.model.expected_total.toFixed(1)}</td>
      <td className="num">{pct(m.model.p_over, 1)}</td>
      <td className="num">
        {pct(m.model.league_over_rate)} <span className="muted small">n={m.model.league_over_n}</span>
      </td>
      <td><Result m={m} /></td>
    </tr>
  );
}

function Result({ m }: { m: Match }) {
  if (m.status === "scheduled") return <span className="muted">–</span>;
  if (m.status !== "finished") return <span className="muted">{m.status}</span>;
  return (
    <span className="num">
      {m.home_sets_won}-{m.away_sets_won} · {m.total_points}{" "}
      <span className={`badge ${m.went_over ? "over" : "under"}`}>{m.went_over ? "O" : "U"}</span>
    </span>
  );
}
