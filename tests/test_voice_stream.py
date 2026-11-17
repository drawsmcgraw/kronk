"""Sentence streaming on the speech mount (sprint M8, criteria 2-13, 23).

With VOICE_STREAM=on a streaming /voice/api/chat request gets its reply in
units as they complete (speech_stream.SpeechBuffer over split_units), one
held back for retracts. With it off (the default) the reply is buffered and
sent as one chunk, byte for byte as before M8.

The pipeline tests drive the real FastAPI app with a fake tool_service and
a scripted model; one drives main._ollama_speech_stream directly to show the
first chunk leaves while the model is still generating. Nothing reaches the
network.
"""
import asyncio
import json
import logging
import os
import random
import re
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import agents  # noqa: E402
import config_loader  # noqa: E402
import errors  # noqa: E402
import render  # noqa: E402
import telemetry  # noqa: E402
from speech_stream import SpeechBuffer, split_units  # noqa: E402
from tests.test_direct_dispatch import (  # noqa: E402,F401  (client is a fixture)
    FAIL, OK, STAMP, FakeToolService, client,
)
from tests.test_speech_stream import CASES, GENERATED, ws  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

ON = {"VOICE_STREAM": "on"}
OFF = {"VOICE_STREAM": "off"}
QUESTION = "explain how a heat pump works"      # rule default: the coordinator

LONG = ("A heat pump moves heat instead of making it. In winter it pulls heat from the "
        "outside air, even when it is cold. A compressor raises the temperature of that heat. "
        "In summer the cycle runs the other way.")


def words(text):
    """The text as a model streams it: word by word."""
    return re.findall(r"\S+\s*|\s+", text)


class Model:
    """agents.llm.stream stand-in. Each agent (coordinator: offered ask_*;
    home: offered play_music) plays its list of rounds in order. A round is
    a dict: tokens (str, streamed word by word), call (tool, args) or
    raise_after (raise once the tokens are out)."""

    def __init__(self, **rounds):
        self.rounds = {k: list(v) for k, v in rounds.items()}

    def __call__(self, messages, model, tools_arg=None):
        names = {t["function"]["name"] for t in (tools_arg or [])}
        who = "coordinator" if any(n.startswith("ask_") for n in names) else (
            "home" if "play_music" in names else "other")
        r = self.rounds.get(who, []).pop(0) if self.rounds.get(who) else {"tokens": f"{who} answer."}

        async def gen():
            for w in words(r.get("tokens", "")):
                yield {"token": w}
            if r.get("raise_after"):
                raise RuntimeError("LiteLLM connection reset")
            if r.get("call"):
                name, args = r["call"]
                yield {"tool_calls": [{"id": "c1", "function": {"name": name, "arguments": args}}]}
            yield {"usage": {}}
        return gen()


def post(c, text, *, env, stream=True, model=None, tools=None, system=STAMP):
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": text}]
    with patch.dict(os.environ, env), \
         patch("agents.llm.stream", new=model or Model()), \
         patch("tools.httpx.AsyncClient", new=tools or FakeToolService(OK)):
        r = c.post("/voice/api/chat", json={"model": "kronk", "messages": msgs, "stream": stream})
    assert r.status_code == 200, r.text
    return r


def lines(r):
    out = [json.loads(line) for line in r.text.split("\n") if line]   # every line is one JSON object
    assert r.text.endswith("\n")
    return out


def contents(r):
    return [x["message"]["content"] for x in lines(r) if not x["done"]]


def strip_time(objs):
    return [{k: v for k, v in o.items() if k != "created_at"} for o in objs]


# ── Criterion 2: several chunks, each whole and spoken ───────────────────────

def test_a_long_reply_leaves_in_several_spoken_chunks(client):
    md = "**Heat pumps** move heat. They do not *make* it.\n\n## How\nA compressor works. Then it cools."
    r = post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": md}]))
    got = contents(r)
    assert len(got) >= 3
    assert all("*" not in c and "#" not in c for c in got)          # each through to_speech
    for c in got:
        assert c.strip() == render.to_speech(c)                      # whole units, already rendered
    assert ws(" ".join(got)) == ws(render.to_speech(md))


@pytest.mark.asyncio
async def test_first_chunk_leaves_while_the_model_is_still_generating():
    """A slow model: after three sentences it waits until the test has seen
    a chunk. Buffered, nothing could leave and this would time out."""
    import orchestrator.main as orch
    seen_chunk = asyncio.Event()
    finished = {"model": False}

    async def slow(messages, model, tools=None):
        for w in words("One sentence here. Two sentences here. Three sentences here. "):
            await asyncio.sleep(0)
            yield {"token": w}
        await asyncio.wait_for(seen_chunk.wait(), 5)
        for w in words("Four is the last one."):
            yield {"token": w}
        finished["model"] = True
        yield {"usage": {}}

    out = []
    with patch.dict(os.environ, ON), patch("agents.llm.stream", new=slow), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("agents.metrics.record", new=lambda **kw: None):
        gen = orch._ollama_speech_stream(QUESTION, "m", [], None, QUESTION)
        async for line in gen:
            obj = json.loads(line)
            if not obj["done"] and obj["message"]["content"]:
                out.append((obj["message"]["content"], finished["model"]))
                seen_chunk.set()
    # Sentence 1 leaves once sentence 2 has completed (the hold), while the
    # model is still blocked mid-reply.
    assert out[0] == ("One sentence here.", False)
    assert "".join(c for c, _ in out) == ("One sentence here. Two sentences here. "
                                          "Three sentences here. Four is the last one.")


# ── Criterion 3: the same text as the buffered path ──────────────────────────

def _feed(text, rng, hold=1):
    buf = SpeechBuffer(hold)
    out = []
    i = 0
    while i < len(text):
        k = rng.randint(1, 12)
        out += buf.feed(text[i:i + k])
        i += k
    out += buf.finish()
    return out


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("hold", [1, 2])
def test_streamed_text_equals_buffered_text(name, hold):
    text, _ = CASES[name]
    got = _feed(text, random.Random(name), hold)
    assert ws("".join(got)) == ws(render.to_speech(text))


def test_generated_replies_stream_to_the_buffered_text():
    for seed, text in enumerate(GENERATED[:600]):
        got = _feed(text, random.Random(seed))
        assert ws("".join(got)) == ws(render.to_speech(text)), text


@pytest.mark.parametrize("name", list(CASES))
def test_pipeline_streamed_equals_pipeline_buffered(client, name):
    text, _ = CASES[name]
    on = post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": text}]))
    off = post(client, QUESTION, env=OFF, model=Model(coordinator=[{"tokens": text}]))
    [buffered] = contents(off)
    assert ws("".join(contents(on))) == ws(buffered)


# ── Criterion 4: the hold, and a retract inside it ───────────────────────────

def test_a_sentence_leaves_only_when_the_next_completes():
    buf = SpeechBuffer(1)
    assert buf.feed("First one. ") == []
    assert buf.feed("Second") == []              # unit 1 complete, held
    assert buf.feed(" one. Th") == ["First one."]
    assert buf.feed("ird.") == []
    assert buf.finish() == [" Second one. Third."]


def test_a_retract_inside_the_held_text_removes_it_before_anything_is_sent():
    buf = SpeechBuffer(1)
    assert buf.feed("Let me check that. ") == []
    assert buf.retract(len("Let me check that. ")) == 0
    assert buf.feed("Paused on the Office speaker.") == []
    assert buf.finish() == ["Paused on the Office speaker."]
    assert buf.unhonoured == []


def test_tool_round_preamble_is_never_spoken(client):
    """The home agent writes a preamble, then calls play_music (terminal):
    the preamble is retracted while held; only the tool's sentence is spoken."""
    model = Model(home=[{"tokens": "Let me find some jazz for you. ",
                         "call": ("play_music", {"query": "jazz"})}])
    r = post(client, "play some jazz", env=ON, model=model)
    got = contents(r)
    assert not any("Let me" in c for c in got)
    assert got == [OK["/music"][1]["detail"]]


def test_coordinator_preamble_before_delegating_is_never_spoken(client):
    model = Model(coordinator=[{"tokens": "I will ask the home agent. ",
                                "call": ("ask_home", {"task": "weather"})},
                               {"tokens": "It is sunny and 72 today."}],
                  home=[{"tokens": "Sunny, 72."}])
    got = contents(post(client, QUESTION, env=ON, model=model))
    assert got == ["It is sunny and 72 today."]


# ── Criterion 5: a retract longer than the held text ─────────────────────────

PREAMBLE = "First I check. Then I look. Then I call."


def test_a_retract_larger_than_the_hold_is_defined():
    """What is spoken: the units already sent stay spoken (they cannot be
    unsent); everything still held is dropped; the rest of the retract is
    recorded as unhonoured; later chunks are unaffected."""
    buf = SpeechBuffer(1)
    sent = []
    for w in words(PREAMBLE):
        sent += buf.feed(w)
    assert sent == ["First I check."]
    lost = buf.retract(len(PREAMBLE))
    assert lost == len("First I check. ")
    assert buf.unhonoured == [(len(PREAMBLE), len(PREAMBLE) - lost, lost)]
    assert buf.pending == ""
    sent += buf.feed("Paused on the Office speaker.") + buf.finish()
    assert sent == ["First I check.", " Paused on the Office speaker."]


def test_unhonoured_retract_is_logged_and_on_the_trace(client, caplog):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    caplog.set_level(logging.INFO, logger="kronk.events")
    model = Model(home=[{"tokens": PREAMBLE + " ", "call": ("play_music", {"query": "jazz"})}])
    try:
        got = contents(post(client, "play some jazz", env=ON, model=model))
    finally:
        telemetry.ENABLED = False
    assert got[0] == "First I check."                        # leaked: it was sent
    assert "Then I" not in "".join(got)
    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    [u] = [e for e in evs if e["event"] == "voice_stream_retract_unhonoured"]
    assert u["unhonoured"] == len("First I check. ") and u["hold"] == 1
    [vs] = [e for e in evs if e["event"] == "voice_stream"]
    assert vs["retract_unhonoured"] == 1 and vs["retract_unhonoured_chars"] == len("First I check. ")
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "pipeline.shim")
    span = next(s for s in spans if s.name == "voice_stream")
    assert span.parent.span_id == root.context.span_id
    assert span.attributes["kronk.retract_unhonoured_chars"] == len("First I check. ")
    assert root.attributes["kronk.voice_stream"] is True


def test_a_wider_hold_keeps_the_whole_preamble(client):
    model = Model(home=[{"tokens": PREAMBLE + " ", "call": ("play_music", {"query": "jazz"})}])
    wide = config_loader.current().voice.model_copy(
        update={"stream": config_loader.VoiceStream(hold_sentences=3)})
    with patch.object(config_loader.current(), "voice", wide):
        got = contents(post(client, "play some jazz", env=ON, model=model))
    assert got == [OK["/music"][1]["detail"]]


# ── Criterion 6: blocks at pipeline level ────────────────────────────────────

def test_a_table_leaves_as_one_chunk_after_it_ends(client):
    md = "Compare them.\n| Type | COP |\n|---|---|\n| Air | 3.5 |\n| Ground | 4.5 |\nGround wins. It costs more."
    got = contents(post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": md}])))
    table = [c for c in got if "Air" in c]
    assert len(table) == 1 and "Ground, 4.5" in table[0]
    assert ws("".join(got)) == ws(render.to_speech(md))


def test_an_unclosed_fence_is_released_at_the_end(client):
    md = "Run this. Now.\n```\necho one. echo two.\n"
    got = contents(post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": md}])))
    # Held "Now." and the unclosed fence leave together at the end.
    assert got == ["Run this.", " Now.\n\necho one. echo two."]
    assert ws("".join(got)) == ws(render.to_speech(md))


# ── Criterion 8: short replies keep their shape ──────────────────────────────

SHORT = {
    "one sentence": dict(text=QUESTION, model=lambda: Model(coordinator=[{"tokens": "It moves heat."}])),
    "direct dispatch": dict(text="pause", model=lambda: Model()),
    "terminal tool": dict(text="play some jazz", model=lambda: Model(
        home=[{"tokens": "", "call": ("play_music", {"query": "jazz"})}])),
    "friendly failure (direct, tool_service down)": dict(
        text="pause", model=lambda: Model(), tools=lambda: FakeToolService(down=True)),
    "friendly failure (model unreachable)": dict(
        text=QUESTION, model=lambda: Model(coordinator=[{"tokens": "", "raise_after": True}])),
}


@pytest.mark.parametrize("name", list(SHORT))
def test_short_replies_are_one_chunk_with_todays_text_and_done(client, name):
    s = SHORT[name]
    mk_tools = s.get("tools", lambda: FakeToolService(OK))
    on = lines(post(client, s["text"], env=ON, model=s["model"](), tools=mk_tools()))
    off = lines(post(client, s["text"], env=OFF, model=s["model"](), tools=mk_tools()))
    assert len(on) == 2 and on[1]["done"] is True
    assert strip_time(on) == strip_time(off)


# ── Criterion 9: protocol ────────────────────────────────────────────────────

def test_ndjson_protocol(client):
    r = post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": LONG}]))
    assert r.headers["content-type"].startswith("application/x-ndjson")
    objs = lines(r)
    assert [o["done"] for o in objs] == [False] * (len(objs) - 1) + [True]
    off = lines(post(client, QUESTION, env=OFF, model=Model(coordinator=[{"tokens": LONG}])))
    assert set(objs[-1]) == set(off[-1])                               # the same done fields
    assert strip_time([objs[-1]]) == strip_time([off[-1]])             # eval_count from the same text
    for o in objs[:-1]:
        assert set(o) == set(off[0]) and o["message"]["role"] == "assistant"


def test_non_streaming_request_on_the_voice_mount_is_one_object(client):
    on = post(client, QUESTION, env=ON, stream=False, model=Model(coordinator=[{"tokens": LONG}]))
    off = post(client, QUESTION, env=OFF, stream=False, model=Model(coordinator=[{"tokens": LONG}]))
    assert strip_time([on.json()]) == strip_time([off.json()])
    assert on.json()["done"] is True and on.json()["message"]["content"] == render.to_speech(LONG)


# ── Criterion 10: failure in mid-stream ──────────────────────────────────────

def test_model_failure_after_sentences_ends_with_the_friendly_sentence(client):
    model = Model(coordinator=[{"tokens": LONG, "raise_after": True}])
    on = post(client, QUESTION, env=ON, model=model)
    objs = lines(on)
    assert objs[-1]["done"] is True and sum(o["done"] for o in objs) == 1
    got = contents(on)
    assert len(got) >= 2
    friendly = errors.render("llm", "LiteLLM connection reset", "rid", errors.FRIENDLY)
    assert got[-1].endswith(friendly)
    joined = "".join(got)
    for sentence in ("A heat pump moves heat instead of making it.", "In summer"):
        assert joined.count(sentence) == 1                             # nothing repeated
    off = post(client, QUESTION, env=OFF, model=Model(coordinator=[{"tokens": LONG, "raise_after": True}]))
    assert ws(joined) == ws(contents(off)[0])


def test_failure_is_still_decided_by_the_flag_not_the_text(client):
    """A model that writes words that look like a failure is spoken as
    written, streamed or not (M2 fix 2)."""
    text = "Sorry — something went wrong on my end. I've noted the details. Just kidding, it is sunny."
    on = contents(post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": text}])))
    assert ws("".join(on)) == ws(text)


@pytest.mark.parametrize("fail_at", [1, 30])
def test_the_stream_survives_a_bug_in_the_buffer(client, caplog, fail_at):
    """A bug in the buffer after `fail_at` splits: the rest of the reply is
    collected as the buffered path does and leaves as one last chunk; the
    pipeline still runs to its end and releases the model lock."""
    import orchestrator.main as orch
    real, n = split_units, {"calls": 0}

    def flaky(text, final=False):
        n["calls"] += 1
        if n["calls"] >= fail_at:
            raise RuntimeError("bug")
        return real(text, final)
    with patch("speech_stream.split_units", new=flaky):
        r = post(client, QUESTION, env=ON, model=Model(coordinator=[{"tokens": LONG}]))
    objs = lines(r)
    assert objs[-1]["done"] is True and sum(o["done"] for o in objs) == 1
    assert ws("".join(contents(r))) == ws(render.to_speech(LONG))
    assert not orch._llm_lock.locked()
    assert any("voice stream buffer failed" in rec.getMessage() for rec in caplog.records)


# ── Criterion 11: what is stored is what was said ────────────────────────────

def test_trace_holds_the_final_reply_without_retracted_text(client):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    out = {}
    for name, env in (("on", ON), ("off", OFF)):
        exporter = InMemorySpanExporter()
        telemetry.enable_for_tests(exporter)
        model = Model(home=[{"tokens": "Let me look. ", "call": ("play_music", {"query": "jazz"})}])
        try:
            post(client, "play some jazz", env=env, model=model)
        finally:
            telemetry.ENABLED = False
        root = next(s for s in exporter.get_finished_spans() if s.name == "pipeline.shim")
        out[name] = root.attributes.get("kronk.output_chars")
    assert out["on"] == out["off"] == len(OK["/music"][1]["detail"])


# ── Criterion 12: the switch ─────────────────────────────────────────────────

@pytest.mark.parametrize("env", [{}, OFF, {"VOICE_STREAM": "OFF "}, {"VOICE_STREAM": "false"}])
def test_off_and_unset_are_todays_single_chunk(client, env):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VOICE_STREAM", None)
        base = lines(post(client, QUESTION, env={}, model=Model(coordinator=[{"tokens": LONG}])))
        other = lines(post(client, QUESTION, env=env, model=Model(coordinator=[{"tokens": LONG}])))
    assert len(base) == 2 and base[0]["message"]["content"] == render.to_speech(LONG)
    assert strip_time(other) == strip_time(base)


def test_an_unknown_value_is_off_and_logged(client, caplog):
    caplog.set_level(logging.ERROR)
    got = lines(post(client, QUESTION, env={"VOICE_STREAM": "sometimes"},
                     model=Model(coordinator=[{"tokens": LONG}])))
    assert len(got) == 2
    assert any("VOICE_STREAM='sometimes'" in r.getMessage() for r in caplog.records)


# ── Criterion 13: the display profile and the other routes ───────────────────

def _normal(text: str) -> str:
    text = re.sub(r'"created(_at)?": ("[^"]*"|\d+)', '"created": 0', text)
    return re.sub(r'"(ttft_s|generation_s|s|duration_s)": [0-9.]+', r'"\1": 0', text)


@pytest.mark.parametrize("route, body", [
    ("/api/chat", {"stream": True}), ("/api/chat", {"stream": False}),
    ("/v1/chat/completions", {"stream": True}), ("/v1/chat/completions", {"stream": False}),
    ("/message", None),
])
def test_other_routes_send_the_same_bytes_with_the_switch_on_and_off(client, route, body):
    md = "**Heat pumps** move heat. They do not *make* it.\n- one\n- two\nDone."
    out = {}
    for name, env in (("on", ON), ("off", OFF)):
        with patch.dict(os.environ, env), \
             patch("agents.llm.stream", new=Model(coordinator=[{"tokens": md}])), \
             patch("orchestrator.main.new_request_id", new=lambda: "fixedrid"):
            if route == "/message":
                r = client.post("/message", json={"text": QUESTION})
            else:
                r = client.post(route, json={"model": "kronk", "stream": body["stream"],
                                             "messages": [{"role": "user", "content": QUESTION}]})
        out[name] = _normal(r.text)
    assert out["on"] == out["off"]
    assert "**Heat" in out["on"]                                        # display: markdown kept


# ── Criterion 14 with streaming: the line applies either way ─────────────────

@pytest.mark.parametrize("env", [ON, OFF])
def test_spoken_reply_line_applies_with_either_switch_value(client, env):
    seen = []

    def model(messages, m, tools_arg=None):
        seen.append(messages[0]["content"])
        return Model(coordinator=[{"tokens": "Short."}])(messages, m, tools_arg)
    post(client, QUESTION, env=env, model=model)
    assert seen and seen[0].endswith(config_loader.current().voice.spoken_reply)


# ── split_units is the only cutter ───────────────────────────────────────────

def test_buffer_uses_split_units():
    text = "One. Two. Three."
    units, rest = split_units(text, final=True)
    assert ws("".join(_feed(text, random.Random(1)))) == ws(" ".join(render.to_speech(u) for u in units))


# ── config/voice.yaml: stream.hold_sentences ─────────────────────────────────

def test_hold_default_equals_the_shipped_file():
    from pathlib import Path
    cfg = config_loader.load(Path(__file__).resolve().parent.parent / "config")
    assert cfg.voice.stream == config_loader.VoiceStream() and cfg.voice.stream.hold_sentences == 1


@pytest.mark.parametrize("value", ["0", "6", "'1'", "1.5", "true"])
def test_hold_out_of_range_or_wrong_type_is_rejected(tmp_path, value):
    import shutil
    from pathlib import Path
    base = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parent.parent / "config", base)
    (base / "voice.yaml").write_text(f"stream:\n  hold_sentences: {value}\n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(base)
    [p] = [p for p in e.value.problems if p.file == "voice.yaml"]
    assert p.line == 2 and "stream.hold_sentences" in p.message
