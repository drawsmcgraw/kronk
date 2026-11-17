"""The messy-transcript gate: messy input goes to the model.

Sprint M14 step 1 (docs/sprint/M14-criteria.md; Drew, 2026-11-11;
docs/features/messy-transcript-gate.md). transcript_gate.check() decides
"messy" (more sentences or more words than config/tuning.yaml's
transcript_gate); routing's `messy` rule, after the typed-link rules, sends
such a request to the coordinator with the whole transcript, past every
rule-based fast path and every direct dispatch. Nothing is trimmed.

The pipeline tests drive the real FastAPI app on every mount with a fake
tool_service and a scripted model; nothing reaches the network.
"""
import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import config_loader
import routing
import telemetry
import transcript_gate
from tests.test_direct_dispatch import (  # noqa: F401  (client is a fixture)
    MOUNTS, OK, STAMP, FakeToolService, client, send,
)
from tests.test_lead_in import scripted
from tests.test_mm_update_pin import FakeToolService as MirrorToolService
from tests.test_mm_update_pin import make_fake_llm

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"
S, W = transcript_gate.count_sentences, transcript_gate.count_words

# ── 1. The counter (criterion 3) ─────────────────────────────────────────────

# text -> (sentences, words). The look-alikes the criteria name, each in a
# request, and the plain cases. Every one of these is a single clean request.
LOOKALIKES = [
    ("Play Mr. Brightside", 1, 3),
    ("Play Mr. Brightside by The Killers.", 1, 6),
    ("Play St. Vincent", 1, 3),
    ("Play St. Vincent radio on Pandora.", 1, 6),
    ("Play R.E.M.", 1, 2),
    ("Play R.E.M. on the kitchen speaker", 1, 6),
    ("Play R.E.M. Radio", 1, 3),                  # initials before a capital: still one
    ("play Dr. Dre", 1, 3),
    ("Play Dr. Dre radio", 1, 4),
    ("Play U2.", 1, 2),
    ("Play Mambo No. 5", 1, 4),
    ("Play Chanel No. 5 by Lou Bega.", 1, 7),
    ("Set an alarm for 7 a.m.", 1, 6),
    ("Set an alarm for 7 a.m. tomorrow", 1, 7),
    ("Set an alarm for 7 A.M. Tomorrow.", 1, 7),
    ("Is 98.6 a fever?", 1, 4),
    ("Wake me at 7.30.", 1, 4),
    ("Turn it up to 7.5 please.", 1, 6),
    ("Play... Anime Pop Radio", 1, 4),
    ("Play Anime Pop Radio...", 1, 4),
    ("Play Anime Pop Radio… on the kitchen speaker", 1, 8),
    ("Play jazz.", 1, 2),
    ("What's playing?", 1, 2),
    ("Stop!", 1, 1),
    (" Stop!", 1, 1),
    ("Stop!!", 1, 1),
    ("What's playing?!", 1, 2),
    ("Play jazz. ", 1, 2),
    ("Play jazz.  ?", 1, 2),                      # punctuation with no word after it
    ("Play \"Mr. Brightside\".", 1, 3),
    ("Play (feat. Pharrell) Get Lucky", 1, 5),
    ("Play Hotel Costas Radio. in the Kitchen", 1, 7),   # the blueprint's room suffix
    ("Play the song, beans and cornbread. in the Kitchen", 1, 9),
    ("Play Panic! at the Disco", 1, 5),
    ("Play Godspeed You! black emperor", 1, 5),
    ("play music.youtube.com/watch?v=abc", 1, 2),
    ("Mt. Joy radio", 1, 3),
    ("Play the Op. 27 nocturne", 1, 5),
]

# Messy: more than one sentence.
MULTI = [
    ("Play Anime Pop Radio. That's probably about it. Everything is pretty wet.", 3, 12),
    (" Play Anime Pop Radio.  That's probably about it. Everything is pretty wet.  "
     "I just needed to make some space.", 4, 19),
    (" Pause.  Thank you.", 2, 3),
    (" Resume.  Can you get the door?  Thank you.", 3, 8),
    (" Thumbs up!  I know but you missed the best part.", 2, 10),
    ("Stop.  Stop.", 2, 2),
    ("Play U2. Thanks.", 2, 3),
    ("No. Not that one.", 2, 4),                  # "No." before a word, not a number
    ("What's playing? Play some jazz.", 2, 5),
    ("Godspeed You! Black Emperor", 2, 4),        # cannot tell: a name with "!" before a capital
    ("Put on Portishead on the basement voice pe ma. That is all. in the Basement Theater", 2, 16),
    ("Play jazz.\"  Thanks.", 2, 3),               # a closing quote after the full stop
]

# What the counter cannot tell apart (documented in transcript_gate.py).
CANNOT = [
    ("Set an alarm for 7 a.m. Thanks.", 1),       # a sentence ending on initials
    ("Call Mr. Smith. He knows.", 2),             # "Smith." ends it; "Mr." does not
    ("Play Plan B. Thanks.", 1),                  # one letter before ".": read as an initial
    ("pause. play jazz", 1),                      # typed, lowercase: rule 3e
    ("play anime pop radio that's probably about it everything is pretty wet", 1),   # run-on
]


@pytest.mark.parametrize("text,sentences,words", LOOKALIKES)
def test_lookalikes_are_one_sentence(text, sentences, words):
    assert (S(text), W(text)) == (sentences, words)
    assert transcript_gate.check(text, 1, 20) is None


@pytest.mark.parametrize("text,sentences,words", MULTI)
def test_more_than_one_sentence(text, sentences, words):
    assert (S(text), W(text)) == (sentences, words)
    m = transcript_gate.check(text, 1, 20)
    assert m is not None and m.reasons == ["sentences"]


@pytest.mark.parametrize("text,sentences", CANNOT)
def test_what_the_counter_cannot_tell_apart(text, sentences):
    assert S(text) == sentences


@pytest.mark.parametrize("text", [None, "", "   ", "...", "?!", "- -", "\n\t"])
def test_no_words_is_zero_and_clean(text):
    assert (S(text), W(text)) == (0, 0)
    assert transcript_gate.check(text, 1, 20) is None


def test_words_are_tokens_with_a_letter_or_digit():
    assert W("Play  R.E.M. -- now ! 5") == 4
    assert W("Play\nAnime\tPop") == 3


def test_word_limit():
    text = " ".join(["word"] * 20)
    assert transcript_gate.check(text, 1, 20) is None
    m = transcript_gate.check(text + " more", 1, 20)
    assert m.reasons == ["words"] and (m.sentences, m.words) == (1, 21)


def test_both_halves():
    m = transcript_gate.check("One two. " + " ".join(["Word"] * 25), 1, 20)
    assert m.reason == "sentences+words"
    assert m.as_dict() == {"reason": "sentences+words", "sentences": 2, "words": 27,
                           "max_sentences": 1, "max_words": 20}


def test_a_half_switched_off():
    multi = "Play jazz. Thanks."
    long_ = " ".join(["w"] * 30)
    assert transcript_gate.check(multi, None, 20) is None
    assert transcript_gate.check(long_, None, 20).reasons == ["words"]
    assert transcript_gate.check(long_, 1, None) is None
    assert transcript_gate.check(multi, 1, None).reasons == ["sentences"]
    assert transcript_gate.check(multi + " " + long_, None, None) is None


def test_long_input_is_fast():
    text = ("Play Mr. Brightside. " * 5000) + ("x " * 50000)
    t0 = time.monotonic()
    assert S(text) == 5000 and W(text) == 65000
    assert time.monotonic() - t0 < 1.0


def test_the_counter_trims_nothing():
    """Criterion 11: the gate only counts. The coordinator gets the text as
    said (checked through the pipeline below); here, check() hands back no
    text at all."""
    m = transcript_gate.check(MULTI[0][0], 1, 20)
    assert set(m.as_dict()) == {"reason", "sentences", "words", "max_sentences", "max_words"}


# ── 2. The limits live in config/tuning.yaml (criterion 2) ──────────────────

@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    config_loader.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    config_loader.start()


def _set_limits(cfgdir, sentences, words):
    t = cfgdir / "tuning.yaml"
    text = re.sub(r"(?m)^  max_sentences: .*$", f"  max_sentences: {sentences}", t.read_text())
    text = re.sub(r"(?m)^  max_words: .*$", f"  max_words: {words}", text)
    t.write_text(text)


def test_shipped_limits_equal_the_builtin_defaults():
    cfg = config_loader.load(CONFIG)
    assert cfg.tuning.transcript_gate == config_loader.TranscriptGate()
    assert config_loader.defaults().tuning.transcript_gate == cfg.tuning.transcript_gate
    assert (cfg.tuning.transcript_gate.max_sentences, cfg.tuning.transcript_gate.max_words) == (1, 20)


def test_every_key_is_commented():
    text = (CONFIG / "tuning.yaml").read_text()
    block = text[text.index("transcript_gate:"):].splitlines()
    for i, line in enumerate(block[1:], 1):
        if line.strip() and not line.strip().startswith("#"):
            assert block[i - 1].strip().startswith("#"), line


def test_a_reload_takes_effect(cfgdir):
    _set_limits(cfgdir, 2, 30)
    result = config_loader.reload()
    assert result == {"ok": True, "changed": [
        "tuning.yaml:transcript_gate.max_sentences: 1 -> 2",
        "tuning.yaml:transcript_gate.max_words: 20 -> 30"]}
    assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "music"
    assert routing.decide("Play Anime Pop Radio. That's about it. It's wet.").rule == "messy"


def test_null_switches_a_half_off(cfgdir):
    _set_limits(cfgdir, "null", 20)
    assert config_loader.reload()["ok"]
    assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "music"
    assert routing.decide("play " + " ".join(["jazz"] * 25)).rule == "messy"
    _set_limits(cfgdir, 1, "null")
    assert config_loader.reload()["ok"]
    assert routing.decide("play " + " ".join(["jazz"] * 25)).rule == "music"
    assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "messy"


def test_both_null_switches_the_gate_off(cfgdir):
    _set_limits(cfgdir, "null", "null")
    assert config_loader.reload()["ok"]
    assert routing.messy("Play Anime Pop Radio. That's about it. " + "w " * 40) is None
    assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "music"


@pytest.mark.parametrize("sentences,words,message", [
    (0, 20, "transcript_gate.max_sentences: Input should be greater than or equal to 1 (got 0)"),
    (21, 20, "transcript_gate.max_sentences: Input should be less than or equal to 20 (got 21)"),
    ('"1"', 20, "transcript_gate.max_sentences: Input should be a valid integer (got '1')"),
    (1, 0, "transcript_gate.max_words: Input should be greater than or equal to 1 (got 0)"),
    (1, 501, "transcript_gate.max_words: Input should be less than or equal to 500 (got 501)"),
    (1, 20.5, "transcript_gate.max_words: Input should be a valid integer (got 20.5)"),
    (1, "true", "transcript_gate.max_words: Input should be a valid integer (got True)"),
])
def test_a_bad_limit_is_refused_with_its_line(cfgdir, sentences, words, message):
    _set_limits(cfgdir, sentences, words)
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    (p,) = e.value.problems
    text = (cfgdir / "tuning.yaml").read_text().splitlines()
    key = message.split(":")[0].split(".")[-1]
    assert (p.file, p.message) == ("tuning.yaml", message)
    assert text[p.line - 1].strip().startswith(key)


def test_a_bad_reload_keeps_the_limits_in_effect(cfgdir):
    _set_limits(cfgdir, 0, 20)
    result = config_loader.reload()
    assert result["ok"] is False
    assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "messy"


def test_an_unknown_key_is_refused(cfgdir):
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("  max_words: 20\n", "  max_words: 20\n  max_chars: 200\n"))
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert [p.message for p in e.value.problems] == ["transcript_gate.max_chars: unknown key"]


# ── 3. The rule's place in the table (criteria 1, 5, 7, 11) ─────────────────

AHEAD = ["lead_in", "talkie_explicit", "direct_override", "music_url", "url"]
BEHIND = ["search_phrase", "weather", "playback", "now_playing", "rate", "music_another",
          "taste", "music", "mm_update", "mm_ops", "default"]


def test_the_gate_sits_after_the_typed_link_rules():
    names = [r.name for r in routing.RULES]
    assert names == AHEAD + ["messy"] + BEHIND
    gate = routing.RULES[len(AHEAD)]
    assert (gate.route, gate.direct, gate.request) == ("direct", None, None)
    assert [r.name for r in routing.RULES if r.detail] == ["messy"]


def test_the_gate_is_the_only_new_rule():
    """Criterion 11: one rule added since M19 (sixteen rules then)."""
    assert len(routing.RULES) == 17


# A clean command for each rule behind the gate, and its messy forms.
COMMANDS = {
    "search_phrase": "Search the web for the tallest building in Ohio",
    "weather": "What's the weather tomorrow?",
    "playback": "Pause",
    "now_playing": "What song is this?",
    "rate": "Thumbs up",
    "music_another": "Not that",
    "taste": "Never play Nickelback",
    "music": "Play Anime Pop Radio",
    "mm_update": "Update the magic mirror",
    "mm_ops": "Restart the magic mirror",
}
TAILS = [". That's probably about it.", ".  Thank you.", "! Okay then.",
         " " + " ".join(["um"] * 21)]


@pytest.mark.parametrize("rule", sorted(COMMANDS))
def test_a_messy_transcript_skips_every_fast_path(rule):
    """Criterion 5: the clean command takes its rule (and, for M7's five,
    its direct call); with trailing speech it is the coordinator's, whole."""
    clean = COMMANDS[rule]
    assert routing.decide(clean).rule == rule
    for tail in TAILS:
        d = routing.decide(clean + tail)
        assert (d.route, d.rule, d.direct, d.request) == ("direct", "messy", None, None), (tail, d)
        assert d.detail["reason"] in ("sentences", "words")


@pytest.mark.parametrize("text,rule", [
    ("Ask Kronk to play jazz. That's all.", "lead_in"),
    ("Ask talkie about the moon. Then the stars.", "talkie_explicit"),
    ("Don't search the web. Just tell me. Why is the sky blue?", "direct_override"),
    ("Play this. Here: https://music.youtube.com/watch?v=abc", "music_url"),
    ("Summarize this. Here: https://example.com/a", "url"),
])
def test_the_rules_ahead_still_win_on_a_messy_transcript(text, rule):
    assert routing.messy(text) is not None
    assert routing.decide(text).rule == rule


def test_a_messy_lead_in_gives_the_coordinator_the_whole_request():
    d = routing.decide("Ask Kronk to play jazz. That's all.")
    assert d.request == "play jazz. That's all."


@pytest.mark.parametrize("text,rule", [(t, r) for r, t in COMMANDS.items()] + [
    ("Play Mr. Brightside", "music"), ("Play R.E.M.", "music"), ("play Dr. Dre", "music"),
    ("Play U2.", "music"), ("Play Mambo No. 5", "music"), ("Play St. Vincent radio", "music"),
    ("Play Hotel Costas Radio. in the Kitchen", "music"), ("Play... Anime Pop Radio", "music"),
    ("Stop!", "playback"), (" Stop!", "playback"), ("Thumbs down.", "rate"),
    ("What's playing?", "now_playing"), ("Not that in the office.", "music_another"),
    ("Play the album Destiny Waving by Ulrich Schnauss from YouTube music, in the kitchen", "music"),
    ("Search online to find why someone told me that the texas Senate candidate, Talarico, "
     "said that God is beings", "search_phrase"),
])
def test_a_clean_transcript_is_unaffected(text, rule):
    """Criterion 7, by example (the pin checks every pinned utterance)."""
    d = routing.decide(text)
    assert d.rule == rule and d.detail is None


def test_messy_is_decided_in_one_place():
    """Criterion 1: routing.messy reads the limits in effect and calls
    transcript_gate.check; nothing else counts sentences."""
    import inspect
    src = inspect.getsource(routing)
    assert src.count("transcript_gate.check(text") == 1
    with patch("transcript_gate.check", return_value=None) as check:
        assert routing.decide("Play Anime Pop Radio. That's about it.").rule == "music"
    check.assert_called_once_with("Play Anime Pop Radio. That's about it.", 1, 20)


def test_classify_logs_why(caplog):
    """Criterion 10: route_shortcut names the rule and why, with the numbers."""
    caplog.set_level(logging.INFO, logger="kronk.events")
    assert asyncio.run(routing.classify(MULTI[0][0], [])) == "direct"
    ev = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"][-1]
    assert {k: v for k, v in ev.items() if k not in ("ts", "rid", "trace_id")} == {
        "event": "route_shortcut", "rule": "messy", "route": "direct", "reason": "sentences",
        "sentences": 3, "words": 12, "max_sentences": 1, "max_words": 20}


def test_other_events_keep_their_shape(caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    asyncio.run(routing.classify("Play Anime Pop Radio", []))
    asyncio.run(routing.classify("hello there", []))
    a, b = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"][-2:]
    assert set(a) - {"ts", "rid", "trace_id"} == {"event", "rule", "route"}
    assert set(b) - {"ts", "rid", "trace_id"} == {"event", "text_preview", "route", "rule"}


# ── 4. The pipeline (criteria 5, 6, 8, 9, 10) ───────────────────────────────

ANIME = "Play Anime Pop Radio. That's probably about it. Everything is pretty wet."


@pytest.mark.parametrize("mount", MOUNTS)
def test_coordinator_gets_the_whole_transcript(client, mount):
    """Criteria 5, 6 and 9: on every mount the same, the coordinator gets
    the whole transcript with its full tool menu, and extracts the request."""
    model = scripted(home_call=("play_music", {"query": "Anime Pop", "media_type": "radio"}))
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        send(client, mount, ANIME, STAMP if mount != "message" else None)
    assert model.seen["coordinator"][0] == ANIME
    assert model.seen["home"], "the coordinator did not delegate"
    (path, payload), = fake.posts()
    assert path == "/music" and payload["said"] == ANIME


def test_coordinator_gets_its_full_tool_menu(client):
    model = scripted()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        send(client, "voice_api_chat", ANIME, STAMP)
        send(client, "voice_api_chat", "hello there", STAMP)
    messy_menu, default_menu = model.seen["coordinator_tools"]
    assert messy_menu == default_menu and "ask_home" in messy_menu


def test_radio_name_still_stops_at_the_full_stop():
    """Kept by Drew's decision: tool_service re-reads the sentence and cuts
    the station at the full stop."""
    from tool_service import music_resolve
    assert music_resolve.radio_name(ANIME) == "Anime Pop"
    assert music_resolve.radio_name(MULTI[1][0]) == "Anime Pop"
    assert music_resolve.radio_name("Play Anime Pop Radio") == "Anime Pop"


@pytest.mark.parametrize("text", [" Pause.  Thank you.", "What song is this? Okay then.",
                                  "Thumbs up!  I know but you missed the best part.",
                                  "Not that. Something with drums."])
def test_no_direct_dispatch_for_a_messy_transcript(client, text):
    """Criterion 5: the model is asked even when the first sentence alone is
    one of M7's five direct calls."""
    model = scripted()
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, "voice_api_chat", text, STAMP)
    assert model.seen["coordinator"] == [text] and reply == "Done."
    assert fake.posts() == []


def test_the_model_can_still_make_the_call(client):
    model = scripted(home_call=("control_music", {"action": "pause"}))
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, "voice_api_chat", " Pause.  Thank you.", STAMP)
    assert [p for p, _ in fake.posts()] == ["/music/control"]
    assert reply == OK["/music/control"][1]["detail"]


# Criterion 8: the mirror pin (M4b) with the gate in place.

def _mirror(client, mount, text):
    log = []
    fake = MirrorToolService()
    with patch("agents.llm.stream", new=make_fake_llm(log)), patch("tools.httpx.AsyncClient", new=fake):
        if mount == "message":
            client.post("/message", json={"text": text})
        else:
            path = {"api_chat": "/api/chat", "voice_api_chat": "/voice/api/chat",
                    "openai": "/v1/chat/completions"}[mount]
            r = client.post(path, json={"model": "kronk", "stream": False,
                                        "messages": [{"role": "user", "content": text}]})
            assert r.status_code == 200
    return log, fake


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("text", ["Update the magic mirror. And play some jazz.",
                                  "Thanks. Update the magic mirror.",
                                  "Okay! Update the magicmirror please. That's it."])
def test_messy_with_the_pinned_sentence_updates_only_through_the_model(client, mount, text):
    assert routing.decide(text).rule == "messy"
    log, fake = _mirror(client, mount, text)
    assert log[:2] == ["coordinator", "home"]       # the model path, not mm_update's direct call
    assert len(fake.mirror_posts) == 1


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("text", ["Update the mirror. And play some jazz.",
                                  "Restart the magic mirror. It froze again.",
                                  "Upgrade the magic mirror. Thanks."])
def test_messy_without_the_pinned_sentence_cannot_update(client, mount, text, caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    assert routing.decide(text).rule == "messy"
    log, fake = _mirror(client, mount, text)
    assert "home" in log and fake.mirror_posts == []
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    assert any(e["event"] == "mm_update_refused" for e in evs)


# The clear-history intercept, which runs before routing (criterion 5).

@pytest.mark.parametrize("mount", ["api_chat", "voice_api_chat", "openai"])
def test_clear_history_still_works_on_a_clean_request(client, mount):
    model = scripted()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        reply = send(client, mount, "Clear my history.", STAMP if mount == "voice_api_chat" else None)
    assert reply == "Done — fresh start." and not model.seen["coordinator"]


@pytest.mark.parametrize("mount", ["api_chat", "voice_api_chat", "openai"])
def test_a_messy_request_is_not_taken_for_clear_history(client, mount):
    text = "Play some jazz. Forget the conversation we had."
    model = scripted()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        reply = send(client, mount, text, STAMP if mount == "voice_api_chat" else None)
    assert model.seen["coordinator"] == [text] and reply == "Done."


def test_webui_clear_history(client):
    model = scripted(coordinator_answer="Fine.")
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        client.post("/message", json={"text": "hello"})
        client.post("/message", json={"text": "Play jazz. Then clear my history."})
        assert client.sessions.window(client.orch.WEBUI_SESSION), "a messy request cleared the history"
        client.post("/message", json={"text": "clear my history"})
        assert client.sessions.window(client.orch.WEBUI_SESSION) == []


def test_asks_to_clear_history():
    assert routing.asks_to_clear_history("clear my history")
    assert routing.asks_to_clear_history("Please clear the conversation.")
    assert not routing.asks_to_clear_history("Play jazz. Clear my history.")
    assert not routing.asks_to_clear_history("")
    assert not routing.asks_to_clear_history(None)


def test_pipeline_trace_says_why(client, caplog):
    """Criterion 10 through the pipeline: the route_shortcut event and the
    routing.decide span carry the rule, the half that fired and the numbers;
    the request event and the trace input keep the transcript."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    caplog.set_level(logging.INFO, logger="kronk.events")
    model = scripted(home_call=("play_music", {"query": "Anime Pop", "media_type": "radio"}))
    try:
        with patch("agents.llm.stream", new=model), \
             patch("tools.httpx.AsyncClient", new=FakeToolService()):
            send(client, "voice_api_chat", ANIME, STAMP)
    finally:
        telemetry.ENABLED = False
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    by = {}
    for e in evs:
        by.setdefault(e["event"], e)
    assert by["request"]["text_preview"] == ANIME
    rs = by["route_shortcut"]
    assert (rs["rule"], rs["route"], rs["reason"], rs["sentences"], rs["words"]) == (
        "messy", "direct", "sentences", 3, 12)
    assert [(e["agent"], e["tool"]) for e in evs if e["event"] == "tool_call"] == [
        ("coordinator", "ask_home"), ("home", "play_music")]
    spans = exporter.get_finished_spans()
    decide = next(s for s in spans if s.name == "routing.decide")
    root = next(s for s in spans if s.name == "pipeline.shim")
    assert {k: v for k, v in decide.attributes.items() if k.startswith("kronk.")} == {
        "kronk.rule": "messy", "kronk.messy_reason": "sentences", "kronk.messy_sentences": 3,
        "kronk.messy_words": 12, "kronk.messy_max_sentences": 1, "kronk.messy_max_words": 20,
        "kronk.output_chars": len("direct")}
    assert root.attributes.get("kronk.route") == "direct"
    assert root.attributes.get("kronk.input_chars") == len(ANIME)


def test_a_switched_off_half_is_not_on_the_span(client, cfgdir):
    """A limit of null is left off the span (OTel has no null), and the
    event says null."""
    _set_limits(cfgdir, 1, "null")
    assert config_loader.reload()["ok"]
    d = routing.decide(ANIME)
    assert d.detail == {"reason": "sentences", "sentences": 3, "words": 12,
                        "max_sentences": 1, "max_words": None}
