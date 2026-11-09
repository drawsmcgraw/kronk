"""OpenTelemetry helpers for tool_service — Telemetry v2 (docs/plans/TELEMETRY_V2_PLAN.md).

The only module here that imports opentelemetry. Same rules as the
orchestrator's telemetry.py: TELEMETRY_ENABLED != "true" (or a failed
init) makes everything a no-op; export is batched off the request path;
no request/response text on spans.

What it adds over the auto-instrumentation (FastAPI server spans joined to
the orchestrator's tool span through the incoming `traceparent`; httpx
client spans for HA REST, NWS, SearXNG, fetched pages):

    with telemetry.span("ma.command", command=cmd) as s:
        ...                      # websocket calls, resolver, verify wait
        s.set(outcome="started")

A span() opened inside a request nests under the current span; opened
outside one (background polls) it is a root, and roots that are not
server spans are dropped by the sampler, so pollers stay out of Tempo.
"""
import json
import logging
import os
from contextlib import contextmanager, nullcontext

logger = logging.getLogger(__name__)

ENABLED = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
_tracer = None
_trace = None
_provider = None


def _init_sdk(exporter=None, batch: bool = True) -> None:
    global _tracer, _trace, _provider
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult

    class _ServerRootsOnly(Sampler):
        """A trace starts at an incoming request, never at a background
        poll's outbound call or helper span."""

        def should_sample(self, parent_context, trace_id, name, kind=None,
                          attributes=None, links=None, trace_state=None):
            if kind == trace.SpanKind.SERVER:
                return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state=trace_state)
            return SamplingResult(Decision.DROP, trace_state=trace_state)

        def get_description(self) -> str:
            return "ServerRootsOnly"

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "kronk-tool-service")}),
        sampler=ParentBased(root=_ServerRootsOnly()),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter))
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _provider = provider
    _trace = trace
    _tracer = provider.get_tracer("kronk.tool_service")


if ENABLED:
    try:
        _init_sdk()
        logger.info("telemetry: OpenTelemetry tracing enabled → %s",
                    os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "(default endpoint)"))
    except Exception as e:
        logger.error("telemetry: disabled — OpenTelemetry init failed: %s", e)
        ENABLED = False


def instrument_app(app) -> None:
    if not ENABLED:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=_provider,
            excluded_urls=os.getenv("TELEMETRY_EXCLUDED_URLS", r"https?://[^/]+/health$"),
            exclude_spans=["receive", "send"],
        )
        HTTPXClientInstrumentor().instrument(tracer_provider=_provider)
    except Exception as e:
        logger.error("telemetry: auto-instrumentation failed: %s", e)


def _attr(v):
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, (list, tuple)):
        return [x if isinstance(x, str) else json.dumps(x, default=str) for x in v]
    return json.dumps(v, default=str)[:500]


class _Span:
    def __init__(self, raw):
        self._raw = raw

    def set(self, **attrs) -> None:
        try:
            for k, v in attrs.items():
                v = _attr(v)
                if v is not None:
                    self._raw.set_attribute(k, v)
        except Exception as e:
            logger.debug("telemetry: set failed: %s", e)


class _Noop:
    def set(self, **attrs) -> None:
        pass


_NOOP = _Noop()


@contextmanager
def span(name: str, **attrs):
    """Open a child span of the current one for the enclosed block. An
    exception marks it ERROR (and propagates); attributes may be added
    through the yielded object's .set()."""
    if not ENABLED or _tracer is None:
        yield _NOOP
        return
    try:
        cm = _tracer.start_as_current_span(name, record_exception=False)
        raw = cm.__enter__()
    except Exception as e:
        logger.debug("telemetry: span(%s) failed: %s", name, e)
        yield _NOOP
        return
    s = _Span(raw)
    s.set(**attrs)
    try:
        yield s
    except Exception as e:
        try:
            raw.set_attribute("kronk.error", True)
            raw.set_status(_trace.Status(_trace.StatusCode.ERROR, f"{type(e).__name__}: {e}"[:200]))
        except Exception:
            pass
        cm.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)


def current_trace_id() -> str | None:
    if not ENABLED:
        return None
    try:
        ctx = _trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:
        return None


def enable_for_tests(exporter) -> None:
    global ENABLED
    _init_sdk(exporter=exporter, batch=False)
    ENABLED = True
