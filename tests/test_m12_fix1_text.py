"""Sprint M12 fix 1 (docs/sprint/M12-e2e.md, blocks 1-5): the text switch
through the real instrumentation, not the hook called by hand.

- F1: a request sent through an instrumented httpx.AsyncClient (the only
  client either service uses) exports a client span whose URL has lost its
  query string with TELEMETRY_RECORD_TEXT off and keeps it whole with it
  on. The instrumentation is installed by each service's own
  instrument_app(), so removing the async hook there fails these tests.
- F3: the words in DELETE /shopping_list/{item} leave neither the client
  span, nor tool_service's server span, nor the httpx log line, nor the
  access log line, with the switch off.

No sockets: the client's transport is httpx's own AsyncHTTPTransport (the
class the instrumentation wraps) over a fake connection pool, and the
server is reached through httpx.ASGITransport.
"""
import importlib.util
import logging
import os

import httpcore
import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import telemetry as orch_telemetry   # orchestrator/telemetry.py

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_ts_telemetry():
    spec = importlib.util.spec_from_file_location("ts_telemetry_m12_fix1",
                                                  os.path.join(ROOT, "tool_service", "telemetry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TS = _load_ts_telemetry()
MODS = [orch_telemetry, TS]
IDS = ["orchestrator", "tool_service"]


class _FakePool:
    """Stands in for httpcore's connection pool: answers every request with
    200 and opens nothing."""

    async def handle_async_request(self, request):
        return httpcore.Response(200, headers=[(b"content-type", b"application/json")], content=b"{}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def aclose(self):
        pass


class _Wire(httpx.AsyncHTTPTransport):
    """httpx's real async transport class (the one the instrumentation
    patches), over the fake pool."""

    def __init__(self):
        super().__init__()
        self._pool = _FakePool()


def _app(outbound: list[tuple[str, str]]):
    app = FastAPI()

    @app.post("/relay")
    async def relay():
        async with httpx.AsyncClient(transport=_Wire()) as c:
            for method, url in outbound:
                await c.request(method, url)
        return {"ok": True}

    @app.delete("/shopping_list/{item}")
    async def remove(item: str):
        return {"removed": item}

    return app


@pytest.fixture
def instrumented(monkeypatch):
    """Yields install(mod, outbound) → (app, exporter); undoes the global
    httpx instrumentation afterwards (one process holds both modules)."""
    inst = HTTPXClientInstrumentor()
    if inst.is_instrumented_by_opentelemetry:
        inst.uninstrument()
    saved = []

    def install(mod, outbound):
        saved.append((mod, mod.ENABLED))
        exp = InMemorySpanExporter()
        mod.enable_for_tests(exp)
        app = _app(outbound)
        mod.instrument_app(app)
        assert inst.is_instrumented_by_opentelemetry
        return app, exp

    yield install
    if inst.is_instrumented_by_opentelemetry:
        inst.uninstrument()
    for mod, was in saved:
        mod.ENABLED = was


OUTBOUND = [("GET", "http://searxng:8080/search?q=zqxw%20florbent&format=json"),
            ("DELETE", "http://tool_service:8003/shopping_list/zqxw%20florbent"),
            ("DELETE", "http://tool_service:8003/shopping_list/clear")]


async def _send(app, method, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ts:8003") as c:
        r = await c.request(method, path)
        assert r.status_code == 200


def _client_urls(exp):
    from opentelemetry.trace import SpanKind
    out = []
    for s in exp.get_finished_spans():
        if s.kind == SpanKind.CLIENT:
            out.append(s.attributes.get("http.url") or s.attributes.get("url.full"))
    return out


def _span_blob(exp):
    return "\n".join(f"{s.name} {dict(s.attributes or {})}" for s in exp.get_finished_spans())


@pytest.mark.parametrize("mod", MODS, ids=IDS)
@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_async_client_span_through_the_instrumentation(mod, record, instrumented, monkeypatch, caplog):
    import asyncio
    app, exp = instrumented(mod, OUTBOUND)
    # every loaded copy of a telemetry module filters the one process-wide
    # httpx logger (in a service process there is one); setitem only, since
    # pytest undoes setattr and setitem in separate passes
    scopes = {id(mod.__dict__): mod.__dict__}
    for f in logging.getLogger("httpx").filters:
        scopes.setdefault(id(type(f).filter.__globals__), type(f).filter.__globals__)
    for g in scopes.values():
        monkeypatch.setitem(g, "RECORD_TEXT", record)
    caplog.set_level(logging.INFO, logger="httpx")
    asyncio.run(_send(app, "POST", "/relay"))
    urls = _client_urls(exp)
    assert len(urls) == 3, _span_blob(exp)
    httpx_lines = [r.getMessage() for r in caplog.records
                   if r.name == "httpx" and "/relay" not in r.getMessage()]   # not the test's own call
    assert len(httpx_lines) == 3
    if record:
        assert urls == [u for _, u in OUTBOUND]
        assert all("zqxw" in line for line in httpx_lines[:2])
    else:
        assert urls == ["http://searxng:8080/search?<query>",
                        "http://tool_service:8003/shopping_list/<item>",
                        "http://tool_service:8003/shopping_list/clear"]
        assert "zqxw" not in _span_blob(exp)
        assert not any("zqxw" in line for line in httpx_lines), httpx_lines
        assert "/shopping_list/<item>" in httpx_lines[1] and "/shopping_list/clear" in httpx_lines[2]


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_tool_service_server_span_for_a_shopping_list_item(record, instrumented, monkeypatch):
    import asyncio
    from opentelemetry.trace import SpanKind
    app, exp = instrumented(TS, [])
    monkeypatch.setattr(TS, "RECORD_TEXT", record)
    asyncio.run(_send(app, "DELETE", "/shopping_list/zqxw%20florbent?x=zqxwq"))
    server = [s for s in exp.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert server, _span_blob(exp)
    attrs = dict(server[0].attributes)
    target = attrs.get("http.target") or attrs.get("url.path")
    if record:
        assert "zqxw" in target
    else:
        assert "zqxw" not in _span_blob(exp)
        assert target.startswith("/shopping_list/<item>")
        assert server[0].name == "DELETE /shopping_list/{item}"


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_access_line_with_a_shopping_list_item(mod, monkeypatch):
    f = mod._NoQueryText()
    monkeypatch.setattr(mod, "RECORD_TEXT", False)
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("172.18.0.5:5000", "DELETE", "/shopping_list/zqxw%20florbent", "1.1", 200), None)
    f.filter(rec)
    assert rec.getMessage() == '172.18.0.5:5000 - "DELETE /shopping_list/<item> HTTP/1.1" 200'
    clear = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                              ("172.18.0.5:5000", "DELETE", "/shopping_list/clear", "1.1", 200), None)
    f.filter(clear)
    assert "/shopping_list/clear " in clear.getMessage()
    monkeypatch.setattr(mod, "RECORD_TEXT", True)
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", ("/shopping_list/zqxw",), None)
    f.filter(rec)
    assert rec.getMessage() == "/shopping_list/zqxw"


def test_orchestrator_access_line_with_an_upload_name(monkeypatch):
    f = orch_telemetry._NoQueryText()
    monkeypatch.setattr(orch_telemetry, "RECORD_TEXT", False)
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("172.18.0.9:4000", "DELETE", "/files/zqxw%20notes.txt", "1.1", 200), None)
    f.filter(rec)
    assert rec.getMessage() == '172.18.0.9:4000 - "DELETE /files/<name> HTTP/1.1" 200'
    # GET /files (the listing) and the static paths carry no name
    for p in ("/files", "/static/app.js", "/api/status"):
        assert orch_telemetry.strip_url_text(p) == p
