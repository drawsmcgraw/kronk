"""POST /music/say (sprint M11, criteria 12 and 13): a sentence and a device
in, the decision order in tool_service, the existing play path, one sentence
back. Music Assistant is the recorded snapshot (tests/music_snapshot.py)."""
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tests import music_snapshot as snapmod

pytestmark = pytest.mark.usefixtures("no_network")

SNAP = snapmod.load()
ROOT = Path(__file__).resolve().parent.parent
KITCHEN = snapmod.DEVICE_IDS["kitchen_pe"]


class _NoModel:
    """The model question is out of these tests' scope: no answer."""
    async def __call__(self, sentence, words):
        return None, {"error": "not asked in this test"}


@pytest.fixture
def say(tmp_path):
    with snapmod.offline_env(SNAP, tmp_path) as replay, \
         patch.object(ts, "ask_style_or_named", new=_NoModel()):
        def post(sentence, **kw):
            r = TestClient(ts.app).post("/music/say", json={"sentence": sentence, **kw})
            assert r.status_code == 200, r.text
            return r.json()
        post.replay = replay
        yield post


def test_a_named_artist_resolves_and_answers_with_what_would_play(say):
    b = say("play Portishead", origin_device=KITCHEN, origin_area="Kitchen")
    assert b["ok"] and b["dry_run"] is True
    assert b["decision"]["decided_by"] == "named" and b["decision"]["via"] == "exact"
    assert b["result"]["would_play"] == {"media_id": "library://artist/1251", "media_type": "artist", "enqueue": "replace"}
    assert b["result"]["targets"] == ["media_player.home_assistant_voice_0ac919_pe_01"]
    assert b["say"] == ("Dry run: would play the artist Portishead on the Kitchen speaker. "
                        "Nothing was sent to the speaker.")
    assert b["request"]["said"] == "play Portishead" and b["request"]["kind"] == "named"


def test_a_style_plays_a_genre_pick(say):
    b = say("put on some jazz", origin_device=KITCHEN, origin_area="Kitchen")
    assert b["ok"] and b["decision"]["kind"] == "genre" and b["decision"]["name"] == "jazz"
    assert b["result"]["pick"]["phrase"] == "jazz" and "://" in b["result"]["would_play"]["media_id"]


def test_room_at_the_right_hand_end(say):
    b = say("play The Girl with the Sun in Her Head by Orbital in the office", origin_device=KITCHEN,
            origin_area="Kitchen")
    assert b["ok"] and b["result"]["targets"] == ["media_player.satellite1_d8d1bc"]
    assert b["decision"]["media_type"] == "track" and b["decision"]["artist"] == "Orbital"


def test_not_music_is_refused_without_a_lookup(say):
    b = say("play the news")
    assert (b["ok"], b["decision"]["action"]) == (False, "not_music")
    assert say.replay.calls == []


def test_an_unknown_speaker_says_what_exists(say):
    b = say("play pink floyd on the blue group")
    assert b["ok"] is False and b["say"].startswith("I couldn't play that. I don't know a speaker or room called "
                                                    "'blue group'.")


def test_nothing_found_is_said_plainly_and_nothing_plays(say):
    b = say("put on the radio station Zorblax Fnordwave Nonexistent")
    assert b["ok"] is False and "result" not in b and b["targets"] == [snapmod.DEFAULT_PLAYER]
    assert b["say"].startswith("I couldn't find a station called Zorblax Fnordwave Nonexistent")


def test_every_corpus_play_is_a_uri_never_the_words(tmp_path):
    """Rule 8 and the plan: the new path never passes the raw words to
    Music Assistant. Every corpus row that plays on /music/say plays a URI."""
    with snapmod.offline_env(SNAP, tmp_path), patch.object(ts, "ask_style_or_named", new=_NoModel()):
        for row in snapmod.corpus_rows():
            area, device = snapmod.origin_of(row)
            r = TestClient(ts.app).post("/music/say", json={"sentence": row["text"], "origin_area": area,
                                                            "origin_device": device}).json()
            if r.get("result"):
                mid = r["result"]["would_play"]["media_id"]
                assert re.match(r"^[a-z_]+(--\w+)?://", mid), (row["id"], mid)


# ── dry run: the setting, and a field that can only turn it on ──────────────

def test_dry_run_field_forces_a_dry_run_when_the_setting_is_off(tmp_path):
    with snapmod.offline_env(SNAP, tmp_path), patch.object(ts, "MUSIC_DRY_RUN", False), \
         patch.object(ts, "ask_style_or_named", new=_NoModel()):
        b = TestClient(ts.app).post("/music/say", json={"sentence": "play Portishead", "dry_run": True}).json()
    assert b["ok"] and b["dry_run"] is True and b["result"]["dry_run"] is True     # no HA call was made


def test_dry_run_field_false_cannot_turn_the_setting_off(tmp_path):
    with snapmod.offline_env(SNAP, tmp_path), patch.object(ts, "ask_style_or_named", new=_NoModel()):
        b = TestClient(ts.app).post("/music/say", json={"sentence": "play Portishead", "dry_run": False}).json()
    assert b["dry_run"] is True and b["result"]["dry_run"] is True


def test_off_dry_run_it_plays_the_decided_uri_through_the_existing_path(tmp_path):
    played = []

    async def verified(client, headers, entities, data):
        played.append((entities, data))
        return (entities[0], {"state": "playing", "attributes": {"media_title": "Glory Box",
                                                                 "media_artist": "Portishead",
                                                                 "shuffle": False}}), None

    async def no_shuffle(*a, **kw):
        return None
    with snapmod.offline_env(SNAP, tmp_path), patch.object(ts, "MUSIC_DRY_RUN", False), \
         patch.object(ts, "_play_verified", new=verified), patch.object(ts, "_shuffle_before_play", new=no_shuffle), \
         patch.object(ts, "ask_style_or_named", new=_NoModel()):
        b = TestClient(ts.app).post("/music/say", json={"sentence": "play Portishead", "origin_device": KITCHEN,
                                                        "origin_area": "Kitchen"}).json()
    assert played == [(["media_player.home_assistant_voice_0ac919_pe_01"],
                       {"media_id": "library://artist/1251", "media_type": "artist", "enqueue": "replace"})]
    assert b["ok"] and b["say"] == "Portishead on the Kitchen speaker."


# ── not on the LAN, and not wired to anything live ──────────────────────────

def test_nginx_proxies_nothing_but_solar_and_health_to_tool_service():
    conf = (ROOT / "nginx" / "nginx.conf").read_text()
    targets = re.findall(r"proxy_pass\s+http://tool_service(?:-dev)?:\d+(/[^;]*)?;", conf)
    assert targets and set(targets) <= {"/solar/", "/health"}, targets


def test_tool_service_ports_are_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    for port in ("8003", "8103"):
        binds = re.findall(rf'-\s*"([^"]*:{port}:{port})"', compose)
        assert binds and all(b.startswith("127.0.0.1:") for b in binds), (port, binds)


def test_nothing_in_the_orchestrator_or_ha_calls_music_say():
    hits = [str(f.relative_to(ROOT)) for d in ("orchestrator", "ha", "nginx", "voice_tracer")
            for f in (ROOT / d).rglob("*") if f.is_file() and f.suffix in (".py", ".yaml", ".conf", ".json")
            and "/music/say" in f.read_text(errors="ignore")]
    assert hits == []
