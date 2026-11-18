"""Sprint M21 step 2, criteria 5, 6 and 24 (unlock part): the passphrase
hash, the helper script, sessions, rate limits and lockout, the Origin and
token checks, and that the passphrase reaches no log, trace or audit line.

Passphrases here are obvious throwaways. No sockets."""
import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

import settings_api
import settings_auth
from tests.m21s2_helpers import HOST, ORIGIN, REPO, orch_transport

pytestmark = pytest.mark.usefixtures("no_network")

PASS = "throwaway-test-passphrase-1"
WRONG = "throwaway-wrong-guess-zz9"


@pytest.fixture(scope="module")
def stored():
    return settings_auth.hash_passphrase(PASS)


@pytest.fixture
def env(tmp_path, monkeypatch, stored):
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", stored)
    monkeypatch.setenv("SETTINGS_INSTANCE", "production")
    monkeypatch.setenv("SETTINGS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    settings_auth.reset()
    yield tmp_path
    settings_auth.reset()


def post(path, body=None, *, origin=ORIGIN, ip="192.0.2.10", cookies=None, token=None, headers=None):
    async def go():
        h = {"x-real-ip": ip}
        if origin:
            h["origin"] = origin
        if token:
            h["x-settings-token"] = token
        h.update(headers or {})
        async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}",
                                     cookies=cookies or {}) as c:
            return await c.post(path, json=body if body is not None else {}, headers=h)
    return asyncio.run(go())


def audit_lines(tmp_path):
    p = tmp_path / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── the hash ─────────────────────────────────────────────────────────────────

def test_hash_is_salted_scrypt_with_the_stated_cost(stored):
    kind, log2_n, r, p, salt, key = stored.split(":")
    assert (kind, log2_n, r, p) == ("scrypt", "15", "8", "1")
    assert len(settings_auth._unb64(salt)) == 16 and len(settings_auth._unb64(key)) == 32
    assert "$" not in stored                         # compose .env interpolation leaves it alone
    assert settings_auth.hash_passphrase(PASS) != stored     # salted
    assert settings_auth.verify(PASS, stored) and not settings_auth.verify(WRONG, stored)
    assert not settings_auth.verify(PASS, "scrypt:15:8:1:AAAA:BBBB")
    assert settings_auth.parse_hash("not a hash") is None


def test_comparison_is_constant_time():
    import inspect
    src = inspect.getsource(settings_auth.verify)
    assert "hmac.compare_digest" in src and "==" not in src.split("hmac.compare_digest")[1].split("\n")[0]
    assert "hmac.compare_digest" in inspect.getsource(settings_auth.require)


def test_editing_is_off_without_a_hash(monkeypatch):
    monkeypatch.delenv("SETTINGS_PASSPHRASE_HASH", raising=False)
    assert "not set" in settings_auth.hash_problem()
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", "plain-text-oops")
    assert "not a hash" in settings_auth.hash_problem()
    settings_auth.reset()
    r = post("/api/settings/unlock", {"passphrase": PASS})
    assert r.status_code == 403 and "Editing is off" in r.json()["detail"]


# ── the helper script ────────────────────────────────────────────────────────

def _helper(env_file: Path, stdin: str):
    return subprocess.run(["bash", str(REPO / "scripts" / "settings_passphrase.sh"), str(env_file)],
                          input=stdin, capture_output=True, text=True, timeout=60)


def test_helper_writes_one_hash_line_replacing_the_old_and_prints_nothing_secret(tmp_path):
    f = tmp_path / ".env"
    f.write_text("HA_TOKEN=abc\nSETTINGS_PASSPHRASE_HASH=scrypt:15:8:1:old:old\nMA_TOKEN=def")
    f.chmod(0o640)
    r = _helper(f, f"{PASS}\n{PASS}\n")
    assert r.returncode == 0, r.stderr
    lines = f.read_text().splitlines()
    assert lines[:2] == ["HA_TOKEN=abc", "MA_TOKEN=def"]
    (h,) = [x for x in lines if x.startswith("SETTINGS_PASSPHRASE_HASH=")]
    stored = h.split("=", 1)[1]
    assert settings_auth.verify(PASS, stored)
    assert oct(f.stat().st_mode & 0o777) == "0o640"
    for out in (r.stdout, r.stderr):
        assert PASS not in out and stored not in out and stored.split(":")[-1] not in out
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".env.settings")]


def test_helper_refuses_a_mismatch_or_a_short_passphrase_and_writes_nothing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("HA_TOKEN=abc\n")
    r = _helper(f, f"{PASS}\n{PASS}x\n")
    assert r.returncode == 1 and "differ" in r.stderr and PASS not in r.stderr
    r = _helper(f, "short\nshort\n")
    assert r.returncode == 1 and "at least 12" in r.stderr
    assert f.read_text() == "HA_TOKEN=abc\n"


def test_helper_creates_a_new_file_mode_600(tmp_path):
    f = tmp_path / "new.env"
    assert _helper(f, f"{PASS}\n{PASS}\n").returncode == 0
    assert oct(f.stat().st_mode & 0o777) == "0o600"


# ── unlock, cookie, lock ─────────────────────────────────────────────────────

def test_unlock_gives_an_httponly_samesite_strict_session(env):
    r = post("/api/settings/unlock", {"passphrase": PASS})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("kronk_settings_production=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    sid = cookie.split(";")[0].split("=", 1)[1]
    assert len(sid) >= 40 and sid in settings_auth._sessions
    assert r.json()["token"] == settings_auth._sessions[sid].token != sid
    assert [a["event"] for a in audit_lines(env)] == ["unlock"]


def test_dev_cookie_has_its_own_name_and_path(env, monkeypatch):
    monkeypatch.setenv("SETTINGS_INSTANCE", "dev")
    monkeypatch.setenv("SETTINGS_URL_PREFIX", "/dev")
    r = post("/api/settings/unlock", {"passphrase": PASS})
    assert r.headers["set-cookie"].startswith("kronk_settings_dev=") and "Path=/dev/" in r.headers["set-cookie"]


def test_wrong_origin_or_none_is_refused(env):
    assert post("/api/settings/unlock", {"passphrase": PASS}, origin="http://evil.example").status_code == 403
    assert post("/api/settings/unlock", {"passphrase": PASS}, origin=None).status_code == 403
    r = post("/api/settings/unlock", {"passphrase": PASS}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert settings_auth._sessions == {}


def test_lock_ends_the_session(env):
    r = post("/api/settings/unlock", {"passphrase": PASS})
    cookies = {"kronk_settings_production": r.headers["set-cookie"].split(";")[0].split("=", 1)[1]}
    assert post("/api/settings/lock", cookies=cookies).status_code == 200
    assert settings_auth._sessions == {}
    assert [a["event"] for a in audit_lines(env)] == ["unlock", "lock"]


def _request(cookie=None, token=None, origin=ORIGIN):
    headers = [(b"host", HOST.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", f"kronk_settings_production={cookie}".encode()))
    if token:
        headers.append((b"x-settings-token", token.encode()))
    return Request({"type": "http", "method": "POST", "path": "/api/settings/save", "headers": headers,
                    "query_string": b""})


def test_require_needs_cookie_token_and_origin(env):
    s = settings_auth._new_session("192.0.2.10")
    assert settings_auth.require(_request(s.sid, s.token)) is s
    for req in (_request(None, s.token), _request(s.sid, None), _request(s.sid, "x" * 43),
                _request(s.sid, s.token, origin="http://evil.example"), _request(s.sid, s.token, origin=None)):
        with pytest.raises(Exception) as e:
            settings_auth.require(req)
        assert e.value.status_code == 403


def test_idle_timeout_and_absolute_lifetime(env, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(settings_auth.time, "monotonic", lambda: now[0])
    s = settings_auth._new_session("192.0.2.10")
    now[0] += settings_auth.IDLE_S - 1
    assert settings_auth.require(_request(s.sid, s.token)) is s       # a request refreshes it
    now[0] += settings_auth.IDLE_S + 1
    with pytest.raises(Exception):
        settings_auth.require(_request(s.sid, s.token))
    s = settings_auth._new_session("192.0.2.10")
    for _ in range(20):                                               # active all along
        now[0] += settings_auth.IDLE_S - 60
        if now[0] - s.created > settings_auth.ABSOLUTE_S:
            break
        settings_auth.require(_request(s.sid, s.token))
    with pytest.raises(Exception):
        settings_auth.require(_request(s.sid, s.token))
    assert settings_auth.IDLE_S == 15 * 60 and settings_auth.ABSOLUTE_S == 2 * 3600


def test_a_restart_ends_every_session(env):
    """Sessions are in memory only: nothing is written but the audit line,
    and a fresh process (reset) knows no session."""
    r = post("/api/settings/unlock", {"passphrase": PASS})
    sid = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    files = sorted(p.name for p in env.iterdir())
    assert files == ["audit.jsonl"]
    assert sid not in (env / "audit.jsonl").read_text()
    settings_auth.reset()
    with pytest.raises(Exception):
        settings_auth.require(_request(sid, r.json()["token"]))


# ── rate limit and lockout ───────────────────────────────────────────────────

def test_per_client_lockout(env):
    codes = [post("/api/settings/unlock", {"passphrase": WRONG}, ip="192.0.2.20").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    r = post("/api/settings/unlock", {"passphrase": PASS}, ip="192.0.2.20")
    assert r.status_code == 429 and "minutes" in r.json()["detail"]
    assert post("/api/settings/unlock", {"passphrase": PASS}, ip="192.0.2.21").status_code == 200
    events = [a["event"] for a in audit_lines(env)]
    assert events == ["unlock_failed"] * 5 + ["lockout", "unlock_refused_locked_out", "unlock"]
    lock = next(a for a in audit_lines(env) if a["event"] == "lockout")
    assert lock["client"] == "192.0.2.20" and lock["scope"] == "client"


def test_global_lockout(env):
    for i in range(settings_auth.FAILS_GLOBAL):
        post("/api/settings/unlock", {"passphrase": WRONG}, ip=f"192.0.2.{100 + i}")
    r = post("/api/settings/unlock", {"passphrase": PASS}, ip="192.0.2.250")
    assert r.status_code == 429
    assert any(a["event"] == "lockout" and a["scope"] == "global" for a in audit_lines(env))


def test_lockout_expires(env, monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(settings_auth.time, "monotonic", lambda: now[0])
    for _ in range(5):
        post("/api/settings/unlock", {"passphrase": WRONG}, ip="192.0.2.30")
    assert post("/api/settings/unlock", {"passphrase": PASS}, ip="192.0.2.30").status_code == 429
    now[0] += settings_auth.LOCKOUT_S + 1
    assert post("/api/settings/unlock", {"passphrase": PASS}, ip="192.0.2.30").status_code == 200


@pytest.mark.parametrize("record_text", [False, True], ids=["text-off", "text-on"])
def test_the_passphrase_is_in_no_log_trace_or_audit_line(env, caplog, monkeypatch, record_text):
    import telemetry
    from fastapi import FastAPI
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    monkeypatch.setattr(telemetry, "RECORD_TEXT", record_text)
    exporter = InMemorySpanExporter()
    was = telemetry.ENABLED
    telemetry.enable_for_tests(exporter)
    app = FastAPI()
    app.include_router(settings_api.router)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=telemetry._provider,
                                       server_request_hook=telemetry._strip_url_attrs)
    caplog.set_level(logging.DEBUG)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as c:
            for pw in (WRONG, WRONG, PASS):
                await c.post("/api/settings/unlock", json={"passphrase": pw},
                             headers={"origin": ORIGIN, "x-real-ip": "192.0.2.40"})
    try:
        asyncio.run(go())
    finally:
        telemetry.ENABLED = was
        FastAPIInstrumentor.uninstrument_app(app)
    spans = exporter.get_finished_spans()
    assert spans
    span_text = json.dumps([{"name": s.name, "attrs": dict(s.attributes or {}),
                             "events": [dict(e.attributes or {}) for e in s.events]} for s in spans], default=str)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    audit = (env / "audit.jsonl").read_text()
    assert "wrong passphrase from 192.0.2.40" in logs
    for secret in (PASS, WRONG):
        assert secret not in span_text and secret not in logs and secret not in audit
