"""tool_service /music — players discovered from HA, resolved in the MA
blueprint's order (docs/plans/MUSIC_PLAYERS_FROM_HA_PLAN.md).

The resolution order is pinned here because the blueprint's Jinja and this
Python cannot share code: name → area → origin area → default. First tests
the /music route has had (2026-09-04).
"""
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tool_service.main as ts

KITCHEN = {"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen", "state": "idle"}
OFFICE = {"entity_id": "media_player.satellite1", "name": "satellite-01-ma", "area": "Office", "state": "idle"}
OFFICE2 = {"entity_id": "media_player.office_sonos", "name": "Office Sonos", "area": "Office", "state": "idle"}
SONOS = {"entity_id": "media_player.sonos_move_2", "name": "Sonos Move Derp", "area": None, "state": "unavailable"}
PLAYERS = [KITCHEN, OFFICE, OFFICE2, SONOS]
DEFAULT = KITCHEN["entity_id"]


def _resolve(spoken=None, origin=None, players=PLAYERS, default=DEFAULT):
    return ts.resolve_players(spoken, origin, players, default)


def _err(*a, **kw) -> HTTPException:
    with pytest.raises(HTTPException) as ei:
        _resolve(*a, **kw)
    return ei.value


# ── parser ──────────────────────────────────────────────────────────────────

def test_parse_players_normalizes_and_skips_junk():
    raw = json.dumps([
        {"entity_id": "media_player.a", "name": "A", "area": "Kitchen", "state": "idle"},
        {"entity_id": "media_player.b", "name": None, "area": None, "state": None},
        {"name": "no entity"},
        "garbage",
    ])
    out = ts.parse_players(raw)
    assert out == [
        {"entity_id": "media_player.a", "name": "A", "area": "Kitchen", "state": "idle"},
        {"entity_id": "media_player.b", "name": "media_player.b", "area": None, "state": "unknown"},
    ]


def test_parse_players_rejects_non_list():
    with pytest.raises(ValueError):
        ts.parse_players('{"not": "a list"}')


# ── resolution order: name → area → origin → default ────────────────────────

def test_exact_player_name_wins_over_area():
    # "Office Sonos" is both a player name and contains an area name; name wins.
    targets, label = _resolve("Office Sonos")
    assert targets == [OFFICE2] and label == "the Office Sonos speaker"


def test_area_name_targets_every_player_in_the_room():
    targets, label = _resolve("office")
    assert targets == [OFFICE, OFFICE2] and label == "the Office speakers"


def test_spoken_phrasing_is_normalized():
    for spoken in ("the office", "Office speaker", "the office speakers", "office room"):
        targets, _ = _resolve(spoken)
        assert {t["entity_id"] for t in targets} == {OFFICE["entity_id"], OFFICE2["entity_id"]}, spoken


def test_technical_names_match_spoken_forms():
    # "satellite-01-ma" is unspeakable; hyphens fold to spaces for matching.
    targets, _ = _resolve("satellite 01 ma")
    assert targets == [OFFICE]


def test_substring_matches_a_player_name():
    targets, label = _resolve("sonos move")
    assert targets == [SONOS] and label == "the Sonos Move Derp speaker"


def test_ambiguous_name_is_an_error_that_names_both():
    err = _err("sonos")   # Sonos Move Derp AND Office Sonos
    assert err.status_code == 400
    assert "Sonos Move Derp" in err.detail and "Office Sonos" in err.detail


def test_unknown_speaker_lists_what_exists():
    err = _err("garage")
    assert err.status_code == 400
    assert "garage" in err.detail and "Kitchen" in err.detail and "kitchen voice pe" in err.detail


def test_origin_area_used_when_nothing_spoken():
    targets, label = _resolve(None, origin="Office")
    assert targets == [OFFICE, OFFICE2] and label == "the Office speakers"


def test_spoken_name_beats_origin_area():
    targets, _ = _resolve("kitchen", origin="Office")
    assert targets == [KITCHEN]


def test_origin_area_without_a_player_falls_to_default():
    targets, label = _resolve(None, origin="Bedroom")
    assert targets == [KITCHEN] and label == "the Kitchen speaker"


def test_default_used_when_nothing_spoken_and_no_origin():
    targets, label = _resolve()
    assert targets == [KITCHEN] and label == "the Kitchen speaker"


def test_default_not_registered_is_a_clear_error():
    err = _err(default="media_player.gone")
    assert err.status_code == 503 and "media_player.gone" in err.detail


def test_no_default_configured():
    err = _err(default="")
    assert err.status_code == 400


def test_empty_player_list_is_a_clear_error():
    err = _err("office", players=[])
    assert err.status_code == 503 and "no Music Assistant players" in err.detail


# ── the route, against a fake HA ────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._body


class FakeHA:
    """Minimal httpx.AsyncClient stand-in: template → players, play_media → 200,
    state polls → `playing` on the first live target after one poll."""
    def __init__(self, *a, **kw):
        self.posts = []
        self.polls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append((url, json))
        FakeHA.last = self
        if url.endswith("/api/template"):
            return _Resp(FakeHA.template_status, text=FakeHA.template_text)
        if url.endswith("/music_assistant/play_media"):
            return _Resp(200, {})
        raise AssertionError(url)

    async def get(self, url, headers=None):
        self.polls += 1
        entity = url.rsplit("/", 1)[1]
        playing = entity == FakeHA.playing_entity
        return _Resp(200, {"state": "playing" if playing else "idle",
                           "attributes": {"media_artist": "Aerosmith", "media_title": "Toys in the Attic"}})


@pytest.fixture
def ha():
    FakeHA.template_status = 200
    FakeHA.template_text = json.dumps(PLAYERS)
    FakeHA.playing_entity = OFFICE["entity_id"]
    FakeHA.last = None

    async def no_sleep(_):
        return None

    with patch.object(ts.httpx, "AsyncClient", FakeHA), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", DEFAULT):
        yield FakeHA


def test_route_plays_on_every_live_player_in_the_room(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "player": "the office"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "playing" and body["player"] == "the Office speakers"
    assert body["artist"] == "Aerosmith" and body["title"] == "Toys in the Attic"
    play = [j for u, j in ha.last.posts if u.endswith("play_media")][0]
    assert play["entity_id"] == [OFFICE["entity_id"], OFFICE2["entity_id"]]
    assert play["media_id"] == "Aerosmith"


def test_route_uses_origin_area_when_nothing_named(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "origin_area": "Office"})
    assert resp.status_code == 200
    play = [j for u, j in ha.last.posts if u.endswith("play_media")][0]
    assert OFFICE["entity_id"] in play["entity_id"]


def test_route_unavailable_target_is_spoken_clearly(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "player": "sonos move"})
    assert resp.status_code == 503
    assert "Sonos Move Derp is unavailable" in resp.json()["detail"]
    assert not [u for u, _ in ha.last.posts if u.endswith("play_media")]


def test_route_ha_template_failure_is_specific(ha):
    ha.template_status = 500
    ha.template_text = "<html>boom</html>"
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith"})
    assert resp.status_code == 502
    assert "could not list the music players (HTTP 500)" in resp.json()["detail"]


def test_route_unreadable_player_list_is_specific(ha):
    ha.template_text = "not json"
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith"})
    assert resp.status_code == 502
    assert "unreadable player list" in resp.json()["detail"]


def test_route_playback_never_starting_names_the_target(ha):
    ha.playing_entity = "media_player.nothing"
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 0):
        resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "player": "kitchen"})
    assert resp.status_code == 502
    assert "did not start on the Kitchen speaker" in resp.json()["detail"]
