"""The spoken lead-in: "ask Kronk to <request>" goes straight to the model.

Sprint M19 (docs/sprint/M19-criteria.md; docs/features/lead-in.md). The
lead-in is the first rule of routing.RULES. It skips every regex shortcut
and every direct dispatch, cuts the lead-in off, and hands the request to
the coordinator with its full tool menu. Its words live in
config/lead_in.yaml (config_loader.LeadIn).

The pipeline tests drive the real FastAPI app on every mount with a fake
tool_service and a scripted model; nothing reaches the network.
"""
import asyncio
import json
import logging
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

import config_loader
import origin
import routing
import telemetry
from tests.test_direct_dispatch import (  # noqa: F401  (client is a fixture)
    MOUNTS, OK, STAMP, FakeToolService, client, send,
)
from tests.test_mm_update_pin import FakeToolService as MirrorToolService
from tests.test_mm_update_pin import REFUSAL, make_fake_llm

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"

# ── 1. The rule (criteria 1, 2, 5) ───────────────────────────────────────────

# utterance -> the request the model is given
LEAD_INS = [
    ("ask Kronk to pause", "pause"),
    ("Ask Kronk to pause.", "pause."),
    ("ASK KRONK TO PAUSE", "PAUSE"),
    ("ask kronk pause", "pause"),
    ("ask Kronk what song is this", "what song is this"),
    ("ask Kronk, what's playing?", "what's playing?"),
    ("ask Kronk: to pause", "pause"),
    ("ask   Kronk,  to  update the magic mirror", "update the magic mirror"),
    ("ask Kronk to find some deep house music and play it", "find some deep house music and play it"),
    (" Ask Kronk to play Groove Armada Radio on Pandora.", "play Groove Armada Radio on Pandora."),
    ("hey, ask Kronk to pause", "pause"),
    ("Hey ask Kronk to pause", "pause"),
    ("Okay, ask Kronk to pause", "pause"),
    ("OK ask Kronk to pause", "pause"),
    ("ok, ask Kronk to pause", "pause"),
    ("please ask Kronk to pause", "pause"),
    ("can you ask Kronk to play some jazz", "play some jazz"),
    ("Could you ask Kronk to play some jazz?", "play some jazz?"),
    ("Okay, can you ask Kronk to turn it up?", "turn it up?"),
    ("hey. okay. ask Kronk to pause", "pause"),
    ("ask Kronk tonight's forecast", "tonight's forecast"),      # "to" needs a word boundary
    ("ask Kronk to to the moon", "to the moon"),
    ("ask Kronk please pause", "please pause"),
    ("ask Kronk to ask talkie about the moon", "ask talkie about the moon"),
    ("ask Kronk to\nplay some jazz", "play some jazz"),
]

# Not a lead-in: routed by the rest of the table (criterion 5).
NOT_LEAD_INS = [
    "what happens if I ask Kronk to pause",   # not at the start (anchored)
    "can I ask Kronk to pause the music",     # "can I" is not a filler
    "tell Kronk to pause",                    # not the lead-in verb
    "Kronk, pause",
    "ask Kronky to pause",                    # the name must end at a separator
    "ask Kronk's opinion on jazz",
    "asked Kronk to pause",
    "so ask Kronk to pause",                  # "so" is not a filler
    "ask Kronk",                              # nothing after it
    "Ask Kronk.",
    "ask Kronk to",
    "ask Kronk to.",
    "ask Kronk to please",
    "ask Kronk to ...!",
    "hey ask Kronk",
    "",
    "pause",
]


@pytest.mark.parametrize("text,request_", LEAD_INS)
def test_lead_in_is_cut_off(text, request_):
    d = routing.decide(text)
    assert (d.route, d.rule, d.direct, d.request) == ("direct", "lead_in", None, request_)
    assert routing.lead_in_request(text) == request_


@pytest.mark.parametrize("text", NOT_LEAD_INS)
def test_not_a_lead_in(text):
    d = routing.decide(text)
    assert d.rule != "lead_in" and d.request is None, d
    assert routing.lead_in_request(text) is None


@pytest.mark.parametrize("request_,shortcut", [
    ("pause", "playback"),
    ("play some jazz", "music"),
    ("what song is this", "now_playing"),
    ("search the web for the tallest building in Ohio", "search_phrase"),
    ("update the magic mirror", "mm_update"),
    ("thumbs up", "rate"),
    ("not that", "music_another"),
    ("what's the weather tomorrow", "weather"),
    ("never play nickelback", "taste"),
    ("restart the magic mirror", "mm_ops"),
    ("ask talkie about the moon", "talkie_explicit"),
    ("answer it yourself", "direct_override"),
    ("play https://music.youtube.com/watch?v=abc", "music_url"),
    ("summarize https://example.com", "url"),
])
def test_lead_in_skips_every_shortcut(request_, shortcut):
    """Criterion 2: the bare request is a shortcut (and, for the five M7
    rules, a direct call); with the lead-in it is the coordinator's."""
    assert routing.decide(request_).rule == shortcut
    for lead in ("ask Kronk to ", "Ask Kronk ", "hey, ask Kronk to "):
        d = routing.decide(lead + request_)
        assert (d.route, d.rule, d.direct) == ("direct", "lead_in", None), (lead, d)


def test_lead_in_is_the_first_rule():
    assert routing.RULES[0].name == "lead_in" and routing.RULES[0].route == "direct"
    assert [r.name for r in routing.RULES if r.request] == ["lead_in"]


def test_classify_logs_the_rule(caplog):
    """Criterion 7: a route_shortcut event names the rule (same shape as
    every other non-direct shortcut). The span attribute is checked through
    the pipeline below (test_pipeline_trace_names_the_rule)."""
    caplog.set_level(logging.INFO, logger="kronk.events")
    assert asyncio.run(routing.classify("ask Kronk to pause", [])) == "direct"
    ev = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"][-1]
    assert {k: ev[k] for k in ("event", "rule", "route")} == {
        "event": "route_shortcut", "rule": "lead_in", "route": "direct"}
    assert set(ev) - {"ts", "rid", "trace_id"} == {"event", "rule", "route"}


def test_long_input_is_fast():
    """No pathological backtracking on a long, filler-heavy utterance."""
    import time
    text = "hey, " * 2000 + "ask Kronk to " + "x " * 5000
    t0 = time.monotonic()
    assert routing.lead_in_request(text).startswith("x")
    assert routing.lead_in_request("hey, " * 2000 + "pause") is None
    assert time.monotonic() - t0 < 1.0


# ── 2. The words come from config/lead_in.yaml (criterion 3) ────────────────

@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    config_loader.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    config_loader.start()


def _errors(base):
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(base)
    return [(p.file, p.line, p.message) for p in e.value.problems]


def test_shipped_file_equals_the_builtin_default():
    cfg = config_loader.load(CONFIG)
    assert cfg.lead_in == config_loader.LeadIn()
    assert config_loader.defaults().lead_in == cfg.lead_in
    assert cfg.lead_in.verbs == ["ask"] and cfg.lead_in.names == ["Kronk"]
    assert "lead_in.yaml" in cfg.digests and not cfg.warnings


def test_no_invented_mishearing():
    """Mis-heard spellings of the name go in only with captured data; none
    was found by 2026-11-17 (docs/sprint/M19-build.md)."""
    assert config_loader.load(CONFIG).lead_in.names == ["Kronk"]


def test_a_reloaded_name_takes_effect(cfgdir):
    f = cfgdir / "lead_in.yaml"
    f.write_text(f.read_text().replace("  - Kronk\n", "  - Kronk\n  - Crank\n"))
    result = config_loader.reload()
    assert result == {"ok": True, "changed": ["lead_in.yaml:names: ['Kronk'] -> ['Kronk', 'Crank']"]}
    assert routing.decide("ask Crank to pause").request == "pause"
    assert routing.decide("ask Kronk to pause").request == "pause"


def test_no_fillers_means_none(cfgdir):
    (cfgdir / "lead_in.yaml").write_text("fillers: []\n")
    assert config_loader.reload()["ok"]
    assert routing.decide("ask Kronk to pause").rule == "lead_in"
    assert routing.decide("hey, ask Kronk to pause").rule != "lead_in"


def test_a_file_with_no_keys_is_all_defaults(cfgdir):
    (cfgdir / "lead_in.yaml").write_text("# nothing\n")
    cfg = config_loader.load(cfgdir)
    assert cfg.lead_in == config_loader.LeadIn()
    assert [(w.file, w.message) for w in cfg.warnings] == [
        ("lead_in.yaml", config_loader.EMPTY_YAML)]


@pytest.mark.parametrize("text,line,message", [
    ("verbs: [ask]\nnamez: [Kronk]\n", 2, "namez: unknown key"),
    ("names: Kronk\n", 1, "names: Input should be a valid list (got 'Kronk')"),
    ("names: []\n", 1, "names: List should have at least 1 item after validation, not 0 (got [])"),
    ("verbs: []\n", 1, "verbs: List should have at least 1 item after validation, not 0 (got [])"),
    ("names:\n  - Kronk\n  - kronk\n", 1,
     "names: Value error, 'kronk' is listed twice (matching ignores case) (got ['Kronk', 'kronk'])"),
    ("names:\n  - Kr.nk\n", 1,
     "names.0: String should match pattern '^[A-Za-z0-9'-]+( [A-Za-z0-9'-]+)*$' (got 'Kr.nk')"),
    ("fillers:\n  - \"can  you\"\n", 1,
     "fillers.0: String should match pattern '^[A-Za-z0-9'-]+( [A-Za-z0-9'-]+)*$' (got 'can  you')"),
    ("names:\n  - 3\n", 1, "names.0: Input should be a valid string (got 3)"),
    ("names:\n  - \"(.*)\"\n", 1,
     "names.0: String should match pattern '^[A-Za-z0-9'-]+( [A-Za-z0-9'-]+)*$' (got '(.*)')"),
])
def test_a_bad_file_is_refused_with_its_line(cfgdir, text, line, message):
    (cfgdir / "lead_in.yaml").write_text(text)
    assert _errors(cfgdir) == [("lead_in.yaml", line, message)]


def test_a_bad_reload_keeps_the_words_in_effect(cfgdir):
    (cfgdir / "lead_in.yaml").write_text("names: []\n")
    result = config_loader.reload()
    assert result["ok"] is False and result["errors"][0]["file"] == "config/lead_in.yaml"
    assert routing.decide("ask Kronk to pause").request == "pause"


def test_a_missing_file_falls_back_to_the_defaults_at_startup(cfgdir):
    (cfgdir / "lead_in.yaml").unlink()
    config_loader.start()
    st = config_loader.status()
    assert st["ok"] is False and st["source"] == "defaults"
    assert st["errors"] == [{"file": "config/lead_in.yaml", "line": None, "message": "file is missing"}]
    assert routing.decide("ask Kronk to pause").request == "pause"


# ── 3. The pipeline (criteria 1, 4, 5, 6) ────────────────────────────────────

def scripted(home_call=None, coordinator_answer="Done."):
    """A coordinator that delegates to home (when home_call is given) or
    answers in words, and a home agent that makes home_call. Records what
    each was given."""
    seen = {"coordinator": [], "home": [], "coordinator_tools": []}

    async def stream(messages, model, tools=None):
        names = {t["function"]["name"] for t in (tools or [])}
        user = [m["content"] for m in messages if m.get("role") == "user"]
        had_result = any(m.get("role") == "tool" for m in messages)
        if "ask_home" in names:
            seen["coordinator"].append(user[-1] if user else None)
            seen["coordinator_tools"].append(sorted(names))
            if home_call and not had_result:
                yield {"tool_calls": [{"id": "c1", "function": {
                    "name": "ask_home", "arguments": {"query": user[-1]}}}]}
            else:
                yield {"token": coordinator_answer}
        elif home_call and home_call[0] in names:
            seen["home"].append(user[-1] if user else None)
            yield {"tool_calls": [{"id": "h1", "function": {
                "name": home_call[0], "arguments": dict(home_call[1])}}]}
        else:
            yield {"token": "ok"}
        yield {"usage": {}}
    stream.seen = seen
    return stream


@pytest.mark.parametrize("mount", MOUNTS)
def test_coordinator_gets_the_request_without_the_lead_in(client, mount):
    model = scripted(home_call=("control_music", {"action": "pause"}))
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, mount, "ask Kronk to pause", STAMP if mount != "message" else None)
    assert model.seen["coordinator"][0] == "pause"
    assert model.seen["home"], "the coordinator did not delegate"
    # The model made the call: one POST, from the agent path, not the direct one.
    assert [p for p, _ in fake.posts()] == ["/music/control"]
    assert reply == OK["/music/control"][1]["detail"]


def test_coordinator_gets_its_full_tool_menu(client):
    model = scripted()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        send(client, "api_chat", "ask Kronk to pause")
        send(client, "api_chat", "hello there")
    lead, default = model.seen["coordinator_tools"]
    assert lead == default and "ask_home" in lead


@pytest.mark.parametrize("text", ["ask Kronk to pause", "ask Kronk what song is this",
                                  "ask Kronk to update the magic mirror", "ask Kronk to not that"])
def test_no_direct_dispatch_with_the_lead_in(client, text):
    """The model is asked even when the request alone is one of M7's five."""
    model = scripted()
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, "voice_api_chat", text, STAMP)
    assert model.seen["coordinator"] and reply == "Done."
    assert fake.posts() == []


@pytest.mark.parametrize("mount", ["api_chat", "voice_api_chat", "openai"])
def test_said_is_the_request_for_tool_service(client, mount):
    """origin.said (forwarded by play_music as `said`) is the request
    without the lead-in, so tool_service reads the sentence as it would
    without it (music_resolve.radio_name anchors on the request verb)."""
    model = scripted(home_call=("play_music", {"query": "Groove Armada", "media_type": "radio"}))
    fake = FakeToolService()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        send(client, mount, " Ask Kronk to play Groove Armada Radio on Pandora.",
             STAMP if mount == "voice_api_chat" else None)
    (path, payload), = fake.posts()
    assert path == "/music" and payload["said"] == "play Groove Armada Radio on Pandora."
    from tool_service import music_resolve
    assert music_resolve.radio_name(payload["said"]) == "Groove Armada"
    assert music_resolve.radio_name(" Ask Kronk to play Groove Armada Radio on Pandora.") is None


def test_said_stays_none_when_the_turn_has_no_words_of_its_own(client):
    """A shim request whose last message is not the user's: _turn_said is
    None, and the lead-in does not invent one."""
    seen = []
    model = scripted()

    async def spy(messages, model_name, tools=None):
        seen.append(origin.said.get())
        async for ev in model(messages, model_name, tools):
            yield ev
    with patch("agents.llm.stream", new=spy), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        r = client.post("/api/chat", json={"model": "kronk", "stream": False, "messages": [
            {"role": "user", "content": "ask Kronk to update the magic mirror"},
            {"role": "assistant", "content": "On it."}]})
    assert r.status_code == 200 and seen and set(seen) == {None}


# Criterion 4: the mirror pin (M4b). The pinned sentence inside the user's
# own words lets the update run when the model calls the tool; nothing else
# can start it.

def _mirror(client, mount, messages):
    log = []
    fake = MirrorToolService()
    with patch("agents.llm.stream", new=make_fake_llm(log)), patch("tools.httpx.AsyncClient", new=fake):
        if mount == "message":
            client.post("/message", json={"text": messages[-1]["content"]})
        else:
            path = {"api_chat": "/api/chat", "voice_api_chat": "/voice/api/chat",
                    "openai": "/v1/chat/completions"}[mount]
            r = client.post(path, json={"model": "kronk", "stream": False, "messages": messages})
            assert r.status_code == 200
    return log, fake


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("text", ["ask Kronk to update the magic mirror",
                                  "Hey, ask Kronk to update the magicmirror please"])
def test_lead_in_mirror_update_runs_when_the_model_calls_it(client, mount, text):
    log, fake = _mirror(client, mount, [{"role": "user", "content": text}])
    assert log[:2] == ["coordinator", "home"]          # the model path, not mm_update's direct call
    assert len(fake.mirror_posts) == 1


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("text", ["ask Kronk to update the mirror",
                                  "ask Kronk to upgrade the magic mirror",
                                  "ask Kronk to update magic mirror",
                                  "ask Kronk to run the mirror update"])
def test_lead_in_does_not_widen_the_mirror_pin(client, mount, text, caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    log, fake = _mirror(client, mount, [{"role": "user", "content": text}])
    assert "home" in log and fake.mirror_posts == []
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    assert any(e["event"] == "mm_update_refused" for e in evs)


@pytest.mark.parametrize("mount", ["api_chat", "voice_api_chat", "openai"])
def test_lead_in_from_an_earlier_turn_cannot_start_the_update(client, mount):
    log, fake = _mirror(client, mount, [
        {"role": "user", "content": "ask Kronk to update the magic mirror"},
        {"role": "assistant", "content": "Working on it."}])
    assert fake.mirror_posts == []


def test_refusal_sentence_unchanged():
    assert REFUSAL == 'To update the magic mirror, say "update the magic mirror".'


@pytest.mark.parametrize("text", ["Ask Kronk.", "ask Kronk to", "ask Kronk to please"])
def test_a_bare_lead_in_gets_a_plain_answer(client, text):
    """Criterion 5: no crash, no empty request: the utterance goes to the
    coordinator as said (the default rule) and is answered."""
    model = scripted(coordinator_answer="What should I ask Kronk?")
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        reply = send(client, "voice_api_chat", text, STAMP)
    assert model.seen["coordinator"] == [text]
    assert reply == "What should I ask Kronk?"


def test_webui_history_keeps_what_was_said(client):
    model = scripted(coordinator_answer="Paused.")
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        client.post("/message", json={"text": "ask Kronk to pause"})
    hist = client.sessions.window(client.orch.WEBUI_SESSION)
    assert hist[-2:] == [{"role": "user", "content": "ask Kronk to pause"},
                         {"role": "assistant", "content": "Paused."}]


def test_clear_history_is_still_the_transport_intercept(client):
    """"clear my history" is checked by the transports before routing; no
    model has a tool for it, so the lead-in does not change it."""
    model = scripted()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        reply = send(client, "api_chat", "ask Kronk to clear my history")
    assert reply == "Done — fresh start." and not model.seen["coordinator"]


def test_pipeline_trace_names_the_rule(client, caplog):
    """Criterion 7 through the pipeline: the request event keeps the
    utterance (the replay matches on it), the decision names lead_in, the
    root span's route is the coordinator's."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    caplog.set_level(logging.INFO, logger="kronk.events")
    model = scripted(home_call=("control_music", {"action": "pause"}))
    try:
        with patch("agents.llm.stream", new=model), \
             patch("tools.httpx.AsyncClient", new=FakeToolService()):
            send(client, "voice_api_chat", "ask Kronk to pause", STAMP)
    finally:
        telemetry.ENABLED = False
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    by = {}
    for e in evs:
        by.setdefault(e["event"], e)
    assert by["request"]["text_preview"] == "ask Kronk to pause"
    assert (by["route_shortcut"]["rule"], by["route_shortcut"]["route"]) == ("lead_in", "direct")
    assert by["request_complete"]["route"] == "direct"
    tool_calls = [e for e in evs if e["event"] == "tool_call"]
    assert [(e["agent"], e["tool"]) for e in tool_calls] == [
        ("coordinator", "ask_home"), ("home", "control_music")]
    assert not any(e.get("direct") for e in tool_calls)
    spans = exporter.get_finished_spans()
    decide = next(s for s in spans if s.name == "routing.decide")
    root = next(s for s in spans if s.name == "pipeline.shim")
    assert decide.attributes["kronk.rule"] == "lead_in"
    assert decide.attributes["kronk.lead_in_chars"] == len("ask Kronk to pause") - len("pause")
    assert "kronk.direct" not in decide.attributes
    assert root.attributes.get("kronk.route") == "direct"
