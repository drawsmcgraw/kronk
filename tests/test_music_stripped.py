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
from pathlib import Path
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

# Generic sentences: the end-to-end tester's 303 and the builder's 39 (the
# blueprint's loose sentences with the hand-off's room suffixes, then
# everyday forms), none of them household text. Each must pick from the
# profile exactly as before M23 (Drew's condition: "play some music" is a
# feature). Fix round 1 turned the test round: a word list of fillers could
# not be closed (55 of the tester's sentences were refused).
FIXTURE = Path(__file__).parent / "fixtures" / "music_generic_sentences.txt"
GENERIC_ALL = [line.rstrip("\n") for line in FIXTURE.read_text().splitlines(True)
               if line.strip() and not line.startswith("#")]
# The one that stays refused: a Whisper form with no word for music left in
# it. Saving it takes a rule for that one mishearing ("muse" alone is a
# band), which is the word-list problem again.
REFUSED_GENERIC = {"Play some muse sick."}
GENERIC = [s for s in GENERIC_ALL if s not in REFUSED_GENERIC]

# The sentence names something and holds no word for music: refused.
NOT_GENERIC = [
    ("Play right here, right now, bud.  That boy's slow. in the Kitchen", "right here, right now, bud"),
    ("Play right here, right now, bud.  That boy's slow.", "right here, right now, bud"),
    ("Play back in the Kitchen", "back in the Kitchen"),
    ("Play back", "back"),
    ("play some jazz", "some jazz"),
    ("play Praise You by Fatboy Slim", "Praise You by Fatboy Slim"),
    ("play it's my life", "it's my life"),
    ("Play some muse sick.", "some muse sick"),
]

# The known cost of the inversion: a stripped request whose sentence also
# holds a word for music is read as generic, so it gets the pick it got
# before M23 (no worse than before).
MISSED = [
    "play the song right here right now",
    "play something by Orbital",
    "play music by pink floyd",
    "Play the song Praise You by Fatboy Slim.",
    "play some 80s music in the kitchen",
    "play Low by Cracker again",
    "play this is america",
]

# No sentence, or no request verb in it: unknown, so today's behaviour.
UNKNOWN = [None, "", "   ", "music", "some tunes in the kitchen", "Try playing it from YouTube music",
           "start some music", "turn on some music", "surprise me"]


@pytest.fixture(autouse=True)
def _no_state_left_behind():
    """A last pick left in ts._LAST_PICKS made a later file's "something
    else" succeed (the end-to-end test's finding 10); FakeHA's class
    attributes are module state too."""
    yield
    ts._LAST_PICKS.clear()
    FakeHA.playing, FakeHA.shuffle, FakeHA.title = False, False, "Glory Box"


def test_the_fixture_holds_both_lists():
    assert len(GENERIC_ALL) == 342 and len(set(GENERIC_ALL)) == 342
    assert REFUSED_GENERIC <= set(GENERIC_ALL)


@pytest.mark.parametrize("said", GENERIC)
def test_generic_sentences_are_not_refused(said):
    assert taste.said_request(said) in ((True, ""), (None, ""))


@pytest.mark.parametrize("said,what", NOT_GENERIC)
def test_sentences_that_name_something_are_not(said, what):
    assert taste.said_request(said) == (False, what)


@pytest.mark.parametrize("said", MISSED)
def test_the_known_cost_a_named_request_with_a_word_for_music(said):
    assert taste.said_request(said) == (True, "")


@pytest.mark.parametrize("said", UNKNOWN)
def test_no_sentence_or_no_verb_is_unknown(said):
    assert taste.said_request(said) == (None, "")


@pytest.mark.parametrize("said", ["play it", "play that again", "play this", "play one", "Play.", "play",
                                  "play some", "play some, please", "Play some music's.", "play a musical",
                                  "Play, surprise me"])
def test_the_edges_of_the_rule(said):
    """A pronoun counts as the thing played; "some" and a bare verb count
    when only filler follows; any word starting with "music" counts."""
    assert taste.said_request(said) == (True, "")


@pytest.mark.parametrize("said", ["play back, that one's good", "play right here, it is slow",
                                  "play right here, this boy"])
def test_a_pronoun_in_chatter_does_not_count(said):
    assert taste.said_request(said)[0] is False


def test_every_word_list_entry_is_one_word():
    for w in taste.SAID_MUSIC_WORDS | taste.SAID_MUSIC_PRONOUNS | taste.SAID_FILLER:
        assert taste._said_words(w) == [w], w
    assert not taste.SAID_MUSIC_WORDS & taste.SAID_FILLER


def test_no_genre_word_is_a_word_for_music():
    """A genre is something: "play some jazz" stripped to "music" is a
    stripped request, not a generic one."""
    for g in taste.BUILTIN_GENRES:
        for w in g.split():
            assert w not in taste.SAID_MUSIC_WORDS and not w.startswith("music"), g


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


def test_the_orchestrator_knows_the_refusal_sentence():
    import agents
    assert agents.STRIPPED_REFUSAL == ts.STRIPPED_SENTENCE


@pytest.mark.parametrize("hint", ["", " The closest I found was Right Here, Right Now by Fatboy Slim."])
@pytest.mark.parametrize("style", ["debug", "friendly"])
def test_the_refusal_is_said_once_through_the_orchestrator(hint, style):
    """The end-to-end test's finding 11: "I couldn't play that. I didn't
    catch what to play." said the same thing twice. Other music failures
    keep their lead-in."""
    import agents
    import errors
    st = errors.FRIENDLY if style == "friendly" else errors.DEBUG
    line = f"[Could not play music: {ts.STRIPPED_SENTENCE}{hint}]\nPlayback FAILED — tell the user."
    assert agents._terminal_speech(line, st, tool="play_music") == ts.STRIPPED_SENTENCE + hint
    other = "[Could not play music: The speaker may be powered off.]\nPlayback FAILED — tell the user."
    assert agents._terminal_speech(other, st, tool="play_music") == \
        "I couldn't play that. The speaker may be powered off."


@pytest.mark.parametrize("said", sorted(REFUSED_GENERIC))
def test_the_generic_sentence_that_stays_refused(stripped_env, said):
    r = _post(query="music", kind="genre", said=said)
    assert r.status_code == 422 and stripped_env.calls == []


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


@pytest.mark.parametrize("mount", MOUNTS)
def test_the_refusal_reaches_the_user_once_on_every_mount(client, mount):
    """Run 1's path end to end, with tool_service's 422: the reply is the
    refusal, said once."""
    detail = f"{ts.STRIPPED_SENTENCE} The closest I found was Right Here, Right Now by Fatboy Slim."

    async def stream(messages, model, tools=None):
        names = {t["function"]["name"] for t in (tools or [])}
        had_result = any(m.get("role") == "tool" for m in messages)
        if "ask_home" in names and not had_result:
            yield {"tool_calls": [{"id": "c1", "function": {
                "name": "ask_home", "arguments": {"query": "play music in the Kitchen"}}}]}
        elif "play_music" in names and not had_result:
            yield {"tool_calls": [{"id": "h1", "function": {
                "name": "play_music", "arguments": {"player": "Kitchen", "query": "music"}}}]}
        else:
            yield {"token": "Done."}
        yield {"usage": {}}

    fake = FakeToolService(answers={"/music": (422, {"detail": detail})})
    with patch("agents.llm.stream", new=stream), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, mount, CAPTURES[0], STAMP if mount == "voice_api_chat" else None)
    assert reply.count(ts.STRIPPED_SENTENCE) == 1 and "I couldn't play that" not in reply, reply
    assert "Fatboy Slim" in reply
