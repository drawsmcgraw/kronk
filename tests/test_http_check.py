"""GET /http_check and the devops agent's http_check tool (sprint M5).

Criteria B10-B15 in docs/sprint/M5-criteria.md, as changed by Drew's
decision of 2026-11-16 (docs/sprint/M5-change1.md): no host allow-list;
http_check may GET any http/https address the host can reach.

No test can touch the network: every request goes to the FakeClient, and
the no_network fixture (tests/conftest.py) makes any real lookup or
connection fail the test. Placeholder hosts are .test names (RFC 6761),
documentation addresses (RFC 5737, RFC 3849) or loopback.
"""
import asyncio
import json
import os
import socket
import ssl
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import tool_service.main as ts
import tool_service.netguard as ng
import tool_service.ops as ops
from tests.conftest import NetworkBlocked
from tests.test_fetch_address_block import FakeClient, _key

pytestmark = pytest.mark.usefixtures("no_network")

NAS = "nas.test"
DEV = "192.0.2.10"           # TEST-NET-1
DEV6 = "2001:db8::10"        # IPv6 documentation prefix


class TrackedBody(httpx.AsyncByteStream):
    """A response body that records whether anything read it."""

    def __init__(self, data=b"SECRET-BODY-TEXT"):
        self.data, self.read = data, False

    async def __aiter__(self):
        self.read = True
        yield self.data


def _resp(status=200, headers=None, body=None):
    return httpx.Response(status, headers=headers or {}, stream=body or TrackedBody())


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Audit log in tmp; returns a reader for it."""
    audit_file = tmp_path / "ops_audit.log"
    monkeypatch.setattr(ops, "OPS_AUDIT_LOG", audit_file)

    class Env:
        def audit(self):
            if not audit_file.exists():
                return []
            return [json.loads(l) for l in audit_file.read_text().splitlines()]

    return Env()


def _check(url, routes=None):
    client = FakeClient(routes)
    with patch.object(ts.httpx, "AsyncClient", client):
        resp = TestClient(ts.app).get("/http_check", params={"url": url})
    assert resp.status_code == 200
    return resp.json(), client


def _chained(outer, *inner):
    """outer raised from inner[0], which was raised from inner[1], …"""
    exc = None
    for e in reversed(inner):
        e.__cause__ = exc
        exc = e
    outer.__cause__ = exc
    return outer


# ── any host, no allow-list (M5 change 1) ───────────────────────────────────

@pytest.mark.parametrize("url", [
    f"http://{DEV}/",
    f"http://{DEV}:5000/status",
    f"https://{NAS}/ui",
    f"http://[{DEV6}]:8080/",
    "https://example.com/",
    # Not public: the /fetch address block does not apply to http_check.
    "http://127.0.0.1:9/",
    "http://[::1]:9/",
    "http://localhost:9/health",
])
def test_any_http_or_https_host_is_checked(env, url):
    out, client = _check(url, {url: _resp(204)})
    assert out["ok"] is True and out["status"] == 204
    assert client.calls == [_key(url)]
    assert env.audit()[-1]["allowed"] is True


def test_no_allow_list_is_consulted(env, monkeypatch, tmp_path):
    """The M5 allow-list is gone, not dormant: no file or variable changes
    what http_check reaches."""
    assert not hasattr(ng, "load_http_check_allowlist")
    assert not hasattr(ng, "HTTP_CHECK_HOSTS_FILE")
    empty = tmp_path / "http_check_hosts.json"
    empty.write_text('{"hosts": []}')
    monkeypatch.setenv("OPS_HTTP_CHECK_HOSTS", str(empty))
    out, _ = _check(f"http://{DEV}/", {f"http://{DEV}/": _resp(200)})
    assert out["ok"] is True


@pytest.mark.parametrize("url,why", [
    ("file:///etc/passwd", "only http and https"),
    (f"ftp://{DEV}/", "only http and https"),
    (f"http://admin:secret@{DEV}/", "credentials"),
    (f"http://admin@{NAS}/", "credentials"),
    ("not a url", "only http and https"),
])
def test_schemes_and_credentials_are_refused_before_any_connection(env, url, why):
    out, client = _check(url)
    assert out["ok"] is False and out["cause"] == "refused" and why in out["error"]
    assert client.calls == []
    assert env.audit()[-1]["allowed"] is False


# ── B10: one GET, filtered headers, never the body ──────────────────────────

def test_success_returns_only_the_four_headers_and_never_the_body(env):
    body = TrackedBody(b"SECRET-BODY-TEXT")
    out, client = _check(f"http://{NAS}/", {f"http://{NAS}/": _resp(200, {
        "Server": "nginx/1.25", "Content-Type": "text/html", "Content-Length": "16",
        "Set-Cookie": "session=abc", "X-Api-Key": "k", "WWW-Authenticate": "Basic",
    }, body)})
    assert out["headers"] == {"server": "nginx/1.25", "content-type": "text/html",
                              "content-length": "16"}
    assert set(out) == {"url", "ok", "elapsed_ms", "status", "redirects", "final_url", "headers"}
    assert out["redirects"] == [] and out["final_url"] == f"http://{NAS}/"
    assert isinstance(out["elapsed_ms"], int)
    assert body.read is False
    assert "SECRET-BODY" not in json.dumps(out)
    assert "SECRET-BODY" not in json.dumps(env.audit())


def test_one_plain_get_with_a_five_second_budget(env):
    _, client = _check(f"http://{NAS}/", {f"http://{NAS}/": _resp()})
    assert client.methods == ["GET"]
    assert client.init_kwargs == {"follow_redirects": False, "verify": True}
    # Only the timeout is passed: no headers, params, body, cookies or auth.
    assert list(client.request_kwargs[0]) == ["timeout"]
    assert 0 < client.request_kwargs[0]["timeout"] <= 5.0


# ── redirects ───────────────────────────────────────────────────────────────

def test_redirect_to_another_host_is_followed_and_reported(env):
    out, client = _check(f"http://{NAS}/", {
        f"http://{NAS}/": _resp(301, {"Location": f"https://{DEV}/ui"}),
        f"https://{DEV}/ui": _resp(200, {"Server": "lighttpd"}),
    })
    assert out["ok"] is True and out["status"] == 200
    assert out["redirects"] == [{"url": f"http://{NAS}/", "status": 301,
                                 "location": f"https://{DEV}/ui"}]
    assert out["final_url"] == f"https://{DEV}/ui"
    assert client.methods == ["GET", "GET"]
    assert "note" not in out


@pytest.mark.parametrize("start,target", [
    ("https://example.com/", "http://127.0.0.1:9/admin"),   # public to loopback
    (f"http://{DEV}/", "https://example.com/"),             # private-style to public
    (f"http://{DEV}/", f"http://[{DEV6}]/x"),
])
def test_redirects_are_followed_to_any_http_host(env, start, target):
    out, client = _check(start, {start: _resp(302, {"Location": target}),
                                 target: _resp(200)})
    assert out["ok"] is True and out["status"] == 200
    assert out["final_url"] == _key(target)
    assert client.calls == [_key(start), _key(target)]


@pytest.mark.parametrize("target,why", [
    # observation 6: a redirect target carrying credentials
    (f"http://user:pw@{NAS}/in", "URLs with credentials (user:password@) are not allowed"),
    (f"http://user@{DEV}/in", "URLs with credentials (user:password@) are not allowed"),
    # observation 4 (a): the cause is the scheme, not the host
    (f"ftp://{NAS}/file", "only http and https URLs are allowed, not 'ftp'"),
    ("file:///etc/passwd", "only http and https URLs are allowed, not 'file'"),
])
def test_unfollowable_redirect_names_its_own_cause(env, target, why):
    out, client = _check(f"http://{NAS}/", {
        f"http://{NAS}/": _resp(302, {"Location": target}),
    })
    assert out["ok"] is True and out["status"] == 302
    assert out["redirects"] == [{"url": f"http://{NAS}/", "status": 302, "location": target}]
    assert out["note"].endswith(f"not followed: {why}")
    assert "allow-list" not in out["note"]
    assert out["final_url"] == f"http://{NAS}/"
    assert client.calls == [_key(f"http://{NAS}/")]
    line = env.audit()[-1]
    assert line["exit_code"] == 302 and line["note"] == out["note"]


def test_redirect_with_an_unparseable_location_is_reported_not_followed(env):
    out, client = _check(f"http://{NAS}/", {
        f"http://{NAS}/": _resp(302, {"Location": "http://[::1"}),
    })
    assert out["ok"] is True and out["status"] == 302
    assert "not followed: invalid URL" in out["note"]
    assert out["redirects"][0]["location"] == "http://[::1"
    assert client.calls == [_key(f"http://{NAS}/")]


def test_five_redirects_are_followed(env):
    routes = {f"http://{NAS}/{i}": _resp(302, {"Location": f"/{i + 1}"}) for i in range(5)}
    routes[f"http://{NAS}/5"] = _resp(200)
    out, client = _check(f"http://{NAS}/0", routes)
    assert out["ok"] is True and out["status"] == 200
    assert len(client.calls) == 6 and len(out["redirects"]) == 5
    assert out["final_url"] == f"http://{NAS}/5"


def test_redirect_loop_stops_after_five_and_reports_the_last_url_requested(env):
    """Observation 4 (b): final_url is a URL that was requested."""
    routes = {f"http://{NAS}/{i}": _resp(302, {"Location": f"/{i + 1}"}) for i in range(7)}
    out, client = _check(f"http://{NAS}/0", routes)
    assert out["ok"] is False and out["cause"] == "too_many_redirects"
    assert len(client.calls) == 6 and len(out["redirects"]) == 6
    assert client.calls[-1] == _key(f"http://{NAS}/5")
    assert out["final_url"] == f"http://{NAS}/5"
    assert out["final_url"] in client.calls


def test_budget_spent_between_hops_reports_the_last_url_requested(env, monkeypatch):
    monkeypatch.setattr(ts, "HTTP_CHECK_TIMEOUT_S", 0.05)

    class Slow(FakeClient):
        def _respond(self, url, kw):
            r = super()._respond(url, kw)
            time.sleep(0.1)       # spends the whole budget on hop 0
            return r

    client = Slow({f"http://{NAS}/a": _resp(302, {"Location": "/b"})})
    with patch.object(ts.httpx, "AsyncClient", client):
        out = TestClient(ts.app).get("/http_check", params={"url": f"http://{NAS}/a"}).json()
    assert out["ok"] is False and out["cause"] == "timeout"
    assert client.calls == [_key(f"http://{NAS}/a")]
    assert out["final_url"] == f"http://{NAS}/a"
    assert f"http://{NAS}/b not requested" in out["error"]


# ── B11: failure causes ──────────────────────────────────────────────────────

FAILURES = [
    ("dns", httpx.ConnectError("[Errno -2] Name or service not known"), "DNS failure"),
    ("dns", _chained(httpx.ConnectError("x"),
                     socket.gaierror(-2, "Name or service not known")), "DNS failure"),
    ("connection_refused", _chained(httpx.ConnectError("x"),
                                    ConnectionRefusedError(111, "Connection refused")),
     "connection refused"),
    # anyio: OSError("All connection attempts failed") from an ExceptionGroup
    ("connection_refused", _chained(
        httpx.ConnectError("All connection attempts failed"),
        OSError("All connection attempts failed"),
        ExceptionGroup("", [ConnectionRefusedError(111, "Connection refused")])),
     "connection refused"),
    ("timeout", httpx.ConnectTimeout("timed out"), "timed out after 5s"),
    ("timeout", httpx.ReadTimeout("timed out"), "timed out after 5s"),
    ("tls", _chained(httpx.ConnectError("x"),
                     ssl.SSLCertVerificationError(1, "certificate verify failed")), "TLS error"),
    ("tls", httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
     "TLS error"),
    ("network", httpx.ConnectError("[Errno 113] No route to host"), "network error"),
]


@pytest.mark.parametrize("cause,exc,words", FAILURES)
def test_failure_names_the_cause(env, cause, exc, words):
    out, _ = _check(f"http://{NAS}/", {f"http://{NAS}/": exc})
    assert out["ok"] is False
    assert out["cause"] == cause
    assert words in out["error"]
    assert out["final_url"] == f"http://{NAS}/"
    line = env.audit()[-1]
    assert line["allowed"] is True and line["note"].startswith(cause)


# ── B14: the audit log ──────────────────────────────────────────────────────

def test_every_call_writes_one_audit_line_in_the_ops_exec_form(env):
    _check("file:///etc/passwd")                                           # scheme
    _check(f"http://u:p@{DEV}/")                                           # credentials
    _check(f"http://{NAS}/", {f"http://{NAS}/": _resp(200)})               # success
    _check(f"http://{DEV}/x", {f"http://{DEV}/x": httpx.ConnectTimeout("t")})
    _check(f"http://{NAS}/r", {f"http://{NAS}/r": _resp(302, {"Location": f"ftp://{NAS}/"})})
    lines = env.audit()
    assert len(lines) == 5
    for l in lines:
        # The same keys ops.audit_exec writes for /ops/exec.
        assert set(l) == {"ts", "host", "command", "allowed", "exit_code", "out_len", "note"}
        assert l["command"].startswith("http_check GET ") and l["out_len"] == 0
    assert [l["allowed"] for l in lines] == [False, False, True, True, True]
    assert [l["host"] for l in lines] == ["?", "?", NAS, DEV, NAS]
    assert lines[2]["exit_code"] == 200 and lines[2]["note"] == "HTTP 200"
    assert lines[3]["exit_code"] is None and lines[3]["note"].startswith("timeout")
    assert lines[4]["exit_code"] == 302 and "not followed: only http and https" in lines[4]["note"]


# ── the orchestrator tool ───────────────────────────────────────────────────

os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")


def _tool(result: dict):
    import tools

    class C:
        def __init__(self):
            self.calls = []

        async def get(self, url, params=None):
            self.calls.append((url, params))
            return httpx.Response(200, json=result, request=httpx.Request("GET", url))

    c = C()
    return asyncio.run(tools._tool_http_check(c, {"url": f"http://{NAS}/"})), c


def test_tool_formats_success_with_redirects_and_headers():
    text, c = _tool({"url": f"http://{NAS}/", "ok": True, "status": 200, "elapsed_ms": 12,
                     "redirects": [{"url": f"http://{NAS}/", "status": 301,
                                    "location": f"https://{NAS}/"}],
                     "final_url": f"https://{NAS}/", "headers": {"server": "nginx"}})
    assert c.calls[0][0].endswith("/http_check")
    assert c.calls[0][1] == {"url": f"http://{NAS}/"}
    assert text.startswith(f"[http_check http://{NAS}/: HTTP 200 in 12 ms; final URL https://{NAS}/; headers: server: nginx]")
    assert f"redirect: http://{NAS}/ -> HTTP 301 -> https://{NAS}/" in text


def test_tool_formats_failure_with_its_cause():
    import tools
    text, _ = _tool({"url": f"http://{NAS}/", "ok": False, "cause": "connection_refused",
                     "elapsed_ms": 3, "error": "connection refused",
                     "redirects": [], "final_url": f"http://{NAS}/"})
    assert text == tools.failure(
        f"[http_check http://{NAS}/: FAILED (connection_refused) after 3 ms: connection refused]")


def test_tool_shows_a_redirect_that_was_not_followed():
    text, _ = _tool({"url": f"http://{NAS}/", "ok": True, "status": 302, "elapsed_ms": 4,
                     "redirects": [{"url": f"http://{NAS}/", "status": 302,
                                    "location": f"ftp://{NAS}/"}],
                     "final_url": f"http://{NAS}/", "headers": {"location": f"ftp://{NAS}/"},
                     "note": f"redirect to ftp://{NAS}/ not followed: only http and https URLs are allowed, not 'ftp'"})
    assert text.endswith(f"note: redirect to ftp://{NAS}/ not followed: only http and https URLs are allowed, not 'ftp'")


def test_tool_requires_a_url():
    import tools
    assert asyncio.run(tools._tool_http_check(None, {})) == \
        tools.failure("[http_check error: url is required]")


# ── B13: only devops, and the M4 loop check still holds ─────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["research", "coding", "home", "assistant"])
async def test_other_agents_loops_reject_http_check(agent_name):
    """A fetched page telling research to probe the LAN gets a rejection."""
    import agents
    seen = []

    async def fake_stream(messages, model, tools=None):
        seen.append(list(messages))
        if len(seen) == 1:
            yield {"tool_calls": [{"id": "c1", "function": {
                "name": "http_check", "arguments": {"url": f"http://{DEV}/"}}}]}
        else:
            yield {"token": "ok"}
        yield {"usage": {}}

    executed = []

    async def fake_execute(name, a):
        executed.append(name)
        return "should not run"

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        [ev async for ev in agents.run_stream(agents.AGENTS[agent_name], "x", [])]
    assert executed == []
    assert "REJECTED" in [m for m in seen[1] if m["role"] == "tool"][0]["content"]


# ── the offline guard itself ────────────────────────────────────────────────

def test_the_network_guard_is_on_for_this_module():
    """If this fails, the guard is off and no other test here may run."""
    assert socket.getaddrinfo.__name__ == "refuse"
    assert socket.socket.connect.__name__ == "connect" and \
        socket.socket.connect is not socket.socket.__mro__[1].connect
    # Order matters: the identity checks above pass before anything is sent.
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo(NAS, 80)
    with pytest.raises(NetworkBlocked):
        socket.create_connection((DEV, 80), timeout=0.01)
    with pytest.raises(NetworkBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((DEV, 80))
    # A real client built behind the fake's back (the M5 e2e mutants) fails too.
    with pytest.raises(NetworkBlocked):
        httpx.get(f"http://{DEV}/")
    with pytest.raises(NetworkBlocked):
        asyncio.run(httpx.AsyncClient().get(f"http://{NAS}/"))
