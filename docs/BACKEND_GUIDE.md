# TT Analytics backend: a learning guide

This guide walks through the backend as it exists in the repo today (`backend/`). It's written for someone who knows programming (JavaScript/TypeScript) but is new to Python, FastAPI, SQLAlchemy, Postgres and Docker.

Every code excerpt is copied from the real file named above it. If the code and this guide ever disagree, the code wins; please fix the guide.

Contents:

1. [Big picture](#1-big-picture)
2. [The tools, from zero](#2-the-tools-from-zero)
3. [File-by-file walkthrough](#3-file-by-file-walkthrough)
4. [Key design ideas](#4-key-design-ideas)
5. [Running everything from scratch](#5-running-everything-from-scratch)
6. [How to find things](#6-how-to-find-things)
7. [Current results, and what they do and don't prove](#7-current-results-and-what-they-do-and-dont-prove)
8. [Glossary](#8-glossary)

---

## 1. Big picture

The project collects table tennis matches from four high-frequency leagues (Czech Liga Pro, Setka Cup, TT Cup, TT Elite Series). It computes stats and model probabilities for each match: who wins, and whether the total points go over a line. It serves all of that over an HTTP API to a Next.js frontend.

Right now the data comes from a **mock provider**, a simulator that plays fake matches point by point. A real data source (BetsAPI) will replace it later, and the code is structured so that swap touches one file.

How data flows:

```
 ┌──────────────┐   ProviderEvent    ┌───────────┐   upserts    ┌──────────────────────┐
 │  Provider    │ ─────────────────▶ │ ingest.py │ ───────────▶ │  Postgres (Docker)   │
 │ mock / later │  (typed Python     └───────────┘              │  leagues, players,   │
 │  BetsAPI     │   objects)               │                    │  matches, match_sets,│
 └──────────────┘                          └── raw payload ───▶ │  raw_events          │
                                                                └──────────┬───────────┘
                                                                           │ reads (as_of)
               ┌───────────────────────────────────────────────────────────┤
               ▼                         ▼                                 ▼
        ┌─────────────┐          ┌───────────────┐                ┌────────────────┐
        │  stats.py   │          │  ratings.py   │ ─ writes ─▶    │ player_ratings │
        │  hit rates, │          │  Elo rebuild  │                └────────────────┘
        │  set stats  │          └───────────────┘
        └─────────────┘          ┌───────────────┐   ┌────────────┐   ┌──────────────┐
               │                 │  points.py    │──▶│ predict.py │──▶│ predictions  │
               │                 │  totals model │   └────────────┘   └──────┬───────┘
               │                 └───────────────┘                           │
               │                         │                            grade.py (Brier,
               ▼                         ▼                             calibration)
        ┌──────────────────────────────────────┐
        │  main.py (FastAPI)  /leagues /matches │  JSON over HTTP
        └──────────────────────────────────────┘ ─────────────────▶  Next.js frontend
```

In words:

1. A **provider** returns matches as `ProviderEvent` objects. Nothing outside `providers/` ever sees the provider's raw JSON shape.
2. **`ingest.py`** writes them into **Postgres**. It *upserts*: it inserts new matches and updates known ones. It also keeps every raw payload in `raw_events`.
3. **`ratings.py`** replays all finished matches in time order and stores Elo ratings per match.
4. **`stats.py`** and **`points.py`** compute numbers for a match, using only matches that happened before it.
5. **`predict.py`** writes model probabilities into a `predictions` table. **`grade.py`** later marks them right or wrong and scores the model.
6. **`main.py`** exposes it all as a JSON API. The frontend (in `frontend/`, not covered here) calls it.

---

## 2. The tools, from zero

### Docker and docker-compose

**What it is.** Docker runs software in *containers*: isolated mini-environments with everything a program needs. Instead of installing Postgres on your Mac, you run the official Postgres image in a container. `docker compose` reads a YAML file that describes which containers to run and how.

**Why here.** Everyone gets the same Postgres 16 with one command, and deleting it doesn't leave junk on your machine.

`docker-compose.yml` (repo root):

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: tt
      POSTGRES_PASSWORD: tt
      POSTGRES_DB: tt
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

- `image: postgres:16` is the official Postgres 16 image, downloaded the first time.
- `environment` sets the user, password and database name the container creates on first start: all `tt`.
- `ports: "5432:5432"` maps port 5432 on your Mac to 5432 inside the container. Your Python code connects to `localhost:5432`.
- `volumes: pgdata:...` stores the database files in a named Docker volume, so **data survives** stopping and restarting the container. It only goes away if you delete the volume (`docker compose down -v`).

Commands (from the repo root; Docker Desktop must be running):

```bash
docker compose up -d                          # start Postgres in the background (-d = detached)
docker compose ps                             # is it running?
docker compose logs db                        # its log output
docker compose exec db psql -U tt -d tt       # open a SQL prompt inside the container
docker compose down                           # stop it (data kept)
docker compose down -v                        # stop it AND delete the data volume
```

### Postgres

**What it is.** A relational database: tables with typed columns, SQL queries, transactions, constraints (unique, foreign keys).

**Why here.** The data is relational: matches reference players and leagues, and sets belong to matches. We need fast "last N matches before time T" queries. Postgres also has `JSONB`, a binary JSON column type, which we use to store raw provider payloads as they arrived.

Try it:

```bash
docker compose exec db psql -U tt -d tt
```

```sql
\dt                                               -- list tables
SELECT status, count(*) FROM matches GROUP BY status;
SELECT * FROM match_sets WHERE match_id = 1;
\q                                                -- quit
```

### Python virtual environments and pip

**What it is.** A *virtual environment* (venv) is a folder (`backend/.venv/`) holding its own Python interpreter and its own installed packages. It's the Python version of a project-local `node_modules`, except it also pins the interpreter. `pip` is the package installer, like `npm install`. `requirements.txt` is the package list, like the `dependencies` in `package.json`, but without a lockfile.

**Why here.** Without a venv, `pip install` puts packages into your system Python, and projects collide. On this Mac there's a sharper reason: `python` on the PATH is **Python 2.7**, which can't run this code at all (see [section 5](#common-errors-and-fixes)).

`backend/requirements.txt`:

```
fastapi
uvicorn[standard]
sqlalchemy>=2.0
psycopg[binary]
pydantic>=2
pydantic-settings
scipy
pytest
httpx
```

| Package | Role |
|---|---|
| `fastapi` | web framework for the API |
| `uvicorn` | the server process that runs FastAPI (like `node server.js`) |
| `sqlalchemy` | database toolkit / ORM |
| `psycopg[binary]` | the actual Postgres driver SQLAlchemy talks through |
| `pydantic` | typed data classes with validation (like Zod) |
| `pydantic-settings` | reads settings from env vars / `.env` using pydantic |
| `scipy` | only used for the Spearman correlation check in `ratings.py` |
| `pytest` | test runner |
| `httpx` | HTTP client that FastAPI's `TestClient` needs for API tests |

Commands (from `backend/`):

```bash
python3 -m venv .venv            # create the venv (once). Note python3, not python
source .venv/bin/activate        # "enter" it: now `python` and `pip` mean the venv's
python --version                 # should say 3.13.x, not 2.7
pip install -r requirements.txt  # install packages into the venv
deactivate                       # leave the venv
```

`python -m app.something` means "run the module `app/something.py` as a script". The `-m` form makes `app` importable as a package, which is why every command is run from `backend/`.

### .env files and settings

**What it is.** A `.env` file holds per-machine configuration as `KEY=value` lines. It's not committed to git (`.gitignore` excludes it) because it can contain secrets.

`backend/.env.example` (committed, as a template):

```
DATABASE_URL=postgresql+psycopg://tt:tt@localhost:5432/tt
DATA_PROVIDER=mock
BETSAPI_TOKEN=
```

`backend/app/config.py` turns that into a typed object:

```python
class Settings(BaseSettings):
    """Reads from environment variables / .env. Field names map to UPPER_CASE env vars."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://tt:tt@localhost:5432/tt"
    data_provider: str = "mock"  # "mock" now, "betsapi" once the key arrives
    betsapi_token: str | None = None


settings = Settings()
```

- `BaseSettings` (from pydantic-settings) fills each field from the environment variable with the same name in UPPER_CASE, falling back to `.env`, then to the default written here.
- `env_file=".env"` is a *relative* path, so it's looked up in the directory you run the command from. That's another reason to run everything from `backend/`.
- `settings = Settings()` runs once when the module is first imported. Other files do `from app.config import settings`.
- In `DATABASE_URL`, `postgresql+psycopg://user:password@host:port/dbname` tells SQLAlchemy to use the psycopg driver.

### SQLAlchemy: models, sessions, queries

**What it is.** SQLAlchemy is Python's main database toolkit. We use its **ORM** (object-relational mapper): you declare a Python class per table, and rows come back as objects. It's similar to Prisma or TypeORM, with the schema written in Python instead of a separate schema file.

**Engine and sessions.** `backend/app/db.py`:

```python
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass
```

- The **engine** owns the connection pool to Postgres. There's one per process.
- A **session** is a unit of work: you query through it, add or change objects, then `commit()` to write everything in one transaction. `SessionLocal()` creates a new session. The pattern everywhere is:

  ```python
  with SessionLocal() as session:   # opens; closes automatically at the end of the block
      ...
      session.commit()              # nothing is saved until you commit
  ```

- `Base` is the parent class every table class inherits from, so SQLAlchemy knows about all of them (`Base.metadata`).

**Models (tables).** From `backend/app/models.py`:

```python
class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[str] = mapped_column(String, unique=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    home_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    away_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # scheduled / live / finished / cancelled / walkover / retired
    status: Mapped[str] = mapped_column(String(20), index=True)
    home_sets_won: Mapped[int | None]
    away_sets_won: Mapped[int | None]
    ...
    sets: Mapped[list["MatchSet"]] = relationship(
        back_populates="match", order_by="MatchSet.set_number", cascade="all, delete-orphan"
    )
```

- `Mapped[int]` is a type annotation that also declares a column. `int | None` makes the column nullable.
- `mapped_column(...)` adds details: primary key, unique, foreign key, index.
- `ForeignKey("players.id")` means this column must hold an existing `players.id`.
- `relationship(...)` isn't a column. It lets you write `match.sets` and get a list of `MatchSet` objects. `order_by` keeps them in set order.

**Queries.** SQLAlchemy 2.0 builds queries with `select()`, chaining methods like a query builder. From `backend/app/stats.py`:

```python
stmt = (
    select(Match)
    .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
    .where(Match.status == "finished")
    .where(Match.scheduled_at < as_of)
    .order_by(Match.scheduled_at.desc())
    .limit(n)
    .options(selectinload(Match.sets))
)
matches = session.scalars(stmt).all()
```

- `Match.status == "finished"` doesn't evaluate to True/False in Python. SQLAlchemy overloads `==` to build a SQL condition.
- Chained `.where()` calls are ANDed together. `or_(...)` builds an OR.
- `.options(selectinload(Match.sets))` loads all these matches' sets in **one** extra query. Without it, touching `m.sets` in a loop would fire one query per match (the classic "N+1 queries" problem).
- `session.scalars(stmt).all()` runs the query and returns a list of `Match` objects.

For aggregates, `session.execute(stmt).one()` returns a row tuple, as in `league_over_rate` (see [section 3](#backendappstatspy)).

**Creating tables.** `backend/app/init_db.py` calls `Base.metadata.create_all(engine)`. That creates any tables that don't exist yet. It does **not** alter existing tables; that's what migrations are for (see [glossary](#8-glossary)). We don't use migrations yet, so a schema change means `python -m app.init_db --reset`, which drops everything.

### FastAPI: routes, dependencies, response models, /docs

**What it is.** A Python web framework, comparable to Express but built around type hints. You declare a function's parameters with types, and FastAPI parses and validates query parameters, path parameters and bodies from them.

**Why here.** It's fast to write, it validates input for free, and it generates interactive API docs.

A route, from `backend/app/main.py`:

```python
@app.get("/matches/{match_id}", response_model=MatchDetailOut)
def match_detail(
    match_id: int,
    line: float = DEFAULT_LINE,
    n: int = Query(DEFAULT_N, ge=1, le=200),
    session: Session = Depends(get_session),
):
    m = session.scalars(_match_query().where(Match.id == match_id)).first()
    if m is None:
        raise HTTPException(404, "match not found")
```

- `@app.get("/matches/{match_id}")` registers a GET handler. `{match_id}` in the path becomes the `match_id: int` parameter. FastAPI converts it to an int and returns 422 if it isn't one.
- Parameters not in the path become **query parameters**: `?line=80.5&n=30`. The default value is used when a parameter is missing.
- `Query(DEFAULT_N, ge=1, le=200)` adds validation: `n` must be between 1 and 200.
- `raise HTTPException(404, ...)` returns an HTTP 404 with that message.

**Dependencies.** `session: Session = Depends(get_session)` tells FastAPI to call `get_session()` and pass in what it yields:

```python
def get_session():
    with SessionLocal() as session:
        yield session
```

The function pauses at `yield` while the request is handled, then continues and closes the session. Each request gets its own session. Tests swap this dependency for one that uses a test database (see [pytest](#pytest)).

**Response models.** `response_model=MatchDetailOut` names a pydantic class:

```python
class LeagueOut(BaseModel):
    id: int
    name: str
```

FastAPI validates the handler's return value against the class and serializes it to JSON. That keeps the API's output shape explicit and stops internal fields leaking by accident. The frontend's TypeScript types in `frontend/lib/api.ts` mirror these classes by hand.

**/docs.** Run the server and open http://localhost:8000/docs. FastAPI generates an interactive page listing every endpoint, its parameters and its response shape, and you can send requests from it.

```bash
uvicorn app.main:app --reload    # from backend/; "app.main:app" = module app/main.py, variable `app`
                                 # --reload restarts on code changes (dev only)
```

### pytest

**What it is.** Python's standard test runner, comparable to Jest or Vitest. Any function named `test_*` in a file named `test_*.py` is a test, and a plain `assert` is the assertion.

**Why here.** The stats and models are pure logic with subtle edge cases, such as "is a match at exactly `as_of` excluded?". Tests pin that behaviour down.

Two pytest ideas show up everywhere:

**Fixtures** are setup functions that tests request by naming them as parameters. `backend/tests/conftest.py` defines `session`:

```python
@pytest.fixture
def session():
    # SQLite doesn't support JSONB; substitute JSON for the in-memory engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Patch JSONB -> JSON for SQLite before creating tables.
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as sess:
        yield sess
```

Each test that asks for `session` gets a fresh, empty **in-memory SQLite** database with all our tables. That's why tests don't need Docker, and why they're fast (about 1.5s for 102 tests). The trade-off: SQLite isn't Postgres, so Postgres-only features aren't exercised. The `JSONB` column type is swapped for plain `JSON` to make SQLite accept the schema, and the Postgres-specific upsert in `ingest.py` has no tests at all.

**Parametrize** runs one test for several inputs:

```python
@pytest.mark.parametrize("status", ["retired", "walkover"])
def test_over_hit_rate_ignores_non_finished(session, status):
```

Test data is built by hand with helpers in `backend/tests/helpers.py`. Sets are written as `(home_points, away_points)` tuples:

```python
make_match(session, lg, a, b, NOW - timedelta(hours=1), [(11, 9), (9, 11), (11, 6), (11, 4)])
```

API tests (`test_api.py`) use FastAPI's `TestClient` and swap in the test session:

```python
app.dependency_overrides[get_session] = lambda: session
```

Commands (from `backend/`, venv active):

```bash
python -m pytest -q                        # all tests, quiet
python -m pytest tests/test_stats.py -v    # one file, verbose
python -m pytest -k split                  # only tests whose name contains "split"
python -m pytest -x                        # stop at first failure
```

---

## 3. File-by-file walkthrough

```
backend/
  app/
    __init__.py         empty; marks app/ as a package
    config.py           settings from .env
    db.py               engine, SessionLocal, Base
    models.py           tables
    init_db.py          create/drop tables
    providers/
      __init__.py       get_provider(): picks mock or (later) BetsAPI
      base.py           the provider contract
      mock.py           the simulator
    ingest.py           provider -> Postgres
    stats.py            hit rates, set stats, Wilson CI, shrinkage
    ratings.py          Elo
    points.py           points-total model
    predict.py          log predictions
    grade.py            grade predictions, Brier, calibration
    main.py             FastAPI app
  tests/                pytest suite (102 tests)
  samples/              real BetsAPI JSON responses, for the upcoming provider (step 6)
  requirements.txt
  .env.example
```

`config.py` and `db.py` are covered in [section 2](#env-files-and-settings).

### `backend/app/models.py`

**Responsible for:** defining every table. There are seven:

| Table | What a row is | Key columns |
|---|---|---|
| `leagues` | a league | `api_id` (provider's id, unique), `name` |
| `players` | a player | `api_id` (unique), `name` |
| `matches` | a match | `api_id` (unique), `league_id`, `home_player_id`, `away_player_id`, `scheduled_at`, `status`, `home_sets_won`, `away_sets_won` |
| `match_sets` | one set of one match | primary key is (`match_id`, `set_number`); `home_points`, `away_points` |
| `player_ratings` | a player's Elo before/after one match | `player_id`, `match_id`, `rating_before`, `rating_after` |
| `predictions` | one model probability for one match | `market`, `line`, `model_version`, `probability`, `as_of`, `inputs`, `outcome` |
| `raw_events` | one raw provider payload as received | `source`, `api_id`, `payload` (JSONB) |

Two details worth understanding:

**Two kinds of id.** Every table has our own `id` (an auto-increment integer), and provider-facing tables also have `api_id` (the provider's string id, e.g. `mock-czech-20260928-005`). Foreign keys always use our `id`. Ingestion matches incoming data on `api_id`. So if the provider changes, our internal references don't.

**Composite primary key on sets:**

```python
class MatchSet(Base):
    __tablename__ = "match_sets"

    # Composite primary key: a match can only have one "set 3".
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    set_number: Mapped[int] = mapped_column(primary_key=True)
```

Two columns marked `primary_key=True` make the pair unique, so the database itself refuses a second "set 3" for the same match. `ondelete="CASCADE"` deletes a match's sets when the match is deleted.

**Unique constraint on predictions:**

```python
    __table_args__ = (UniqueConstraint("match_id", "market", "line", "model_version"),)
```

A model version can predict a market at a given line only once per match. `line` for `match_winner` is stored as `-1.0` rather than NULL. In SQL, NULL never equals NULL, so a unique constraint including a NULL column wouldn't stop duplicates.

(The module docstring says ratings "will get their own table in the stats step". That's stale: `player_ratings` exists now.)

### `backend/app/init_db.py`

**Responsible for:** creating tables.

```python
from app import models  # noqa: F401  (importing registers the tables)
from app.db import Base, engine

if __name__ == "__main__":
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
        print("Dropped all tables")
    Base.metadata.create_all(engine)
    print("Tables ready")
```

- `from app import models` looks unused, but importing the module *defines* the classes, which registers them on `Base.metadata`. Without the import, `create_all` would see no tables. `# noqa: F401` tells linters the unused import is deliberate.
- `if __name__ == "__main__":` is Python's "only run this when executed as a script, not when imported". You'll see it at the bottom of every runnable module.
- `--reset` drops every table first, **including predictions and ratings**. Fine with mock data that regenerates in seconds; dangerous once real data exists.

### `backend/app/providers/base.py`

**Responsible for:** the contract between the outside world and the app. It defines what a provider must return, as pydantic models:

```python
MatchStatus = Literal["scheduled", "live", "finished", "cancelled", "walkover", "retired"]

class ProviderEvent(BaseModel):
    api_id: str
    league: ProviderLeague
    home: ProviderPlayer
    away: ProviderPlayer
    scheduled_at: datetime
    status: MatchStatus
    home_sets_won: int | None = None
    away_sets_won: int | None = None
    sets: list[ProviderSet] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)  # untouched original payload
```

- `Literal[...]` restricts `status` to those exact strings. Pydantic raises an error for anything else, so a typo in a provider fails loudly at ingestion.
- `Field(default_factory=list)` gives each event its own new empty list. (A plain `= []` default is a classic Python trap: one list shared by every instance. Pydantic actually handles it, but `default_factory` makes the intent explicit.)
- `raw` carries the provider's original JSON, which ingestion stores in `raw_events`.

And the interface a provider class must satisfy:

```python
class DataProvider(Protocol):
    name: str

    def get_leagues(self) -> list[ProviderLeague]: ...

    def get_upcoming(self, league_api_id: str) -> list[ProviderEvent]: ...

    def get_ended(self, league_api_id: str, day: date) -> list[ProviderEvent]: ...
```

A `Protocol` is like a TypeScript interface. It's *structural*: a class doesn't have to inherit from `DataProvider`; it just needs these attributes and methods with these signatures. The `...` bodies mean "no implementation here".

### `backend/app/providers/__init__.py`

**Responsible for:** choosing the provider from settings.

```python
def get_provider() -> DataProvider:
    if settings.data_provider == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()
    raise ValueError(f"Unknown provider '{settings.data_provider}' (BetsAPI client not built yet)")
```

Setting `DATA_PROVIDER=betsapi` today raises this error. The BetsAPI branch is step 6 in `CLAUDE.md`.

### `backend/app/providers/mock.py`

**Responsible for:** generating realistic fake matches by actually simulating table tennis, point by point.

Each league has 24 players, each with a hidden skill drawn from a normal distribution (mean 0, standard deviation 1). Forty matches per league per day are scheduled every 25 minutes from 06:00 UTC. A match whose start time is at least 20 minutes in the past is simulated as finished.

The heart of it:

```python
def simulate_match(rng: random.Random, home_skill: float, away_skill: float):
    """Returns (status, sets)."""
    p_home_point = 1 / (1 + math.exp(-POINT_SCALE * (home_skill - away_skill)))

    roll = rng.random()
    if roll < WALKOVER_RATE:
        return "walkover", []
    retire_after_set = rng.randint(1, 4) if roll < WALKOVER_RATE + RETIRE_RATE else None

    sets: list[ProviderSet] = []
    home_won = away_won = 0
    while home_won < 3 and away_won < 3:
        h = a = 0
        while not (max(h, a) >= 11 and abs(h - a) >= 2):
            if rng.random() < p_home_point:
                h += 1
            else:
                a += 1
        ...
```

- `p_home_point` is the chance the home player wins any single point. It's a logistic function of the skill gap: equal skills give 0.5, and `POINT_SCALE = 0.12` controls how much a gap matters. Each point is an independent weighted coin flip.
- 1% of matches are walkovers (no sets) and 1% are retirements after a random set.
- The inner loop plays one set to 11, win by 2. The outer loop plays until someone has 3 sets (best of 5).

Two properties matter for the rest of the project:

**It's deterministic.** Each match gets its own random generator seeded from its id:

```python
rng = random.Random(f"{self.seed}-{api_id}")  # per-match RNG = stable results
```

The same match always produces the same result. A match ingested as "scheduled" today turns into the same "finished" match tomorrow.

**It knows the truth.** `true_skill(player_api_id)` returns a player's hidden skill. Models must never use it for predictions. It exists to *check* models: does Elo's ranking recover the real skill order?

Also note: `ev.raw = ev.model_dump(...)` means the mock's "raw payload" is just our own event serialized. It has a made-up shape. Real BetsAPI responses look different; samples are in `backend/samples/`.

### `backend/app/ingest.py`

**Responsible for:** pulling events from the provider and writing them to Postgres, safely re-runnable.

The upsert helper:

```python
def upsert(session: Session, model, values: dict, extra_updates: dict | None = None) -> int:
    """Insert a row keyed on api_id, or update it. Returns our internal id."""
    stmt = insert(model).values(**values)
    updates = {col: stmt.excluded[col] for col in values if col != "api_id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["api_id"], set_={**updates, **(extra_updates or {})}
    ).returning(model.id)
    return session.execute(stmt).scalar_one()
```

Line by line:

- `insert(model).values(**values)` builds `INSERT INTO <table> (...) VALUES (...)`. `insert` here is imported from `sqlalchemy.dialects.postgresql`, because `ON CONFLICT` is Postgres syntax.
- `stmt.excluded[col]` refers to "the value we tried to insert". Postgres calls that row `EXCLUDED`.
- `on_conflict_do_update(index_elements=["api_id"], set_=...)` says: if a row with this `api_id` already exists, update these columns instead of failing. That's what makes it an **upsert**.
- `.returning(model.id)` makes Postgres return our internal id, whether the row was inserted or updated. `scalar_one()` extracts that single value.

Per event:

```python
def ingest_event(session: Session, source: str, ev: ProviderEvent) -> None:
    session.add(RawEvent(source=source, api_id=ev.api_id, payload=ev.raw))

    league_id = upsert(session, League, {"api_id": ev.league.api_id, "name": ev.league.name})
    home_id = upsert(session, Player, {"api_id": ev.home.api_id, "name": ev.home.name})
    away_id = upsert(session, Player, {"api_id": ev.away.api_id, "name": ev.away.name})

    match_id = upsert(session, Match, {...}, extra_updates={"updated_at": func.now()})

    # Simplest correct way to sync sets: wipe and rewrite this match's sets.
    session.execute(delete(MatchSet).where(MatchSet.match_id == match_id))
    if ev.sets:
        session.execute(
            insert(MatchSet), [{"match_id": match_id, **s.model_dump()} for s in ev.sets]
        )
```

- The raw payload is always *appended* to `raw_events`, not upserted. That makes `raw_events` a log of everything received, and it also means every re-run of ingest adds duplicate payloads. On the current dev database there are 10,240 `raw_events` rows for 5,920 matches.
- League and players are upserted before the match, because the match needs their ids.
- Sets are replaced wholesale: delete this match's sets, insert the new ones. That's simpler than working out which sets changed.

`run(days)` loops over leagues, pulls `days` days of finished matches plus upcoming ones, and commits **once per league**. If something fails midway through a league, that league's changes roll back as a unit.

Known limitation: it upserts row by row, which is slow for large backfills. Batching is on the housekeeping list.

### `backend/app/stats.py`

**Responsible for:** everything that counts things in match history. Every function takes `as_of` and returns counts, not bare percentages.

**Core helpers** turn `(hits, n)` into display numbers. They're explained in [section 4](#sample-sizes-wilson-confidence-intervals-shrinkage):

```python
def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
def shrunk_rate(hits: int, n: int, baseline: float, prior_strength: float = 10) -> float:
def summarize(hits: int, n: int, baseline: float, prior_strength: float = 10) -> dict:
```

**Over/under.** `over_hit_rate` loads the player's last `n` finished matches before `as_of` and counts how many went over the line. `league_over_rate` does the league-wide version *in SQL* so it doesn't load thousands of matches:

```python
totals = (
    select(MatchSet.match_id,
           func.sum(MatchSet.home_points + MatchSet.away_points).label("total"))
    .group_by(MatchSet.match_id)
    .subquery()
)
stmt = (
    select(func.count(), func.count().filter(totals.c.total > line))
    .select_from(Match)
    .join(totals, totals.c.match_id == Match.id)
    .where(Match.league_id == league_id)
    .where(Match.status == "finished")
    .where(Match.scheduled_at < as_of)
)
total, hits = session.execute(stmt).one()
```

- The subquery sums points per match: `SELECT match_id, SUM(home_points + away_points) AS total FROM match_sets GROUP BY match_id`.
- `func.count().filter(totals.c.total > line)` becomes `COUNT(*) FILTER (WHERE total > line)`, a Postgres feature that counts only matching rows. One query returns both the total count and the over count.
- `totals.c.total` means "column `total` of the subquery" (`c` is short for columns).

**Set-sequence stats** are the biggest part of the file. Instead of a separate function per stat, each stat is a small rule in a dictionary:

```python
SetRule = Callable[[list[bool], bool], bool | None]

SET_STATS: dict[str, SetRule] = {
    # Match ended 3-0 either way.
    "sweep": lambda s, won: len(s) == 3,
    # Won the match after winning / losing set 1.
    "win_after_set1_win": lambda s, won: won if s[0] else None,
    ...
    # Split = the loser of set 1 wins set 2 (1-1 after two sets).
    "split": lambda s, won: s[0] != s[1] if _two(s) else None,
    # Player lost set 1, won set 2.
    "split_after_losing_set1": lambda s, won: s[1] if _two(s) and not s[0] else None,
    # Player won set 1, lost set 2.
    "split_allowed_after_winning_set1": lambda s, won: not s[1] if _two(s) and s[0] else None,
}
```

- Each rule sees one player's view of one match. `s` is a list like `[True, False, True, True]` (did the player win set 1, set 2, ...), and `won` says whether they won the match.
- The rule returns `None` if the match doesn't qualify (for example, "win after losing set 1" doesn't apply if they won set 1), otherwise `True` (hit) or `False` (miss).
- `lambda s, won: ...` is Python's inline function, like `(s, won) => ...` in JS. `x if cond else y` is Python's ternary, like `cond ? x : y`.

`_apply` counts hits over a list of views:

```python
def _apply(rule: SetRule, views: list[tuple[list[bool], bool]]) -> tuple[int, int]:
    hits = n = 0
    for sets, won in views:
        if not sets:
            continue
        result = rule(sets, won)
        if result is None:
            continue
        n += 1
        hits += bool(result)
    return hits, n
```

The same rules work for a player (`player_set_stat`: their last `n` matches) and for the league (`league_set_stat`: every league match, from both players' sides). Adding a stat means adding one dictionary entry, plus its expected value in `tests/test_advanced_stats.py`. A test fails if you forget the expected value.

Semantics to know:

- For player stats, the window is "last `n` finished matches", *then* filtered to qualifying ones. So a conditional stat's sample is at most `n` and often much smaller.
- League baselines count each match once per player, so their `n` is "player-matches", up to twice the number of matches.
- League-wide, the three split rates are equal by construction: every split has exactly one set-1 loser who won set 2. Only the per-player versions carry information.

`all_set_stat_summaries` computes every stat for several players while loading the league's matches only once. The API's match detail page uses it.

**Other stats:** `h2h_record` (head-to-head wins over the last `n` meetings) and `avg_total_points` / `avg_total_summary` (mean total points, shrunk toward the league mean, with no Wilson interval because that's for proportions, not means).

### `backend/app/ratings.py`

**Responsible for:** Elo ratings. It computes them and answers "what was this player's rating at time T?". See [section 4](#how-elo-works) for the math.

```python
def rebuild(session: Session, k: float = 32.0) -> dict[int, float]:
    session.execute(delete(PlayerRating))

    matches = session.scalars(
        select(Match)
        .where(Match.status == "finished")
        .order_by(Match.scheduled_at.asc())
    ).all()

    ratings: dict[int, float] = {}
    rows: list[PlayerRating] = []

    for m in matches:
        h_id = m.home_player_id
        a_id = m.away_player_id
        h_before = ratings.get(h_id, DEFAULT_RATING)
        a_before = ratings.get(a_id, DEFAULT_RATING)

        home_won = (m.home_sets_won or 0) > (m.away_sets_won or 0)
        h_score = 1.0 if home_won else 0.0

        e_home = p_win(h_before, a_before)
        h_after = h_before + k * (h_score - e_home)
        a_after = a_before + k * ((1.0 - h_score) - (1.0 - e_home))
        ...
```

- It deletes all existing ratings and replays every finished match oldest-first. A full recompute is simple and always consistent, and fast enough at mock scale.
- `ratings` is a dict of each player's *current* rating as the replay walks forward. `ratings.get(h_id, DEFAULT_RATING)` means "their rating so far, or 1500 if we haven't seen them yet".
- `(m.home_sets_won or 0)`: `x or 0` is Python's way of saying "use 0 if x is None".
- For each match it stores both players' `rating_before` and `rating_after`.

Looking up a rating at a point in time:

```python
def rating_as_of(session: Session, player_id: int, as_of: datetime) -> float:
    row = session.scalars(
        select(PlayerRating)
        .join(Match, Match.id == PlayerRating.match_id)
        .where(PlayerRating.player_id == player_id)
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(1)
    ).first()
    return row.rating_after if row else DEFAULT_RATING
```

This finds the player's most recent match *strictly before* `as_of` and returns the rating after it.

`python -m app.ratings --check` also prints the Spearman rank correlation between final Elo and the mock's hidden skill, per league (see [section 7](#7-current-results-and-what-they-do-and-dont-prove)).

### `backend/app/points.py`

**Responsible for:** the points-total model. Given a matchup, it produces the full probability distribution of total match points, so you can get P(over) at any line, plus P(home wins). See [section 4](#the-points-total-model) for how it works.

Its public pieces:

```python
@dataclass(frozen=True)
class MatchDistribution:
    totals: dict[int, float]  # total points -> probability
    p_home_win: float

    def p_over(self, line: float) -> float:
        return sum(prob for total, prob in self.totals.items() if total > line)

    def mean(self) -> float:
        return sum(t * prob for t, prob in self.totals.items())
```

- `@dataclass` auto-generates a constructor and equality from the annotated fields, like a lightweight typed record. `frozen=True` makes instances immutable.
- `sum(prob for ... if ...)` is a *generator expression*, roughly `entries.filter(...).reduce(...)` in JS.

- `match_distribution(p_home_point)` returns the exact distribution for a given P(home wins a point).
- `player_point_win_rate(...)` gives a player's share of points over their last `n` matches before `as_of`.
- `matchup(...)` combines two players into a distribution and also returns the inputs it used, so predictions can log them.

`@lru_cache(maxsize=4096)` on `_match_distribution` memoizes results. The probability is rounded to 4 decimals first, so similar matchups reuse a cached distribution.

### `backend/app/predict.py`

**Responsible for:** writing model probabilities into `predictions` *before* results are known, so they can be graded honestly later.

Two loggers:

- `log_match_winner` uses Elo: `p_win(rating_as_of(home), rating_as_of(away))`.
- `log_total_over` uses the points model: `matchup(...).p_over(line)`.

Both follow the same pattern:

```python
existing = session.scalar(select(Prediction).where(...same match/market/line/version...))
if existing:
    return False
...
pred = Prediction(match_id=match.id, market="total_over", line=line,
                  model_name=POINTS_MODEL_NAME, model_version=POINTS_MODEL_VERSION,
                  probability=dist.p_over(line), as_of=as_of, inputs=inputs)
try:
    session.add(pred)
    session.flush()
    return True
except IntegrityError:
    session.rollback()
    return False
```

- It checks for an existing row first, so running the script twice doesn't duplicate predictions.
- `session.flush()` sends the INSERT to the database *without* committing, so constraint violations surface immediately.
- `inputs` stores what the model saw (ratings, point rates) as JSON. You can later ask why it predicted what it did.

**Rough edge:** if the `except IntegrityError` branch ever ran during a backfill, `session.rollback()` would discard *every* unsaved prediction in that run, not just the duplicate. The backfill commits only once, at the end. Today the existence check prevents this branch from being reached. A savepoint (`session.begin_nested()`) would make it safe.

The `ratings_cache` parameter of `log_match_winner` is unused by any caller.

Two modes:

```bash
python -m app.predict --backfill   # every finished match, as_of = its scheduled_at
python -m app.predict              # every scheduled match, as_of = now
```

Backfill skips each player's first 10 matches (`BURN_IN = 10`), because a rating built from almost no games is noise. The module docstring says backfill covers "last 30 days"; in fact it covers every finished match in the database.

### `backend/app/grade.py`

**Responsible for:** marking predictions right or wrong once matches finish, then scoring the models.

```python
def outcome_for(pred: Prediction, match: Match) -> bool | None:
    if pred.market == "match_winner":
        return (match.home_sets_won or 0) > (match.away_sets_won or 0)
    if pred.market == "total_over":
        return sum(s.home_points + s.away_points for s in match.sets) > pred.line
    return None
```

`grade_predictions` finds ungraded predictions whose match is `finished` and sets `outcome` and `graded_at`. Walkovers and retirements never get graded. `brier_score` and `calibration_table` score the graded ones. `report()` prints each model next to a naive baseline and an oracle (see [section 4](#logging-and-grading-predictions)).

```bash
python -m app.grade
```

### `backend/app/main.py`

**Responsible for:** the HTTP API.

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"ok": true}` |
| `GET /leagues` | all leagues |
| `GET /matches?league_id&status&line&n&limit` | matches with model numbers (Elo and points-model P(home), P(over line), expected total, league over rate) |
| `GET /matches/{id}?line&n` | one match plus per-player stats, head-to-head, and the total-points distribution |

The important design choice is in `_model`:

```python
def _model(session: Session, m: Match, line: float, n: int) -> tuple[ModelOut, object]:
    as_of = m.scheduled_at
    h_elo = rating_as_of(session, m.home_player_id, as_of)
    a_elo = rating_as_of(session, m.away_player_id, as_of)
    dist, _ = matchup(session, m.home_player_id, m.away_player_id, as_of, n)
    lg_hits, lg_n = league_over_rate(session, m.league_id, line, as_of)
```

Every number for a match is computed as of that match's start time, with the same functions the backfill uses. So a finished match shows what the model would have said before it started, not something computed with hindsight. The line is a parameter, so the frontend can change it and get fresh probabilities.

Cost: 4–5 queries per match. `/matches` with 100 rows takes about 1.7s locally. That's acceptable for now, and batching is the first fix if it grows.

### `backend/tests/`

| File | Covers |
|---|---|
| `conftest.py` | the `session` fixture (in-memory SQLite) |
| `helpers.py` | `make_league`, `make_player`, `make_match`, `NOW` |
| `test_stats.py` | Wilson, shrinkage, over/under counts, leakage |
| `test_advanced_stats.py` | every set stat against a hand-worked 6-match history, both players' views, league pooling, leakage for every stat |
| `test_ratings.py` | `p_win`, the Elo update via `rebuild()`, zero-sum, time order, `rating_as_of` leakage |
| `test_points.py` | distributions sum to 1, known values, exact model vs the mock's own simulator, point-rate leakage |
| `test_predict.py` | predictions use pre-match ratings; no duplicates |
| `test_grade.py` | per-market grading (including the regression test for the grading bug), Brier, calibration bins |
| `test_api.py` | endpoints, filters, line parameter, point-in-time model numbers, 404 |

Not tested: `ingest.py` (its upsert is Postgres-only and tests run on SQLite) and the mock provider's scheduling.

---

## 4. Key design ideas

### Point-in-time correctness (`as_of`) and why leakage ruins backtests

A **backtest** asks: "if I'd used this model in the past, how would it have done?" To answer honestly, the model may only use information that existed at the moment of each prediction. Using anything later is called **leakage**, and it makes a model look better than it can ever be in real life.

A concrete example. You predict a match that starts at 14:00. If the player's stats include that 14:00 match itself, the model already "knows" part of the answer. Its backtest accuracy is inflated, you trust it, and it fails with real money.

Players here play several matches a day, so it matters to compare **full timestamps**, not dates. Every stat and model function takes `as_of` and filters strictly:

```python
    .where(Match.scheduled_at < as_of)
```

It uses `<`, not `<=`. A match starting exactly at `as_of` hasn't happened yet from `as_of`'s point of view.

The tests check this on purpose. For every stat, they add a match at exactly `as_of`, one after it, and a retired one, and assert the result doesn't change (`tests/test_advanced_stats.py`):

```python
    for a_home, pattern in HISTORY:
        _add(session, lg, a, b, NOW, a_home, pattern)                           # boundary
        _add(session, lg, a, b, NOW + timedelta(hours=1), a_home, pattern)      # future
        _add(session, lg, a, b, NOW - timedelta(days=1), a_home, pattern[:2],   # retired
             status="retired")

    assert player_set_stat(session, stat, a.id, 50, NOW) == before_player
```

When the stats code was temporarily changed from `<` to `<=`, 13 tests failed, which shows the tests actually catch it.

The backtest and the live app call **the same functions**. The only difference is `as_of`: the match's start time for backfill, or now for upcoming matches. There's no separate backtest code path that could drift.

### Store raw data, compute derived values

The database stores what the provider tells us: which sets were played and their scores. Things you can derive, such as total points, the winner, or hit rates, are computed in queries rather than stored.

Why? A stored derived value can go stale or disagree with its source. If a set score gets corrected, a stored "total points" column would silently be wrong. Computing from the source can't disagree with it. For example, the API computes the total on the fly (`main.py`):

```python
    total = sum(s.home_points + s.away_points for s in m.sets) if m.status == "finished" else None
```

The exception is **ratings**. Elo depends on the entire history in order, so recomputing it per request would be too slow. It's stored, but in a way that can't leak (next section).

### Ratings stored per match (`rating_before` / `rating_after`)

The tempting design is a `current_elo` column on `players`. It's wrong for this project. In a backtest of a match from two weeks ago, "current" Elo includes the last two weeks of results. That's leakage.

Instead, `player_ratings` has one row per player per match, with the rating before and after it. To get a player's rating at any moment, find their last match before that moment (`rating_as_of`, shown above). Any point in history can be reconstructed exactly.

Because `rebuild()` recomputes from scratch in time order, the table is always consistent with the matches table. Re-running it produces identical ratings (tested in `test_rebuild_is_idempotent`).

### Finished matches only in stats

Every stat filters `Match.status == "finished"`. Walkovers have no sets. Retirements stop partway, so a retired match's "total points" or "sets played" would be misleading, and "won the match" doesn't mean the same thing. Including them would quietly skew totals and win rates. The same rule applies to Elo (`rebuild`), to the points model's inputs, and to grading: a prediction on a match that ends in retirement is never graded.

On the current mock data, 60 walkovers and 49 retirements are excluded out of 5,752 played or attempted matches.

### Sample sizes, Wilson confidence intervals, shrinkage

"Player X goes over 74.5 in 80% of matches" means little if it's 4 out of 5. So stats return **`(hits, n)`**, never a bare percentage, and the display layer adds two corrections.

**Wilson confidence interval** (`stats.wilson_interval`). A 95% range the true rate plausibly lies in. It's wide for small samples and narrow for large ones. The textbook "p ± 1.96 × standard error" formula breaks near 0% and 100% and for small n. Wilson's version behaves sensibly there. With 7 hits out of 10, the raw rate is 70% but the Wilson interval is about **40%–89%**: you can't say much.

**Shrinkage** (`stats.shrunk_rate`):

```python
def shrunk_rate(hits: int, n: int, baseline: float, prior_strength: float = 10) -> float:
    """Blend the player's rate with the league baseline, weighted by sample size."""
    return (hits + prior_strength * baseline) / (n + prior_strength)
```

Think of it as adding `prior_strength` imaginary matches that went exactly like the league average. With 7/10 and a league rate of 47%: (7 + 10 × 0.47) / (10 + 10) = **58.5%**. With 70/100 it would be (70 + 4.7) / 110 = 67.9%, much closer to the raw 70%, because there's real evidence. Small samples get pulled toward the league; large samples mostly speak for themselves.

`prior_strength = 10` is a **guess** that hasn't been tuned. The right value depends on how much players genuinely differ on each stat.

`summarize()` packages all of it, and the frontend highlights anything under 10 matches:

```python
    return {
        "hits": hits,
        "n": n,
        "rate": round(hits / n, 3) if n else None,
        "ci_95": [round(low, 3), round(high, 3)],
        "shrunk_rate": round(shrunk_rate(hits, n, baseline, prior_strength), 3),
        "small_sample": n < SMALL_SAMPLE,
    }
```

### The provider interface and why it makes BetsAPI swappable

`providers/base.py` defines `ProviderEvent` and the `DataProvider` protocol. Everything after ingestion (models, stats, API) only sees our own types and tables, never a provider's JSON.

So adding BetsAPI means writing **one class**, `providers/betsapi.py`, whose `get_leagues`, `get_upcoming` and `get_ended` call BetsAPI and translate its JSON into `ProviderEvent`s, plus one branch in `get_provider()`. Nothing else changes. Its tests can parse the real responses saved in `backend/samples/` without hitting the network.

The translation is where real-world messiness lives: BetsAPI's status codes, how it formats set scores, how it marks retirements and walkovers. Containing that in one file keeps the rest of the code clean.

### Upserts and `raw_events`

The same match is seen many times: as "scheduled", maybe "live", then "finished", and ingestion is re-run regularly. **Upsert on `api_id`** means each sighting updates the one existing row instead of creating duplicates (see `ingest.upsert` in [section 3](#backendappingestpy)). You can re-run `python -m app.ingest` any time.

**`raw_events`** stores each payload exactly as received, in a JSONB column. If we later find a parsing bug, or want a field we ignored, we can reprocess history from `raw_events` without re-fetching. With a paid API that has rate limits, re-fetching may be expensive or impossible.

Caveat, as noted above: `raw_events` is append-only, so it grows by every event on every ingest run, including duplicates of unchanged payloads.

### How Elo works

Elo gives each player a number (everyone starts at 1500) and predicts matches from the *difference*:

```python
def p_win(rating_a: float, rating_b: float) -> float:
    """Standard Elo win probability: P(a beats b)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
```

- Equal ratings give 50%.
- A player rated 400 higher is a 10:1 favourite (about 91%). That's the definition of the 400 in the formula, and there's a test for it.

After each match, both ratings move toward the result:

```
new rating = old rating + K × (actual result − expected result)
```

The actual result is 1 for a win and 0 for a loss. `K = 32` sets how fast ratings react.

- Two 1500 players: the winner expected 0.5 and got 1, so gains 32 × 0.5 = **+16**. The loser drops 16.
- An upset: a 1484 player beats a 1516 player. The winner expected only about 0.45, so gains about 17.5, more than 16. Surprises move ratings more.
- Points are only transferred, never created, so the ratings stay **zero-sum** and the average stays 1500 (tested).

This version only uses win/loss, not the set score (3-0 vs 3-2) or points margin. K isn't tuned either. Both are obvious improvements to try, measured with the grading below.

### Logging and grading predictions

The idea: **write down the prediction before you know the answer, then check it.** It's the only honest way to know if a model works.

Each prediction row records the match, market, line, model version, probability, `as_of`, the model's inputs, and later its outcome. Two markets exist:

- `match_winner`: probability = P(home wins). Line is stored as −1, meaning no line.
- `total_over`: probability = P(total points > line).

**Brier score** measures how good the probabilities are. For each prediction, take (probability − outcome)², where the outcome is 1 or 0, and average:

```python
    total = sum((p.probability - float(p.outcome)) ** 2 for p in preds)
    return total / len(preds), len(preds)
```

Lower is better. 0 is perfect. Always saying 50% scores exactly 0.25. Saying 90% and being right costs 0.01; saying 90% and being wrong costs 0.81, so confident mistakes hurt a lot.

**Calibration** asks: when the model says 70%, does it happen about 70% of the time? `calibration_table` groups predictions into buckets (0–10%, 10–20%, ...) and compares the average predicted probability with the actual hit rate in each bucket. A model can have a decent Brier score and still be systematically over- or under-confident. Calibration shows *where*.

The report compares every model to two reference points on the **same predictions**:

- a **naive baseline**: always 50% for match winner; the league's historical over rate for totals. If the model can't beat this, it's useless.
- an **oracle** (mock only): the true probabilities computed from the hidden skills. It's the best score any model could get on this data, which tells you how much room is left.

**The grading bug that was fixed.** An earlier version of `grade_predictions` set every prediction's outcome the same way:

```python
outcome = (match.home_sets_won or 0) > (match.away_sets_won or 0)   # old code: "did home win?"
```

That's right for `match_winner` but wrong for `total_over`. A totals prediction was marked correct if the *home player won*, which has nothing to do with the total points. Any Brier score for totals was therefore meaningless: it measured how well "P(over)" happened to correlate with "home won".

It mattered because grading is how we decide whether a model works. A broken grader can make a bad model look fine, or a good one look bad, and you'd never know from the code that produced the predictions. The fix is `outcome_for()` (shown in [section 3](#backendappgradepy)), which branches on market. There's a regression test that would have caught it: a match the home player won 3-0 with 57 points must grade `total_over 74.5` as **False**, and an away win with 101 points as **True** (`tests/test_grade.py::test_grade_total_over_uses_points_not_winner`). The misgraded rows in the dev database were deleted and regenerated.

A related bug was fixed at the same time. The oracle used the chance of winning a single *point* as if it were the chance of winning the *match*, which made the "perfect" oracle score *worse* than Elo. That's impossible, and a good hint that something is broken. It now converts point probability to match probability through the exact model below.

### The points-total model

**Goal:** P(total points > line) for any line, not just a hit rate at one fixed line.

**Approach, in three steps:**

1. **Estimate each player's point-winning rate** from their last 20 finished matches: points won ÷ points played, taken from set scores. It's shrunk toward 0.5 with 50 points of prior, which is light: a match is about 75 points.

2. **Combine two players into one matchup probability** with the "log5" formula, the standard way to combine two rates each measured against the whole field:

   ```python
   def matchup_point_prob(rate_home: float, rate_away: float) -> float:
       num = rate_home * (1.0 - rate_away)
       denom = num + rate_away * (1.0 - rate_home)
       return num / denom if denom else 0.5
   ```

   A 55% player against an average (50%) player gets 55%. Against a 45% player they get more than 55%.

3. **Turn P(home wins a point) into the exact distribution of total match points.** If every point is an independent coin flip with probability p, the chance of each set score has a closed form. For example, winning 11–k requires the opponent to win exactly k of the first 10+k points, then you win the last one:

   ```python
   for k in range(10):
       ways = comb(10 + k, k)
       home[11 + k] = ways * p**11 * q**k
       away[11 + k] = ways * q**11 * p**k
   ```

   From 10–10 (deuce), each pair of points either ends the set (win-win or lose-lose) or returns to deuce (split), so the deuce tail is a geometric series. That gives the distribution of one set's total, by winner. `_match_distribution` then runs a small dynamic program over the set score (0-0, 1-0, 1-1, ... up to 3 sets won), adding up totals, until every path ends. The result is the probability of every possible match total, plus P(home wins).

Why exact math instead of simulating thousands of matches? It's deterministic (no random noise in the numbers), about 1ms per matchup, and it can be checked. A test plays 20,000 matches through the mock's own simulator and confirms the exact model matches (`test_exact_model_matches_mock_simulator`).

**A design call to be aware of.** A project rule says "grain is set-level, no point-level modelling". The model's *inputs* are set-level (only set scores are used), but its *math* assumes points are independent coin flips, which is a point-level assumption. That was accepted deliberately, because it's the standard way to get a totals distribution. Real table tennis has streaks and momentum, so expect it to fit real data worse than mock data. The mock generates points exactly this way (see [section 7](#7-current-results-and-what-they-do-and-dont-prove)).

---

## 5. Running everything from scratch

All commands assume macOS and the repo at `~/Documents/Projects/tt-analytics`.

**1. Start Postgres.** Open Docker Desktop and wait until it says it's running. Then:

```bash
cd ~/Documents/Projects/tt-analytics
docker compose up -d
docker compose ps          # the db service should be "running"
```

**2. Create the Python environment (once).**

```bash
cd backend
python3 -m venv .venv      # python3, NOT python (see errors below)
source .venv/bin/activate  # your prompt shows (.venv)
python --version           # must print Python 3.13.x
pip install -r requirements.txt
cp .env.example .env       # then edit if needed
```

Every new terminal needs `source .venv/bin/activate` again (from `backend/`).

**3. Create tables and load data.**

```bash
python -m app.init_db            # "Tables ready"
python -m app.ingest --days 30   # ~30s; prints events per league
```

**4. Build ratings, predictions, grades.**

```bash
python -m app.ratings --check    # Elo + Spearman check vs hidden skill
python -m app.predict --backfill # ~35s; predictions for past matches
python -m app.predict            # predictions for upcoming matches
python -m app.grade              # Brier scores + calibration tables
```

Order matters: ratings before predictions, because Elo predictions read `player_ratings`. After new data, re-run `ingest`, then `ratings`, then `predict --backfill`, `predict` and `grade`.

**5. Run the API.**

```bash
uvicorn app.main:app --reload    # http://localhost:8000/docs
```

**6. Run the tests** (no Docker needed):

```bash
python -m pytest -q              # expect 102 passed
```

**7. Frontend (optional, separate terminal):**

```bash
cd ~/Documents/Projects/tt-analytics/frontend
npm install
npm run dev                      # http://localhost:3000, needs the API running
```

### Common errors and fixes

**`python` is Python 2.7.** On this Mac, `python` on the PATH is `/Library/Frameworks/Python.framework/Versions/2.7/bin/python` (Python 2.7.18), and `python3` is 3.13. If you create the venv or run commands with the wrong one, you get syntax errors on modern code (type hints like `int | None`, f-strings), or "No module named ..." errors for packages you did install.

Fix: create the venv with `python3 -m venv .venv`, and always activate it. Inside an active venv, `python` means the venv's 3.13. Check with `python --version` and `which python`, which should point into `backend/.venv/`. If a venv was created with the wrong Python, delete it (`rm -rf .venv`) and recreate it.

**Docker Desktop not running.** Symptoms:

- `docker compose up -d` fails with *"Cannot connect to the Docker daemon at unix:///.../docker.sock. Is the docker daemon running?"*
- Or Postgres isn't up, so Python fails with a psycopg `OperationalError` mentioning *"connection refused"* on port 5432.

Fix: start Docker Desktop, wait for it to finish starting, then run `docker compose up -d` again and check `docker compose ps`.

A related case: if you have a separate Postgres installed locally already listening on 5432, the container can't bind that port. Stop the local one, or change the port mapping and `DATABASE_URL` together.

**Claude Code "file descriptor" error.** The exact message wasn't recorded in the project history, so this section describes the usual cause. **Please paste the real error here next time it appears.** On macOS the common form is `EMFILE: too many open files`. Tools that watch files open one handle per file, and this repo has very large folders (`frontend/node_modules`, `backend/.venv`, `.next`), which can exceed macOS's low default per-process limit.

Things that usually help:

- Raise the limit for the shell before starting the tool: `ulimit -n 10240`.
- Start tools from the project folder rather than a higher-level folder.
- Restart the tool once the limit is raised.

**Running a command from the wrong directory.** `python -m app.x` must be run from `backend/`. Otherwise you get *"No module named app"*. Also, `.env` wouldn't be found and the settings would fall back to defaults.

**"relation ... does not exist".** The tables haven't been created: run `python -m app.init_db`. After a model change that adds columns, `create_all` won't alter existing tables. Use `python -m app.init_db --reset`, which deletes all data, then re-ingest.

---

## 6. How to find things

| If you want to change... | Look in |
|---|---|
| Database connection string or provider choice | `backend/.env` (template: `.env.example`), read by `app/config.py` |
| A table or column | `app/models.py`, then `python -m app.init_db --reset` and re-ingest (no migrations yet) |
| How provider data maps to our tables | `app/ingest.py` (`ingest_event`) |
| The shape every provider must return | `app/providers/base.py` |
| Add the BetsAPI provider | new `app/providers/betsapi.py` plus a branch in `app/providers/__init__.py`; test against `backend/samples/` |
| Mock data: players per league, match frequency, skill effect | constants at the top of `app/providers/mock.py` (`PLAYERS_PER_LEAGUE`, `MATCHES_PER_DAY`, `POINT_SCALE`) |
| Add a set-based stat (e.g. "wins set 4 when down 1-2") | one entry in `SET_STATS` in `app/stats.py`, plus its expected value in `tests/test_advanced_stats.py` |
| Shrinkage strength or small-sample threshold | `prior_strength` defaults and `SMALL_SAMPLE` in `app/stats.py` |
| Elo K-factor or starting rating | `python -m app.ratings --k 24`, or `DEFAULT_RATING` in `app/ratings.py` |
| Points model window or prior | `n` in `points.matchup` / `predict.log_total_over`; `POINT_PRIOR_STRENGTH` in `app/points.py` |
| The totals line used for logged predictions | `DEFAULT_TOTAL_LINE` in `app/predict.py` |
| Burn-in before predicting | `BURN_IN` in `app/predict.py` |
| A new prediction market | a logger in `app/predict.py`, a branch in `grade.outcome_for`, an entry in `grade.MODELS`, and a test in `tests/test_grade.py` |
| What the API returns | pydantic `...Out` classes and routes in `app/main.py`; mirror changes in `frontend/lib/api.ts` |
| Test data builders | `tests/helpers.py` |
| Project rules, status and roadmap | `CLAUDE.md` at the repo root |

A shape change in `main.py` must be copied by hand into `frontend/lib/api.ts`. Nothing checks that the two agree.

---

## 7. Current results, and what they do and don't prove

All numbers are on **mock data** (30+ days, 4 leagues, 24 players each). Predictions were made with `as_of` = kickoff, skipping each player's first 10 matches. Numbers come from `python -m app.grade` and `python -m app.ratings --check` on 2026-09-28.

**Elo recovers the hidden skill order.** Spearman rank correlation between final Elo and `true_skill()` (1.0 would be a perfect ranking):

| League | rho |
|---|---|
| Czech Liga Pro | 0.964 |
| TT Elite Series | 0.861 |
| Setka Cup | 0.876 |
| TT Cup | 0.897 |
| All 96 players | 0.903 |

**Match winner, n = 5,082 graded predictions:**

| Model | Brier (lower is better) |
|---|---|
| Always 50% | 0.2500 |
| **Elo v1** | **0.1952** |
| Oracle (true skill) | 0.1874 |

Elo closes about 87% of the gap between a coin flip and perfect knowledge. Calibration, by predicted P(home wins):

| Bucket | n | Predicted | Actual |
|---|---|---|---|
| 0.0–0.1 | 176 | 0.069 | 0.085 |
| 0.1–0.2 | 446 | 0.153 | 0.184 |
| 0.2–0.3 | 605 | 0.252 | 0.255 |
| 0.3–0.4 | 681 | 0.352 | 0.335 |
| 0.4–0.5 | 671 | 0.451 | 0.455 |
| 0.5–0.6 | 676 | 0.548 | 0.577 |
| 0.6–0.7 | 661 | 0.648 | 0.672 |
| 0.7–0.8 | 583 | 0.748 | 0.750 |
| 0.8–0.9 | 420 | 0.847 | 0.869 |
| 0.9–1.0 | 163 | 0.929 | 0.933 |

Every bucket is within about 3 points of the actual rate: well calibrated.

**Total over 74.5, n = 5,082:**

| Model | Brier |
|---|---|
| League historical over rate | 0.2496 |
| **Points model v1** | **0.2455** |
| Oracle (true skill) | 0.2413 |

| Bucket | n | Predicted | Actual |
|---|---|---|---|
| 0.0–0.1 | 19 | 0.072 | 0.211 |
| 0.1–0.2 | 105 | 0.155 | 0.257 |
| 0.2–0.3 | 276 | 0.254 | 0.301 |
| 0.3–0.4 | 583 | 0.356 | 0.395 |
| 0.4–0.5 | 1,201 | 0.457 | 0.485 |
| 0.5–0.6 | 2,898 | 0.547 | 0.520 |

The points model beats the naive baseline and closes about half the gap to the oracle. But notice how small every gap is: even perfect knowledge only gets to 0.2413. Totals are mostly luck. The model's low predictions are too extreme (it says 7–15%, reality is 21–26%). Since the oracle is well calibrated, the exact math is right and the problem is noisy input estimates. Tuning shrinkage in-sample didn't fix it.

**What this does prove:**

- The code does what it claims. Point-in-time filtering works, and the tests show it. Grading is correct per market. The exact totals math agrees with brute-force simulation.
- Both models extract real signal from *this* data, and Elo is well calibrated on it.
- The whole evaluation pipeline (log, then grade, then compare to baseline and oracle) works end to end, ready for real data.

**What this does not prove:**

- **That the models work on real matches.** The mock generates matches from exactly the assumptions the models make: a fixed skill per player, independent points, no home advantage, no form, fatigue or momentum. A model that shares the simulator's assumptions is being tested on its own home turf. Real table tennis will be noisier, and both Elo and the points model should be expected to score worse.
- **That the gains are meaningful for betting.** Nothing here involves odds. Beating a naive baseline isn't the same as beating a bookmaker's line, which already prices in skill.
- **That the parameters are good.** K = 32, the shrinkage strengths (10 and 50) and the 20-match window are guesses. The one tuning attempt (point shrinkage) was in-sample.
- **Anything about the oracle outside the mock.** It exists only because the mock knows the hidden skills. There's no oracle on real data.

The next step in `CLAUDE.md` (step 6: BetsAPI provider, then rerun steps 1–4) exists to find out how much of this survives contact with real data.

---

## 8. Glossary

**API endpoint.** One URL + HTTP method the server answers, e.g. `GET /matches`. Also called a route.

**`as_of`.** The moment a calculation pretends to be standing at. Only data from strictly before it may be used.

**Backfill.** Computing something (predictions, ratings) for past data in one go, as if it had been done at the time.

**Backtest.** Evaluating a model on past data as if you had used it back then. Only honest without leakage.

**Baseline (naive).** The simple prediction a model must beat to be worth anything, e.g. "always 50%" or "the league's usual over rate".

**Brier score.** Average of (predicted probability − actual outcome)². Lower is better; 0.25 = always saying 50%.

**Burn-in.** Ignoring a player's first N matches when predicting, because their rating hasn't settled yet. Here N = 10.

**Calibration.** Whether predicted probabilities match reality: things predicted at 70% should happen about 70% of the time.

**Container / image.** An *image* is a packaged filesystem and program (e.g. `postgres:16`); a *container* is a running instance of an image.

**Dependency (FastAPI).** A function FastAPI calls for you and injects into a route, via `Depends(...)`. Used here to give each request a database session.

**Elo.** A rating system where each player has a number, win probability comes from the rating gap, and ratings move after each match by K × (actual − expected).

**Engine / session (SQLAlchemy).** The engine manages database connections for the whole process. A session is one unit of work: queries and changes, committed together.

**Fixture (pytest).** A setup function a test receives by naming it as a parameter, e.g. `session`.

**Foreign key.** A column whose value must exist in another table's primary key, e.g. `matches.home_player_id` → `players.id`.

**Idempotent.** Safe to run repeatedly with the same result, e.g. ingest (upserts), `rebuild()`, `grade_predictions()`.

**JSONB.** Postgres's binary JSON column type. Stores arbitrary JSON and can be queried.

**Leakage.** Using information from after the prediction moment. Makes backtests look better than reality.

**log5.** A formula that combines two players' rates (each measured against the field) into a head-to-head probability.

**Market.** What a prediction is about: `match_winner` or `total_over` here.

**Migration.** A versioned script that changes an existing database schema (add a column, etc.) without losing data. The standard tool with SQLAlchemy is Alembic. Not set up yet; we drop and recreate instead.

**Mock provider.** The simulator standing in for a real data API.

**N+1 queries.** Running one query for a list, then one more per item. Avoided with `selectinload`.

**Oracle.** A "perfect knowledge" predictor built from the mock's hidden skills. Shows the best achievable score. Mock only.

**ORM (object-relational mapper).** Maps database tables to classes and rows to objects (SQLAlchemy here; Prisma or TypeORM in JS).

**Point-in-time correctness.** Every calculation uses only data available at its `as_of` moment.

**Primary key.** The column(s) that uniquely identify a row. Composite = more than one column, like `(match_id, set_number)`.

**Protocol (Python).** A structural interface: any class with the right methods qualifies, like a TypeScript interface.

**Pydantic model.** A class with typed fields that validates data on creation; used for provider events and API responses.

**Response model (FastAPI).** The pydantic class an endpoint's output is validated against and serialized from.

**Shrinkage.** Pulling a small-sample rate toward a baseline, in proportion to how little data there is: (hits + k × baseline) / (n + k).

**Spearman correlation (rho).** How well two rankings agree, from −1 to 1. Used to compare Elo order with hidden-skill order.

**Split.** In this project: the loser of set 1 wins set 2, so it's 1-1 after two sets.

**Upsert.** Insert a row, or update it if one with the same key already exists. Here: Postgres `INSERT ... ON CONFLICT (api_id) DO UPDATE`.

**uvicorn.** The server process that runs the FastAPI app.

**Virtual environment (venv).** A project-local Python interpreter plus packages (`backend/.venv`).

**Walkover / retirement.** A match not played (walkover) or abandoned partway (retirement). Both are excluded from stats, ratings and grading.

**Wilson interval.** A confidence interval for a proportion that behaves well for small samples and rates near 0% or 100%.
