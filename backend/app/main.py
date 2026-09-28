"""API. Run: uvicorn app.main:app --reload   then open http://localhost:8000/docs

Every model number is computed with as_of = the match's scheduled_at, exactly like
the backfill, so a finished match shows what the model said before it started.
"""
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import SessionLocal
from app.models import League, Match
from app.points import matchup
from app.ratings import p_win, rating_as_of
from app.stats import (
    all_set_stat_summaries, avg_total_summary, h2h_record, league_over_rate, over_summary,
)

app = FastAPI(title="TT Analytics")

DEFAULT_LINE = 74.5
DEFAULT_N = 20


def get_session():
    with SessionLocal() as session:
        yield session


class LeagueOut(BaseModel):
    id: int
    name: str


class SetOut(BaseModel):
    set_number: int
    home_points: int
    away_points: int


class ModelOut(BaseModel):
    line: float
    home_elo: float
    away_elo: float
    p_home_win_elo: float
    p_home_win_points: float
    p_over: float                # points model
    expected_total: float
    league_over_rate: float | None
    league_over_n: int


class MatchOut(BaseModel):
    id: int
    league_id: int
    league: str
    home_id: int
    away_id: int
    home: str
    away: str
    scheduled_at: datetime
    status: str
    home_sets_won: int | None
    away_sets_won: int | None
    sets: list[SetOut]
    total_points: int | None     # computed here, not stored; finished matches only
    went_over: bool | None       # vs model.line
    model: ModelOut


class DistPoint(BaseModel):
    total: int
    prob: float


class PlayerStats(BaseModel):
    player_id: int
    name: str
    over: dict
    avg_total: dict
    set_stats: dict[str, dict]


class MatchDetailOut(BaseModel):
    match: MatchOut
    n: int
    h2h: dict
    home_stats: PlayerStats
    away_stats: PlayerStats
    distribution: list[DistPoint]


def _model(session: Session, m: Match, line: float, n: int) -> tuple[ModelOut, object]:
    as_of = m.scheduled_at
    h_elo = rating_as_of(session, m.home_player_id, as_of)
    a_elo = rating_as_of(session, m.away_player_id, as_of)
    dist, _ = matchup(session, m.home_player_id, m.away_player_id, as_of, n)
    lg_hits, lg_n = league_over_rate(session, m.league_id, line, as_of)
    return ModelOut(
        line=line,
        home_elo=round(h_elo, 1),
        away_elo=round(a_elo, 1),
        p_home_win_elo=round(p_win(h_elo, a_elo), 4),
        p_home_win_points=round(dist.p_home_win, 4),
        p_over=round(dist.p_over(line), 4),
        expected_total=round(dist.mean(), 1),
        league_over_rate=round(lg_hits / lg_n, 4) if lg_n else None,
        league_over_n=lg_n,
    ), dist


def _match_out(session: Session, m: Match, line: float, n: int) -> tuple[MatchOut, object]:
    model, dist = _model(session, m, line, n)
    total = sum(s.home_points + s.away_points for s in m.sets) if m.status == "finished" else None
    return MatchOut(
        id=m.id,
        league_id=m.league_id,
        league=m.league.name,
        home_id=m.home_player_id,
        away_id=m.away_player_id,
        home=m.home_player.name,
        away=m.away_player.name,
        scheduled_at=m.scheduled_at,
        status=m.status,
        home_sets_won=m.home_sets_won,
        away_sets_won=m.away_sets_won,
        sets=[SetOut(set_number=s.set_number, home_points=s.home_points,
                     away_points=s.away_points) for s in m.sets],
        total_points=total,
        went_over=(total > line) if total is not None else None,
        model=model,
    ), dist


def _match_query():
    return select(Match).options(
        joinedload(Match.league),
        joinedload(Match.home_player),
        joinedload(Match.away_player),
        selectinload(Match.sets),
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/leagues", response_model=list[LeagueOut])
def list_leagues(session: Session = Depends(get_session)):
    return [LeagueOut(id=lg.id, name=lg.name)
            for lg in session.scalars(select(League).order_by(League.name))]


@app.get("/matches", response_model=list[MatchOut])
def list_matches(
    league_id: int | None = None,
    league: str | None = Query(None, description="league name (legacy; prefer league_id)"),
    status: str | None = None,
    line: float = DEFAULT_LINE,
    n: int = Query(DEFAULT_N, ge=1, le=200),
    limit: int = Query(50, le=200),
    session: Session = Depends(get_session),
):
    stmt = _match_query()
    if status == "scheduled":
        stmt = stmt.order_by(Match.scheduled_at.asc())   # soonest first
    else:
        stmt = stmt.order_by(Match.scheduled_at.desc())  # most recent first
    stmt = stmt.limit(limit)
    if league_id is not None:
        stmt = stmt.where(Match.league_id == league_id)
    if league:
        stmt = stmt.where(
            Match.league_id == select(League.id).where(League.name == league).scalar_subquery()
        )
    if status:
        stmt = stmt.where(Match.status == status)

    return [_match_out(session, m, line, n)[0] for m in session.scalars(stmt)]


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

    out, dist = _match_out(session, m, line, n)
    as_of = m.scheduled_at
    h, a = m.home_player_id, m.away_player_id
    set_stats = all_set_stat_summaries(session, [h, a], m.league_id, n, as_of)

    def player(pid: int, name: str) -> PlayerStats:
        return PlayerStats(
            player_id=pid,
            name=name,
            over=over_summary(session, pid, m.league_id, line, n, as_of),
            avg_total=avg_total_summary(session, pid, m.league_id, n, as_of),
            set_stats=set_stats[pid],
        )

    h2h_wins, h2h_n = h2h_record(session, h, a, n, as_of)
    return MatchDetailOut(
        match=out,
        n=n,
        h2h={"home_wins": h2h_wins, "n": h2h_n},
        home_stats=player(h, m.home_player.name),
        away_stats=player(a, m.away_player.name),
        distribution=[DistPoint(total=t, prob=round(p, 6))
                      for t, p in sorted(dist.totals.items()) if p >= 1e-5],
    )
