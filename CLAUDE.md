TT Analytics

Table tennis analytics and betting-research tool for four high-frequency leagues: Czech Liga Pro, Setka Cup, TT Cup, TT Elite Series. Also a learning project for AI engineering: agents/tool use, eval harnesses, observability, and judging where an LLM helps vs hurts.

Stack
Backend: Python 3.13, FastAPI, SQLAlchemy 2.0, psycopg, pydantic-settings
DB: PostgreSQL 16 in Docker (docker-compose.yml at repo root)
Frontend: Next.js 16 (App Router) + TypeScript in frontend/, plain CSS, no UI libs
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
cd frontend && npm run dev        # UI at http://localhost:3000 (needs the API running)
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
  main.py            FastAPI: /health, /leagues, /matches?line&n (model numbers per match),
                     /matches/{id}?line&n (player stats, H2H, total distribution)
backend/tests/
  conftest.py        `session` fixture: in-memory SQLite (JSONB patched to JSON)
  helpers.py         make_league / make_player / make_match(sets=[(h, a), ...]), NOW
Every stat/model function gets a leakage test: a match at exactly as_of and one after it must be ignored.
frontend/
  lib/api.ts         typed fetch client (types mirror main.py's Pydantic models; keep in sync by hand)
  app/page.tsx       match list; app/matches/[id]/page.tsx detail. Server Components, state in URL.
Design rules (do not break these)
Point-in-time correctness. Every stat/model function takes as_of and only uses matches with scheduled_at < as_of (full timestamp, not date: players play several matches a day). Backtests and the live app use the same functions.
Store what the API gives; compute the rest. Total points, winner, hit rates are computed in queries, not stored. Exception: ratings.
Ratings get their own table (player_ratings: player_id, match_id, rating_before, rating_after). Never a single "current Elo" column; that leaks the future into backtests.
Stats use status == "finished" only. Walkovers and retirements are excluded.
Return sample sizes, not bare percentages. Stats return (hits, n); display layers add Wilson intervals and shrinkage toward the league baseline.
Grain is set-level. No point-level modelling. Every raw provider payload is kept in raw_events (JSONB) so history can be reprocessed.
Providers are swappable. Only providers/ knows about external APIs; everything else sees ProviderEvent. The mock's raw payload shape is made up; real BetsAPI payloads are in backend/samples/ (see step 6).
The stats layer is never an LLM.
Where an LLM is and isn't the right tool
Deterministic pipeline (fixtures -> history -> ratings -> odds -> edge): plain functions, NOT an agent.
Natural-language queries: the LLM outputs a typed, Pydantic-validated filter object; code turns it into SQL. The LLM never writes raw SQL.
Agents only for open-ended research questions where the path genuinely varies.
Point these calls out explicitly when they come up.
Status
Step numbers below are canonical. Refer to them as "step N" in prompts and commit messages (feat(step-N): ...).

Done (all merged to main, 102 tests passing)
Step 0 - Tests. pytest on in-memory SQLite (tests/conftest.py, tests/helpers.py). Every stat/model function has a leakage test (a match at exactly as_of and one after it are ignored; retired/walkover ignored).
Step 1 - Elo (ratings.py, K=32, start 1500, full chronological rebuild into player_ratings). Spearman rho vs mock true_skill: czech 0.964, elite 0.861, setka 0.876, ttcup 0.897 (all 0.903), on data to 2026-09-28. Mock points are iid given skill, so real data will be noisier.
Step 2 - Predictions + grading (predict.py, grade.py). Markets: match_winner (probability = P(home wins), line = -1 sentinel so the unique key works) and total_over (P(total > line)). Grading is per market (grade.outcome_for); walkovers/retirements never graded. New market = a branch in outcome_for() + a test. Lines should be x.5: total == line grades as under (no push handling).
Step 3 - Set-sequence stats (stats.SET_STATS rule table): sweep, win after set 1 win/loss, win at 1-1 from 1-0, set 3 when up 2-0, set 5, and splits. Split = loser of set 1 wins set 2; three versions: split (match-level), split_after_losing_set1, split_allowed_after_winning_set1. Player version = last n finished matches, then filter to qualifying (n_qualifying <= n); league baseline = all league matches from both players' sides (n = player-matches). League-wide the three split rates are equal by construction (~0.426 on mock); only player-level versions carry information, on ~half of matches each. Plus h2h_record, avg_total_points (+ shrunk mean). New stat = one SET_STATS entry + expected value in tests/test_advanced_stats.py (a test fails if missing).
Step 4 - Points-total model (points.py). Player's share of points over last 20 matches (set scores only, shrunk to 0.5 with 50 points of prior) -> log5 -> P(home wins a point) -> exact match-total distribution (closed-form set scores + DP over best of 5, no Monte Carlo). Gives P(over) at any line and P(home wins). Design call: inputs are set-level but the model assumes iid points (point-level math); accepted deliberately. The mock generates data with exactly this assumption, so mock results flatter it.
Step 5 - UI + API. FastAPI /leagues, /matches?line&n, /matches/{id}?line&n; every model number computed with as_of = scheduled_at (same functions as backfill). Next.js 16 frontend/: match list (league/status filters, adjustable line, form window) and match detail (model cards, total distribution chart, per-player stats with CI/shrinkage/small-sample highlight). Server Components, state in the URL.

Model scoreboard (mock data, as_of = kickoff, burn-in 10 matches per player; python -m app.grade)
match_winner, n=5082: always 0.5 = 0.2500 | Elo v1 = 0.1952 | oracle = 0.1874 -> Elo gets ~87% of achievable gain. Calibration within ~3pts per decile.
total_over 74.5, n=5082: league over rate = 0.2496 | points model v1 = 0.2455 | oracle = 0.2413 -> ~50% of achievable gain. Totals are mostly noise even with perfect knowledge. Oracle is well calibrated (math verified); model tails are too extreme (p < 0.2 bins, n=124: predicted ~0.15, actual ~0.25). Shrinkage sweep (0-1000 points) didn't fix it.
Oracle (grade.py, mock only): true skills through the same exact model = best achievable Brier.

Known limitations
/matches with 100 rows ~1.7s (4-5 queries per match); league baselines load every league match in Python. First things to batch/cache if the API gets slow.
frontend/lib/api.ts types mirror main.py's Pydantic models by hand; no codegen.
No Alembic: schema changes need init_db --reset + re-ingest.

Next (in order)
Step 6 - Real data: BetsAPI provider. BETSAPI_TOKEN is in .env; real payloads are in backend/samples/ (ended.json, upcoming.json: {success, pager, results[]}, event keys id, league, home, away, time (unix), time_status, ss, scores). Write providers/betsapi.py mapping to ProviderEvent (confirm time_status codes, per-set scores format, retirement/walkover signals against BetsAPI docs, and league ids for the 4 leagues). Parser tests run against the sample files. Then backfill real history, rerun steps 1-4 on it, and record the real scoreboard next to the mock one. Oracle doesn't exist on real data.
Step 7 - NL -> typed filter. LLM outputs a Pydantic-validated filter object (league, players, date range, line, stat thresholds); code turns it into SQL; the LLM never writes SQL. Build an eval set of question -> expected filter pairs first and score exact-match on fields. Log token cost + latency per call from the first call (start of step 10).
Step 8 - Agent layer, only for open-ended research questions where the path genuinely varies. Tools = the existing plain functions (stats, ratings, points model, predictions). Not for the deterministic pipeline.
Step 9 - Odds + EV. Store odds snapshots per (match, market, line, bookmaker, fetched_at); design for missing odds. Edge = model probability - implied probability (de-vigged). Log bets-that-would-have-been-placed as predictions so closing-line value and ROI are graded like everything else.
Step 10 - Tracing: token cost, latency, per LLM/agent call. Minimal version ships with step 7; this step is the dashboard/aggregation.

Housekeeping when convenient: Alembic once the schema settles; batch upserts in ingest (currently row-by-row); batch the /matches model queries.

How to work with Nima
Full-stack engineer (TypeScript/React/Node/Postgres), rusty on backend setup, newer to Python.
Plain language, concise. Direct and casual.
Give a short "why" (a few sentences) before code, then write the code. Don't quiz him or make him answer questions before proceeding.
Give exact file paths and full code to paste/replace, plus the exact command to test it.
Be honest about trade-offs and flag anything that looks wrong, including his ideas and anything inflated or unverified.