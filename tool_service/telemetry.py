"""OpenTelemetry helpers for tool_service — Telemetry v2 (docs/plans/TELEMETRY_V2_PLAN.md).

The only module here that imports opentelemetry. Same rules as the
orchestrator's telemetry.py: TELEMETRY_ENABLED != "true" (or a failed
init) makes everything a no-op; export is batched off the request path;
no request/response text on spans, and with TELEMETRY_RECORD_TEXT off no
spoken or model-written text in span attributes or log lines (sprint M12:
text(), the URL query-string hooks and log filter below).

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
import re
from contextlib import contextmanager, nullcontext
from typing import Any

logger = logging.getLogger(__name__)

ENABLED = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
RECORD_TEXT = os.getenv("TELEMETRY_RECORD_TEXT", "false").lower() == "true"
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


# ── The text switch beyond span previews (sprint M12) ──────────────────────
# With TELEMETRY_RECORD_TEXT off, no span attribute and no log line carries
# words a person said or a model wrote. Three places did regardless:
# event-log previews (text(), below, at each call site), URL query strings
# on the auto-instrumented server and client spans (a web search's q=, a
# fetched URL), and the same query strings in the httpx and uvicorn access
# log lines. With the switch on, all three are kept as before.

_QUERY_RE = re.compile(r"(?<=[\w/])\?[\w%.~+-]+=[^\s\"']*")
_URL_ATTRS = ("http.target", "http.url", "url.full", "url.path", "url.query")
# Words in a URL path (M12 fix 1, block 3): the one route here that carries
# them, by rule. DELETE /shopping_list/{item} (the orchestrator's
# shopping_list_remove tool). Every other route has a fixed path
# (docs/sprint/M12-fix1.md, F3). /shopping_list/clear is a fixed word and
# stays. The orchestrator's telemetry.py has the same rule.
_PATH_TEXT = (
    (re.compile(r"(?<=/shopping_list/)(?!clear(?=[?#\s\"']|$))[^?#\s\"']+"), "<item>"),
)


def text(value: Any, limit: int) -> str | None:
    """A text field for a log line or event: the first `limit` characters
    with TELEMETRY_RECORD_TEXT on, else None."""
    if not RECORD_TEXT or value is None:
        return None
    return str(value)[:limit]


def strip_query(s: str) -> str:
    """A URL or log message with each query string replaced by ?<query>."""
    return _QUERY_RE.sub("?<query>", s)


def mask_path(s: str) -> str:
    """A URL, path or log message with the words of a text-carrying path
    segment (_PATH_TEXT) replaced by a placeholder."""
    for rx, placeholder in _PATH_TEXT:
        s = rx.sub(placeholder, s)
    return s


def strip_url_text(s: str) -> str:
    """mask_path and strip_query together: what the switch-off log filter
    and span hooks apply."""
    return strip_query(mask_path(s))


def _strip_url_attrs(span, *_a) -> None:
    """Request hook for the FastAPI and httpx instrumentations: drop the
    query string, and the words of a text-carrying path segment, from the
    URL attributes unless text recording is on."""
    if RECORD_TEXT:
        return
    try:
        attrs = getattr(span, "attributes", None) or {}
        for k in _URL_ATTRS:
            v = attrs.get(k)
            if isinstance(v, str) and v:
                if k == "url.query":
                    new = "<query>"
                else:
                    base, q, _ = v.partition("?")
                    new = mask_path(base) + ("?<query>" if q else "")
                if new != v:
                    span.set_attribute(k, new)
    except Exception as e:
        logger.debug("telemetry: url strip failed: %s", e)


async def _strip_url_attrs_async(span, request) -> None:
    """The same hook for httpx.AsyncClient (every outgoing call here)."""
    _strip_url_attrs(span, request)


class _NoQueryText(logging.Filter):
    """Rewrites query strings and text-carrying path segments out of a log
    record's arguments (httpx's "HTTP Request: GET <url>", uvicorn's access
    line) unless text recording is on. Arguments, not the message:
    uvicorn's formatter unpacks them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if RECORD_TEXT or not isinstance(record.args, tuple):
            return True
        record.args = tuple(_clean_arg(a) for a in record.args)
        return True


def _clean_arg(a):
    if a is None or isinstance(a, (int, float)):
        return a
    s = str(a)
    return strip_url_text(s) if ("?" in s or "/" in s) else a


def install_log_filters() -> None:
    """Attach the query-string filter to the httpx and uvicorn access
    loggers. Idempotent. Runs at import, whether or not tracing is on."""
    for name in ("httpx", "uvicorn.access"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, _NoQueryText) for f in lg.filters):
            lg.addFilter(_NoQueryText())


install_log_filters()


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
            server_request_hook=_strip_url_attrs,
        )
        # AsyncClient takes its hook as async_request_hook, a coroutine
        # function; request_hook is for the sync Client only (M12-e2e block 1).
        HTTPXClientInstrumentor().instrument(tracer_provider=_provider, request_hook=_strip_url_attrs,
                                             async_request_hook=_strip_url_attrs_async)
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
