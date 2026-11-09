"""Telemetry v2 (OpenTelemetry) — span creation, the no-op switch, and the
logs↔traces join. Uses the SDK's in-memory exporter; nothing leaves the
process. docs/plans/TELEMETRY_V2_PLAN.md."""
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestrator"))

import telemetry  # noqa: E402
import events     # noqa: E402

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    telemetry.enable_for_tests(exp)
    yield exp
    telemetry.ENABLED = False


@pytest.fixture(autouse=True)
def _clear(exporter):
    exporter.clear()
    yield


def _by_name(spans):
    return {s.name: s for s in spans}


def test_pipeline_span_tree_and_attributes(exporter):
    class O:  # stands in for origin.Origin
        device_id = "dev123"
        area = "Kitchen"

    root = telemetry.start_pipeline("pipeline.shim", "play some jazz", rid="r1",
                                    transport="shim", origin=O())
    agent = telemetry.current().child_span("agent.home", input="play some jazz",
                                           metadata={"model": "gemma", "agent": "home"})
    gen = agent.child_generation("llm.gemma", model="gemma", metadata={"round": 1})
    gen.first_token()
    gen.end(output={"content": "", "tool_calls": ["play_music"]},
            usage={"input": 120, "output": 9}, metadata={"phase": "plan_1"})
    tool = agent.child_span("tool.play_music", input={"query": "jazz", "player": "basement"})
    with tool.current():
        # anything that runs here (httpx, a delegated agent) sees the tool span
        inner = telemetry.current().child_span("agent.sub")
        inner.end()
        trace_id_inside = telemetry.current_trace_id()
    tool.end(output="Now playing jazz")
    agent.end(output="Now playing jazz")
    telemetry.end_pipeline(root, output="Now playing jazz", route="home")

    spans = _by_name(exporter.get_finished_spans())
    assert set(spans) == {"pipeline.shim", "agent.home", "llm.gemma", "tool.play_music", "agent.sub"}

    p = spans["pipeline.shim"]
    assert p.parent is None
    assert p.attributes["kronk.rid"] == "r1"
    assert p.attributes["kronk.transport"] == "shim"
    assert p.attributes["kronk.route"] == "home"
    assert p.attributes["kronk.origin.device_id"] == "dev123"
    assert p.attributes["kronk.origin.area"] == "Kitchen"
    assert "kronk.input" not in p.attributes          # no text by default
    assert p.attributes["kronk.output_chars"] == len("Now playing jazz")

    assert spans["agent.home"].parent.span_id == p.context.span_id
    g = spans["llm.gemma"]
    assert g.parent.span_id == spans["agent.home"].context.span_id
    assert g.attributes["gen_ai.request.model"] == "gemma"
    assert g.attributes["gen_ai.usage.input_tokens"] == 120
    assert g.attributes["gen_ai.usage.output_tokens"] == 9
    assert g.attributes["kronk.phase"] == "plan_1"
    assert tuple(g.attributes["kronk.tool_calls"]) == ("play_music",)
    assert "kronk.ttft_ms" in g.attributes
    assert [e.name for e in g.events] == ["first_token"]

    t = spans["tool.play_music"]
    assert t.attributes["kronk.tool"] == "play_music"
    assert tuple(t.attributes["kronk.args_keys"]) == ("player", "query")
    assert "kronk.input" not in t.attributes
    # .current() made the tool span the parent of whatever ran inside it
    assert spans["agent.sub"].parent.span_id == t.context.span_id
    assert trace_id_inside == format(p.context.trace_id, "032x")
    # all one trace
    assert len({s.context.trace_id for s in spans.values()}) == 1


def test_error_marks_span_status(exporter):
    root = telemetry.start_pipeline("pipeline.message", "hi", rid="r2", transport="message")
    telemetry.end_pipeline(root, output="failed", route="health", level="ERROR",
                           status_message="health specialist: database is locked")
    (p,) = exporter.get_finished_spans()
    from opentelemetry.trace import StatusCode
    assert p.status.status_code is StatusCode.ERROR
    assert "database is locked" in p.status.description
    assert p.attributes["kronk.error"] is True


def test_root_is_cleared_after_pipeline(exporter):
    root = telemetry.start_pipeline("pipeline.message", "hi", rid="r3", transport="message")
    assert telemetry.root() is root
    assert telemetry.current_trace_id() is not None
    telemetry.end_pipeline(root)
    assert telemetry.root() is telemetry._NOOP
    assert telemetry.current_trace_id() is None     # context detached


def test_events_carry_trace_id(exporter, caplog):
    root = telemetry.start_pipeline("pipeline.message", "hi", rid="r4", transport="message")
    with caplog.at_level(logging.INFO, logger="kronk.events"):
        events.emit("request", text_preview="hi")
    telemetry.end_pipeline(root)
    line = json.loads(caplog.records[-1].getMessage())
    assert line["event"] == "request"
    assert line["trace_id"] == format(root._raw.get_span_context().trace_id, "032x")

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="kronk.events"):
        events.emit("request", text_preview="outside any trace")
    assert "trace_id" not in json.loads(caplog.records[-1].getMessage())


def test_client_spans_never_start_a_trace(exporter):
    """Outbound httpx calls outside a request (background polls) would each
    become a one-span trace; the sampler drops CLIENT-kind roots."""
    from opentelemetry.trace import SpanKind
    telemetry._tracer.start_span("GET", kind=SpanKind.CLIENT).end()
    assert exporter.get_finished_spans() == ()
    root = telemetry.start_pipeline("pipeline.message", "hi", rid="r5", transport="message")
    telemetry._tracer.start_span("GET", kind=SpanKind.CLIENT).end()   # inside a run: kept
    telemetry.end_pipeline(root)
    assert {s.name for s in exporter.get_finished_spans()} == {"GET", "pipeline.message"}


def test_record_text_toggle(exporter, monkeypatch):
    monkeypatch.setattr(telemetry, "RECORD_TEXT", True)
    root = telemetry.start_pipeline("pipeline.message", "what's the weather", rid="r6",
                                    transport="message")
    tool = root.child_span("tool.get_weather", input={"location": "Laurel"})
    tool.end(output="Sunny, 61F")
    telemetry.end_pipeline(root, output="Sunny.")
    spans = _by_name(exporter.get_finished_spans())
    assert spans["pipeline.message"].attributes["kronk.input"] == "what's the weather"
    assert spans["pipeline.message"].attributes["kronk.output"] == "Sunny."
    assert json.loads(spans["tool.get_weather"].attributes["kronk.input"]) == {"location": "Laurel"}


def test_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(telemetry, "ENABLED", False)
    root = telemetry.start_pipeline("pipeline.message", "hi", rid="r7", transport="message")
    assert root is telemetry._NOOP
    gen = telemetry.root().child_span("agent.x").child_generation("llm.x", model="x")
    gen.first_token()
    gen.end(output="x", usage={"input": 1, "output": 1})
    with telemetry.root().child_span("tool.x").current():
        pass
    with telemetry.span("anything", k="v"):
        pass
    telemetry.end_pipeline(root, output="x", route="direct", level="ERROR", status_message="boom")
    assert telemetry.current_trace_id() is None
    assert telemetry.current() is telemetry._NOOP
