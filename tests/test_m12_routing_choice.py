"""Sprint M12: the routing.decide span carries a direct call's fixed choice
(control_music's action, rate_music's thumb) as kronk.direct_action, with
text recording off, so voice_tracer can tell a stop from a skip and a
thumbs down from a thumbs up without the words. And routing's command
forms are the ones in command_forms.py (one copy, read by voice_tracer)."""
import asyncio
import os
import sys

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestrator"))

import command_forms  # noqa: E402
import routing        # noqa: E402
import telemetry      # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    telemetry.enable_for_tests(exp)
    yield exp
    telemetry.ENABLED = False


@pytest.fixture(autouse=True)
def _clear(exporter, monkeypatch):
    monkeypatch.setattr(telemetry, "RECORD_TEXT", False)
    exporter.clear()
    yield


def _decide_span(exporter, text):
    root = telemetry.start_pipeline("pipeline.test", text)
    asyncio.run(routing.classify(text, []))
    telemetry.end_pipeline(root)
    return next(s for s in exporter.get_finished_spans() if s.name == "routing.decide")


@pytest.mark.parametrize("text, rule, choice", [
    ("Stop!", "playback", "pause"),
    ("skip this song", "playback", "next"),
    ("Thumbs down.", "rate", "down"),
    ("thumbs up", "rate", "up"),
])
def test_direct_choice_is_on_the_span_without_text(exporter, text, rule, choice):
    a = _decide_span(exporter, text).attributes
    assert a["kronk.rule"] == rule
    assert a["kronk.direct_action"] == choice
    assert "kronk.input" not in a and "kronk.output" not in a


def test_not_that_has_a_rule_and_no_choice(exporter):
    a = _decide_span(exporter, "not that").attributes
    assert a["kronk.rule"] == "music_another" and a["kronk.direct"] == "play_music"
    assert "kronk.direct_action" not in a


def test_routing_uses_the_shared_forms():
    assert routing._PLAYBACK_RE is command_forms.PLAYBACK_RE
    assert routing._RATE_RE is command_forms.RATE_RE
    assert routing._ANOTHER_RE is command_forms.ANOTHER_RE
    assert command_forms.command_of("Stop!") == ("playback", "pause")
    assert command_forms.command_of("not that in the kitchen") == ("music_another", None)
    assert command_forms.command_of("play some jazz") is None
    assert command_forms.command_of("") is None
