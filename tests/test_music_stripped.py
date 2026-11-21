"""A stripped request is not a generic request (sprint M23, fix 2 of
docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md).

"Play right here, right now, bud.  That boy's slow." reached /music as
query "music": the coordinator's rewrite kept "play music in the Kitchen".
A query with no terms is "play some music", which plays the top of the
taste profile, so the kitchen got Cracker. The user's own sentence
(MusicRequest.said) decides, with the guard on: when it names something,
/music refuses and names the closest match; when it is generic, the pick
happens exactly as before (Drew's condition: "play some music" is a
feature).

Fix round 2: the guard ships off (music.yaml stripped_request.refuse,
false by default and when the key is absent). Both end-to-end tests found
ordinary requests refused. With it off, /music does not read the sentence:
every no-phrase request picks exactly as with no sentence (the 342, the two
captures, the second tester's 314). The guard's own tests run with it on
(the `guard_on` fixture).
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
from tool_service import config_loader, taste
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
# The second end-to-end tester's 314 (docs/sprint/M23-e2e2.md, appendix):
# each picked on ff8a7a5; 107 were refused by the guard of fix round 1.
FIXTURE_E2E2 = Path(__file__).parent / "fixtures" / "music_generic_sentences_e2e2.txt"
E2E2 = [line for line in FIXTURE_E2E2.read_text().splitlines() if line.strip() and not line.startswith("#")]

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
    assert len(E2E2) == 314 and len(set(E2E2)) == 314 and not set(E2E2) & set(GENERIC_ALL)


@pytest.fixture
def guard_on():
    """The guard's own tests: music.yaml stripped_request.refuse on."""
    with patch.object(config_loader.current().music.stripped_request, "refuse", True):
        yield


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


def test_run_1_is_refused_with_the_closest_match(stripped_env, guard_on):
    stripped_env.answer = RIGHT_HERE
    r = _post(query="music", player="Kitchen", said=CAPTURES[0])
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == ("I didn't catch what to play. "
                                  "The closest I found was Right Here, Right Now by Fatboy Slim.")
    assert _plays(stripped_env) == [] and stripped_env.calls == []        # nothing sent to a player
    assert _picks(stripped_env) == []                                     # and no pick recorded


def test_the_first_empty_pick_is_refused_without_a_hint_when_nothing_is_close(stripped_env, guard_on):
    r = _post(query="music", player="Kitchen", said=CAPTURES[1])
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "I didn't catch what to play."
    assert stripped_env.calls == [] and _picks(stripped_env) == []


def test_a_weak_hint_is_not_named(stripped_env, guard_on):
    stripped_env.answer = {"albums": [{"name": "Zebra Crossing Lullabies", "uri": "library://album/1",
                                       "media_type": "album", "provider": "filesystem_local",
                                       "artists": [{"name": "Nobody"}],
                                       "provider_mappings": [{"provider_domain": "filesystem_local"}]}]}
    r = _post(query="music", said=CAPTURES[1])
    assert r.json()["detail"] == "I didn't catch what to play."


def test_ma_unreachable_still_refuses_plainly(genre_env, guard_on):
    async def down(command, _timeout=20, **args):
        raise ts.MAError("Music Assistant is unreachable (OSError).")
    with patch.object(ts, "ma_command", new=down):
        r = _post(query="music", said=CAPTURES[0])
    assert r.status_code == 422 and r.json()["detail"] == "I didn't catch what to play."
    assert genre_env.calls == []


@pytest.mark.parametrize("kind", [None, "genre"])
def test_the_refusal_holds_for_an_undeclared_or_declared_genre(stripped_env, guard_on, kind):
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
def test_generic_sentences_pick_exactly_as_before(stripped_env, guard_on, said):
    want = _baseline(stripped_env, query="music", kind="genre")
    r = _post(query="music", kind="genre", said=said)
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want
    assert r.json()["pick"]["phrase"] == ""                               # the top of the profile


@pytest.mark.parametrize("said", UNKNOWN)
def test_unknown_sentences_pick_exactly_as_before(stripped_env, guard_on, said):
    want = _baseline(stripped_env, query="music")
    r = _post(query="music", **({"said": said} if said is not None else {}))
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want


def test_a_query_with_terms_is_never_guarded(stripped_env, guard_on):
    """The guard reads the sentence only when the query cleans to nothing."""
    r = _post(query="some deep house", kind="genre", said=CAPTURES[0])
    assert r.status_code == 200 and r.json()["pick"]["phrase"] == "deep house"


def test_not_that_is_never_guarded(stripped_env, guard_on):
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
def test_the_generic_sentence_that_stays_refused(stripped_env, guard_on, said):
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


# ── fix round 2: the guard ships off (music.yaml stripped_request.refuse) ───

# With the guard off, every no-phrase request picks exactly as with no
# sentence, whatever `said` holds: the 342, the two captures (with and
# without the hand-off suffix), the second tester's 314.
OFF_SENTENCES = list(dict.fromkeys([*GENERIC_ALL, *CAPTURES, CAPTURES[0].removesuffix(" in the Kitchen"), *E2E2]))


def test_the_guard_is_off_in_the_shipped_defaults_and_in_the_code():
    assert config_loader.Music().stripped_request.refuse is False
    assert config_loader.load(Path(__file__).parent.parent / "config").music.stripped_request.refuse is False
    assert config_loader.current().music.stripped_request.refuse is False


def test_a_music_yaml_without_the_key_is_valid_and_off(tmp_path):
    """Production's live music.yaml predates the key: it must still load
    (and pass deploy_tag.sh plan's schema check) and mean off."""
    shipped = (Path(__file__).parent.parent / "config" / "music.yaml").read_text()
    head = shipped[:shipped.index("# The stripped-request guard")]
    assert "stripped_request" not in head
    (tmp_path / "music.yaml").write_text(head)
    cfg = config_loader.load(tmp_path)
    assert cfg.music.stripped_request.refuse is False
    from tool_service import settings_info
    assert settings_info.validate("music.yaml", head)["ok"] is True
    on = head + "stripped_request:\n  refuse: true\n"
    (tmp_path / "music.yaml").write_text(on)
    assert config_loader.load(tmp_path).music.stripped_request.refuse is True
    for bad in ("yes", "1", '"true"'):        # strict: only a YAML boolean
        assert settings_info.validate("music.yaml", head + f"stripped_request:\n  refuse: {bad}\n")["ok"] is False


def test_settings_shows_the_key_with_its_one_line_description():
    """The page shows the schema's description when the live file has no
    comment for the key; the shipped file's comment says the same."""
    from tool_service import config_files, settings_info
    keys = {k["key"]: k for k in settings_info.effective()["files"]["music.yaml"]}
    k = keys["stripped_request.refuse"]
    assert k["value"] is False and k["default"] is False and k["type"] == "bool"
    assert k["description"] == config_loader.STRIPPED_REFUSE_DESCRIPTION
    for report in ("M23-e2e.md", "M23-e2e2.md"):
        assert report in k["description"]
    assert "refused ordinary requests" in k["description"]
    text = (Path(__file__).parent.parent / "config" / "music.yaml").read_text()
    _, lines = config_loader.parse_yaml(text, "music.yaml")
    assert config_files.key_comments(text, lines)["stripped_request.refuse"] == config_loader.STRIPPED_REFUSE_DESCRIPTION


def test_the_settings_page_falls_back_to_the_schema_description(tmp_path):
    """orchestrator/settings_view.py: a key the live file lacks shows the
    schema's description; a key with a comment keeps its comment."""
    import settings_view
    shipped = (Path(__file__).parent.parent / "config" / "music.yaml").read_text()
    (tmp_path / "music.yaml").write_text(shipped[:shipped.index("# The stripped-request guard")])
    ts_keys = [{"key": "stripped_request.refuse", "value": False, "source": "code default",
                "description": config_loader.STRIPPED_REFUSE_DESCRIPTION},
               {"key": "verify.timeout_s", "value": 8, "source": "file", "description": None}]
    with patch.object(settings_view.settings_store, "EDITABLE",
                      {"music.yaml": settings_view.settings_store.EDITABLE["music.yaml"]}):
        entry, = settings_view.file_entries(tmp_path, {"files": {"music.yaml": ts_keys}}, git_ok=False)
    got = {k["key"]: k["comment"] for k in entry["keys"]}
    assert got["stripped_request.refuse"] == config_loader.STRIPPED_REFUSE_DESCRIPTION
    assert got["verify.timeout_s"].startswith("Seconds tool_service polls a player")


def test_with_the_guard_off_the_sentence_is_not_read(stripped_env):
    def boom(*a, **k):
        raise AssertionError("taste.said_request called with the guard off")
    with patch.object(taste, "said_request", side_effect=boom) as spy:
        for said in (CAPTURES[0], "play whatever's good", "Play back"):
            r = _post(query="music", kind="genre", player="Kitchen", said=said)
            assert r.status_code == 200, r.text
    assert spy.call_count == 0


def test_with_the_guard_on_the_sentence_is_read(stripped_env, guard_on):
    with patch.object(taste, "said_request", wraps=taste.said_request) as spy:
        r = _post(query="music", kind="genre", player="Kitchen", said=CAPTURES[0])
    assert r.status_code == 422 and spy.call_count == 1


@pytest.mark.parametrize("said", OFF_SENTENCES)
def test_with_the_guard_off_every_sentence_picks_as_with_none(stripped_env, said):
    assert config_loader.current().music.stripped_request.refuse is False
    want = _baseline(stripped_env, query="music", kind="genre")
    r = _post(query="music", kind="genre", said=said)
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want
    assert r.json()["pick"]["phrase"] == ""


@pytest.mark.parametrize("body", [{"query": "music"}, {"query": "music", "kind": "genre"},
                                  {"query": "some music", "kind": "genre"},
                                  {"query": "music", "kind": "genre", "player": "Kitchen"}])
@pytest.mark.parametrize("said", [*CAPTURES, CAPTURES[0].removesuffix(" in the Kitchen")])
def test_with_the_guard_off_the_captures_pick_in_every_shape(stripped_env, body, said):
    want = _baseline(stripped_env, **body)
    r = _post(**body, said=said)
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want
    assert len(_picks(stripped_env)) == 2                                # the baseline's and this one


# ── fix round 2: two repairs, for when the guard is turned on ───────────────

PLAYERS = [{"entity_id": "media_player.kitchen_ma", "name": "kitchen voice pe", "area": "Kitchen"}]


@pytest.mark.parametrize("said", ["play whatever's good", "play whatever’s good", "play whatever's popular",
                                  "play whatever's next", "play something's on", "Play anything'll do"])
def test_a_list_word_with_an_apostrophe_suffix_counts(said):
    """The second end-to-end test's finding 1."""
    assert taste.said_request(said) == (True, "")


@pytest.mark.parametrize("said,what", [("play it's my life", "it's my life"),
                                       ("Play it’s a beautiful day", "it’s a beautiful day"),
                                       ("play that's life", "that's life")])
def test_a_pronoun_with_an_apostrophe_suffix_still_does_not_count(said, what):
    assert taste.said_request(said) == (False, what)


@pytest.mark.parametrize("said", ["Play in the Kitchen", "play in the kitchen", "Play on the Kitchen speaker",
                                  "Play some in the Kitchen", "play some in the kitchen, please",
                                  "Play in the Kitchen.", "play now in the kitchen"])
def test_a_bare_verb_with_a_room_is_generic(said):
    """The second end-to-end test's finding 3: the room is dropped before
    the bare-verb and filler rules, as the M11 parser drops it."""
    assert taste.said_request(said, PLAYERS) == (True, "")


@pytest.mark.parametrize("said,what", [("Play back in the Kitchen", "back in the Kitchen"),
                                       (CAPTURES[0], "right here, right now, bud"),
                                       ("Play back on the Kitchen speaker", "back on the Kitchen speaker"),
                                       ("play in the air tonight", "in the air tonight")])
def test_dropping_the_room_keeps_the_captures_refused(said, what):
    assert taste.said_request(said, PLAYERS) == (False, what)


def test_a_room_that_is_not_a_player_is_not_dropped():
    """Only a real room or a speaker said as one: with no players known,
    "in the kitchen" is words."""
    assert taste.said_request("Play in the Kitchen")[0] is False
    assert taste.said_request("Play on the Kitchen speaker") == (True, "")      # a speaker, said as one


def test_the_guard_drops_the_room_on_music(stripped_env, guard_on):
    want = _baseline(stripped_env, query="music", kind="genre", player="Kitchen")
    r = _post(query="music", kind="genre", player="Kitchen", said="Play in the Kitchen")
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want


def test_whatevers_good_picks_with_the_guard_on(stripped_env, guard_on):
    """The second end-to-end test's blocker, with the model's arguments."""
    body = {"query": "good music", "kind": "genre", "player": "Kitchen"}
    want = _baseline(stripped_env, **body)
    r = _post(**body, said="play whatever's good in the Kitchen")
    assert r.status_code == 200, r.text
    assert (r.json(), [c[2] for c in _plays(stripped_env)]) == want


# ── fix round 2, ruling E: one test per word of the guard's lists ──────────
# The lists as ruled (fix round 1). Each word gets a sentence that is
# generic only because of that word, so removing a word from taste.py
# fails its test; adding one fails the equality test until it has a test.

MUSIC_WORDS = ["something", "anything", "everything", "whatever", "stuff", "song", "songs", "tune", "tunes",
               "track", "tracks", "jam", "jams", "mix", "playlist", "playlists", "radio", "station", "album",
               "albums", "record", "records", "library", "sounds", "vibes", "noise", "favorites", "favourites",
               "usual", "more", "again"]
PRONOUNS = ["that", "it", "this", "one"]
FILLER = ["please", "thanks", "thank", "you", "now", "kronk", "okay", "ok", "um", "uh"]
SOME = ["some", "sum"]


def test_the_word_lists_are_the_ones_tested():
    assert taste.SAID_MUSIC_WORDS == set(MUSIC_WORDS) and len(MUSIC_WORDS) == 31
    assert taste.SAID_MUSIC_PRONOUNS == set(PRONOUNS)
    assert taste.SAID_FILLER == set(FILLER)
    assert taste._SAID_SOME == set(SOME)


def test_the_controls_are_refused():
    """Without a listed word each sentence shape below is refused."""
    for said in ("play back zzz", "play back zzz's", "play zzz back", "play zzz", "play some zzz"):
        assert taste.said_request(said)[0] is False, said


@pytest.mark.parametrize("w", MUSIC_WORDS)
def test_each_word_for_music_counts(w):
    assert taste.said_request(f"play back {w}") == (True, "")
    assert taste.said_request(f"play back {w}'s") == (True, "")
    assert taste.said_request(f"play back {w}’s") == (True, "")


@pytest.mark.parametrize("w", PRONOUNS)
def test_each_pronoun_counts_right_after_the_verb_only(w):
    assert taste.said_request(f"play {w} back") == (True, "")
    assert taste.said_request(f"play back, {w} is slow")[0] is False
    assert taste.said_request(f"play {w}'s back")[0] is False


@pytest.mark.parametrize("w", FILLER)
def test_each_filler_word_counts_after_a_bare_verb_and_after_some(w):
    assert taste.said_request(f"play {w}") == (True, "")
    assert taste.said_request(f"play some {w}") == (True, "")


@pytest.mark.parametrize("w", SOME)
def test_each_some_word_counts_with_nothing_after_it(w):
    assert taste.said_request(f"play {w}") == (True, "")
    assert taste.said_request(f"play {w} zzz")[0] is False
