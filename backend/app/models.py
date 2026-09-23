"""Database tables. Rule of thumb: store what the API tells you; compute everything else.

- Total points, winner, hit rates -> computed in queries, never stored twice.
- Ratings (Elo) -> will get their own table (player_ratings) in the stats step,
  stored per match as rating_before / rating_after, so backtests never see the future.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str]


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str]


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    league: Mapped[League] = relationship()
    home_player: Mapped[Player] = relationship(foreign_keys="Match.home_player_id")
    away_player: Mapped[Player] = relationship(foreign_keys="Match.away_player_id")
    sets: Mapped[list["MatchSet"]] = relationship(
        back_populates="match", order_by="MatchSet.set_number", cascade="all, delete-orphan"
    )


class MatchSet(Base):
    __tablename__ = "match_sets"

    # Composite primary key: a match can only have one "set 3".
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    set_number: Mapped[int] = mapped_column(primary_key=True)
    home_points: Mapped[int]
    away_points: Mapped[int]

    match: Mapped[Match] = relationship(back_populates="sets")


class RawEvent(Base):
    """Every payload exactly as the provider sent it. Lets you reprocess history later
    (new fields, bug fixes) without re-fetching or paying again."""

    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    api_id: Mapped[str] = mapped_column(String, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONB)
