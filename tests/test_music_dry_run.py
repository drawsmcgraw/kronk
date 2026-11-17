"""MUSIC_DRY_RUN (sprint M17; M10 step 5): the dev tool_service resolves a
music request as usual and answers with what it would have played, without
sending anything that changes a player or Music Assistant.

Both states are pinned: off (the production default) still calls play_media;
on sends no HA service call and no MA write, marks the result and the trace,
and the orchestrator never renders it as "Now playing". Transport control and
ratings under dry run are tested next to their features
(test_playback_control.py, test_music_rate.py).
"""
import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import tool_service.main as ts
from tool_service import taste, telemetry
from tests.test_error_surfacing import FakeHA, music_env  # noqa: F401  (fixture)
from tests.test_music_resolve import SCHNAUSS_WORKS, search_env  # noqa: F401  (fixture)
from tests.test_music_taste import genre_env  # noqa: F401  (fixture)

import agents
import tools


@pytest.fixture
def dry():
    with patch.object(ts, "MUSIC_DRY_RUN", True):
        yield


def _post(path="/music", **body):
    return TestClient(ts.app).post(path, json=body)


# ── off: production behaviour is unchanged ──────────────────────────────────

def test_default_is_off():
    """Production compose sets no MUSIC_DRY_RUN; the test env sets none either."""
    assert ts.MUSIC_DRY_RUN is False
    assert TestClient(ts.app).get("/health").json() == {"status": "ok", "music_dry_run": False}


def test_off_still_plays(music_env):
    r = _post(query="Portishead", media_type="artist")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "playing" and "dry_run" not in r.json()
    assert [c[1] for c in music_env.calls] == ["play_media"]


# ── on: resolve, then stop before anything is sent ──────────────────────────

def test_on_named_play_sends_nothing_and_says_what_it_would_play(music_env, dry):
    r = _post(query="Portishead", media_type="artist", shuffle=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert music_env.calls == []                              # no play_media, no shuffle_set
    assert body["dry_run"] is True and body["status"] == "dry_run" and body["action"] == "play"
    assert body["would_play"] == {"media_id": "Portishead", "media_type": "artist"}
    assert body["targets"] == ["media_player.kitchen_ma"]
    assert body["detail"] == ("Dry run: would play the artist Portishead on the Kitchen speaker, shuffled. "
                              "Nothing was sent to the speaker.")


def test_on_resolved_album_names_the_record_search_found(search_env, dry):
    search_env.search = {"Ulrich Schnauss": SCHNAUSS_WORKS}
    r = _post(query="Destiny Waving by Ulrich Schnauss on YouTube Music", media_type="album")
    assert r.status_code == 200, r.text
    assert search_env.calls == []
    body = r.json()
    assert body["would_play"] == {"media_id": "ytmusic://album/MPREb_FZkLnRytAxt", "media_type": "album"}
    assert body["resolved"]["name"] == "Destiny Waiving"
    assert body["detail"].startswith("Dry run: would play the album Destiny Waiving by Ulrich Schnauss")
    # the searches themselves still ran: dry run exercises the resolver
    assert [c[0] for c in search_env.ma_calls] and all(c[0] == "music/search" for c in search_env.ma_calls)


def test_on_pandora_station_lookup_runs_but_nothing_plays(music_env, dry):
    music_env.station_item = {"uri": "pandora://radio/S1", "name": "Portishead Radio", "item_id": "S1",
                              "provider": "pandora", "provider_mappings": []}
    r = _post(query="Portishead Radio", media_type="radio")
    assert r.status_code == 200, r.text
    assert music_env.calls == []
    assert [c[0] for c in music_env.ma_calls] == ["music/item_by_name"]      # a read
    assert r.json()["station"] == {"name": "Portishead Radio", "created": False}
    assert r.json()["would_play"]["media_id"] == "pandora://radio/S1"
    assert "the Pandora station Portishead Radio" in r.json()["detail"]


def test_on_genre_pick_is_chosen_but_not_played_or_recorded(genre_env, dry):
    r = _post(query="some deep house", kind="genre")
    assert r.status_code == 200, r.text
    assert genre_env.calls == []                              # no shuffle_set, no play_media
    body = r.json()
    assert body["pick"] == {"label": "Parov Stelar", "phrase": "deep house", "kind": "artist"}
    assert body["would_play"] == {"media_id": "library://artist/ps", "media_type": "artist"}
    assert body["detail"] == ("Dry run: would play Parov Stelar, for deep house, on the Kitchen speaker. "
                              "Nothing was sent to the speaker.")
    with taste.db(genre_env.db) as con:
        assert con.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0
    assert genre_env.watched == [] and ts._LAST_PICKS == {}


def test_on_voice_handoff_speaks_the_dry_run_sentence(music_env, dry):
    r = _post("/music/voice", query="Portishead", media_type="artist")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["say"].startswith("Dry run: would play the artist Portishead")
    assert music_env.calls == []


def test_on_health_reports_it(dry):
    assert TestClient(ts.app).get("/health").json()["music_dry_run"] is True


def test_on_marks_the_trace(music_env, dry, monkeypatch):
    monkeypatch.setattr(telemetry, "RECORD_TEXT", True)     # music.media_id is text (M12)
    exp = InMemorySpanExporter()
    telemetry.enable_for_tests(exp)
    try:
        from opentelemetry.trace import SpanKind
        with telemetry._tracer.start_as_current_span("POST /music", kind=SpanKind.SERVER):
            asyncio.run(ts.play_music(ts.MusicRequest(query="Portishead", media_type="artist")))
    finally:
        telemetry.ENABLED = False
    spans = {s.name: s for s in exp.get_finished_spans()}
    assert "music.play_verified" not in spans
    mark = spans["music.dry_run"]
    assert mark.attributes["music.dry_run"] is True
    assert mark.attributes["music.action"] == "play"
    assert mark.attributes["music.media_id"] == "Portishead"
    assert tuple(mark.attributes["music.targets"]) == ("media_player.kitchen_ma",)


# ── orchestrator: a dry run is never spoken as a play ───────────────────────

class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code = body, status_code

    def json(self):
        return self._body


class _Client:
    def __init__(self, body):
        self.body = body

    async def post(self, url, json=None):
        return _Resp(self.body)


@pytest.mark.asyncio
async def test_orchestrator_renders_dry_run_and_speaks_it_verbatim():
    detail = "Dry run: would play the artist Portishead on the Office speaker. Nothing was sent to the speaker."
    result = await tools._tool_play_music(_Client({"status": "dry_run", "dry_run": True, "player": "the Office speaker",
                                                   "detail": detail}), {"query": "Portishead"})
    assert result == f"[Music dry run: {detail}]"
    assert agents._terminal_speech(result, tool="play_music") == detail


@pytest.mark.asyncio
async def test_orchestrator_real_play_still_renders_as_playing():
    result = await tools._tool_play_music(_Client({"status": "playing", "player": "the Office speaker",
                                                   "title": "Glory Box", "artist": "Portishead"}),
                                          {"query": "Portishead"})
    assert result == "[Music playing: Glory Box by Portishead on the Office speaker]"
    assert agents._terminal_speech(result, tool="play_music") == "Now playing Glory Box by Portishead on the Office speaker."


# ── SOLAR_POLL_ENABLED: the dev tool_service reads solar.db, never polls ────

@pytest.mark.parametrize("flag, polls", [(None, True), ("1", True), ("0", False)])
def test_solar_poller_gate(monkeypatch, flag, polls):
    started = []

    async def idle():
        await asyncio.sleep(3600)

    async def fake_poll():
        started.append("poll")
        await asyncio.sleep(3600)

    monkeypatch.setattr(ts, "_weather_refresh_loop", idle)
    monkeypatch.setattr(ts, "_solar_poll_loop", fake_poll)
    monkeypatch.setattr(ts.solar, "SOLAR_SERIAL", "ZT0000")
    monkeypatch.setattr(ts.solar, "init_db", lambda: started.append("init_db"))
    monkeypatch.setenv("NEWS_ENABLED", "0")
    monkeypatch.setenv("MUSIC_TASTE_ENABLED", "0")
    if flag is None:
        monkeypatch.delenv("SOLAR_POLL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SOLAR_POLL_ENABLED", flag)
    with TestClient(ts.app) as c:
        assert c.get("/health").status_code == 200
    assert started == (["init_db", "poll"] if polls else [])
