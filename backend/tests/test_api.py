"""API tests: FastAPI TestClient on the in-memory SQLite session."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_session
from app.stats import SET_STATS
from tests.helpers import NOW, make_league, make_match, make_player

LONG = [(11, 9), (9, 11), (12, 10), (10, 12), (11, 6)]   # 101 points
SHORT = [(11, 7), (11, 8), (11, 9)]                      # 57 points


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def data(session):
    lg1, lg2 = make_league(session, "lg1"), make_league(session, "lg2")
    a, b = make_player(session, "a"), make_player(session, "b")
    old = make_match(session, lg1, a, b, NOW - timedelta(hours=2), SHORT)
    recent = make_match(session, lg1, a, b, NOW - timedelta(hours=1), LONG)
    upcoming = make_match(session, lg1, a, b, NOW + timedelta(hours=1), [], status="scheduled")
    other = make_match(session, lg2, a, b, NOW, SHORT)
    return lg1, lg2, a, b, old, recent, upcoming, other


def test_leagues(client, data):
    names = [lg["name"] for lg in client.get("/leagues").json()]
    assert names == ["League lg1", "League lg2"]


def test_matches_filters_and_order(client, data):
    lg1, *_ , old, recent, upcoming, other = data
    ids = [m["id"] for m in client.get("/matches", params={"league_id": lg1.id}).json()]
    assert ids == [upcoming.id, recent.id, old.id]           # newest first

    sched = client.get("/matches", params={"status": "scheduled"}).json()
    assert [m["id"] for m in sched] == [upcoming.id]
    assert sched[0]["total_points"] is None
    assert sched[0]["went_over"] is None


def test_matches_line_changes_went_over_and_p_over(client, data):
    lg1, *_ , old, recent, _, _ = data
    at_60 = {m["id"]: m for m in client.get(
        "/matches", params={"league_id": lg1.id, "line": 60.5}).json()}
    at_100 = {m["id"]: m for m in client.get(
        "/matches", params={"league_id": lg1.id, "line": 100.5}).json()}

    assert at_60[recent.id]["total_points"] == 101
    assert at_60[old.id]["went_over"] is False and at_60[recent.id]["went_over"] is True
    assert at_100[recent.id]["went_over"] is True
    assert at_60[recent.id]["model"]["p_over"] > at_100[recent.id]["model"]["p_over"]
    assert at_100[recent.id]["model"]["line"] == 100.5


def test_matches_model_is_point_in_time(client, data):
    """The oldest match has no history before it: default Elo and 0 league sample."""
    lg1, *_ , old, recent, _, _ = data
    by_id = {m["id"]: m for m in client.get("/matches", params={"league_id": lg1.id}).json()}

    first = by_id[old.id]["model"]
    assert first["home_elo"] == first["away_elo"] == 1500.0
    assert first["league_over_n"] == 0
    assert first["p_home_win_points"] == pytest.approx(0.5, abs=1e-3)

    second = by_id[recent.id]["model"]
    assert second["league_over_n"] == 1                  # sees `old` only, not itself


def test_match_detail(client, data):
    *_, recent, _, _ = data
    body = client.get(f"/matches/{recent.id}", params={"line": 74.5, "n": 10}).json()

    assert body["match"]["id"] == recent.id
    assert body["h2h"] == {"home_wins": 1, "n": 1}          # only `old` is before it
    assert set(body["home_stats"]["set_stats"]) == set(SET_STATS)
    assert body["home_stats"]["over"]["n"] == 1
    assert sum(p["prob"] for p in body["distribution"]) == pytest.approx(1.0, abs=1e-3)


def test_match_detail_404(client, data):
    assert client.get("/matches/999999").status_code == 404
