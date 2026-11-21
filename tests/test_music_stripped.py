"""A stripped request is not a generic request (sprint M23, fix 2 of
docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md).

"Play right here, right now, bud.  That boy's slow." reached /music as
query "music": the coordinator's rewrite kept "play music in the Kitchen".
A query with no terms is "play some music", which plays the top of the
taste profile, so the kitchen got Cracker. The user's own sentence
(MusicRequest.said) now decides: when it names something, /music refuses
and names the closest match; when it is generic, the pick happens exactly
as before (Drew's condition: "play some music" is a feature).
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import taste
from tests.test_error_surfacing import FakeHA, music_env  # noqa: F401  (fixture)
from tests.test_music_taste import genre_env  # noqa: F401  (fixture)
from tests.test_direct_dispatch import (  # noqa: F401  (client is a fixture)
    MOUNTS, STAMP, FakeToolService, client, send,
)

pytestmark = pytest.mark.usefixtures("no_network")

# The two captures (the incident document): run 1 of 2026-11-21 and the
# first empty pick, 2026-11-20 20:53 UTC. Both reached /music as "music".
CAPTURES = [
    "Play right here, right now, bud.  That boy's slow. in the Kitchen",
    "Play back in the Kitchen",
]

# Generic sentences: the blueprint's own loose sentences (change 9, which
# hands them to Kronk as "pick something"), with the room suffixes its
# hand-off adds (change 14), then everyday forms. The corpus holds no
# generic sentence of the household's own (every play_music row with no
# name carries a genre). Each must pick from the profile exactly as before.
GENERIC = [
    # blueprint loose trigger: (play|put on) [some ](music|something) [(in|on) [the ]{area}]
    "play some music",
    "play music",
    "play something",
    "put on some music",
    "put on something",
    "play some music in the Kitchen",
    "play something in the Office",
    "put on some music in the Basement Theater",
    "play some music on the kitchen voice pe ma",
    "Play music. in the Kitchen",
    # everyday forms
    "Play some music.",
    "put on some tunes",
    "Put on some tunes in the kitchen.",
    "throw on some tunes",
    "listen to some music",
    "let's listen to something",
    "Can you play some music?",
    "Kronk, play some music please.",
    "Hey Kronk, could you put on some music for dinner?",
    "play me something",
    "play us something nice",
    "Play some good music",
    "play some songs",
    "play a song",
    "play anything",
    "play some random music",
    "play some background music",
    "play some music on the Sonos Move",
    "play some music from YouTube Music",
    "shuffle some music",
    "queue up some music",
    "play something again",
    "I'm cooking dinner, play some music",
    "turn on the lights and play some music",
    "Play some music. That's probably about it.",
    "Play some music for us, now",
    "Okay, um, play some music",
    " Play some music in the kitchen!",
    "Let’s play some music",
]

# The sentence names something: not generic.
NOT_GENERIC = [
    ("Play right here, right now, bud.  That boy's slow. in the Kitchen", "right here, right now, bud"),
    ("Play back in the Kitchen", "back in the Kitchen"),
    ("play some jazz", "some jazz"),
    ("play something by Orbital", "something by Orbital"),
    ("play music by pink floyd", "music by pink floyd"),
    ("Play the song Praise You by Fatboy Slim.", "the song Praise You by Fatboy Slim"),
    ("play some 80s music in the kitchen", "some 80s music in the kitchen"),
]

# No sentence, or no request verb in it: unknown, so today's behaviour.
UNKNOWN = [None, "", "   ", "music", "some tunes in the kitchen", "Try playing it from YouTube music",
           "start some music", "turn on some music"]


@pytest.mark.parametrize("said", GENERIC)
def test_generic_sentences_are_generic(said):
    assert taste.said_request(said) == (True, "")


def test_the_generic_table_is_at_least_twenty():
    assert len(GENERIC) >= 20 and len(set(GENERIC)) == len(GENERIC)


@pytest.mark.parametrize("said,what", NOT_GENERIC)
def test_sentences_that_name_something_are_not(said, what):
    assert taste.said_request(said) == (False, what)


@pytest.mark.parametrize("said", UNKNOWN)
def test_no_sentence_or_no_verb_is_unknown(said):
    assert taste.said_request(said) == (None, "")


def test_every_word_list_entry_is_one_word():
    for w in taste.SAID_GENERIC_WORDS | taste.SAID_STOP_WORDS:
        assert taste._said_words(w) == [w], w
    assert not taste.SAID_GENERIC_WORDS & taste.SAID_STOP_WORDS


def test_no_genre_word_is_generic():
    """A genre is something: "play some jazz" stripped to "music" is a
    stripped request, not a generic one."""
    for g in taste.BUILTIN_GENRES:
        assert not set(g.split()) <= taste.SAID_GENERIC_WORDS, g


# ── /music ───────────────────────────────────────────────────────────────────

RIGHT_HERE = {"tracks": [{"name": "Right Here, Right Now", "uri": "ytmusic://track/vqNIeInr9kA",
                          "media_type": "track", "provider": "ytmusic", "artists": [{"name": "Fatboy Slim"}],
                          "provider_mappings": [{"provider_domain": "ytmusic"}]}]}


@pytest.fixture
def stripped_env(genre_env):
    """genre_env with an MA whose every search answers `genre_env.answer`."""
    genre_env.answer = {}

    async def fake_ma(command, _timeout=20, **args):
        genre_env.ma_calls.append((command, args))
        if command == "music/search":
            return genre_env.answer
        return None

    with patch.object(ts, "ma_command", new=fake_ma):
        yield genre_env


def _post(**body):
    return TestClient(ts.app).post("/music", json=body)


def _plays(env):
    return [c for c in env.calls if c[:2] == ("music_assistant", "play_media")]


def _picks(env):
    with taste.db(env.db) as con:
        return [tuple(r) for r in con.execute("SELECT label, phrase FROM picks")]


def test_run_1_is_refused_with_the_closest_match(stripped_env):
    stripped_env.answer = RIGHT_HERE
    r = _post(query="music", player="Kitchen", said=CAPTURES[0])
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == ("I didn't catch what to play. "
                                  "The closest I found was Right Here, Right Now by Fatboy Slim.")
    assert _plays(stripped_env) == [] and stripped_env.calls == []        # nothing sent to a player
    assert _picks(stripped_env) == []                                     # and no pick recorded


def test_the_first_empty_pick_is_refused_without_a_hint_when_nothing_is_close(stripped_env):
    r = _post(query="music", player="Kitchen", said=CAPTURES[1])
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "I didn't catch what to play."
    assert stripped_env.calls == [] and _picks(stripped_env) == []


def test_a_weak_hint_is_not_named(stripped_env):
    stripped_env.answer = {"albums": [{"name": "Zebra Crossing Lullabies", "uri": "library://album/1",
                                       "media_type": "album", "provider": "filesystem_local",
                                       "artists": [{"name": "Nobody"}],
                                       "provider_mappings": [{"provider_domain": "filesystem_local"}]}]}
    r = _post(query="music", said=CAPTURES[1])
    assert r.json()["detail"] == "I didn't catch what to play."


def test_ma_unreachable_still_refuses_plainly(genre_env):
    async def down(command, _timeout=20, **args):
        raise ts.MAError("Music Assistant is unreachable (OSError).")
    with patch.object(ts, "ma_command", new=down):
        r = _post(query="music", said=CAPTURES[0])
    assert r.status_code == 422 and r.json()["detail"] == "I didn't catch what to play."
    assert genre_env.calls == []


@pytest.mark.parametrize("kind", [None, "genre"])
def test_the_refusal_holds_for_an_undeclared_or_declared_genre(stripped_env, kind):
    body = {"query": "some music", "said": CAPTURES[0]}
    if kind:
        body["kind"] = kind
    assert _post(**body).status_code == 422


def _baseline(env, **body):
    """What /music does with no sentence at all: today's no-phrase pick."""
    r = _post(**body)
    assert r.status_code == 200, r.text
    out = (r.json(), [c[2] for c in _plays(env)])
    env.calls.clear()
    ts._LAST_PICKS.clear()
    FakeHA.playing, FakeHA.title = False, "Glory Box"     # the player as it was before the first play
    return out


@pytest.mark.parametrize("said", GENERIC)
def test_generic_sentences_pick_exactly_as_before(stripped_env, said):
    want = _baseline(stripped_env, query="music", kind="genre")
    r = _post(query="music", kind="genre", said=said)
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want
    assert r.json()["pick"]["phrase"] == ""                               # the top of the profile


@pytest.mark.parametrize("said", UNKNOWN)
def test_unknown_sentences_pick_exactly_as_before(stripped_env, said):
    want = _baseline(stripped_env, query="music")
    r = _post(query="music", **({"said": said} if said is not None else {}))
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want


def test_a_query_with_terms_is_never_guarded(stripped_env):
    """The guard reads the sentence only when the query cleans to nothing."""
    r = _post(query="some deep house", kind="genre", said=CAPTURES[0])
    assert r.status_code == 200 and r.json()["pick"]["phrase"] == "deep house"


def test_not_that_is_never_guarded(stripped_env):
    _post(query="some deep house", kind="genre")
    r = _post(query="something else", kind="another", said="play something else, not that")
    assert r.status_code == 200, r.text


def test_the_voice_handoff_has_no_sentence_and_picks_as_before(stripped_env):
    """/music/voice (the unused rest_command path) carries no `said`."""
    r = TestClient(ts.app).post("/music/voice", json={"query": ""})
    assert r.status_code == 200 and r.json()["ok"], r.text


# ── the orchestrator: the sentence survives the coordinator's rewrite ──────

@pytest.mark.parametrize("mount", MOUNTS)
def test_said_reaches_tool_service_through_the_coordinator_rewrite(client, mount):
    """Run 1's path: rule `messy`, the coordinator delegates a rewrite that
    drops the title, the home agent asks for "music". origin.said still
    carries the user's sentence (the blueprint hand-off text arrives on
    /api/chat), so tool_service can refuse. Every mount."""
    import routing
    assert routing.decide(CAPTURES[0]).rule == "messy"
    seen = []

    async def stream(messages, model, tools=None):
        names = {t["function"]["name"] for t in (tools or [])}
        had_result = any(m.get("role") == "tool" for m in messages)
        if "ask_home" in names:
            seen.append("coordinator")
            if not had_result:
                yield {"tool_calls": [{"id": "c1", "function": {
                    "name": "ask_home", "arguments": {"query": "play music in the Kitchen"}}}]}
            else:
                yield {"token": "Done."}
        elif "play_music" in names:
            seen.append("home")
            yield {"tool_calls": [{"id": "h1", "function": {
                "name": "play_music", "arguments": {"player": "Kitchen", "query": "music"}}}]}
        else:
            yield {"token": "ok"}
        yield {"usage": {}}

    fake = FakeToolService()
    with patch("agents.llm.stream", new=stream), patch("tools.httpx.AsyncClient", new=fake):
        send(client, mount, CAPTURES[0], STAMP if mount == "voice_api_chat" else None)
    assert seen[:2] == ["coordinator", "home"]
    (path, payload), = fake.posts()
    assert path == "/music" and payload["query"] == "music" and payload["said"] == CAPTURES[0]
    assert taste.said_request(payload["said"])[0] is False
