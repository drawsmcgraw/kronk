"""tool_service /music — players discovered from HA, resolved in the Kronk
blueprint fork's order (docs/plans/MUSIC_PLAYERS_FROM_HA_PLAN.md,
docs/plans/VOICE_MUSIC_ORIGIN_KRONK_PLAN.md).

The order is pinned here because the fork's Jinja and this Python cannot
share code: exact name → exact room (group-preferred) → substring name →
substring room → own device → own room (group-preferred) → default.
"""
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tool_service.main as ts

KITCHEN = {"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen",
           "state": "idle", "type": "player", "device_key": "up20f83b0ac919"}
OFFICE = {"entity_id": "media_player.satellite1", "name": "satellite-01-ma", "area": "Office",
          "state": "idle", "type": "player", "device_key": "up14c19fd8d1bc"}
OFFICE2 = {"entity_id": "media_player.office_sonos", "name": "Office Sonos", "area": "Office",
           "state": "idle", "type": "player", "device_key": "upaaaaaaaaaaaa"}
SONOS = {"entity_id": "media_player.sonos_move_2", "name": "Sonos Move Derp", "area": None,
         "state": "unavailable", "type": None, "device_key": "upbbbbbbbbbbbb"}
PLAYERS = [KITCHEN, OFFICE, OFFICE2, SONOS]
DEFAULT = KITCHEN["entity_id"]
OFFICE_DEV = "ac6dd1f1c8cf1d229cda0f42224a2013"


def _resolve(spoken=None, origin=None, players=PLAYERS, default=DEFAULT, origin_key=None):
    return ts.resolve_players(spoken, origin, players, default, origin_key=origin_key)


def _err(*a, **kw) -> HTTPException:
    with pytest.raises(HTTPException) as ei:
        _resolve(*a, **kw)
    return ei.value


# ── parsers ─────────────────────────────────────────────────────────────────

def test_parse_players_normalizes_and_skips_junk():
    raw = json.dumps([
        {"entity_id": "media_player.a", "name": "A", "area": "Kitchen", "state": "idle",
         "type": "player", "device_key": "up1"},
        {"entity_id": "media_player.b", "name": None, "area": None, "state": None},
        {"name": "no entity"},
        "garbage",
    ])
    out = ts.parse_players(raw)
    assert out == [
        {"entity_id": "media_player.a", "name": "A", "area": "Kitchen", "state": "idle",
         "type": "player", "device_key": "up1"},
        {"entity_id": "media_player.b", "name": "media_player.b", "area": None, "state": "unknown",
         "type": None, "device_key": None},
    ]


def test_parse_players_rejects_non_list():
    with pytest.raises(ValueError):
        ts.parse_players('{"not": "a list"}')


def test_parse_player_payload_dict_and_bare_list():
    players, key = ts.parse_player_payload(json.dumps({"players": [KITCHEN], "origin_key": "up20f83b0ac919"}))
    assert players[0]["entity_id"] == KITCHEN["entity_id"] and key == "up20f83b0ac919"
    players, key = ts.parse_player_payload(json.dumps({"players": [KITCHEN], "origin_key": ""}))
    assert key is None
    players, key = ts.parse_player_payload(json.dumps([KITCHEN]))
    assert players[0]["entity_id"] == KITCHEN["entity_id"] and key is None
    with pytest.raises(ValueError):
        ts.parse_player_payload('{"players": "nope"}')


def test_players_template_only_injects_a_valid_device_id():
    assert f"'{OFFICE_DEV}'" in ts._players_template(OFFICE_DEV)
    assert "none" in ts._players_template(None)
    assert "none" in ts._players_template("'; drop table") and "drop" not in ts._players_template("'; drop table")


# ── spoken: name → room → substring ─────────────────────────────────────────

def test_exact_player_name_wins_over_area():
    targets, label = _resolve("Office Sonos")
    assert targets == [OFFICE2] and label == "the Office Sonos speaker"


def test_area_name_targets_every_player_in_the_room():
    targets, label = _resolve("office")
    assert targets == [OFFICE, OFFICE2] and label == "the Office speakers"


def test_room_with_a_sync_group_targets_the_group_only():
    group = {"entity_id": "media_player.office_group", "name": "Office Group", "area": "Office",
             "state": "idle", "type": "group", "device_key": None}
    targets, label = _resolve("the office", players=PLAYERS + [group])
    assert targets == [group] and label == "the Office speaker"


def test_spoken_phrasing_is_normalized():
    for spoken in ("the office", "Office speaker", "the office speakers", "office room"):
        targets, _ = _resolve(spoken)
        assert {t["entity_id"] for t in targets} == {OFFICE["entity_id"], OFFICE2["entity_id"]}, spoken


def test_technical_names_match_spoken_forms():
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


# ── unspoken: own device → own room → default ───────────────────────────────

def test_own_device_wins_over_own_room():
    # heard by satellite 1 in the Office, which also has the Office Sonos
    targets, label = _resolve(None, origin="Office", origin_key=OFFICE["device_key"])
    assert targets == [OFFICE] and label == "the Office speaker"


def test_own_device_without_area_is_labelled_by_name():
    lone = dict(OFFICE, area=None)
    targets, label = _resolve(None, origin=None, origin_key=lone["device_key"],
                              players=[KITCHEN, lone])
    assert targets == [lone] and label == "the satellite-01-ma speaker"


def test_unknown_device_key_falls_to_own_room():
    targets, label = _resolve(None, origin="Office", origin_key="upnobody")
    assert targets == [OFFICE, OFFICE2] and label == "the Office speakers"


def test_own_room_prefers_its_sync_group():
    group = {"entity_id": "media_player.office_group", "name": "Office Group", "area": "Office",
             "state": "idle", "type": "group", "device_key": None}
    targets, _ = _resolve(None, origin="Office", origin_key="upnobody", players=PLAYERS + [group])
    assert targets == [group]


def test_spoken_name_beats_own_device():
    targets, _ = _resolve("kitchen", origin="Office", origin_key=OFFICE["device_key"])
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
    """Minimal httpx.AsyncClient stand-in: template → payload, play_media → 200,
    state polls → `playing` on the configured entity."""
    def __init__(self, *a, **kw):
        self.posts = []

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
        entity = url.rsplit("/", 1)[1]
        playing = entity == FakeHA.playing_entity
        return _Resp(200, {"state": "playing" if playing else "idle",
                           "attributes": {"media_artist": "Aerosmith", "media_title": "Toys in the Attic"}})


@pytest.fixture
def ha():
    FakeHA.template_status = 200
    FakeHA.template_text = json.dumps({"players": PLAYERS, "origin_key": ""})
    FakeHA.playing_entity = OFFICE["entity_id"]
    FakeHA.last = None

    async def no_sleep(_):
        return None

    with patch.object(ts.httpx, "AsyncClient", FakeHA), \
         patch.object(ts.asyncio, "sleep", new=no_sleep), \
         patch.object(ts, "HA_TOKEN", "t"), \
         patch.object(ts, "MUSIC_DEFAULT_PLAYER", DEFAULT):
        yield FakeHA


def _play_calls(ha):
    return [j for u, j in ha.last.posts if u.endswith("play_media")]


def test_route_plays_on_every_live_player_in_the_room(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "player": "the office"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "playing" and body["player"] == "the Office speakers"
    assert body["artist"] == "Aerosmith" and body["title"] == "Toys in the Attic"
    play = _play_calls(ha)[0]
    assert play["entity_id"] == [OFFICE["entity_id"], OFFICE2["entity_id"]]
    assert play["media_id"] == "Aerosmith"


def test_route_own_device_from_origin_key(ha):
    ha.template_text = json.dumps({"players": PLAYERS, "origin_key": OFFICE["device_key"]})
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "origin_device": OFFICE_DEV,
                                                   "origin_area": "Office"})
    assert resp.status_code == 200
    assert _play_calls(ha)[0]["entity_id"] == [OFFICE["entity_id"]]
    assert resp.json()["player"] == "the Office speaker"
    # the origin device id reached the template
    tmpl = [j for u, j in ha.last.posts if u.endswith("/api/template")][0]["template"]
    assert f"'{OFFICE_DEV}'" in tmpl


def test_route_uses_origin_area_when_device_has_no_player(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "origin_area": "Office",
                                                   "origin_device": "not-a-device-id"})
    assert resp.status_code == 200
    assert OFFICE["entity_id"] in _play_calls(ha)[0]["entity_id"]
    tmpl = [j for u, j in ha.last.posts if u.endswith("/api/template")][0]["template"]
    assert "not-a-device-id" not in tmpl and "device_attr(none" in tmpl


def test_route_unavailable_target_is_spoken_clearly(ha):
    resp = TestClient(ts.app).post("/music", json={"query": "Aerosmith", "player": "sonos move"})
    assert resp.status_code == 503
    assert "Sonos Move Derp is unavailable" in resp.json()["detail"]
    assert not _play_calls(ha)


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
