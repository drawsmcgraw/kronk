"""Every play states what happens to the queue: enqueue replace (sprint M23,
fix 1 of docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md).

Music Assistant's per-queue default for a track is `play`: insert the track
at the current position, start it, keep everything after it. Neither
tool_service nor the blueprint said otherwise, so "play the song X" played X
and then the old shuffled queue came back (2026-11-21, twice). Every other
media type already defaulted to `replace`, so the explicit value changes
only tracks.

Pinned here: the service data of every play path (the named play, the
literal fallback, a station, a link, Kronk's picks, "not that", the dry-run
answer) and the blueprint's only play_media.
"""
from pathlib import Path

import jinja2
import pytest
import yaml
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import music_resolve, taste
from tests.test_error_surfacing import FakeHA, music_env  # noqa: F401  (fixture)
from tests.test_music_taste import genre_env  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
BLUEPRINT = REPO / "ha/blueprints/mass_assist_kronk.yaml"

# HA 2026.5.4, components/music_assistant/services.yaml, play_media `enqueue`
# (read from the running container, 2026-11-21); the schema coerces the
# value to Music Assistant's QueueOption.
HA_ENQUEUE_OPTIONS = ("play", "replace", "next", "replace_next", "add")


def test_the_value_is_replace_and_ha_accepts_it():
    assert music_resolve.PLAY_ENQUEUE == "replace"
    assert music_resolve.PLAY_ENQUEUE in HA_ENQUEUE_OPTIONS


def test_tool_service_has_one_play_media_call_and_the_orchestrator_none():
    """Every play goes through _play_verified_inner, which sends what its
    callers built. A second call site would need its own pin here."""
    calls = {}
    for f in sorted((REPO / "tool_service").glob("*.py")):
        n = f.read_text().count('ha_call_service("music_assistant", "play_media"')
        if n:
            calls[f.name] = n
    assert calls == {"main.py": 1}
    assert "ma_command(\"player_queues/play_media\"" not in (REPO / "tool_service/main.py").read_text()
    for f in (REPO / "orchestrator").glob("*.py"):
        assert "play_media" not in f.read_text(), f.name


def _plays(env) -> list[dict]:
    return [c[2] for c in env.calls if c[:2] == ("music_assistant", "play_media")]


@pytest.mark.parametrize("body", [
    {"query": "Right Here, Right Now by Fatboy Slim", "media_type": "track"},   # the incident's shape
    {"query": "Hotel Costes 3 on YouTube Music", "media_type": "album"},
    {"query": "Portishead", "media_type": "artist"},
    {"query": "Portishead Radio", "media_type": "radio"},
    {"query": "Portishead"},                                                     # untyped literal
    {"query": "https://music.youtube.com/playlist?list=OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU"},
], ids=["track", "album-provider-hint", "artist", "radio", "untyped", "link"])
def test_named_play_sends_enqueue_replace(music_env, body):
    r = TestClient(ts.app).post("/music", json=body)
    assert r.status_code == 200, r.text
    plays = _plays(music_env)
    assert plays and all(p["enqueue"] == "replace" for p in plays), plays


def test_named_play_of_a_resolved_uri_sends_enqueue_replace(music_env):
    music_env.station_item = {"name": "Portishead Radio", "uri": "library://radio/9", "provider": "pandora"}
    r = TestClient(ts.app).post("/music", json={"query": "Portishead Radio", "media_type": "radio"})
    assert r.status_code == 200, r.text
    assert _plays(music_env) == [{"media_id": "library://radio/9", "media_type": "radio", "enqueue": "replace"}]


def test_genre_pick_sends_enqueue_replace(genre_env):
    r = TestClient(ts.app).post("/music", json={"query": "some deep house", "kind": "genre"})
    assert r.status_code == 200, r.text
    assert _plays(genre_env) == [{"media_id": "library://artist/ps", "media_type": "artist", "enqueue": "replace"}]


def test_no_phrase_pick_sends_enqueue_replace(genre_env):
    """'Play some music': the top of the profile, the incident's run 1."""
    r = TestClient(ts.app).post("/music", json={"query": "music", "kind": "genre"})
    assert r.status_code == 200, r.text
    plays = _plays(genre_env)
    assert len(plays) == 1 and plays[0]["enqueue"] == "replace"


def test_not_that_sends_enqueue_replace(genre_env):
    TestClient(ts.app).post("/music", json={"query": "some deep house", "kind": "genre"})
    r = TestClient(ts.app).post("/music", json={"query": "something else", "kind": "another"})
    assert r.status_code == 200, r.text
    plays = _plays(genre_env)
    assert len(plays) == 2 and all(p["enqueue"] == "replace" for p in plays), plays


@pytest.mark.parametrize("pick", [
    taste.Pick("artist", "library://artist/1", "Cracker", "artist:cracker", 1.0, "artist"),
    taste.Pick("artist", "pandora://artist/Santana", "Santana radio", "artist:santana", 1.0, "artist",
               radio_mode=True, name="Santana"),
    taste.Pick("station", "library://radio/1", "Mushroom Jazz Radio", "uri:1", 1.0, "radio"),
    taste.Pick("playlist", "ytmusic://playlist/x", "Deep House Mix", "uri:x", 0.25, "playlist"),
], ids=["artist", "pandora-artist-radio", "station", "playlist"])
def test_every_pick_kind_says_replace(pick):
    assert pick.play_data()["enqueue"] == "replace"


def test_dry_run_answer_carries_it(music_env):
    """What the dev path shows: the data that would be sent."""
    from unittest.mock import patch
    with patch.object(ts, "MUSIC_DRY_RUN", True):
        r = TestClient(ts.app).post("/music", json={"query": "Portishead", "media_type": "artist"})
    assert r.json()["would_play"]["enqueue"] == "replace"
    assert _plays(music_env) == []


# ── the blueprint (fork change 15) ─────────────────────────────────────────

class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _bp() -> dict:
    return yaml.load(BLUEPRINT.read_text(), Loader=_Loader)


def test_blueprint_has_one_play_media_and_it_sends_action_data():
    plays = [n for n in _walk(_bp()) if n.get("action") == "music_assistant.play_media"]
    assert len(plays) == 1
    assert plays[0]["data"] == "{{ dict(action_data.items() | selectattr('1')) }}"


def test_blueprint_action_data_says_enqueue_replace():
    assignments = [n["action_data"] for n in _walk(_bp()) if "action_data" in n]
    first, rest = assignments[0], assignments[1:]
    assert first["enqueue"] == "replace"
    # every later assignment derives from action_data, so it keeps the key
    assert rest and all(isinstance(a, str) and "dict(action_data," in a for a in rest), rest


@pytest.mark.parametrize("playlist_uri", ["", "library://playlist/49"])
def test_blueprint_play_data_renders_with_enqueue_replace(playlist_uri):
    """The play's data template keeps enqueue (a truthy value survives
    selectattr('1')), also after change 13 swaps in a playlist's URI."""
    env = jinja2.Environment()
    action_data = {"media_id": "Rockefeller Skank by Fatboy Sled", "media_type": "track", "artist": "",
                   "radio_mode": False, "enqueue": _bp()["actions"][0]["variables"]["action_data"]["enqueue"]}
    swap = next(a for a in (n["action_data"] for n in _walk(_bp()) if "action_data" in n) if isinstance(a, str))
    if playlist_uri:
        action_data = env.compile_expression(swap.strip("{} "))(action_data=action_data, playlist_uri=playlist_uri)
    out = env.compile_expression("dict(action_data.items() | selectattr('1'))")(action_data=action_data)
    assert out["enqueue"] == "replace"
    assert "radio_mode" not in out and "artist" not in out       # falsy values still dropped
    if playlist_uri:
        assert out["media_id"] == playlist_uri


def test_blueprint_header_and_version_name_change_15():
    text = BLUEPRINT.read_text()
    assert "#  15. (2026-11-21) every play_media says `enqueue: replace`" in text
    assert "version: 20250404-kronk15" in text
