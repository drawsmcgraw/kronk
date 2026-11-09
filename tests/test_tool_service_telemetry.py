"""tool_service telemetry (Telemetry v2): spans nest under the current one,
errors mark status, and the switch makes everything a no-op."""
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tool_service import telemetry


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    telemetry.enable_for_tests(exp)
    yield exp
    telemetry.ENABLED = False


@pytest.fixture(autouse=True)
def _clear(exporter):
    exporter.clear()


def _server_root():
    """Stand-in for the auto-instrumented request span (the sampler keeps
    only SERVER-kind roots)."""
    from opentelemetry.trace import SpanKind
    return telemetry._tracer.start_as_current_span("POST /music", kind=SpanKind.SERVER)


def test_spans_nest_and_carry_attributes(exporter):
    with _server_root():
        with telemetry.span("music.play_verified", **{"music.targets": 1}) as outer:
            with telemetry.span("ha.call_service", **{"ha.domain": "music_assistant",
                                                      "ha.service": "play_media",
                                                      "ha.entity_id": ["media_player.x"]}):
                pass
            outer.set(**{"music.outcome": "started", "music.failure": None})
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"POST /music", "music.play_verified", "ha.call_service"}
    assert spans["music.play_verified"].parent.span_id == spans["POST /music"].context.span_id
    assert spans["ha.call_service"].parent.span_id == spans["music.play_verified"].context.span_id
    assert spans["music.play_verified"].attributes["music.outcome"] == "started"
    assert "music.failure" not in spans["music.play_verified"].attributes   # None skipped
    assert tuple(spans["ha.call_service"].attributes["ha.entity_id"]) == ("media_player.x",)


def test_exception_marks_error_and_propagates(exporter):
    from opentelemetry.trace import StatusCode
    with _server_root():
        with pytest.raises(ValueError):
            with telemetry.span("ma.command", **{"ma.command": "music/search"}):
                raise ValueError("Music Assistant is unreachable (OSError).")
    (ma, _root) = sorted(exporter.get_finished_spans(), key=lambda s: s.start_time, reverse=True)
    assert ma.name == "ma.command"
    assert ma.status.status_code is StatusCode.ERROR
    assert "unreachable" in ma.status.description
    assert ma.attributes["kronk.error"] is True


def test_background_spans_do_not_start_traces(exporter):
    """A poller's helper span outside any request is dropped, not exported
    as a one-span trace."""
    with telemetry.span("ma.command", **{"ma.command": "players/all"}):
        pass
    assert exporter.get_finished_spans() == ()


def test_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(telemetry, "ENABLED", False)
    with telemetry.span("anything", k="v") as s:
        s.set(outcome="x")
    assert telemetry.current_trace_id() is None
