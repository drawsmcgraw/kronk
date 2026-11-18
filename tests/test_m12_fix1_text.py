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


# ── F2: the genre request's terms ───────────────────────────────────────────

def _ts():
    import tool_service.main as ts
    return ts


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_genre_request_log_line_terms(record, monkeypatch, caplog):
    import asyncio
    import types
    ts = _ts()
    monkeypatch.setattr(ts.telemetry, "RECORD_TEXT", record)
    monkeypatch.setattr(ts.taste.TASTE, "load", lambda create=True: {})
    monkeypatch.setattr(ts.taste, "genre_phrase", lambda text: "snargleblatx jazz")
    monkeypatch.setattr(ts, "_taste_con", lambda readonly=False: None)
    monkeypatch.setattr(ts.taste, "provider_genre_picks", lambda *a: [])

    async def no_search(*a, **kw):
        return []
    monkeypatch.setattr(ts, "_ma_search", no_search)
    caplog.set_level(logging.INFO, logger="tool_service")
    nq = types.SimpleNamespace(text="play some snargleblatx jazz")
    assert asyncio.run(ts._genre_plan_inner(nq, True, None, readonly=True)) is None
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("genre request"))
    if record:
        assert "snargleblatx" in line
    else:
        assert "snargleblatx" not in line
        assert line == "genre request None -> terms None rule None: 0 picks, first []"


# ── F4: music.failure and the three log lines that carry HA's message ──────

RESOLVE = "Could not resolve zqxwspoken name to playable media item"
NO_TRACKS = "Playback failed for zqxwspoken Radio - no more tracks available"


@pytest.fixture
def ts_traced():
    from tool_service import telemetry as ts_tel
    exp = InMemorySpanExporter()
    was = ts_tel.ENABLED
    ts_tel.enable_for_tests(exp)
    yield exp
    ts_tel.ENABLED = was


def _under_server_span(coro_fn):
    import asyncio
    from opentelemetry.trace import SpanKind
    from tool_service import telemetry as ts_tel

    async def go():
        with ts_tel._tracer.start_as_current_span("POST /music", kind=SpanKind.SERVER):
            return await coro_fn()
    return asyncio.run(go())


def _fake_player(monkeypatch, ts, fail: str, starts: bool):
    state = {"n": 0}

    async def get_state(client, headers, entity):
        state["n"] += 1
        if starts and state["n"] > 1:
            return {"state": "playing", "attributes": {"media_title": "new", "media_content_id": "new"}}
        return {"state": "idle", "attributes": {}}

    async def call(domain, service, service_data=None, target=None):
        raise ts.HAServiceError(fail)

    async def no_sleep(_):
        return None
    monkeypatch.setattr(ts, "_get_state", get_state)
    monkeypatch.setattr(ts, "ha_call_service", call)
    monkeypatch.setattr(ts.asyncio, "sleep", no_sleep)


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_play_verified_failure_kind_without_the_name(record, ts_traced, monkeypatch):
    ts = _ts()
    monkeypatch.setattr(ts.telemetry, "RECORD_TEXT", record)
    _fake_player(monkeypatch, ts, RESOLVE, starts=False)
    started, failure = _under_server_span(lambda: ts._play_verified(
        None, {}, ["media_player.kitchen"], {"media_id": "zqxwspoken name", "media_type": "radio"}))
    assert started is None and failure == RESOLVE          # the spoken reply is unchanged
    sp = next(s for s in ts_traced.get_finished_spans() if s.name == "music.play_verified")
    a = dict(sp.attributes)
    assert a["music.outcome"] == "resolve_miss" and a["music.failure_kind"] == "resolve_miss"
    if record:
        assert a["music.failure"] == RESOLVE
    else:
        assert "music.failure" not in a
        assert "zqxw" not in json_dump(a)


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_failed_but_started_log_line(record, monkeypatch, caplog):
    import asyncio
    ts = _ts()
    monkeypatch.setattr(ts.telemetry, "RECORD_TEXT", record)
    _fake_player(monkeypatch, ts, NO_TRACKS, starts=True)
    caplog.set_level(logging.INFO, logger="tool_service")
    started, failure = asyncio.run(ts._play_verified_inner(None, {}, ["media_player.kitchen"], {"media_id": "x"}))
    assert started is not None and failure == NO_TRACKS
    line = next(r.getMessage() for r in caplog.records if "reported failure but" in r.getMessage())
    assert ": ha_error: " in line
    assert ("zqxw" in line) is record


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_genre_pick_did_not_start_log_line(record, monkeypatch, caplog):
    import asyncio
    ts = _ts()
    monkeypatch.setattr(ts.telemetry, "RECORD_TEXT", record)

    async def failed(client, headers, entities, data):
        return None, RESOLVE
    monkeypatch.setattr(ts, "_play_verified", failed)
    pick = ts.taste.Pick("playlist", "library://playlist/1", "Catalog Label", "k1", 1.0, "playlist")
    plan = {"phrase": "zqxw jazz", "picks": [pick], "start": 0}
    caplog.set_level(logging.INFO, logger="tool_service")
    try:
        asyncio.run(ts._play_genre(None, {}, ["media_player.kitchen"], "Kitchen", None, plan, dry_run=False))
    except Exception:
        pass                                    # every pick failed: the caller's error path
    line = next(r.getMessage() for r in caplog.records if "did not start" in r.getMessage())
    assert ": resolve_miss: " in line
    assert ("zqxw" in line) is record


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_ha_call_service_failure_log_line(record, monkeypatch, caplog):
    import asyncio
    from unittest.mock import patch
    from tests.test_error_surfacing import _FakeWS, _install_fake_websockets
    ts = _ts()
    monkeypatch.setattr(ts.telemetry, "RECORD_TEXT", record)
    ws = _FakeWS({"success": False, "error": {"code": "home_assistant_error", "message": RESOLVE}})
    caplog.set_level(logging.INFO, logger="tool_service")
    with _install_fake_websockets(ws), patch.object(ts, "HA_TOKEN", "tok"):
        with pytest.raises(ts.HAServiceError, match="zqxwspoken"):   # the spoken sentence keeps it
            asyncio.run(ts.ha_call_service("music_assistant", "play_media", {"media_id": "zqxwspoken name"}))
    line = next(r.getMessage() for r in caplog.records if "play_media failed" in r.getMessage())
    assert "code=home_assistant_error kind=resolve_miss" in line
    assert ("zqxw" in line) is record


def test_failure_kind_vocabulary():
    ts = _ts()
    assert ts.failure_kind(RESOLVE) == "resolve_miss"
    assert ts.failure_kind("Home Assistant is unreachable (OSError).") == "ha_unreachable"
    assert ts.failure_kind("Home Assistant websocket error (ValueError).") == "ha_websocket"
    assert ts.failure_kind("Home Assistant rejected the token.") == "ha_auth"
    assert ts.failure_kind(NO_TRACKS) == "ha_error"
    assert ts.failure_kind(None) is None and ts.failure_kind("") is None


def json_dump(d):
    import json
    return json.dumps(d, default=str)


# ── F5: the model's broken tool-call arguments ──────────────────────────────

BAD_ARGS = '{"query": "zqxwbadargs unterminated'


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_llm_complete_bad_arguments_warning(record, monkeypatch, caplog):
    import asyncio
    import json
    import llm
    monkeypatch.setattr(orch_telemetry, "RECORD_TEXT", record)
    body = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": BAD_ARGS}}]}}]}
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))))
    caplog.set_level(logging.WARNING, logger="llm")
    out = asyncio.run(llm.complete([{"role": "user", "content": "x"}], [], "m"))
    assert out["tool_calls"][0]["function"]["arguments"] == {}
    line = next(r.getMessage() for r in caplog.records if "not valid JSON" in r.getMessage())
    assert f"Fragment ({len(BAD_ARGS)} chars)" in line
    assert ("zqxwbadargs" in line) is record


@pytest.mark.parametrize("record", [False, True], ids=["off", "on"])
def test_llm_stream_bad_arguments_warning(record, monkeypatch, caplog):
    import asyncio
    import json
    import llm
    monkeypatch.setattr(orch_telemetry, "RECORD_TEXT", record)
    chunk = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": BAD_ARGS}}]}}]}
    sse = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse))))
    caplog.set_level(logging.WARNING, logger="llm")

    async def go():
        return [c async for c in llm._stream_body([{"role": "user", "content": "x"}], "m")]
    chunks = asyncio.run(go())
    assert any("tool_calls" in c for c in chunks)
    line = next(r.getMessage() for r in caplog.records if "not valid JSON" in r.getMessage())
    assert f"Fragment ({len(BAD_ARGS)} chars)" in line
    assert ("zqxwbadargs" in line) is record


# ── M12 fix 2 (M12-e2e2 block 1): the whole item, whatever characters it holds ──

_ITEMS = ["pindlewort vashti cream", "pindlewort%20vashti%20cream", "clear dorvalex wrap",
          "clear%20dorvalex%20wrap", "krelbot's snarfweed", "krelbot's%20snarfweed",
          'qvarg"qvarginner" qvargtail', "qvarg#qvarghashb", "clearance rack", "x?y=zqxw",
          "clear/zqxw", "line\nbreak zqxw"]


@pytest.mark.parametrize("t", MODS, ids=["orchestrator", "tool_service"])
@pytest.mark.parametrize("item", _ITEMS)
def test_the_whole_shopping_list_item_is_masked(t, item):
    for prefix in ("/shopping_list/", "http://tool_service:8003/shopping_list/"):
        assert t.strip_url_text(prefix + item) == prefix + "<item>"
        assert t.mask_path(prefix + item) == prefix + "<item>"


@pytest.mark.parametrize("t", MODS, ids=["orchestrator", "tool_service"])
@pytest.mark.parametrize("fixed", ["/shopping_list", "/shopping_list/clear",
                                   "http://tool_service:8003/shopping_list/clear"])
def test_fixed_shopping_list_paths_are_left_alone(t, fixed):
    assert t.strip_url_text(fixed) == fixed
    assert t.strip_url_text(fixed + "?confirm=zqxw") == fixed + "?<query>"


def test_an_upload_name_with_spaces_and_quotes_is_masked():
    assert orch_telemetry.strip_url_text("/files/tax return 'final'.pdf") == "/files/<name>"


def test_tool_service_server_span_for_a_multi_word_item(instrumented, monkeypatch):
    """The server span sees the decoded path: spaces, an apostrophe, a first
    word "clear" (M12-e2e2 block 1, the tester's live inputs)."""
    import asyncio
    app, exp = instrumented(TS, [])
    monkeypatch.setattr(TS, "RECORD_TEXT", False)
    asyncio.run(_send(app, "DELETE", "/shopping_list/krelbot's%20snarfweed%20vashti"))
    asyncio.run(_send(app, "DELETE", "/shopping_list/clear%20dorvalex%20wrap"))
    blob = _span_blob(exp)
    for word in ("krelbot", "snarfweed", "vashti", "dorvalex", "wrap"):
        assert word not in blob, blob
