// Typed client for the FastAPI backend. Server-side only (called from Server Components).
// Types mirror the Pydantic models in backend/app/main.py.

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export const DEFAULT_LINE = 74.5;
export const DEFAULT_N = 20;

export type League = { id: number; name: string };

export type SetScore = { set_number: number; home_points: number; away_points: number };

export type Model = {
  line: number;
  home_elo: number;
  away_elo: number;
  p_home_win_elo: number;
  p_home_win_points: number;
  p_over: number;
  expected_total: number;
  league_over_rate: number | null;
  league_over_n: number;
};

export type Match = {
  id: number;
  league_id: number;
  league: string;
  home_id: number;
  away_id: number;
  home: string;
  away: string;
  scheduled_at: string;
  status: string;
  home_sets_won: number | null;
  away_sets_won: number | null;
  sets: SetScore[];
  total_points: number | null;
  went_over: boolean | null;
  model: Model;
};

// summarize() in stats.py
export type RateSummary = {
  hits: number;
  n: number;
  rate: number | null;
  ci_95: [number, number];
  shrunk_rate: number;
  small_sample: boolean;
  league_rate: number;
};

export type MeanSummary = {
  n: number;
  mean: number | null;
  shrunk_mean: number;
  league_mean: number;
  small_sample: boolean;
};

export type PlayerStats = {
  player_id: number;
  name: string;
  over: RateSummary;
  avg_total: MeanSummary;
  set_stats: Record<string, RateSummary>;
};

export type MatchDetail = {
  match: Match;
  n: number;
  h2h: { home_wins: number; n: number };
  home_stats: PlayerStats;
  away_stats: PlayerStats;
  distribution: { total: number; prob: number }[];
};

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const url = `${API_URL}${path}${qs.size ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} from ${url}`);
  return res.json() as Promise<T>;
}

export const getLeagues = () => get<League[]>("/leagues");

export const getMatches = (p: { league_id?: string; status?: string; line: number; n: number; limit?: number }) =>
  get<Match[]>("/matches", p);

export const getMatch = (id: string, p: { line: number; n: number }) =>
  get<MatchDetail>(`/matches/${encodeURIComponent(id)}`, p);

// ---- search param parsing (shared by pages) ----

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export function num(v: string | string[] | undefined, fallback: number): number {
  const s = one(v);
  if (s === undefined || s.trim() === "") return fallback;
  const n = Number(s);
  return Number.isFinite(n) ? n : fallback;
}
