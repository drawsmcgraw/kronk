"""Household music taste (docs/plans/MUSIC_TASTE_AGENT_PLAN.md).

Step 3: the told half — one operator-editable YAML file that "we love X" /
"never play X" / "when I say chill I mean …" also write, comments intact.
"""
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import taste


@pytest.fixture
def tf(tmp_path):
    f = taste.TasteFile(tmp_path / "music_taste.yaml")
    with patch.object(taste, "TASTE", f):
        yield f


def test_missing_file_is_created_with_defaults(tf):
    t = tf.load()
    assert tf.path.exists() and "Kronk's household music taste" in tf.path.read_text()
    assert t["love"] == [] and t["never"] == []
    assert t["genres"]["chill"] == ["deep house", "downtempo", "chillout"]
    assert tf.error is None


def test_writes_keep_the_operators_comments(tf):
    tf.load()
    tf.path.write_text("# my notes — keep me\nlove:\n  - Orbital   # since 1993\nnever: []\n")
    said = tf.update("love", "Ulrich Schnauss")
    assert said.startswith("Got it — you love Ulrich Schnauss")
    text = tf.path.read_text()
    assert "# my notes — keep me" in text and "# since 1993" in text
    assert tf.load()["love"] == ["Orbital", "Ulrich Schnauss"]


def test_love_and_never_are_exclusive_and_case_insensitive(tf):
    tf.update("love", "Nickelback")
    tf.update("never", "nickelback")
    t = tf.load()
    assert t["love"] == [] and t["never"] == ["nickelback"]
    tf.update("never", "NICKELBACK")                       # no duplicate
    assert tf.load()["never"] == ["nickelback"]
    assert tf.update("forget", "Nickelback") == "Forgotten — Nickelback is off the list."
    assert tf.load()["never"] == []
    assert tf.update("forget", "Nickelback") == "Nickelback wasn't on the love or never list."


def test_means_defines_a_genre_word(tf):
    tf.update("means", "Chill", ["deep house", "downtempo"])
    assert tf.load()["genres"]["chill"] == ["deep house", "downtempo"]
    with pytest.raises(taste.TasteError):
        tf.update("means", "chill", [])


def test_broken_file_keeps_last_good_and_is_never_overwritten(tf):
    tf.update("love", "Orbital")
    assert tf.load()["love"] == ["Orbital"]
    tf.path.write_text("love: [Orbital\nnever: oops")          # operator typo
    os.utime(tf.path, (1, 1))                                  # force a new mtime
    assert tf.load()["love"] == ["Orbital"]                    # last good
    assert "not valid" in tf.error
    with pytest.raises(taste.TasteError, match="didn't change it"):
        tf.update("love", "Tosca")
    assert tf.path.read_text() == "love: [Orbital\nnever: oops"


@pytest.mark.parametrize("bad, msg", [
    ("love: Orbital\n", "love must be a list"),
    ("rules:\n  - {days: [funday], play: jazz}\n", "days must be"),
    ("rules:\n  - {hours: morning, play: jazz}\n", "hours must look like"),
    ("rules:\n  - {days: [sat]}\n", "needs a 'play'"),
])
def test_validation_names_the_problem(tf, bad, msg):
    tf.load()
    tf.path.write_text(bad)
    os.utime(tf.path, (2, 2))
    tf.load()
    assert msg in tf.error


def test_rules_match_day_hours_and_room():
    t = taste.validate({"rules": [
        {"days": ["sat", "sun"], "hours": "07-11", "room": "Kitchen", "play": "jazz"},
        {"hours": "22-06", "play": "ambient"},
    ]})
    sat_9 = datetime(2026, 10, 24, 9)                   # a Saturday
    assert taste.rule_for(t, sat_9, "Kitchen") == "jazz"
    assert taste.rule_for(t, sat_9, "Office") is None
    assert taste.rule_for(t, datetime(2026, 10, 21, 23), "Office") == "ambient"   # wraps midnight
    assert taste.rule_for(t, datetime(2026, 10, 22, 3), None) == "ambient"


# ── endpoints ───────────────────────────────────────────────────────────────

def test_taste_endpoints(tf):
    c = TestClient(ts.app)
    r = c.post("/music/taste", json={"action": "love", "value": "Tosca"})
    assert r.status_code == 200 and "you love Tosca" in r.json()["detail"]
    got = c.get("/music/taste").json()
    assert got["love"] == ["Tosca"] and got["error"] is None
    r = c.post("/music/taste", json={"action": "shrug", "value": "Tosca"})
    assert r.status_code == 400 and "Unknown preference action" in r.json()["detail"]


# ── the orchestrator side ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "never play Nickelback", "Never play any Coldplay.", "remember that we love Ulrich Schnauss",
    "remember I hate smooth jazz", "we love the band Orbital", "I don't like country music",
    "when I say chill I mean deep house and downtempo", "forget that we love Nickelback",
    "Kronk, never play Creed",
])
@pytest.mark.asyncio
async def test_taste_phrasings_pin_to_home(text):
    import routing
    assert await routing._classify_inner(text) == ("home", "taste"), text


@pytest.mark.parametrize("text", [
    "never play this again", "I love this song", "we love pizza", "I like you", "never mind",
    "play some music", "I hate it when it rains",
])
@pytest.mark.asyncio
async def test_taste_pin_rejects(text):
    import routing
    _, rule = await routing._classify_inner(text)
    assert rule != "taste", text


@pytest.mark.asyncio
async def test_remember_tool_payload_and_speech():
    import agents
    import tools
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"detail": "Got it — chill means deep house, downtempo."}
    client = MagicMock(post=AsyncMock(return_value=resp))
    out = await tools._tool_remember_music_preference(
        client, {"action": "means", "value": "chill", "meaning": "deep house, downtempo"})
    assert client.post.call_args.kwargs["json"] == {"action": "means", "value": "chill",
                                                    "meaning": ["deep house", "downtempo"]}
    assert agents._terminal_speech(out) == "Got it — chill means deep house, downtempo."
    assert "remember_music_preference" in agents.AGENTS["home"].terminal_tools


# ── step 4: the learned profile ─────────────────────────────────────────────

def _fake_ma():
    """A small MA: three library artists (one tagged by MA), a history of
    YTM/Pandora tracks, and two stations."""
    full = {
        "library://artist/1": {"name": "Orbital", "metadata": {"genres": ["Techno"]}},
        "library://artist/2": {"name": "Tosca", "metadata": {}},
        "library://artist/3": {"name": "Wilco", "metadata": {}},
        "ytmusic://track/a": {"artists": [{"name": "Parov Stelar", "uri": "ytmusic://artist/ps"}]},
        "ytmusic://track/b": {"artists": [{"name": "Parov Stelar", "uri": "ytmusic://artist/ps"}]},
        "pandora://track/c": {"artists": [{"name": "Tosca", "uri": "pandora://artist/t"}]},
        "ytmusic://artist/ps": {"name": "Parov Stelar", "metadata": {"genres": ["Electro Swing"]}},
    }
    calls = []

    async def ma(command, _timeout=20, **args):
        calls.append(command)
        if command == "music/artists/library_items":
            return [{"name": "Orbital", "uri": "library://artist/1"}, {"name": "Tosca", "uri": "library://artist/2"},
                    {"name": "Wilco", "uri": "library://artist/3"}][args.get("offset", 0):]
        if command == "music/item_by_uri":
            return full.get(args["uri"], {})
        if command == "music/recently_played_items":
            return [{"media_type": "track", "uri": "ytmusic://track/a", "name": "x"},
                    {"media_type": "radio", "uri": "pandora://radio/1", "name": "Tosca Radio"},
                    {"media_type": "track", "uri": "ytmusic://track/b", "name": "y"},
                    {"media_type": "track", "uri": "pandora://track/c", "name": "z"}]
        if command == "music/radios/library_items":
            return [{"name": "Tosca Radio", "uri": "library://radio/1", "provider": "pandora"},
                    {"name": "DEEP HOUSE Radio", "uri": "library://radio/2", "provider": "pandora"}][args.get("offset", 0):]
        raise AssertionError(command)
    return ma, calls


@pytest.mark.asyncio
async def test_build_profile_weights_history_over_library(tmp_path):
    ma, _ = _fake_ma()
    asked = []

    async def tagger(names):
        asked.append(sorted(names))
        return {"Tosca": ["downtempo", "trip hop"], "Wilco": []}

    summary = await taste.build_profile(ma, tmp_path / "t.db", tagger=tagger)
    assert summary["artists"] == 4 and summary["history_items"] == 4 and summary["stations"] == 2
    prof = taste.profile(path=tmp_path / "t.db")
    names = [a["name"] for a in prof["artists"]]
    # Parov Stelar: most history, not on the NAS → top. Tosca: history + its
    # own station + library. Orbital/Wilco: library only → bottom.
    assert names[:2] == ["Parov Stelar", "Tosca"] and set(names[2:]) == {"Orbital", "Wilco"}
    by = {a["name"]: a for a in prof["artists"]}
    assert by["Parov Stelar"]["genres"] == ["electro swing"] and by["Parov Stelar"]["genre_source"] == "ma"
    assert by["Orbital"]["genres"] == ["techno"]
    assert by["Tosca"]["genres"] == ["downtempo", "trip hop"] and by["Tosca"]["genre_source"] == "model"
    assert by["Tosca"]["score"] > by["Orbital"]["score"]
    # the model is asked once per artist, ever — a rebuild reuses the tags
    await taste.build_profile(ma, tmp_path / "t.db", tagger=tagger)
    assert asked == [["Tosca", "Wilco"]]


@pytest.mark.asyncio
async def test_feedback_moves_the_profile(tmp_path):
    ma, _ = _fake_ma()
    await taste.build_profile(ma, tmp_path / "t.db", tagger=AsyncMock(return_value={}))
    taste.record_feedback(taste.artist_key("Wilco"), 5.0, "test", tmp_path / "t.db")
    assert taste.profile(path=tmp_path / "t.db")["artists"][0]["name"] == "Wilco"


@pytest.mark.asyncio
async def test_tagger_parses_the_models_json_and_drops_strangers():
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content":
        'Sure!\n```json\n{"Tosca": ["Downtempo", "trip hop"], "Nobody": ["x"], "wilco": []}\n```'}}]}
    resp.raise_for_status = MagicMock()
    client = MagicMock(post=AsyncMock(return_value=resp))
    out = await taste.tag_artists(["Tosca", "Wilco"], client=client)
    assert out == {"Tosca": ["downtempo", "trip hop"], "Wilco": []}


def test_next_build_time_is_tonight_or_tomorrow():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    assert taste._next_build_time(datetime(2026, 10, 21, 1, 0, tzinfo=tz)).day == 21
    assert taste._next_build_time(datetime(2026, 10, 21, 4, 0, tzinfo=tz)).day == 22


@pytest.mark.parametrize("name, junk", [
    ("00", True), ("01", True), ("[02] Memories Erased in the Storm-", True), ("03-Eye of the World", True),
    ("02 final fantasy iv", True), ("311", False), ("808 State", False), ("10cc", False), ("U2", False),
    ("Blink-182", False), ("2Pac", False), ("50 Cent", False), ("1 Giant Leap", False),
])
def test_track_number_junk_is_not_an_artist(name, junk):
    assert bool(taste._JUNK_ARTIST.match(name)) is junk


# ── step 5: genre requests — Kronk picks ────────────────────────────────────

import json as _json

from tests.test_error_surfacing import FakeHA, music_env  # noqa: E402,F401  (fixture)


@pytest.mark.parametrize("text, phrase", [
    ("some deep house chill music", "deep house chill"), ("something chill", "chill"), ("music", ""),
    ("some music", ""), ("a little jazz please", "jazz"), ("Jazz", "jazz"), ("some trip-hop", "trip hop"),
])
def test_genre_phrase(text, phrase):
    assert taste.genre_phrase(text) == phrase


def test_genre_terms_use_the_households_meaning():
    doc = taste.validate({"genres": {"chill": ["deep house", "downtempo"]}})
    assert taste.genre_terms("chill", doc) == ["deep house", "downtempo", "chill"]
    assert taste.genre_terms("deep chill", doc) == ["deep house", "downtempo", "deep chill"]
    assert taste.genre_terms("jazz", doc) == ["jazz"]


def _seed_db(path, artists, stations=()):
    con = taste.db(path)
    with con:
        for name, uri, genres, score in artists:
            con.execute("INSERT INTO artists VALUES (?,?,?,?,?,?,?,?,?)",
                        (taste.music_resolve.fold(name), name, uri, _json.dumps(genres), "ma", 1.0, 0, 1, score))
        for uri, name, plays in stations:
            con.execute("INSERT INTO stations VALUES (?,?,?,?)", (uri, name, "pandora", plays))
    con.close()


@pytest.fixture
def genre_env(music_env, tmp_path):
    """music_env + a seeded taste DB/file and an MA that answers searches."""
    dbp = tmp_path / "t.db"
    _seed_db(dbp, [
        ("Parov Stelar", "library://artist/ps", ["electro swing", "deep house"], 3.0),
        ("Bonobo", "library://artist/bo", ["downtempo"], 2.0),
        ("Nickelback", "library://artist/nb", ["deep house"], 9.0),          # never — see the file
        ("Pink Floyd", "library://artist/pf", ["progressive rock"], 3.3),
    ], [("library://radio/dh", "DEEP HOUSE Radio", 0.2)])
    tf = taste.TasteFile(tmp_path / "taste.yaml")
    tf.load()
    tf.update("never", "Nickelback")
    music_env.search = {}

    async def fake_ma(command, _timeout=20, **args):
        music_env.ma_calls.append((command, args))
        if command == "music/search":
            return music_env.search.get(args["search_query"], {})
        return music_env.station_item

    class FirstPick:                                    # deterministic choice: always the top
        @staticmethod
        def choices(head, weights, k):
            return [head[0]]

    ts._LAST_PICKS.clear()
    music_env.watched = []

    def no_watch(entity, pick_id, pick, state):         # the watcher has its own tests
        music_env.watched.append((entity, pick_id, pick.label))
        return None

    with patch.object(taste, "TASTE_DB", dbp), patch.object(taste, "TASTE", tf), \
         patch.object(ts, "_watch_pick", new=no_watch), \
         patch.object(taste, "RANDOM", FirstPick), patch.object(ts, "ma_command", new=fake_ma), \
         patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        music_env.db = dbp
        yield music_env


def _post(**body):
    return TestClient(ts.app).post("/music", json=body)


def test_genre_request_plays_the_households_artist_as_radio(genre_env):
    r = _post(query="some deep house", kind="genre")
    assert r.status_code == 200, r.text
    # an artist's own tracks are shuffled into a mix, set before the play
    assert genre_env.calls[0][:3] == ("media_player", "shuffle_set", {"shuffle": True})
    _, _, data, _ = genre_env.calls[1]
    assert data == {"media_id": "library://artist/ps", "media_type": "artist"}
    assert r.json()["pick"] == {"label": "Parov Stelar", "phrase": "deep house", "kind": "artist"}
    with taste.db(genre_env.db) as con:
        assert [tuple(r) for r in con.execute("SELECT label, phrase FROM picks")] == [("Parov Stelar", "deep house")]


def test_never_is_never_picked(genre_env):
    r = _post(query="deep house", kind="genre")
    assert "Nickelback" not in r.json()["pick"]["label"]


def test_the_households_meaning_of_a_word(genre_env):
    taste.TASTE.update("means", "chill", ["downtempo"])
    r = _post(query="something chill", kind="genre")
    assert r.json()["pick"]["label"] == "Bonobo"


def test_an_undeclared_genre_is_detected_but_a_name_is_not(genre_env):
    r = _post(query="downtempo")                                      # no kind, no media_type
    assert r.json()["pick"]["label"] == "Bonobo"
    genre_env.calls.clear()
    r = _post(query="Pink Floyd")
    assert "pick" not in r.json()
    plays = [c[2] for c in genre_env.calls if c[1] == "play_media"]
    assert plays[0]["media_id"] == "Pink Floyd"                         # the named path


def test_not_that_plays_the_next_pick_and_records_the_rejection(genre_env):
    _post(query="deep house", kind="genre")
    genre_env.calls.clear()
    r = _post(query="another", kind="another")
    assert r.status_code == 200, r.text
    assert r.json()["pick"]["label"] == "DEEP HOUSE Radio"
    assert genre_env.calls[0][2] == {"media_id": "library://radio/dh", "media_type": "radio"}
    with taste.db(genre_env.db) as con:
        fb = [tuple(r) for r in con.execute("SELECT key, delta, reason FROM feedback")]
    assert fb == [("artist:parov stelar", ts.FEEDBACK_REJECTED, "rejected")]
    r = _post(query="another", kind="another")
    assert r.status_code == 404 and "everything I had for deep house" in r.json()["detail"]


def test_not_that_with_nothing_picked(genre_env):
    r = _post(query="another", kind="another")
    assert r.status_code == 409 and "nothing to swap" in r.json()["detail"]


def test_a_pick_that_wont_start_falls_to_the_next(genre_env):
    genre_env.fail = "Playback failed: no tracks"
    genre_env.fail_if = lambda d: d["media_id"] == "library://artist/ps"
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 0.2):
        r = _post(query="deep house", kind="genre")
    assert r.status_code == 200 and r.json()["pick"]["label"] == "DEEP HOUSE Radio"


def test_provider_playlists_need_the_genre_in_the_name_and_seeds_rank_last(genre_env):
    genre_env.search = {"bossa nova": {
        "playlists": [{"name": "Bossa Nova Classics", "uri": "ytmusic://playlist/bn", "provider": "ytmusic--x"},
                      {"name": "Top Hits 2026", "uri": "ytmusic://playlist/th", "provider": "ytmusic--x"}],
        "radio": [{"name": "Bossa Nova Radio", "uri": "pandora://radio/seed:GE:1", "item_id": "seed:GE:1",
                   "provider": "pandora"}]}}
    r = _post(query="bossa nova", kind="genre")
    assert r.json()["pick"]["label"] == "Bossa Nova Classics"
    ids = [c[2]["media_id"] for c in genre_env.calls]
    assert "ytmusic://playlist/th" not in ids


def test_play_some_music_follows_a_standing_rule(genre_env):
    taste.TASTE.path.write_text("rules:\n  - {play: downtempo}\n")
    os.utime(taste.TASTE.path, (3, 3))
    r = _post(query="music", kind="genre")
    assert r.json()["pick"] == {"label": "Bonobo", "phrase": "downtempo", "kind": "artist"}


def test_play_some_music_without_a_rule_plays_the_top_of_the_profile(genre_env):
    r = _post(query="some music", kind="genre")
    assert r.json()["pick"]["label"] == "Pink Floyd"             # Nickelback is never


def test_genre_with_nothing_to_pick_falls_back_to_the_named_path(genre_env):
    r = _post(query="polka", kind="genre")
    assert "pick" not in r.json()
    assert genre_env.calls[0][2]["media_id"] == "polka"


@pytest.mark.parametrize("text", [
    "not that", "No, not that one.", "something else", "play something else", "try something different",
    "a different song", "Kronk, play a different station", "play something else in the Office",
])
@pytest.mark.asyncio
async def test_another_phrasings_pin_to_home(text):
    import routing
    assert await routing._classify_inner(text) == ("home", "music_another"), text


@pytest.mark.asyncio
async def test_pick_speech_and_kind_payload():
    import agents
    import tools
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"status": "playing", "player": "the Kitchen speaker", "title": "Booty Swing",
                              "artist": "Parov Stelar", "pick": {"label": "Parov Stelar",
                                                                 "phrase": "deep house", "kind": "artist"}}
    client = MagicMock(post=AsyncMock(return_value=resp))
    out = await tools._tool_play_music(client, {"query": "deep house", "kind": "genre"})
    assert client.post.call_args.kwargs["json"]["kind"] == "genre"
    assert agents._terminal_speech(out) == "Now playing Parov Stelar, for deep house, on the Kitchen speaker."


def test_choose_keeps_one_entry_per_uri():
    a = taste.Pick("station", "library://radio/1", "Mushroom Jazz Radio", "uri:1", 2.9, "radio")
    b = taste.Pick("station", "library://radio/1", "Mushroom Jazz Radio", "uri:1", 0.8, "radio")
    assert taste.choose([b, a], rng=type("R", (), {"choices": staticmethod(lambda h, weights, k: [h[0]])})) == [a]


def test_a_pandora_only_artist_plays_by_name():
    p = taste.Pick("artist", "pandora://artist/Santana", "Santana radio", "artist:santana", 1.0, "artist",
                   radio_mode=True, name="Santana")
    assert p.play_data() == {"media_id": "Santana", "media_type": "artist", "radio_mode": True}


def test_household_artists_outrank_search_playlists(genre_env):
    """First live run (2026-10-21) played a stranger's YTM playlist over our
    own deep house artists."""
    taste.TASTE.update("forget", "Nickelback")
    with taste.db(genre_env.db) as con:                       # a library-only artist: 0.3
        con.execute("UPDATE artists SET score=0.3 WHERE name='Parov Stelar'")
        con.execute("DELETE FROM stations")                    # our own station would (rightly) win
    genre_env.search = {"deep house": {"playlists": [
        {"name": "Deep House 2026", "uri": "ytmusic://playlist/dh", "provider": "ytmusic--x"}]}}
    taste.TASTE.update("never", "Nickelback")
    r = _post(query="deep house", kind="genre")
    assert r.json()["pick"]["label"] == "Parov Stelar"



# ── step 6: feedback on Kronk's picks ───────────────────────────────────────

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _watch_env(tmp_path, states):
    """Run the watcher against a scripted speaker: `states` is a list of
    (state, title) answered one per poll; the clock advances WATCH_POLL_S."""
    dbp = tmp_path / "w.db"
    con = taste.db(dbp)
    con.close()
    clock = _Clock()
    polls = iter(states)

    async def fake_sleep(s):
        clock.t += s

    class Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    async def fake_state(client, headers, entity):
        st, title = next(polls)
        return {"state": st, "attributes": {"media_title": title}}

    return dbp, [patch.object(taste, "TASTE_DB", dbp), patch.object(ts, "_clock", clock),
                 patch.object(ts.asyncio, "sleep", new=fake_sleep), patch.object(ts.httpx, "AsyncClient", Client),
                 patch.object(ts, "_get_state", new=fake_state)]


async def _run_watch(tmp_path, states):
    from contextlib import ExitStack
    dbp, patches = _watch_env(tmp_path, states)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        pick = taste.Pick("artist", "library://artist/ps", "Parov Stelar", "artist:parov stelar", 1, "artist")
        pid = taste.record_pick("media_player.k", "deep house", pick, dbp)
        await ts._watch_pick_loop("media_player.k", pid, pick, "Booty Swing")
    with taste.db(dbp) as con:
        return ([tuple(r) for r in con.execute("SELECT outcome FROM picks")],
                [tuple(r)[:3] for r in con.execute("SELECT key, delta, reason FROM feedback")])


@pytest.mark.asyncio
async def test_watch_skip_inside_30s_counts_against(tmp_path):
    out, fb = await _run_watch(tmp_path, [("playing", "Booty Swing"), ("playing", "Something Else")])
    assert out == [("skipped",)] and fb == [("artist:parov stelar", ts.FEEDBACK_SKIPPED, "skipped")]


@pytest.mark.asyncio
async def test_watch_stop_inside_60s_counts_against(tmp_path):
    out, fb = await _run_watch(tmp_path, [("playing", "Booty Swing")] * 3 + [("paused", "Booty Swing")])
    assert out == [("stopped",)] and fb[0][1] == ts.FEEDBACK_STOPPED


@pytest.mark.asyncio
async def test_watch_ten_minutes_counts_for(tmp_path):
    states = [("playing", "Booty Swing")] * 3 + [("playing", f"track {i}") for i in range(60)]
    out, fb = await _run_watch(tmp_path, states)
    assert out == [("listened",)] and fb[0][1] == ts.FEEDBACK_LISTENED


@pytest.mark.asyncio
async def test_watch_a_late_pause_says_nothing(tmp_path):
    out, fb = await _run_watch(tmp_path, [("playing", "Booty Swing")] * 8 + [("paused", "x")])
    assert out == [(None,)] and fb == []


def test_genre_pick_starts_a_watcher(genre_env):
    _post(query="deep house", kind="genre")
    assert genre_env.watched and genre_env.watched[0][2] == "Parov Stelar"


def test_thumbs_feed_the_artist_score(tmp_path):
    with patch.object(taste, "TASTE_DB", tmp_path / "th.db"):
        ts._taste_feedback("Tosca, Rodney Hunter", ts.FEEDBACK_THUMB, "thumb_up")
        ts._taste_feedback(None, 1.0, "nothing")
        with taste.db() as con:
            assert [tuple(r)[:3] for r in con.execute("SELECT key, delta, reason FROM feedback")] == \
                [("artist:tosca", ts.FEEDBACK_THUMB, "thumb_up")]


def test_first_outcome_wins(tmp_path):
    dbp = tmp_path / "o.db"
    pick = taste.Pick("artist", "u", "Jem", "artist:jem", 1, "artist")
    pid = taste.record_pick("e", "trip hop", pick, dbp)
    assert taste.set_pick_outcome(pid, "skipped", -0.5, pick.key, dbp) is True
    assert taste.set_pick_outcome(pid, "rejected", -1.0, pick.key, dbp) is False
    with taste.db(dbp) as con:
        assert [tuple(r)[:3] for r in con.execute("SELECT key, delta, reason FROM feedback")] == \
            [("artist:jem", -0.5, "skipped")]


def test_watcher_starts_from_the_track_after_the_shuffle(genre_env):
    """Shuffle jumps tracks; the watcher's first title must be the new one."""
    seen = {}

    def watch(entity, pick_id, pick, state):
        seen["state"] = state
    with patch.object(ts, "_watch_pick", new=watch):
        _post(query="deep house", kind="genre")
    # music_env's FakeHA reports the title after play_media; the state handed
    # to the watcher is a fresh read, not the pre-shuffle one
    assert seen["state"]["state"] == "playing"
    assert ("media_player", "shuffle_set", {"shuffle": True}) in [c[:3] for c in genre_env.calls]
