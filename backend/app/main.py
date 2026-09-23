"""API. Run: uvicorn app.main:app --reload   then open http://localhost:8000/docs"""
from datetime import datetime

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import SessionLocal
from app.models import League, Match

app = FastAPI(title="TT Analytics")


def get_session():
    with SessionLocal() as session:
        yield session


class SetOut(BaseModel):
    set_number: int
    home_points: int
    away_points: int


class MatchOut(BaseModel):
    id: int
    league: str
    home: str
    away: str
    scheduled_at: datetime
    status: str
    home_sets_won: int | None
    away_sets_won: int | None
    sets: list[SetOut]
    total_points: int | None  # computed here, not stored


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/matches", response_model=list[MatchOut])
def list_matches(
    league: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=500),
    session: Session = Depends(get_session),
):
    stmt = (
        select(Match)
        .options(
            joinedload(Match.league),
            joinedload(Match.home_player),
            joinedload(Match.away_player),
            selectinload(Match.sets),
        )
        .order_by(Match.scheduled_at.desc())
        .limit(limit)
    )
    if league:
        stmt = stmt.where(
            Match.league_id == select(League.id).where(League.name == league).scalar_subquery()
        )
    if status:
        stmt = stmt.where(Match.status == status)

    return [
        MatchOut(
            id=m.id,
            league=m.league.name,
            home=m.home_player.name,
            away=m.away_player.name,
            scheduled_at=m.scheduled_at,
            status=m.status,
            home_sets_won=m.home_sets_won,
            away_sets_won=m.away_sets_won,
            sets=[SetOut(set_number=s.set_number, home_points=s.home_points,
                         away_points=s.away_points) for s in m.sets],
            # Only complete matches count toward totals. Retirements would skew them.
            total_points=sum(s.home_points + s.away_points for s in m.sets)
            if m.status == "finished" else None,
        )
        for m in session.scalars(stmt)
    ]
