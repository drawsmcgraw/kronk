"""Named-request resolution (docs/plans/MUSIC_TASTE_AGENT_PLAN.md, steps 1–2).

Triggered by INVESTIGATION_2026-10-19_ytmusic_album_misses.md: a provider
hint left in the query ("… on YouTube Music") made MA find nothing; a
misspelled title ("Destiny Waving") or artist ("Kuru Dorf Meister") had no
tolerance; a pasted YouTube Music link had no play path.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import music_resolve as mr
from tests.test_error_surfacing import FakeHA, music_env  # noqa: F401  (fixture)


# ── step 1: normalize ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, text, provider", [
    ("Destiny Waving by Ulrich Schnauss on YouTube Music", "Destiny Waving by Ulrich Schnauss", "ytmusic"),
    ("Hotel Costes 3 from Atlas", "Hotel Costes 3", "filesystem_local"),
    ("Hotel Costes 3 from the NAS.", "Hotel Costes 3", "filesystem_local"),
    ("Woodstock from Pandora", "Woodstock", "pandora"),
    ("Orbital, on youtube", "Orbital", "ytmusic"),
    ("Daft Punk via Spotify", "Daft Punk", "spotify"),
    ("Pink Floyd", "Pink Floyd", None),
    ("Live in Pandora", "Live in Pandora", None),      # the whole thing is a title
    ("Songs from the Big Chair", "Songs from the Big Chair", None),
])
def test_normalize_strips_provider_hints(raw, text, provider):
    q = mr.normalize(raw)
    assert (q.text, q.provider) == (text, provider)


@pytest.mark.parametrize("url, uri, kind", [
    ("https://music.youtube.com/playlist?list=OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU&si=AsX18WllrLfDbH_v",
     "ytmusic://playlist/OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU", "playlist"),
    ("https://music.youtube.com/browse/MPREb_FZkLnRytAxt", "ytmusic://album/MPREb_FZkLnRytAxt", "album"),
    ("https://music.youtube.com/channel/UCabc123", "ytmusic://artist/UCabc123", "artist"),
    ("https://music.youtube.com/watch?v=5ooz4Ams8-w&list=RDAMVM", "ytmusic://track/5ooz4Ams8-w", "track"),
    ("https://youtu.be/5ooz4Ams8-w", "ytmusic://track/5ooz4Ams8-w", "track"),
])
def test_normalize_youtube_music_links(url, uri, kind):
    q = mr.normalize(f"It's this album {url}")
    assert (q.uri, q.uri_type, q.provider) == (uri, kind, "ytmusic")


def test_normalize_splits_by_at_the_last_by():
    q = mr.normalize("Far Away Trains Passing By by Ulrich Schnauss")
    assert (q.name, q.artist) == ("Far Away Trains Passing By", "Ulrich Schnauss")
    q = mr.normalize("Toys in the Attic", artist="Aerosmith")
    assert (q.name, q.artist) == ("Toys in the Attic", "Aerosmith")


def test_play_sends_the_query_without_the_provider_hint(music_env):
    FakeHA.playing = False
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Hotel Costes 3 from Atlas", "media_type": "album"})
    assert resp.status_code == 200, resp.text
    _, _, data, _ = music_env.calls[0]
    assert data == {"media_id": "Hotel Costes 3", "media_type": "album"}


def test_play_a_pasted_link_by_uri(music_env):
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={
            "query": "https://music.youtube.com/playlist?list=OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU"})
    assert resp.status_code == 200, resp.text
    _, _, data, _ = music_env.calls[0]
    assert data == {"media_id": "ytmusic://playlist/OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU",
                    "media_type": "playlist"}


def test_radio_station_lookup_gets_the_clean_name(music_env):
    music_env.fail = "Could not resolve ['Woodstock'] to playable media item"
    TestClient(ts.app).post("/music", json={"query": "Woodstock from Pandora", "media_type": "radio"})
    assert music_env.ma_calls[0] == ("music/item_by_name", {"name": "Woodstock", "media_type": "radio"})


# ── step 1: a pasted YouTube Music link routes to home, not research ────────

@pytest.mark.parametrize("text", [
    "It's this album on YouTube music https://music.youtube.com/playlist?list=OLAK5uy_mR95WuMRvxqS1RiPym7_tb4Xwj4BzTCtU",
    "https://music.youtube.com/watch?v=5ooz4Ams8-w",
    "play https://youtu.be/5ooz4Ams8-w in the kitchen",
])
@pytest.mark.asyncio
async def test_music_links_pin_to_home(text):
    import routing
    assert await routing._classify_inner(text) == ("home", "music_url")


@pytest.mark.parametrize("text", [
    "summarize https://www.youtube.com/watch?v=abc123",
    "what does this page say https://example.com/music",
])
@pytest.mark.asyncio
async def test_other_links_stay_with_research(text):
    import routing
    assert await routing._classify_inner(text) == ("research", "url")


# ── step 2: search, score, decide ───────────────────────────────────────────

def _album(name, artists, uri, provider="ytmusic"):
    return {"name": name, "uri": uri, "media_type": "album", "provider": provider,
            "artists": [{"name": a} for a in artists], "provider_mappings": [{"provider_domain": provider}]}


SCHNAUSS_WORKS = {"albums": [
    _album("Far Away Trains Passing By", ["Ulrich Schnauss"], "library://album/1372", "filesystem_local"),
    _album("Goodbye", ["Ulrich Schnauss"], "ytmusic://album/G"),
    _album("Destiny Waiving Köln Versionen EP", ["Ulrich Schnauss", "Mark Peters"], "ytmusic://album/K"),
    _album("Destiny Waiving", ["Ulrich Schnauss", "Mark Peters"], "ytmusic://album/MPREb_FZkLnRytAxt"),
]}


def test_rank_misspelled_title_finds_the_album():
    q = mr.normalize("Destiny Waving by Ulrich Schnauss")
    ranked = mr.rank(mr.candidates_from_search(SCHNAUSS_WORKS), q, "album")
    assert ranked[0].uri == "ytmusic://album/MPREb_FZkLnRytAxt"
    assert ranked[0].score >= mr.PLAY_MIN_SCORE
    # the right artist does not carry the wrong title over the line
    wrong = next(c for c in ranked if c.name == "Far Away Trains Passing By")
    assert wrong.score < mr.PLAY_MIN_SCORE


def test_rank_misheard_artist():
    res = {"albums": [_album("DJ-Kicks", ["Kruder & Dorfmeister"], "ytmusic://album/KD"),
                      _album("DJ-Kicks: Sofia Kourtesis", ["Sofia Kourtesis"], "ytmusic://album/SK")]}
    ranked = mr.rank(mr.candidates_from_search(res), mr.normalize("DJ Kicks by Kuru Dorf Meister"))
    assert ranked[0].uri == "ytmusic://album/KD" and ranked[0].score >= mr.PLAY_MIN_SCORE


def test_rank_numbers_must_match():
    res = {"albums": [_album("Hôtel Costes", ["Various Artists"], "u1"), _album("Hôtel Costes 6", ["Hôtel Costes"], "u6"),
                      _album("Hôtel Costes 3 - Etage 3", ["Hôtel Costes"], "u3")]}
    assert mr.rank(mr.candidates_from_search(res), mr.normalize("Hotel Costes 3"))[0].uri == "u3"


def test_rank_gibberish_stays_below_the_line():
    res = {"tracks": [{"name": "Effeccctt", "uri": "t1", "media_type": "track", "artists": [{"name": "Noisuf-X"}]},
                      {"name": "Akon", "uri": "a1", "media_type": "artist"}]}
    ranked = mr.rank(mr.candidates_from_search(res), mr.normalize("zzqx florbent nonexistent"))
    assert ranked[0].score < 0.5


@pytest.fixture
def search_env(music_env):
    """music_env plus an MA that answers music/search from a table."""
    music_env.search = {}
    music_env.searches = []

    async def fake_ma(command, _timeout=20, **args):
        music_env.ma_calls.append((command, args))
        if command == "music/search":
            music_env.searches.append((args["search_query"], tuple(args["media_types"]), args["limit"]))
            return music_env.search.get(args["search_query"], {})
        return music_env.station_item

    with patch.object(ts, "ma_command", new=fake_ma):
        yield music_env


def test_play_resolves_a_misspelled_album_by_uri(search_env):
    search_env.search = {"Ulrich Schnauss": SCHNAUSS_WORKS}
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Destiny Waving by Ulrich Schnauss on YouTube Music",
                                                       "media_type": "album"})
    assert resp.status_code == 200, resp.text
    _, _, data, _ = search_env.calls[0]
    assert data == {"media_id": "ytmusic://album/MPREb_FZkLnRytAxt", "media_type": "album"}
    assert resp.json()["resolved"] == {"name": "Destiny Waiving", "artist": "Ulrich Schnauss and Mark Peters",
                                       "media_type": "album"}
    # stage 1 searched the title (with and without the artist); stage 2 the artist's works
    assert [s[0] for s in search_env.searches] == ["Destiny Waving by Ulrich Schnauss", "Destiny Waving",
                                                   "Ulrich Schnauss"]


def test_play_confident_first_stage_skips_the_artist_search(search_env):
    search_env.search = {"Far Away Trains Passing By by Ulrich Schnauss": SCHNAUSS_WORKS}
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        TestClient(ts.app).post("/music", json={"query": "Far Away Trains Passing By by Ulrich Schnauss",
                                                "media_type": "album"})
    assert search_env.calls[0][2]["media_id"] == "library://album/1372"
    assert "Ulrich Schnauss" not in [s[0] for s in search_env.searches]


def test_play_no_confident_match_fails_and_names_the_closest(search_env):
    search_env.search = {"Destiny Waving": {"albums": [_album("Destiny Wave", ["Peaky Triangle"], "ytmusic://album/P")]}}
    resp = TestClient(ts.app).post("/music", json={"query": "Destiny Waving", "media_type": "album"})
    assert resp.status_code == 502
    assert search_env.calls == []      # no literal play: MA's own search would play a rejected candidate
    assert resp.json()["detail"] == ("I couldn't find an album called Destiny Waving in Music Assistant. "
                                     "The closest I found was Destiny Wave by Peaky Triangle.")


def test_play_playlist_not_in_the_library_plays_nothing(search_env):
    # 2026-10-21: "Old Man Sings" (a new personal YTM playlist, not yet synced)
    # scored 0.62 against public playlists; the literal fallback then played
    # MA's top hit, "Late Night with Jimmy Fallon".
    search_env.search = {"Old Man Sings": {"playlists": [
        {"name": "Late Night with Jimmy Fallon", "uri": "ytmusic://playlist/F", "media_type": "playlist"},
        {"name": "Old South African songs", "uri": "ytmusic://playlist/S", "media_type": "playlist"}]}}
    resp = TestClient(ts.app).post("/music", json={"query": "Old Man Sings", "media_type": "playlist", "shuffle": True})
    assert resp.status_code == 502
    assert search_env.calls == []
    assert resp.json()["detail"].startswith("I couldn't find a playlist called Old Man Sings in Music Assistant.")


def test_play_ma_unreachable_falls_back_to_the_literal_play(search_env):
    async def down(command, _timeout=20, **args):
        raise ts.MAError("Music Assistant is unreachable (OSError).")
    with patch.object(ts, "ma_command", new=down), patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "Portishead"})
    assert resp.status_code == 200
    assert search_env.calls[0][2] == {"media_id": "Portishead"}


@pytest.mark.asyncio
async def test_album_speech_names_the_record_not_track_one():
    import tools
    from unittest.mock import AsyncMock, MagicMock
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"status": "playing", "player": "the Kitchen speaker", "title": "The Supposed Middle Class",
                              "artist": "Ulrich Schnauss", "shuffle": False,
                              "resolved": {"name": "Destiny Waiving", "artist": "Ulrich Schnauss and Mark Peters",
                                           "media_type": "album"}}
    client = MagicMock(post=AsyncMock(return_value=resp))
    out = await tools._tool_play_music(client, {"query": "Destiny Waving by Ulrich Schnauss"})
    assert out == "[Music playing: Destiny Waiving by Ulrich Schnauss and Mark Peters on the Kitchen speaker]"


# ── step 7: the voice tier's handoff endpoint ───────────────────────────────

def test_voice_handoff_resolves_and_speaks_one_sentence(search_env):
    search_env.search = {"Ulrich Schnauss": SCHNAUSS_WORKS}
    r = TestClient(ts.app).post("/music/voice", json={
        "query": "Destiny Waving", "artist": "Ulrich Schnauss", "media_type": "album"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["say"] == "Destiny Waiving by Ulrich Schnauss and Mark Peters on the Kitchen speaker."
    assert search_env.calls[0][2]["media_id"] == "ytmusic://album/MPREb_FZkLnRytAxt"


def test_voice_handoff_failure_is_a_sentence_not_an_error(search_env):
    search_env.fail = "Could not resolve ['zzqx'] to playable media item"
    r = TestClient(ts.app).post("/music/voice", json={"query": "zzqx", "media_type": "album"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "say": "I couldn't play that. Playback failed: Could not resolve zzqx to playable media item"}


def test_voice_handoff_something_else_is_another(search_env):
    r = TestClient(ts.app).post("/music/voice", json={"query": "else"})
    assert r.json()["ok"] is False and "nothing to swap" in r.json()["say"]


def test_voice_sentence_shapes():
    assert ts.voice_sentence({"player": "the Office speaker", "pick": {"label": "Massive Attack", "phrase": "trip hop"}}) \
        == "Massive Attack, for trip hop, on the Office speaker."
    assert ts.voice_sentence({"player": "the Office speaker", "station": {"name": "Woodstock Radio", "created": True}}) \
        == "Made a new Pandora station, Woodstock Radio, on the Office speaker."
    assert ts.voice_sentence({"player": "the Office speaker", "title": "Halcyon", "artist": "Orbital"}) \
        == "Halcyon by Orbital on the Office speaker."


# ── radio: a station MA can't name exactly (2026-10-25) ─────────────────────
# docs/incidents/INVESTIGATION_2026-10-25_pandora_station_name_miss.md

HITS_STATION = {"radio": [
    {"name": "90s, 2000s, Today's Hits Radio", "uri": "library://radio/263", "media_type": "radio",
     "provider": "library", "provider_mappings": [{"provider_domain": "pandora"}]},
    {"name": "Today's Country Radio", "uri": "library://radio/7", "media_type": "radio",
     "provider": "library", "provider_mappings": [{"provider_domain": "pandora"}]},
]}


def test_rank_station_name_with_and_and_without_radio():
    for said in ("90s, 2000's and today's hits", "90s, 2000s, and today's hits from Pandora"):
        ranked = mr.rank(mr.candidates_from_search(HITS_STATION), mr.normalize(said), "radio")
        assert ranked[0].uri == "library://radio/263" and ranked[0].score >= mr.PLAY_MIN_SCORE, said


def test_word_coverage_ignores_and_but_not_content_words():
    c = mr.candidates_from_search(HITS_STATION)[0]
    assert mr.word_coverage("90s 2000s and todays hits", c) >= mr.COVERAGE_MIN
    assert mr.word_coverage("90s 2000s and todays jazz", c) < mr.COVERAGE_MIN


def test_play_radio_miss_by_name_resolves_the_station(search_env):
    search_env.station_item = None                     # item_by_name: no exact match
    search_env.search = {"90s, 2000's and today's hits": HITS_STATION}
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "90s, 2000's and today's hits",
                                                       "media_type": "radio", "player": "kitchen"})
    assert resp.status_code == 200, resp.text
    assert search_env.calls[0][2] == {"media_id": "library://radio/263", "media_type": "radio"}
    assert resp.json()["station"] == {"name": "90s, 2000s, Today's Hits Radio", "created": False}
    assert search_env.searches[0][1] == ("radio",)


def test_play_radio_exact_station_skips_the_search(search_env):
    search_env.station_item = {"name": "Woodstock Radio", "uri": "library://radio/9", "item_id": "9",
                               "provider": "library", "provider_mappings": [{"provider_domain": "pandora"}]}
    with patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        TestClient(ts.app).post("/music", json={"query": "Woodstock", "media_type": "radio"})
    assert search_env.searches == []
    assert search_env.calls[0][2]["media_id"] == "library://radio/9"


def test_play_radio_near_miss_names_the_closest_station(search_env):
    search_env.station_item = None
    search_env.search = {"todays hits": HITS_STATION}
    resp = TestClient(ts.app).post("/music", json={"query": "todays hits", "media_type": "radio"})
    assert resp.status_code == 502 and search_env.calls == []
    assert resp.json()["detail"].startswith("I couldn't find a station called todays hits in Music Assistant.")


def test_play_radio_scores_the_library_stations_when_search_finds_none(search_env):
    # MA's search matches substrings: the full phrase found nothing live.
    search_env.station_item = None
    inner = ts.ma_command

    async def with_library(command, _timeout=20, **args):
        if command == "music/radios/library_items":
            search_env.ma_calls.append((command, args))
            return HITS_STATION["radio"]
        return await inner(command, _timeout=_timeout, **args)

    with patch.object(ts, "ma_command", new=with_library), patch.object(ts, "MUSIC_VERIFY_TIMEOUT_S", 5):
        resp = TestClient(ts.app).post("/music", json={"query": "90s, 2000's and today's hits from Pandora",
                                                       "media_type": "radio"})
    assert resp.status_code == 200, resp.text
    assert search_env.calls[0][2] == {"media_id": "library://radio/263", "media_type": "radio"}
