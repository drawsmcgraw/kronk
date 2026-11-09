"""OpenTelemetry instrumentation helpers — Telemetry v2 (docs/plans/TELEMETRY_V2_PLAN.md).

All tracing-SDK contact lives in this module so the rest of the
orchestrator never imports opentelemetry directly. Design rules (kept
from v1, Langfuse):

- When TELEMETRY_ENABLED != "true", or the SDK import/init fails, every
  helper degrades to a silent no-op. The pipeline must never break or slow
  down because telemetry is misconfigured.
- One *trace* per pipeline run: start_pipeline() opens the root span under
  the FastAPI server span (auto-instrumented) and makes it the current
  OTel context, so every outbound httpx call inside the run lands in the
  same trace with W3C `traceparent` propagated to tool_service. Stages
  attach children through root(). end_pipeline() closes and detaches.
- Spans never carry prompt or completion text unless
  TELEMETRY_RECORD_TEXT=true (privacy + size); `kronk.events` keeps its
  previews and now carries the trace id, so logs and traces join.
- Export is batched on a background thread (BatchSpanProcessor). A dead
  Tempo costs a log line per failed batch, never a slow request.
- Outbound-call spans never start a trace: a sampler drops CLIENT-kind
  root spans, so background polls (weather cache, /api/servers) do not
  litter Tempo with one-span traces.

Env (set on the container — see docker-compose.yml):
  TELEMETRY_ENABLED           "true" to instrument (default: off)
  TELEMETRY_RECORD_TEXT       "true" to add input/output previews to spans
  OTEL_SERVICE_NAME           resource service.name (kronk-orchestrator)
  OTEL_EXPORTER_OTLP_ENDPOINT http://tempo:4318 (the exporter appends /v1/traces)
  OTEL_BSP_SCHEDULE_DELAY     batch export interval, ms
"""
import json
import logging
import os
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

ENABLED = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
RECORD_TEXT = os.getenv("TELEMETRY_RECORD_TEXT", "false").lower() == "true"
TEXT_PREVIEW_CHARS = 500

_tracer = None       # opentelemetry.trace.Tracer when enabled
_trace = None        # the opentelemetry.trace module
_otel_context = None  # the opentelemetry.context module
_provider = None


def _init_sdk(exporter=None, batch: bool = True) -> bool:
    """Build the tracer provider. exporter=None → OTLP/HTTP from env."""
    global _tracer, _trace, _otel_context, _provider
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import (
        Decision, ParentBased, Sampler, SamplingResult,
    )

    class _NoClientRoots(Sampler):
        """A trace starts at a server span or a pipeline root, never at an
        outbound call (httpx CLIENT spans made outside any request)."""

        def should_sample(self, parent_context, trace_id, name, kind=None,
                          attributes=None, links=None, trace_state=None):
            if kind == trace.SpanKind.CLIENT:
                return SamplingResult(Decision.DROP, trace_state=trace_state)
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes,
                                  trace_state=trace_state)

        def get_description(self) -> str:
            return "NoClientRoots"

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter()   # endpoint + headers from OTEL_* env
    provider = TracerProvider(
        resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "kronk-orchestrator")}),
        sampler=ParentBased(root=_NoClientRoots()),
    )
    provider.add_span_processor(
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    )
    try:
        trace.set_tracer_provider(provider)   # once per process; later calls warn
    except Exception:
        pass
    _provider = provider
    _trace = trace
    _otel_context = otel_context
    _tracer = provider.get_tracer("kronk.orchestrator")
    return True


if ENABLED:
    try:
        _init_sdk()
        logger.info("telemetry: OpenTelemetry tracing enabled → %s",
                    os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "(default endpoint)"))
    except Exception as e:  # bad import, bad env
        logger.error("telemetry: disabled — OpenTelemetry init failed: %s", e)
        ENABLED = False


def instrument_app(app) -> None:
    """Auto-instrument the FastAPI app (server spans) and httpx (client
    spans + traceparent propagation). No-op when disabled."""
    if not ENABLED:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=_provider,
            excluded_urls=os.getenv("TELEMETRY_EXCLUDED_URLS", _DEFAULT_EXCLUDED),
            # One ASGI receive/send span per SSE chunk is a span per token.
            exclude_spans=["receive", "send"],
        )
        HTTPXClientInstrumentor().instrument(tracer_provider=_provider)
    except Exception as e:
        logger.error("telemetry: auto-instrumentation failed: %s", e)


# Page loads, polling endpoints and the Ollama/OpenAI discovery calls are
# not interactions — they would outnumber real traces a hundred to one.
_DEFAULT_EXCLUDED = ",".join([
    r"https?://[^/]+/$",
    r"https?://[^/]+/(static/|services|agents|finances|health|resources|performance|solar|shopping_list|history|files)",
    r"https?://[^/]+/api/(metrics|system|agents|servers|status|tags|version|show)",
    r"https?://[^/]+/voice/api/(tags|version|show)",
    r"https?://[^/]+/v1/models",
])


def current_trace_id() -> str | None:
    """Hex trace id of the current span, or None (logs ↔ traces join)."""
    if not ENABLED:
        return None
    try:
        ctx = _trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:
        return None


def _attr_value(v: Any):
    """Coerce to an OTel attribute value (str/bool/int/float or list of str)."""
    if v is None:
        return None
    if isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, (list, tuple)):
        return [x if isinstance(x, str) else json.dumps(x, default=str) for x in v]
    return json.dumps(v, default=str)[:TEXT_PREVIEW_CHARS]


def _text_preview(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s[:TEXT_PREVIEW_CHARS]


class _NoopObs:
    """Absorbs every call. Returned whenever telemetry is off or errored."""
    ended = True

    def child_span(self, *a, **kw):       return self
    def child_generation(self, *a, **kw): return self
    def first_token(self):                pass
    def end(self, *a, **kw):              pass
    def update_trace(self, *a, **kw):     pass
    def set(self, **kw):                  pass
    def current(self):                    return nullcontext()


_NOOP = _NoopObs()


class _Obs:
    """Thin wrapper over an OTel span. Guarantees no telemetry exception
    ever propagates into the pipeline."""

    def __init__(self, raw, kind: str = "span"):
        self._raw = raw
        self._kind = kind
        self._completion_start: float | None = None
        self._token = None   # context token when this span was attached
        self.ended = False

    # ── children ────────────────────────────────────────────────────────
    def _child(self, name: str, attrs: dict, input=None):
        try:
            span = _tracer.start_span(
                name, context=_trace.set_span_in_context(self._raw),
            )
            obs = _Obs(span)
            obs.set(**attrs)
            if RECORD_TEXT and input is not None:
                span.set_attribute("kronk.input", _text_preview(input))
            return obs
        except Exception as e:
            logger.debug("telemetry: child %s failed: %s", name, e)
            return _NOOP

    def child_span(self, name: str, input=None, metadata: dict | None = None):
        attrs = {f"kronk.{k}": v for k, v in (metadata or {}).items()}
        if name.startswith("tool."):
            attrs["kronk.tool"] = name[5:]
            if isinstance(input, dict):
                attrs["kronk.args_keys"] = sorted(input.keys())
        return self._child(name, attrs, input)

    def child_generation(self, name: str, model: str, input=None,
                         metadata: dict | None = None):
        attrs = {f"kronk.{k}": v for k, v in (metadata or {}).items()}
        attrs["gen_ai.request.model"] = model
        attrs["kronk.kind"] = "generation"
        obs = self._child(name, attrs, input)
        if obs is not _NOOP:
            obs._kind = "generation"
        return obs

    # ── attributes / context ────────────────────────────────────────────
    def set(self, **attrs) -> None:
        try:
            for k, v in attrs.items():
                v = _attr_value(v)
                if v is not None:
                    self._raw.set_attribute(k, v)
        except Exception as e:
            logger.debug("telemetry: set failed: %s", e)

    def current(self):
        """Context manager making this span the current one, so httpx
        client spans (and tool_service, via traceparent) hang under it."""
        try:
            return _trace.use_span(self._raw, end_on_exit=False)
        except Exception:
            return nullcontext()

    def first_token(self):
        """Call when the first streamed token arrives → TTFT on the span."""
        if self._completion_start is None:
            self._completion_start = time.time()
            try:
                self._raw.add_event("first_token")
            except Exception:
                pass

    def end(self, output=None, usage: dict | None = None,
            level: str | None = None, status_message: str | None = None,
            metadata: dict | None = None):
        """End the span. usage: {"input": n, "output": m} token counts."""
        if self.ended:
            return
        self.ended = True
        try:
            if metadata:
                self.set(**{f"kronk.{k}": v for k, v in metadata.items()})
            if usage:
                self.set(**{"gen_ai.usage.input_tokens": usage.get("input"),
                            "gen_ai.usage.output_tokens": usage.get("output")})
            if self._completion_start is not None:
                start_ns = getattr(self._raw, "start_time", None)
                if start_ns:
                    self.set(**{"kronk.ttft_ms": round(self._completion_start * 1e3 - start_ns / 1e6, 1)})
                out_tokens = (usage or {}).get("output") or 0
                gen_s = time.time() - self._completion_start
                if out_tokens and gen_s > 0:
                    self.set(**{"kronk.tokens_per_sec": round(out_tokens / gen_s, 1)})
            if output is not None:
                if isinstance(output, dict) and "tool_calls" in output:
                    self.set(**{"kronk.tool_calls": list(output.get("tool_calls") or []),
                                "kronk.output_chars": len(output.get("content") or "")})
                elif isinstance(output, str):
                    self.set(**{"kronk.output_chars": len(output)})
                if RECORD_TEXT:
                    self._raw.set_attribute("kronk.output", _text_preview(output))
            if level == "ERROR":
                self.set(**{"kronk.error": True})
                self._raw.set_status(_trace.Status(_trace.StatusCode.ERROR, status_message))
            elif status_message:
                self.set(**{"kronk.status_message": status_message[:200]})
            self._raw.end()
        except Exception as e:
            logger.debug("telemetry: end failed: %s", e)
        finally:
            if self._token is not None:
                try:
                    _otel_context.detach(self._token)
                except Exception as e:
                    logger.debug("telemetry: detach failed: %s", e)
                self._token = None

    def update_trace(self, input=None, output=None, metadata: dict | None = None,
                     **_ignored):
        """Trace-level fields live on the root span: route etc. as
        attributes, input/output previews only with TELEMETRY_RECORD_TEXT."""
        try:
            if metadata:
                self.set(**{f"kronk.{k}": v for k, v in metadata.items()})
            if RECORD_TEXT:
                if input is not None:
                    self._raw.set_attribute("kronk.input", _text_preview(input))
                if output is not None:
                    self._raw.set_attribute("kronk.output", _text_preview(output))
        except Exception as e:
            logger.debug("telemetry: update_trace failed: %s", e)


_TRANSPORT_BY_ROUTE = {
    "/message": "webui",
    "/v1/chat/completions": "openai",
    "/api/chat": "ollama",
    "/voice/api/chat": "voice",
}


# ── pipeline root management ────────────────────────────────────────────────
# A ContextVar (not a module global): asyncio copies the context per task,
# so concurrent requests cannot see each other's root even without _llm_lock.
_current_root: ContextVar[Any] = ContextVar("kronk_trace_root", default=_NOOP)


def start_pipeline(name: str, input_text: str, rid: str = "-",
                   transport: str | None = None, origin=None):
    """Open the root span for one pipeline run and make it current."""
    if not ENABLED or _tracer is None:
        _current_root.set(_NOOP)
        return _NOOP
    try:
        # The server span (auto-instrumented) knows the route; the three shim
        # routes all reach here as transport="shim", so the route names the
        # client instead: webui | openai | ollama | voice.
        http_route = None
        try:
            http_route = (getattr(_trace.get_current_span(), "attributes", None) or {}).get("http.route")
        except Exception:
            pass
        span = _tracer.start_span(name)   # child of the server span, if any
        obs = _Obs(span)
        obs.set(**{
            "kronk.rid": rid,
            "kronk.transport": _TRANSPORT_BY_ROUTE.get(http_route, transport),
            "kronk.http_route": http_route,
            "kronk.input_chars": len(input_text or ""),
            "kronk.origin.device_id": getattr(origin, "device_id", None),
            "kronk.origin.area": getattr(origin, "area", None),
        })
        if RECORD_TEXT:
            span.set_attribute("kronk.input", _text_preview(input_text))
        obs._token = _otel_context.attach(_trace.set_span_in_context(span))
        _current_root.set(obs)
        return obs
    except Exception as e:
        logger.debug("telemetry: start_pipeline failed: %s", e)
        _current_root.set(_NOOP)
        return _NOOP


def root():
    """The current pipeline's root observation (no-op outside a pipeline)."""
    return _current_root.get()


def current():
    """The current span (a tool span made current via .current(), else the
    pipeline root) as an observation to hang children on."""
    if not ENABLED:
        return _NOOP
    try:
        span = _trace.get_current_span()
        if span.get_span_context().is_valid:
            return _Obs(span)
    except Exception:
        pass
    return root()


def end_pipeline(obs, output=None, route: str | None = None,
                 level: str | None = None, status_message: str | None = None):
    """Close the root span, stamp the route, release the context."""
    try:
        obs.end(output=output, level=level, status_message=status_message,
                metadata={"route": route} if route else None)
    finally:
        _current_root.set(_NOOP)


@contextmanager
def span(name: str, **attrs):
    """A child span of the current pipeline root (or a standalone span)
    for code that is not an agent round or a tool call."""
    obs = root().child_span(name, metadata=attrs) if ENABLED else _NOOP
    try:
        with obs.current():
            yield obs
    except Exception as e:
        obs.end(level="ERROR", status_message=str(e)[:200])
        raise
    else:
        obs.end()


# ── test support ────────────────────────────────────────────────────────────
def enable_for_tests(exporter) -> None:
    """Turn tracing on in-process with a synchronous exporter (tests use
    the SDK's InMemorySpanExporter). Idempotent per process."""
    global ENABLED
    _init_sdk(exporter=exporter, batch=False)
    ENABLED = True


def flush(timeout_ms: int = 5000) -> None:
    if _provider is not None:
        try:
            _provider.force_flush(timeout_ms)
        except Exception:
            pass
