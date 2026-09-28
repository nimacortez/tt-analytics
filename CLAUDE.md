TT Analytics

Table tennis analytics and betting-research tool for four high-frequency leagues: Czech Liga Pro, Setka Cup, TT Cup, TT Elite Series. Also a learning project for AI engineering: agents/tool use, eval harnesses, observability, and judging where an LLM helps vs hurts.

Stack
Backend: Python 3.13, FastAPI, SQLAlchemy 2.0, psycopg, pydantic-settings
DB: PostgreSQL 16 in Docker (docker-compose.yml at repo root)
Frontend: Next.js + TypeScript (not built yet)
No Redis until a real slow query justifies it
Commands (run from backend/ with .venv active)
bash
docker compose up -d              # from repo root; Docker Desktop must be running
python -m app.init_db             # create tables (--reset to drop first)
python -m app.ingest --days 30    # load mock data
python -m app.ratings --check     # rebuild Elo; print Spearman rho vs mock true_skill
python -m app.predict --backfill  # log predictions for past finished matches (as_of = kickoff)
python -m app.predict             # log predictions for scheduled matches (as_of = now)
python -m app.grade               # grade finished matches; print Brier + calibration
uvicorn app.main:app --reload     # API at http://localhost:8000/docs
python -m pytest -q               # tests run on in-memory SQLite, no Docker needed
Layout
backend/app/
  config.py          settings from .env (DATA_PROVIDER=mock|betsapi)
  db.py              engine, SessionLocal, Base
  models.py          leagues, players, matches, match_sets, player_ratings, raw_events
  providers/base.py  provider contract: ProviderEvent etc. + DataProvider Protocol
  providers/mock.py  point-by-point simulation with hidden per-player skill
  ingest.py          provider -> Postgres via upserts keyed on api_id
  stats.py           wilson_interval, shrunk_rate, summarize; over/under; SET_STATS rule table
                     (sweep, set conditionals, 3 split variants) via player_set_stat /
                     league_set_stat / set_stat_summary; h2h_record; avg_total_points (+ summary)
  ratings.py         Elo: rebuild() full chronological recompute, rating_as_of(), p_win()
  points.py          points-total model: exact P(total > line) + P(home wins) from P(home wins a point)
  predict.py         log_* functions write one predictions row per (match, market, line, model_version)
  grade.py           outcome_for() per market, grade_predictions(), brier_score(), calibration_table()
  main.py            FastAPI: /health, /matches
backend/tests/
  conftest.py        `session` fixture: in-memory SQLite (JSONB patched to JSON)
  helpers.py         make_league / make_player / make_match(sets=[(h, a), ...]), NOW
Every stat/model function gets a leakage test: a match at exactly as_of and one after it must be ignored.
Design rules (do not break these)
Point-in-time correctness. Every stat/model function takes as_of and only uses matches with scheduled_at < as_of (full timestamp, not date: players play several matches a day). Backtests and the live app use the same functions.
Store what the API gives; compute the rest. Total points, winner, hit rates are computed in queries, not stored. Exception: ratings.
Ratings get their own table (player_ratings: player_id, match_id, rating_before, rating_after). Never a single "current Elo" column; that leaks the future into backtests.
Stats use status == "finished" only. Walkovers and retirements are excluded.
Return sample sizes, not bare percentages. Stats return (hits, n); display layers add Wilson intervals and shrinkage toward the league baseline.
Grain is set-level. No point-level modelling. Every raw provider payload is kept in raw_events (JSONB) so history can be reprocessed.
Providers are swappable. Only providers/ knows about external APIs; everything else sees ProviderEvent. The mock's raw payload shape is made up; the real BetsAPI shape must be checked against their docs when the key arrives.
The stats layer is never an LLM.
Where an LLM is and isn't the right tool
Deterministic pipeline (fixtures -> history -> ratings -> odds -> edge): plain functions, NOT an agent.
Natural-language queries: the LLM outputs a typed, Pydantic-validated filter object; code turns it into SQL. The LLM never writes raw SQL.
Agents only for open-ended research questions where the path genuinely varies.
Point these calls out explicitly when they come up.
Status

Done: schema, provider interface, mock provider, ingestion, /matches API, over/under hit rate with Wilson CI + shrinkage (over_summary, prior_strength=10 is a guess).
pytest suite (tests/). Elo (ratings.py, K=32, start 1500): Spearman rho vs true_skill on 30 days of mock data = czech 0.930, elite 0.906, setka 0.850, ttcup 0.889. Mock points are iid given skill, so real data will be noisier.
Predictions table + logging + grading. Markets: match_winner (probability = P(home wins), line = -1 sentinel so the unique key works) and total_over (P(total > line)). Grading is per market; walkovers/retirements are never graded. Elo v1 match_winner: Brier 0.1955 vs 0.25 coin flip, n=4277, calibration within ~3pts per decile. New market = add a branch in grade.outcome_for() + a test.
Set-sequence stats: each is a rule (sets won in order, won match) -> None/hit/miss in stats.SET_STATS. Player version = last n finished matches, then filter to qualifying ones (so n_qualifying <= n); league baseline = all league matches from both players' sides (n = player-matches). Split = loser of set 1 wins set 2. Three versions: split (match-level), split_after_losing_set1, split_allowed_after_winning_set1. League-wide all three rates are equal by construction (~0.426 on mock); only the player-level versions carry information, and each qualifies on ~half of matches, so samples are small. New stat = one entry in SET_STATS + expected value in tests/test_advanced_stats.py (a test fails if it's missing).
League baselines load every league match in Python; fine at mock scale, first candidate for SQL/caching if the API gets slow.
Points-total model (points.py). Each player's share of points over last 20 matches (set scores only, shrunk toward 0.5 with 50 points of prior) -> log5 -> P(home wins a point) -> exact distribution of match total (closed-form set scores + DP over best of 5; no Monte Carlo). Gives P(over) at any line and P(home wins). Design call: inputs are set-level, but the model assumes points are iid, which is point-level math; accepted deliberately. The mock generates data with exactly this assumption, so mock results flatter it; expect worse on real data.
Totals results (line 74.5, n=4277): league over rate 0.2495, points model 0.2446, oracle 0.2406. Totals are mostly noise even with perfect knowledge; the model gets ~55% of the achievable gain. Oracle is well calibrated (math verified); model tails (p < 0.2, n~112) are too extreme. Shrinkage sweep (0/50/300/1000 points) didn't help, in-sample.
Oracle (grade.py, mock only): true skills through the same exact model. Elo match_winner: 0.25 / 0.1955 / oracle 0.1873, so Elo gets ~87% of the achievable gain.

Next (in order)
Next.js UI (match list, filters by league, adjustable line).
NL -> typed filter.
Agent layer (open-ended questions only).
Odds + EV (odds absent at first; design for missing odds).
Tracing: token cost, latency.

Housekeeping when convenient: Alembic once the schema settles; batch upserts in ingest (currently row-by-row).

How to work with Nima
Full-stack engineer (TypeScript/React/Node/Postgres), rusty on backend setup, newer to Python.
Plain language, concise. Direct and casual.
Give a short "why" (a few sentences) before code, then write the code. Don't quiz him or make him answer questions before proceeding.
Give exact file paths and full code to paste/replace, plus the exact command to test it.
Be honest about trade-offs and flag anything that looks wrong, including his ideas and anything inflated or unverified.