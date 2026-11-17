"""Sprint M12, criterion 1: TELEMETRY_RECORD_TEXT decides whether any span
attribute or log line carries what was said or what Kronk answered, in
each service.

- orchestrator: a voice request through the speech mount, end to end, with
  the switch off (the words appear in no span attribute and no log record)
  and on (the pipeline span carries what was heard and the reply);
- the URL query-string hooks and log filter, in the orchestrator and in
  tool_service (a web search's q= is someone's words too);
- tool_service's text() helper and the music decision log line;
- voice_tracer: the voice.run span with the switch off and on.
"""
import importlib
import json
import logging
import os
import sys
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import telemetry   # orchestrator/telemetry.py (orchestrator/ is on sys.path)
from tests.test_direct_dispatch import STAMP, FakeToolService, client, send  # noqa: F401
from tests.test_lead_in import scripted

pytestmark = pytest.mark.usefixtures("no_network")

SAID = "tell me about the zqxw florbent festival"
REPLY = "The zqxw florbent festival is not something I know."


def _all_text(exporter, caplog):
    vals = []
    for s in exporter.get_finished_spans():
        for v in (s.attributes or {}).values():
            vals.extend(v if isinstance(v, (list, tuple)) else [v])
        vals.append(s.name)
    vals.extend(r.getMessage() for r in caplog.records)
    return "\n".join(str(v) for v in vals)


@pytest.fixture
def traced(monkeypatch):
    exp = InMemorySpanExporter()
    telemetry.enable_for_tests(exp)
    yield exp
    telemetry.ENABLED = False


def _run(client, traced, caplog, monkeypatch, record):
    monkeypatch.setattr(telemetry, "RECORD_TEXT", record)
    caplog.set_level(logging.DEBUG)
    model = scripted(coordinator_answer=REPLY)
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        reply = send(client, "voice_api_chat", SAID, STAMP)
    assert reply == REPLY
    return _all_text(traced, caplog)


def test_orchestrator_switch_off_no_words_anywhere(client, traced, caplog, monkeypatch):
    blob = _run(client, traced, caplog, monkeypatch, record=False)
    assert "zqxw" not in blob.lower() and "florbent" not in blob.lower()
    req = [json.loads(r.getMessage()) for r in caplog.records
           if r.name == "kronk.events" and '"event": "request"' in r.getMessage()]
    assert req and req[0]["text_preview"] is None


def test_orchestrator_switch_on_pipeline_span_has_heard_and_reply(client, traced, caplog, monkeypatch):
    _run(client, traced, caplog, monkeypatch, record=True)
    root = next(s for s in traced.get_finished_spans() if s.name.startswith("pipeline."))
    assert root.attributes["kronk.input"] == SAID
    assert root.attributes["kronk.output"] == REPLY
    req = [json.loads(r.getMessage()) for r in caplog.records
           if r.name == "kronk.events" and '"event": "request"' in r.getMessage()]
    assert req[0]["text_preview"] == SAID[:80]


# ── URL query strings, log filter: both services ────────────────────────────

def _tool_service_telemetry():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tool_service")
    spec = importlib.util.spec_from_file_location("ts_telemetry_m12", os.path.join(path, "telemetry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TS = _tool_service_telemetry()


class _Span:
    def __init__(self, attrs):
        self.attributes = dict(attrs)

    def set_attribute(self, k, v):
        self.attributes[k] = v


URLS = {"http.url": "http://tool_service:8003/search?q=zqxw+florbent&count=5",
        "http.target": "/search?q=zqxw+florbent&count=5",
        "url.query": "q=zqxw+florbent", "http.route": "/search"}


@pytest.mark.parametrize("mod", [telemetry, TS], ids=["orchestrator", "tool_service"])
def test_url_query_dropped_from_spans_with_the_switch_off(mod, monkeypatch):
    monkeypatch.setattr(mod, "RECORD_TEXT", False)
    sp = _Span(URLS)
    mod._strip_url_attrs(sp, object())
    assert "zqxw" not in json.dumps(sp.attributes)
    assert sp.attributes["http.url"] == "http://tool_service:8003/search?<query>"
    assert sp.attributes["http.target"] == "/search?<query>"
    monkeypatch.setattr(mod, "RECORD_TEXT", True)
    sp = _Span(URLS)
    mod._strip_url_attrs(sp, object())
    assert sp.attributes == URLS


@pytest.mark.parametrize("mod", [telemetry, TS], ids=["orchestrator", "tool_service"])
def test_httpx_and_uvicorn_access_lines_lose_the_query(mod, monkeypatch):
    f = mod._NoQueryText()
    httpx_rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
                                  ("GET", "http://searxng:8080/search?q=zqxw", "HTTP/1.1", 200, "OK"), None)
    access = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("172.18.0.5:5000", "GET", "/search?q=zqxw&count=5", "1.1", 200), None)
    monkeypatch.setattr(mod, "RECORD_TEXT", False)
    for rec in (httpx_rec, access):
        assert f.filter(rec)
        assert "zqxw" not in rec.getMessage() and "?<query>" in rec.getMessage()
    assert access.args[4] == 200                 # uvicorn's formatter unpacks the args
    monkeypatch.setattr(mod, "RECORD_TEXT", True)
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, "%s", ("/search?q=zqxw",), None)
    f.filter(rec)
    assert "zqxw" in rec.getMessage()


@pytest.mark.parametrize("mod", [telemetry, TS], ids=["orchestrator", "tool_service"])
def test_filters_installed_on_the_two_loggers(mod):
    for name in ("httpx", "uvicorn.access"):
        assert any(type(f).__name__ == "_NoQueryText" for f in logging.getLogger(name).filters)


@pytest.mark.parametrize("mod", [telemetry, TS], ids=["orchestrator", "tool_service"])
def test_text_helper(mod, monkeypatch):
    monkeypatch.setattr(mod, "RECORD_TEXT", False)
    assert mod.text("play some jazz", 5) is None
    monkeypatch.setattr(mod, "RECORD_TEXT", True)
    assert mod.text("play some jazz", 5) == "play "
    assert mod.text(None, 5) is None


def test_question_marks_in_prose_are_left_alone():
    assert telemetry.strip_query("Stop what?") == "Stop what?"
    assert TS.strip_query("is it on? yes") == "is it on? yes"


# ── tool_service: a music log line with the switch off ──────────────────────

def test_tool_service_music_request_log_has_no_query_when_off(monkeypatch, caplog):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tool_service"))
    from tool_service import music_shadow
    monkeypatch.setattr(music_shadow.telemetry, "RECORD_TEXT", False)
    caplog.set_level(logging.INFO)
    music_shadow.logger.warning("music shadow failed for %r: %s",
                                music_shadow.telemetry.text("zqxw florbent radio", 120), "x")
    assert "zqxw" not in caplog.text
    monkeypatch.setattr(music_shadow.telemetry, "RECORD_TEXT", True)
    assert music_shadow.telemetry.text("zqxw florbent radio", 120) == "zqxw florbent radio"


# ── voice_tracer: voice.run, off and on ─────────────────────────────────────

def test_voice_tracer_spans_switch_off_and_on():
    from voice_tracer import convert
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "m12", "ha_run_kronk_answered.json")
    d = json.load(open(fix))
    off = convert.pipeline_run_spans(d["result"], d["run_id"], "kronk", record_text=False)
    blob = json.dumps([s.attrs for s in off])
    assert "French Dinner" not in blob and "Now playing" not in blob
    assert off[0].attrs["voice.transcript_chars"] == len("Play French Dinner Radio.")
    on = convert.pipeline_run_spans(d["result"], d["run_id"], "kronk", record_text=True)
    assert on[0].attrs["voice.transcript"] == "Play French Dinner Radio."
    assert on[0].attrs["voice.speech"] == "Now playing French Dinner Radio."
    assert next(s for s in on if s.key == "intent").attrs["voice.speech"] == "Now playing French Dinner Radio."


# ── the replay refuses a dev pair without text in its logs ──────────────────

def test_replay_refuses_without_text_logs(monkeypatch):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "voice_replay.py")
    spec = importlib.util.spec_from_file_location("voice_replay_m12", path)
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    target = {"orchestrator": "o", "tool_service": "t"}
    monkeypatch.setattr(vr, "docker", lambda *a: "PATH=/usr/bin\nTELEMETRY_RECORD_TEXT=false\n")
    with pytest.raises(vr.Refused, match="TELEMETRY_RECORD_TEXT off"):
        vr.require_text_logs(target)
    monkeypatch.setattr(vr, "docker", lambda *a: "TELEMETRY_RECORD_TEXT=true\n")
    vr.require_text_logs(target)
