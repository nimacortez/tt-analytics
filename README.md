# TT Analytics

## Run it

```bash
# 1. Postgres (needs Docker Desktop running)
docker compose up -d

# 2. Python env
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

# 3. Tables + 30 days of mock data
python -m app.init_db
python -m app.ingest --days 30

# 4. API
uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

## Poke the database directly

```bash
docker compose exec db psql -U tt -d tt
```
```sql
SELECT status, count(*) FROM matches GROUP BY status;

-- average total points per league, finished matches only
SELECT l.name, round(avg(t.total), 1) AS avg_total
FROM (
  SELECT match_id, sum(home_points + away_points) AS total
  FROM match_sets GROUP BY match_id
) t
JOIN matches m ON m.id = t.match_id AND m.status = 'finished'
JOIN leagues l ON l.id = m.league_id
GROUP BY l.name;
```

## Layout

```
backend/app/
  config.py          settings from .env
  db.py              engine + session
  models.py          tables
  providers/base.py  the provider contract (types + interface)
  providers/mock.py  simulated matches
  ingest.py          provider -> Postgres (upserts)
  init_db.py         create/reset tables
  main.py            FastAPI endpoints
```
