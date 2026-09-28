# TT Analytics: project walkthrough (for explaining it in interviews)

This is the "explain the project" companion to [`BACKEND_GUIDE.md`](BACKEND_GUIDE.md). The backend guide teaches the tools. This doc explains how the system fits together, the code worth knowing well, the hard parts and real bugs, and the trade-offs. Each part ends with how to talk about it honestly.

Everything here matches the code and database as of commit `dc64679` (2026-09-28). All numbers come from the mock data provider.

Contents:

- [1.1 What it is, and how to describe your role](#11-what-it-is-and-how-to-describe-your-role)
- [1.2 The full flow, traced with one real match](#12-the-full-flow-traced-with-one-real-match)
- [1.3 Annotated code: the parts to know well](#13-annotated-code-the-parts-to-know-well)
- [1.4 The hard parts, including the real bugs](#14-the-hard-parts-including-the-real-bugs)
- [1.5 Trade-offs](#15-trade-offs)
- [1.6 What the results show and don't show](#16-what-the-results-show-and-dont-show)
- [1.7 Questions you'll likely get, with honest answers](#17-questions-youll-likely-get-with-honest-answers)
- [1.8 Things not to claim](#18-things-not-to-claim)

---

## 1.1 What it is, and how to describe your role

**What it is.** A table tennis analytics and betting-research tool for four high-frequency leagues. It ingests matches from a data provider into Postgres and computes point-in-time stats and two models:

- **Elo**, for who wins.
- **A points-total model**, for whether total points go over a line.

It logs every model probability as a prediction, grades predictions against results (Brier score, calibration), and serves everything through a FastAPI API to a Next.js UI. The data currently comes from a simulator (the "mock provider"), not real matches.

**What's actually in the repo:**

| Part | Size / state |
|---|---|
| Backend (`backend/app/`) | 15 Python files (including 3 in `providers/`) |
| Tests (`backend/tests/`) | 102 tests, all passing, run on in-memory SQLite |
| Frontend (`frontend/`) | Next.js 16: match list + match detail page |
| Data | mock only; real BetsAPI payload samples in `backend/samples/`, provider not written yet |
| Predictions graded | 10,164, all from backfill (see [1.6](#16-what-the-results-show-and-dont-show)) |

**How to describe your role, honestly.** The git history is public, and most commits carry a `Co-Authored-By: Claude` trailer: 12 of the 16 before this doc was added. Only you know how the four without one were written (the initial scaffold, the first Elo commit, the `.gitignore` change and the mock-sample commit). Anyone who opens the repo will see this, so say it up front rather than let them discover it:

> "I built this with an AI coding assistant as a deliberate part of the project. It's partly a learning project for AI engineering. I set the design rules: point-in-time correctness, store raw and compute the rest, per-match ratings, grading every prediction. I directed the work, reviewed it, and I can walk you through any part of the code. Some of the most useful work was catching the AI's mistakes, like a grading bug that made one model's scores meaningless."

Only say the last sentence in a form that's true for you. Section 1.4 records who found what: several bugs were found in AI review passes, not by hand. If you say "I found", make sure you did. "We found it in code review" or "the review caught it" is safe and accurate.

What makes this credible in an interview isn't claiming you typed every line. It's being able to explain the code, the decisions and the bugs without notes. This doc is for that.

---

## 1.2 The full flow, traced with one real match

One real match from the dev database, followed from generation to the screen:

> **Match 10198**, `mock-elite-20260928-037`, TT Elite Series, 2026-09-28 21:25 UTC.
> Filip Bondarenko (home) vs Yurii Novak (away). Result: **0-3** (5-11, 5-11, 8-11), **51 total points**.

### Step 1: The provider produces a `ProviderEvent`

`backend/app/providers/mock.py` simulates the match point by point. Each point is a weighted coin flip based on the hidden skill gap between the players:

```python
p_home_point = 1 / (1 + math.exp(-POINT_SCALE * (home_skill - away_skill)))
```

It returns a `ProviderEvent`, the one shape every provider must produce (`backend/app/providers/base.py`): league, players, `scheduled_at`, `status`, sets, and a `raw` dict holding the original payload.

### Step 2: Ingestion writes it to Postgres

`backend/app/ingest.py` runs `ingest_event()` for each event:

- appends the raw payload to `raw_events`;
- upserts the league, both players and the match (keyed on the provider's `api_id`);
- rewrites the match's sets.

For match 10198 the database now holds:

| Table | Row(s) |
|---|---|
| `matches` | id 10198, status `finished`, home_sets_won 0, away_sets_won 3 |
| `match_sets` | (1, 5, 11), (2, 5, 11), (3, 8, 11) |
| `raw_events` | 1 row with the payload JSON |

The total (51) is **not** stored anywhere. It's computed from the sets whenever it's needed.

### Step 3: Elo is rebuilt

`python -m app.ratings` (`backend/app/ratings.py`, `rebuild()`) replays every finished match in time order and writes one `player_ratings` row per player per match:

| Player | rating_before | rating_after |
|---|---|---|
| Bondarenko (home) | 1375.6 | 1371.4 |
| Novak (away) | 1700.5 | 1704.8 |

The favourite won, as expected, so ratings moved only about 4 points. An even match moves them 16.

### Step 4: Predictions are logged, as of kickoff

`python -m app.predict --backfill` (`backend/app/predict.py`) logs two predictions with `as_of = 21:25`, the match's start time. Each model may only see data from before that moment:

| Market | Model | Probability | Inputs stored |
|---|---|---|---|
| `match_winner` | elo v1 | **0.1335** (home wins) | home_rating 1375.6, away_rating 1700.5 |
| `total_over` 74.5 | points_model v1 | **0.3239** | home point rate 0.479, away 0.554, P(home wins a point) 0.425, from 1,487 and 1,444 points seen |

### Step 5: Predictions are graded

`python -m app.grade` (`backend/app/grade.py`) sets each prediction's `outcome`:

- `match_winner`: did home win? **False**. The prediction said 13%, so this counts as a good prediction.
- `total_over 74.5`: was 51 > 74.5? **False**. The prediction said 32%.

Brier contributions are (0.1335 − 0)² = 0.018 and (0.3239 − 0)² = 0.105. Averaged over all graded predictions, these give the scores in [1.6](#16-what-the-results-show-and-dont-show).

### Step 6: The API serves it

`GET /matches/10198?line=74.5&n=20` (`backend/app/main.py`) recomputes the model numbers with `as_of = scheduled_at`, using the same functions as step 4. It returns them alongside:

- per-player stats (over rate, average total, set stats with confidence intervals and shrinkage);
- head-to-head (H2H) record;
- the full probability distribution of total points.

Change `line` and P(over) is recomputed for the new line.

### Step 7: The UI shows it

`frontend/app/matches/[id]/page.tsx` is a React Server Component. It fetches that endpoint server-side and renders the cards, a bar chart of the total-points distribution with the line marked, and the stats table.

### The flow in one sentence

The provider's data is stored as-is, derived values are computed on demand, and every model number is computed "as of" a moment, so the same code serves both backtests and live predictions.

---

## 1.3 Annotated code: the parts to know well

These are the pieces an interviewer is most likely to ask you to walk through. File paths are above each excerpt.

### (a) The point-in-time query pattern

`backend/app/stats.py`:

```python
def _player_matches(session: Session, player_id: int, n: int,
                    as_of: datetime) -> list[Match]:
    """The player's last n finished matches before as_of, sets preloaded."""
    stmt = (
        select(Match)
        .where(or_(Match.home_player_id == player_id, Match.away_player_id == player_id))
        .where(Match.status == "finished")
        .where(Match.scheduled_at < as_of)
        .order_by(Match.scheduled_at.desc())
        .limit(n)
        .options(selectinload(Match.sets))
    )
    return list(session.scalars(stmt).all())
```

- **Line 4:** the player was either home or away.
- **Line 5:** only completed matches. Walkovers and retirements would distort totals and win rates.
- **Line 6:** the core rule, *strictly before* `as_of`. It compares full timestamps, not dates, because players play several matches a day.
- **Lines 7–8:** the most recent `n` matches.
- **Line 9:** loads all their sets in one extra query, instead of one query per match.

Every stat and model function applies the same three filters.

### (b) Stats as a rule table

`backend/app/stats.py`:

```python
SET_STATS: dict[str, SetRule] = {
    "sweep": lambda s, won: len(s) == 3,
    "win_after_set1_win": lambda s, won: won if s[0] else None,
    ...
    "split": lambda s, won: s[0] != s[1] if _two(s) else None,
    "split_after_losing_set1": lambda s, won: s[1] if _two(s) and not s[0] else None,
    "split_allowed_after_winning_set1": lambda s, won: not s[1] if _two(s) and s[0] else None,
}
```

Each rule takes one player's view of a match: `s` = [won set 1?, won set 2?, ...] and `won` = won the match?. It returns `None` if the match doesn't qualify, otherwise hit or miss.

One loader feeds the player version and one feeds the league version, so all nine stats get identical `as_of`, finished-only and shrinkage handling. Adding a stat is one line, plus a test. A test fails if a stat has no expected value.

**How to talk about it:** it replaced nine hand-written loops that had drifted apart. One stat used a different window from the others, and the split stats had no league baseline at all. The table makes that kind of drift structurally hard.

### (c) Sample sizes, confidence intervals, shrinkage

`backend/app/stats.py`:

```python
def shrunk_rate(hits: int, n: int, baseline: float, prior_strength: float = 10) -> float:
    """Blend the player's rate with the league baseline, weighted by sample size."""
    return (hits + prior_strength * baseline) / (n + prior_strength)
```

Stats return `(hits, n)`, never a bare percentage. The display layer adds:

- a **Wilson 95% interval**. For 7/10 it's about 40%–89%, which tells you how little 10 matches prove.
- a **shrunk rate**: the player's rate plus 10 imaginary league-average matches. For 7/10 against a 47% league it gives 58.5%.

`prior_strength = 10` is a guess that hasn't been tuned. Say so if asked.

### (d) Elo, stored per match

`backend/app/ratings.py`:

```python
        e_home = p_win(h_before, a_before)
        h_after = h_before + k * (h_score - e_home)
        a_after = a_before + k * ((1.0 - h_score) - (1.0 - e_home))
        ...
        rows.append(PlayerRating(player_id=h_id, match_id=m.id,
                                 rating_before=h_before, rating_after=h_after))
```

The standard Elo update, with K = 32. What's worth talking about is the **storage**: one row per player per match, not a `current_elo` column. A "current" rating includes results after any past match you backtest, which is leakage. `rating_as_of(player, t)` looks up the last row before `t`. The ratings are verified by tests that call `rebuild()` and check the stored rows: ±16 on an even match, a bigger swing for an upset, zero-sum, time order rather than insertion order, and idempotency.

### (e) The exact points-total model

`backend/app/points.py`:

```python
def set_outcomes(p: float) -> tuple[dict[int, float], dict[int, float]]:
    q = 1.0 - p
    home: dict[int, float] = {}
    away: dict[int, float] = {}
    for k in range(10):
        ways = comb(10 + k, k)
        home[11 + k] = ways * p**11 * q**k
        away[11 + k] = ways * q**11 * p**k
    deuce = comb(20, 10) * (p * q) ** 10
    for j in range(MAX_DEUCE_ROUNDS):
        reach = deuce * (2 * p * q) ** j
        home[22 + 2 * j] = reach * p * p
        away[22 + 2 * j] = reach * q * q
    return home, away
```

- Given p = P(home wins a point), with points independent: to win 11-k, the opponent wins exactly k of the first 10+k points (`comb(10+k, k)` ways), then you win the last point.
- From 10-10, each pair of points either ends the set (pp or qq) or returns to deuce (2pq), so the deuce tail is a geometric series. The key is the set's total points.
- A small dynamic program over the set score then combines sets into a best-of-5 distribution of match totals (`_match_distribution`). P(over any line) is a sum over that distribution.

p itself comes from each player's share of points over their last 20 matches, combined with the log5 formula.

**How to talk about it:** it replaced a Monte Carlo version (5,000 simulated matches per prediction, in pure Python) that was too slow to backfill thousands of predictions. The exact version is deterministic and takes about a millisecond. It's checked against 20,000 matches from the mock's own simulator (`tests/test_points.py::test_exact_model_matches_mock_simulator`).

### (f) Market-aware grading

`backend/app/grade.py`:

```python
def outcome_for(pred: Prediction, match: Match) -> bool | None:
    if pred.market == "match_winner":
        return (match.home_sets_won or 0) > (match.away_sets_won or 0)
    if pred.market == "total_over":
        return sum(s.home_points + s.away_points for s in match.sets) > pred.line
    return None
```

It's small, but it's the fix for the most important bug in the project ([1.4](#bug-1-totals-predictions-graded-as-did-home-win)).

### (g) One model function, backtest and live

`backend/app/main.py`:

```python
def _model(session: Session, m: Match, line: float, n: int) -> tuple[ModelOut, object]:
    as_of = m.scheduled_at
    h_elo = rating_as_of(session, m.home_player_id, as_of)
    a_elo = rating_as_of(session, m.away_player_id, as_of)
    dist, _ = matchup(session, m.home_player_id, m.away_player_id, as_of, n)
```

The API calls the same functions as the backfill, with `as_of` set to the match's start time. There's no separate "backtest code" that could quietly differ from what users see.

### (h) The provider boundary

`backend/app/providers/base.py`:

```python
class DataProvider(Protocol):
    name: str

    def get_leagues(self) -> list[ProviderLeague]: ...

    def get_upcoming(self, league_api_id: str) -> list[ProviderEvent]: ...

    def get_ended(self, league_api_id: str, day: date) -> list[ProviderEvent]: ...
```

A structural interface, like a TypeScript interface. Only `providers/` knows any external JSON shape. Switching to BetsAPI means writing one class and one branch in `get_provider()`.

**How to talk about it:** that's the design. It hasn't been proven yet, because the BetsAPI provider isn't written. Say "designed so that", not "swapped".

---

## 1.4 The hard parts, including the real bugs

### The genuinely hard part: not fooling yourself

The technical work is simple (queries, counts, formulas). The hard part is making sure the numbers you evaluate models with are honest. Three things can quietly inflate results:

- **leakage** (using the future);
- **a broken grader**;
- **testing on data generated by the model's own assumptions**.

Every bug below falls into one of these.

### Bugs found and fixed

The first four were in code written by an earlier AI agent run. They were caught in a later review pass, when the code was read line by line before being built on.

#### Bug 1: totals predictions graded as "did home win"

**What:** `grade_predictions` set every prediction's outcome to "home won the match", including `total_over` predictions. Old code:

```python
outcome = (match.home_sets_won or 0) > (match.away_sets_won or 0)
```

**Impact:** every Brier score for the totals model measured the wrong thing. It was meaningless, but it still printed a plausible-looking number.

**Fix:** `outcome_for()` branches on market ([1.3 (f)](#f-market-aware-grading)). Regression test: a home win with 57 points must grade `total_over 74.5` as False, and an away win with 101 points as True (`tests/test_grade.py::test_grade_total_over_uses_points_not_winner`). The 4,277 misgraded rows in the dev database were deleted and regenerated.

**Interview framing:** "The grader was market-blind: totals predictions were being scored as match-winner results. A grader bug is worse than a model bug, because it corrupts the thing you use to judge models. The lesson I took: the evaluation code needs tests as much as the models do."

#### Bug 2: an "oracle" that scored worse than the model

**What:** the report compares each model with an oracle, the true probabilities computed from the mock's hidden skills. The oracle used P(win a *point*) as P(win the *match*). A 55% point edge is actually worth over 75% of matches, so the oracle was badly underconfident.

**How it showed up:** the original commit message reported oracle Brier 0.2307 against Elo 0.1955. A perfect-knowledge predictor can't lose to an estimate, so the result itself was the signal. The commit message with those wrong numbers is still in the history (`a522203`).

**Fix:** convert the true point probability to match probability through the exact points model. The oracle now scores 0.1874, better than Elo, as it must.

**Interview framing:** "The result was impossible: the perfect-knowledge baseline lost to the model. That kind of sanity check, asking whether an ordering even makes sense, caught it."

#### Bug 3: a test that didn't test the code

**What:** `test_elo_update_math` computed `1500 + 32 × 0.5 == 1516` inside the test and never called `rebuild()`. It would pass even if the Elo code were deleted.

**Fix:** tests that run `rebuild()` on real rows and check what was stored: ±16, the upset bonus, zero-sum, time order vs insert order, non-finished matches skipped, idempotent.

**Interview framing:** "Some tests looked like coverage but exercised nothing. Now I check that a test would actually fail if the code were wrong."

#### Bug 4: no test for the leakage boundary

**What:** the leakage tests only checked that future matches were excluded, not a match at *exactly* `as_of`. If `<` had been changed to `<=`, nothing would have failed.

**Fix:** boundary + future + retired tests for every stat, for Elo and for the points model. Verified by deliberately changing `<` to `<=` in `stats.py`: **13 tests failed**, then the change was reverted.

**Interview framing:** "I mutation-tested the most important rule: broke it on purpose and confirmed the tests caught it."

#### Smaller issues fixed at the same time

- **Two Elo implementations** existed (`elo.py` and `ratings.py`). `elo.py` was deleted.
- **The Monte Carlo totals model** was too slow to backfill and gave slightly different numbers each run. It was replaced with the exact model.
- **The calibration table** dropped predictions at exactly probability 1.0: every bin, including the top one, used `low <= p < high`.
- **Inconsistent stats:** one stat (set 5 win rate) used a different window from the rest, and split stats had no league baseline. Both were fixed by the rule table.

### Weaknesses still open (not fixed)

Be ready to name these yourself. Raising them before you're asked reads much better than being caught out.

| Issue | Where | Why it matters | Honest one-liner |
|---|---|---|---|
| Rollback discards a whole batch | `predict.py`, `except IntegrityError: session.rollback()` | If a duplicate insert ever hit the database constraint mid-backfill, every unsaved prediction in the run would be lost, not just the duplicate. Today a check before each insert keeps it from happening. | "Latent bug: error handling that's too broad. Fix is a savepoint per insert." |
| `raw_events` duplicates on re-ingest | `ingest.py` appends every payload | 10,240 rows for 5,920 matches on the dev database. Wastes space; harmless for correctness. | "Append-only log with no dedupe. Should hash payloads or upsert on (api_id, payload hash)." |
| No ingest tests | `ingest.py` | The upsert uses Postgres-only SQL, and tests run on SQLite. The one step that touches the real database is untested. | "Tests run on SQLite for speed; the cost is that Postgres-specific code isn't covered. I'd add a Postgres test container." |
| API does 4–5 queries per match | `main.py` | `/matches` with 100 rows takes ~1.7s locally. | "Fine at this scale; I know exactly where the N×queries are and would batch them." |
| League baselines load whole leagues into Python | `stats.py` | Fine at 1,400 matches per league; won't be at real volume. | "First candidate for SQL aggregation or caching." |
| Untuned parameters | K = 32, shrinkage 10 and 50, window 20 | Guesses, not fitted. One tuning attempt was in-sample. | "Defaults, not tuned. Tuning needs a train/test split by time." |
| Totals model overconfident at the low end | predictions 7–15% happen 21–26% of the time | Model tails are too extreme. The oracle is calibrated, so the math is right and the inputs are noisy. | "Diagnosed but not fixed." |
| No push handling | `grade.outcome_for` | A whole-number line grades a tie as under. | "Lines are assumed to end in .5." |
| Frontend types copied by hand | `frontend/lib/api.ts` | They can drift from the API silently. | "Would generate them from the OpenAPI schema." |
| No migrations | `init_db --reset` drops everything | Fine for mock data, not for real data. | "Alembic is on the list, before real data." |
| Minor | unused `ratings_cache` parameter; stale docstrings in `models.py` and `predict.py` | Cosmetic. | — |

---

## 1.5 Trade-offs

Each is a decision you can defend, with the cost stated.

**Recompute ratings from scratch vs update incrementally.** `rebuild()` deletes and replays everything.
- For: it's always consistent, trivially correct, and idempotent.
- Against: the cost grows with history, and nothing updates in real time.
- It takes seconds for about 5,600 matches. Incremental updates would be needed at real volume or real-time latency.

**Per-match rating rows vs a current-rating column.**
- For: backtests can't leak, and history is auditable.
- Against: more rows, and a lookup query (`rating_as_of`) instead of a column read.

**Compute derived values vs store them.** Totals, winners and rates are computed in queries.
- For: they can't go stale or disagree with the source.
- Against: repeated computation, which is part of the API's 1.7s.
- Ratings are the deliberate exception, stored safely.

**Exact math vs Monte Carlo for totals.**
- For exact: deterministic, fast, testable against closed-form values.
- Against: tied to one assumption, independent points. A simulator could model streaks or momentum more easily. That assumption is a known limitation: real points aren't independent.

**Independent-points assumption vs the "set-level only" project rule.** The project rule says no point-level modelling. The model's inputs are set-level (only set scores), but its math assumes points are independent coin flips. This was accepted deliberately and written down in `CLAUDE.md`. Say that, rather than claim the model is purely set-level.

**SQLite for tests vs Postgres.**
- For: 102 tests in about 1.5s with no Docker.
- Against: Postgres-only code (the upsert) is untested, and SQLite needs a patch (JSONB → JSON).

**Server Components + URL state in the UI vs a client-side app.**
- For: no CORS, no client state management, and every view is a shareable URL.
- Against: every filter change is a server round trip, and there's no live updating.

**One rule table for stats vs one function per stat.**
- For: consistency is structural, and a new stat is a single line.
- Against: less obvious to a newcomer than a named function, and lambdas are harder to step through in a debugger.

**Backfill at kickoff time vs only grading live predictions.**
- For: thousands of graded predictions immediately.
- Against: it's a backtest, not a live record ([1.6](#16-what-the-results-show-and-dont-show)). A subtle leak in any function would inflate it; live predictions can't leak.

**An LLM isn't used anywhere in the pipeline.** This is a deliberate rule (`CLAUDE.md`): the stats, ratings and models are plain functions, not an agent. LLM features are planned only for turning questions into typed filters and for open-ended research. If asked "where's the AI?": the AI was the development tool, and the product's number-crunching is deliberately deterministic.

---

## 1.6 What the results show and don't show

Mock data, n = 5,082 graded predictions per market (full tables in [`BACKEND_GUIDE.md` §7](BACKEND_GUIDE.md#7-current-results-and-what-they-do-and-dont-prove)):

| Market | Naive baseline | Model | Oracle |
|---|---|---|---|
| match_winner | 0.2500 (always 50%) | **Elo 0.1952** | 0.1874 |
| total_over 74.5 | 0.2496 (league rate) | **Points model 0.2455** | 0.2413 |

Elo's Spearman rank correlation with the hidden skills is 0.861–0.964 per league.

**What these show:**

- The pipeline works end to end.
- Both models extract real signal from this data and beat naive baselines.
- Elo is well calibrated here (within about 3 points per 10% bucket).
- The exact totals math is correct, since it matches brute-force simulation.

**What they don't show:**

- **Performance on real matches.** The simulator generates data using the same assumptions the models make: fixed skill, independent points, no form or momentum. That's the best case for these models.
- **Live prediction performance.** All 10,164 graded predictions were created *after* their matches, by the backfill, with `as_of` set to kickoff. That's a point-in-time backtest, and the leakage tests make it trustworthy, but it isn't a forward test. The 336 predictions logged before their matches haven't been graded yet.
- **Betting value.** There are no odds in the system. Beating a naive baseline is not beating a bookmaker.
- **Tuned parameters.** None were tuned out-of-sample.

**One honest sentence for interviews:** "On simulated data, Elo gets about 87% of the way from a coin flip to perfect knowledge and is well calibrated. The totals model gets about half the way on a market that's mostly noise. I haven't validated either on real matches yet. That's the next step, and the simulator shares the models' assumptions, so I expect real numbers to be worse."

---

## 1.7 Questions you'll likely get, with honest answers

**"Walk me through the architecture."** Use [1.2](#12-the-full-flow-traced-with-one-real-match): provider → ingest (upsert, raw log) → Postgres → point-in-time stats, ratings and models → predictions logged and graded → FastAPI → Next.js. Then name the one idea tying it together: every calculation takes `as_of`.

**"How do you prevent data leakage?"** Every function filters `scheduled_at < as_of` on full timestamps. Ratings are stored per match so past ratings can be reconstructed. The API and backtests share the same functions. There are tests for the boundary case, and the rule was mutation-tested (changing `<` to `<=` fails 13 tests).

**"How do you know the models work?"** Say what's been measured, not more. Brier scores against a naive baseline and an oracle, plus calibration tables, on simulated data, via point-in-time backfill. Then: "Not yet validated on real data or forward-tested; here's why that matters."

**"What was the hardest bug?"** The market-blind grader (bug 1), or the impossible oracle (bug 2). Explain why grader bugs are worse than model bugs. Say who found it accurately ([1.1](#11-what-it-is-and-how-to-describe-your-role)).

**"What would you do differently / next?"**
- Real data first: the BetsAPI provider is step 6 in `CLAUDE.md`.
- Fix the rollback handling and the `raw_events` duplication.
- Add Postgres-backed ingest tests.
- Tune parameters on a time-based train/test split.
- Batch the API queries.

**"How much of this did you write?"** See [1.1](#11-what-it-is-and-how-to-describe-your-role). Answer plainly, then offer to walk through any file.

**"Why Elo and not something fancier?"** It's a strong, interpretable baseline that's easy to verify: rank correlation with the hidden skills was 0.86–0.96. Anything fancier has to beat it on the same graded predictions, and the grading setup exists for exactly that comparison.

**"Why not use an LLM for the predictions?"** The stats and models should be deterministic, testable and cheap. An LLM belongs at the edges: turning natural-language questions into typed filters (planned), and open-ended research. It shouldn't be computing probabilities.

---

## 1.8 Things not to claim

The code or history doesn't support any of these:

| Don't say | Why | Say instead |
|---|---|---|
| "The model is profitable" or "finds edges" | no odds in the system | "beats naive baselines on simulated data" |
| "Validated on real data" | mock only | "validated on a simulator; real data is next" |
| "Predictions were logged live and graded" | all graded ones are backfilled | "point-in-time backtest" |
| "The provider layer is swapped / BetsAPI-integrated" | BetsAPI provider not written | "designed to be swappable; real payload samples collected" |
| "Parameters are tuned / optimized" | K, shrinkage, window are guesses | "sensible defaults, not yet tuned" |
| "Full test coverage" | ingest untested, Postgres paths untested | "102 tests focused on stats, models, grading, API" |
| "Production-ready" | no migrations, no auth, 1.7s list endpoint, known latent bug | "working prototype with known gaps" |
| "The model is purely set-level" | the math assumes independent points | "set-level inputs, independent-points assumption" |
| "I wrote all of it" / "I found every bug" | commit history shows AI co-authorship; several bugs found in AI review passes | describe your actual role ([1.1](#11-what-it-is-and-how-to-describe-your-role)) |
| "Uses AI/ML for predictions" | Elo and a closed-form probability model | "statistical models; AI used as the development tool" |
