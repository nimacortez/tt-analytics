"""The contract between the outside world and your app.

Everything downstream (ingestion, stats, UI) only ever sees these types.
The BetsAPI client's whole job will be translating their JSON into ProviderEvent.
Swap providers = write one new class; nothing else changes.
"""
from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

MatchStatus = Literal["scheduled", "live", "finished", "cancelled", "walkover", "retired"]


class ProviderLeague(BaseModel):
    api_id: str
    name: str


class ProviderPlayer(BaseModel):
    api_id: str
    name: str


class ProviderSet(BaseModel):
    set_number: int
    home_points: int
    away_points: int


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


class DataProvider(Protocol):
    name: str

    def get_leagues(self) -> list[ProviderLeague]: ...

    def get_upcoming(self, league_api_id: str) -> list[ProviderEvent]: ...

    def get_ended(self, league_api_id: str, day: date) -> list[ProviderEvent]: ...
