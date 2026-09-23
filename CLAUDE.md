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
uvicorn app.main:app --reload     # API at http://localhost:8000/docs
Layout
backend/app/
  config.py          settings from .env (DATA_PROVIDER=mock|betsapi)
  db.py              engine, SessionLocal, Base
  models.py          leagues, players, matches, match_sets, raw_events
  providers/base.py  provider contract: ProviderEvent etc. + DataProvider Protocol
  providers/mock.py  point-by-point simulation with hidden per-player skill
  ingest.py          provider -> Postgres via upserts keyed on api_id
  stats.py           over_hit_rate, league_over_rate, wilson_interval, shrunk_rate, over_summary
  main.py            FastAPI: /health, /matches
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

Next (in order)
Elo: player_ratings table, computed chronologically. Validate against the mock's true_skill() (rating order should correlate strongly with hidden skill).
predictions table + logging, started NOW rather than at the end: what was predicted, model probability, model version, inputs/as_of, created_at; graded later against results. Brier score + calibration from day one.
More stats with the same as_of + shrinkage pattern: H2H, sweeps %, splits %, set conditionals (win % if up 1-0, set 3 sweep when up 2-0, set 5 at 2-2), avg total points.
Points-total distribution model (for P(over line) at any line).
Next.js UI (match list, filters by league, adjustable line).
NL -> typed filter.
Agent layer (open-ended questions only).
Odds + EV (odds absent at first; design for missing odds).
Tracing: token cost, latency.

Housekeeping when convenient: Alembic once the schema settles; batch upserts in ingest (currently row-by-row); pytest tests for stats functions against known mock data.

How to work with Nima
Full-stack engineer (TypeScript/React/Node/Postgres), rusty on backend setup, newer to Python.
Plain language, concise. Direct and casual.
Give a short "why" (a few sentences) before code, then write the code. Don't quiz him or make him answer questions before proceeding.
Give exact file paths and full code to paste/replace, plus the exact command to test it.
Be honest about trade-offs and flag anything that looks wrong, including his ideas and anything inflated or unverified.