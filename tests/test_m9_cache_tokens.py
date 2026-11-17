"""llama.cpp's prompt-cache split, recorded per generation (sprint M9).

LiteLLM passes llama.cpp's own count through as
usage.prompt_tokens_details.cached_tokens. Each agent round records it on
its `agent_round` event and as `kronk.cached_tokens` on its generation
span, so the M9 bench (and Grafana) can read cache reuse per generation.
docs/sprint/M9-criteria.md criterion 2.
"""
import json
import logging
from unittest.mock import patch

import pytest

import agents
import telemetry

pytestmark = pytest.mark.usefixtures("no_network")


def _model(usage):
    async def stream(messages, model, tools=None):
        yield {"token": "hi"}
        yield {"usage": usage}
    return stream


def _rounds(caplog):
    out = []
    for r in caplog.records:
        msg = r.getMessage()
        if '"agent_round"' in msg:
            out.append(json.loads(msg))
    return out


@pytest.mark.parametrize("usage,cached", [
    ({"prompt_tokens": 100, "completion_tokens": 3,
      "prompt_tokens_details": {"cached_tokens": 90}}, 90),
    ({"prompt_tokens": 100, "completion_tokens": 3,
      "prompt_tokens_details": {"cached_tokens": 0}}, 0),
    ({"prompt_tokens": 100, "completion_tokens": 3}, None),
    ({"prompt_tokens": 100, "prompt_tokens_details": None}, None),
    ({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": "x"}}, None),
])
@pytest.mark.asyncio
async def test_agent_round_event_carries_the_cache_split(caplog, usage, cached):
    caplog.set_level(logging.INFO, logger="kronk.events")
    with patch("agents.llm.stream", new=_model(usage)), patch("agents.metrics.record"):
        evs = [e async for e in agents.run_stream(agents.COORDINATOR, "q", [])]
    assert [e["text"] for e in evs if e["type"] == "token"] == ["hi"]
    (rnd,) = _rounds(caplog)
    assert rnd["prompt_tokens"] == 100
    assert rnd["cached_tokens"] == cached


@pytest.mark.asyncio
async def test_generation_span_carries_the_cache_split():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    usage = {"prompt_tokens": 2016, "completion_tokens": 40,
             "prompt_tokens_details": {"cached_tokens": 2001}}
    try:
        root = telemetry.start_pipeline("pipeline.test", "q")
        with patch("agents.llm.stream", new=_model(usage)), patch("agents.metrics.record"):
            [e async for e in agents.run_stream(agents.COORDINATOR, "q", [])]
        telemetry.end_pipeline(root)
    finally:
        telemetry.ENABLED = False
    gen = next(s for s in exporter.get_finished_spans() if s.name.startswith("llm."))
    assert gen.attributes["gen_ai.usage.input_tokens"] == 2016
    assert gen.attributes["kronk.cached_tokens"] == 2001
