"""GET /http_check and the devops agent's http_check tool (sprint M5).

Criteria B10-B15 in docs/sprint/M5-criteria.md. The endpoint is exempt from
the /fetch address block and limited instead by an allow-list file; with the
file absent or empty it refuses everything. No test touches the network.
"""
import asyncio
import json
import os
import socket
import ssl

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import tool_service.main as ts
import tool_service.netguard as ng
import tool_service.ops as ops
from tests.test_fetch_address_block import FakeClient, _key


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
    """Allow-list path and audit log in tmp; returns helpers."""
    allow_file = tmp_path / "http_check_hosts.json"
    audit_file = tmp_path / "ops_audit.log"
    monkeypatch.setattr(ng, "HTTP_CHECK_HOSTS_FILE", allow_file)
    monkeypatch.setattr(ops, "OPS_AUDIT_LOG", audit_file)

    class Env:
        path = allow_file

        def allow(self, *hosts, raw=None):
            allow_file.write_text(raw if raw is not None else json.dumps({"hosts": list(hosts)}))

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


# ── B12: the allow-list ──────────────────────────────────────────────────────

def test_absent_file_refuses_everything_and_names_the_file(env):
    out, client = _check("http://192.168.1.10/")
    assert out["ok"] is False and out["cause"] == "not_allowed"
    assert str(env.path) in out["error"] and "does not exist" in out["error"]
    assert client.calls == []


@pytest.mark.parametrize("raw", ["", "  \n", "{}", '{"hosts": []}', '{"hosts": ["", "  "]}',
                                 '["192.168.1.10"]'])
def test_empty_file_refuses_everything(env, raw):
    env.allow(raw=raw)
    out, client = _check("http://192.168.1.10/")
    assert out["ok"] is False and out["cause"] == "not_allowed"
    assert str(env.path) in out["error"]
    assert client.calls == []


def test_unreadable_json_refuses_and_says_so(env):
    env.allow(raw="{not json")
    out, client = _check("http://192.168.1.10/")
    assert out["cause"] == "not_allowed" and "not valid JSON" in out["error"]
    assert client.calls == []


def test_unlisted_host_is_refused_before_any_connection(env):
    env.allow("192.168.1.10")
    out, client = _check("http://192.168.1.11/")
    assert out["ok"] is False and out["cause"] == "not_allowed"
    assert "192.168.1.11 is not in the allow-list" in out["error"]
    assert str(env.path) in out["error"]
    assert client.calls == []


def test_listed_private_host_is_reached(env):
    """Exempt from the /fetch block: a private address on the list is fine."""
    env.allow("192.168.1.10")
    out, client = _check("http://192.168.1.10:5000/status",
                         {"http://192.168.1.10:5000/status": _resp(200)})
    assert out["ok"] is True and out["status"] == 200
    assert client.calls == [_key("http://192.168.1.10:5000/status")]


@pytest.mark.parametrize("listed,url", [
    ("NAS.local", "http://nas.local/"),
    ("nas.local.", "http://NAS.LOCAL/"),
    ("[fd00::10]", "http://[fd00::10]:8080/"),
    ("localhost", "http://localhost:8103/health"),
])
def test_allow_list_matching_is_exact_host_case_insensitive(env, listed, url):
    env.allow(listed)
    out, client = _check(url, {url: _resp(204)})
    assert out["ok"] is True and out["status"] == 204


def test_list_is_read_on_every_call(env):
    _, client = _check("http://192.168.1.10/")
    assert client.calls == []
    env.allow("192.168.1.10")
    out, _ = _check("http://192.168.1.10/", {"http://192.168.1.10/": _resp()})
    assert out["ok"] is True
    env.path.unlink()
    out, _ = _check("http://192.168.1.10/")
    assert out["cause"] == "not_allowed"


@pytest.mark.parametrize("url,why", [
    ("file:///etc/passwd", "only http and https"),
    ("ftp://192.168.1.10/", "only http and https"),
    ("http://admin:secret@192.168.1.10/", "credentials"),
])
def test_schemes_and_credentials_are_refused_even_when_listed(env, url, why):
    env.allow("192.168.1.10")
    out, client = _check(url)
    assert out["ok"] is False and out["cause"] == "refused" and why in out["error"]
    assert client.calls == []


# ── B10: one GET, filtered headers, never the body ──────────────────────────

def test_success_returns_only_the_four_headers_and_never_the_body(env):
    env.allow("nas.local")
    body = TrackedBody(b"SECRET-BODY-TEXT")
    out, client = _check("http://nas.local/", {"http://nas.local/": _resp(200, {
        "Server": "nginx/1.25", "Content-Type": "text/html", "Content-Length": "16",
        "Set-Cookie": "session=abc", "X-Api-Key": "k", "WWW-Authenticate": "Basic",
    }, body)})
    assert out["headers"] == {"server": "nginx/1.25", "content-type": "text/html",
                              "content-length": "16"}
    assert set(out) == {"url", "ok", "elapsed_ms", "status", "redirects", "final_url", "headers"}
    assert out["redirects"] == [] and out["final_url"] == "http://nas.local/"
    assert isinstance(out["elapsed_ms"], int)
    assert body.read is False
    assert "SECRET-BODY" not in json.dumps(out)
    assert "SECRET-BODY" not in json.dumps(env.audit())


def test_one_plain_get_with_a_five_second_budget(env):
    env.allow("nas.local")
    _, client = _check("http://nas.local/", {"http://nas.local/": _resp()})
    assert client.methods == ["GET"]
    assert client.init_kwargs == {"follow_redirects": False, "verify": True}
    # Only the timeout is passed: no headers, params, body, cookies or auth.
    assert list(client.request_kwargs[0]) == ["timeout"]
    assert 0 < client.request_kwargs[0]["timeout"] <= 5.0


def test_redirect_to_a_listed_host_is_followed_and_reported(env):
    env.allow("nas.local", "192.168.1.10")
    out, client = _check("http://nas.local/", {
        "http://nas.local/": _resp(301, {"Location": "https://192.168.1.10/ui"}),
        "https://192.168.1.10/ui": _resp(200, {"Server": "lighttpd"}),
    })
    assert out["ok"] is True and out["status"] == 200
    assert out["redirects"] == [{"url": "http://nas.local/", "status": 301,
                                 "location": "https://192.168.1.10/ui"}]
    assert out["final_url"] == "https://192.168.1.10/ui"
    assert client.methods == ["GET", "GET"]


def test_redirect_to_an_unlisted_host_is_reported_not_followed(env):
    env.allow("nas.local")
    out, client = _check("http://nas.local/", {
        "http://nas.local/": _resp(302, {"Location": "http://192.168.1.1/login"}),
    })
    assert out["ok"] is True and out["status"] == 302
    assert out["redirects"][0]["location"] == "http://192.168.1.1/login"
    assert "not followed" in out["note"] and "192.168.1.1" in out["note"]
    assert out["final_url"] == "http://nas.local/"
    assert client.calls == [_key("http://nas.local/")]


def test_redirect_loop_stops_after_five(env):
    env.allow("nas.local")
    routes = {f"http://nas.local/{i}": _resp(302, {"Location": f"/{i + 1}"}) for i in range(7)}
    out, client = _check("http://nas.local/0", routes)
    assert out["ok"] is False and out["cause"] == "too_many_redirects"
    assert len(client.calls) == 6 and len(out["redirects"]) == 6


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
    env.allow("nas.local")
    out, _ = _check("http://nas.local/", {"http://nas.local/": exc})
    assert out["ok"] is False
    assert out["cause"] == cause
    assert words in out["error"]
    assert out["final_url"] == "http://nas.local/"
    line = env.audit()[-1]
    assert line["allowed"] is True and line["note"].startswith(cause)


# ── B14: the audit log ──────────────────────────────────────────────────────

def test_every_call_writes_one_audit_line_in_the_ops_exec_form(env):
    _check("http://192.168.1.10/")                                   # no list
    env.allow("nas.local")
    _check("http://192.168.1.11/")                                   # unlisted
    _check("file:///etc/passwd")                                     # scheme
    _check("http://nas.local/", {"http://nas.local/": _resp(200)})   # allowed
    _check("http://nas.local/x", {"http://nas.local/x": httpx.ConnectTimeout("t")})
    lines = env.audit()
    assert len(lines) == 5
    for l in lines:
        # The same keys ops.audit_exec writes for /ops/exec.
        assert set(l) == {"ts", "host", "command", "allowed", "exit_code", "out_len", "note"}
        assert l["command"].startswith("http_check GET ") and l["out_len"] == 0
    assert [l["allowed"] for l in lines] == [False, False, False, True, True]
    assert [l["host"] for l in lines] == ["192.168.1.10", "192.168.1.11", "?", "nas.local", "nas.local"]
    assert lines[3]["exit_code"] == 200 and lines[3]["note"] == "HTTP 200"
    assert lines[4]["exit_code"] is None and lines[4]["note"].startswith("timeout")


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
    return asyncio.run(tools._tool_http_check(c, {"url": "http://nas.local/"})), c


def test_tool_formats_success_with_redirects_and_headers():
    text, c = _tool({"url": "http://nas.local/", "ok": True, "status": 200, "elapsed_ms": 12,
                     "redirects": [{"url": "http://nas.local/", "status": 301,
                                    "location": "https://nas.local/"}],
                     "final_url": "https://nas.local/", "headers": {"server": "nginx"}})
    assert c.calls[0][0].endswith("/http_check")
    assert c.calls[0][1] == {"url": "http://nas.local/"}
    assert text.startswith("[http_check http://nas.local/: HTTP 200 in 12 ms; final URL https://nas.local/; headers: server: nginx]")
    assert "redirect: http://nas.local/ -> HTTP 301 -> https://nas.local/" in text


def test_tool_formats_failure_with_its_cause():
    text, _ = _tool({"url": "http://nas.local/", "ok": False, "cause": "not_allowed",
                     "elapsed_ms": 0, "error": "refused: the allow-list /ops/http_check_hosts.json does not exist, so http_check may reach no host"})
    assert "FAILED (not_allowed)" in text and "/ops/http_check_hosts.json" in text


def test_tool_requires_a_url():
    import tools
    assert asyncio.run(tools._tool_http_check(None, {})) == "[http_check error: url is required]"


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
                "name": "http_check", "arguments": {"url": "http://192.168.1.1/"}}}]}
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
