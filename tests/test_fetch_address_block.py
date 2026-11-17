"""/fetch refuses private addresses and follows redirects by hand (sprint M5).

Plan section "M5. Block private addresses in fetch_url"; pass criteria in
docs/sprint/M5-criteria.md (A1-A8). No test touches the network: the HTTP
client is a fake that fails the test on any request it was not told about,
the resolver is stubbed, and the no_network fixture (tests/conftest.py)
fails any real lookup or connection. The private literals the criteria name
(192.168.1.1, 10.0.0.1, ...) are refusal cases and are never requested;
hostnames that stand for private hosts use the reserved .test domain.
"""
import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
import tool_service.netguard as ng

pytestmark = pytest.mark.usefixtures("no_network")


# ── fakes ─────────────────────────────────────────────────────────────────────

def _key(url) -> str:
    return str(httpx.URL(str(url)))


class FakeClient:
    """Stands in for httpx.AsyncClient. routes: url -> httpx.Response or an
    exception to raise. Any other URL fails the test."""

    def __init__(self, routes=None):
        self.routes = {_key(k): v for k, v in (routes or {}).items()}
        self.calls: list[str] = []
        self.init_kwargs: dict = {}
        self.request_kwargs: list[dict] = []
        self.methods: list[str] = []

    def __call__(self, *a, **kw):
        self.init_kwargs = kw
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _respond(self, url, kw):
        self.calls.append(_key(url))
        self.request_kwargs.append(kw)
        r = self.routes.get(_key(url))
        if r is None:
            raise AssertionError(f"unexpected request to {url}")
        if isinstance(r, BaseException):
            raise r
        return r

    async def get(self, url, **kw):
        self.methods.append("GET")
        return self._respond(url, kw)

    @asynccontextmanager
    async def stream(self, method, url, **kw):
        self.methods.append(method)
        yield self._respond(url, kw)


PUBLIC = {
    "example.com": ["93.184.215.14"],
    "www.example.org": ["93.184.215.15", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
    "docs.example.net": ["198.51.100.20"],  # TEST-NET-2, see stub note below
}
PRIVATE = {
    "router.home.test": ["192.168.1.1"],
    "mixed.home.test": ["93.184.215.14", "10.0.0.5"],
    "rebind6.home.test": ["fd00::5"],
    "meta.home.test": ["169.254.169.254"],
}


@pytest.fixture(autouse=True)
def stub_resolver(monkeypatch):
    """Every hostname lookup goes through this table; nothing reaches DNS."""
    table = {**PUBLIC, **PRIVATE}
    lookups = []

    async def fake_resolve(host, port):
        lookups.append(host)
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [ipaddress.ip_address(a) for a in table[host]]

    monkeypatch.setattr(ng, "_resolve", fake_resolve)
    # TEST-NET ranges are not globally routable; the stub stands in for a
    # public host, so treat this one as public for the redirect tests.
    real = ng.address_refusal
    monkeypatch.setattr(ng, "address_refusal",
                        lambda a: None if str(a) == "198.51.100.20" else real(a))
    return lookups


def _fetch(url, routes=None):
    client = FakeClient(routes)
    with patch.object(ts.httpx, "AsyncClient", client):
        resp = TestClient(ts.app).get("/fetch", params={"url": url})
    assert resp.status_code == 200
    return resp.json(), client


def _html(text="Hello from a public page. " * 20, status=200, headers=None):
    h = {"content-type": "text/html; charset=utf-8", **(headers or {})}
    return httpx.Response(status, headers=h,
                          text=f"<html><body><article><p>{text}</p></article></body></html>")


def _redirect(location, status=302):
    return httpx.Response(status, headers={"location": location})


# ── A1, A2, A7: the address block ───────────────────────────────────────────

REFUSED = [
    # (url, words the error must carry)
    ("http://127.0.0.1/", "loopback"),
    ("http://127.0.0.1:8003/fetch?url=x", "loopback"),
    ("http://10.0.0.1/", "RFC 1918"),
    ("http://192.168.1.1/", "RFC 1918"),
    ("http://172.16.5.4/", "RFC 1918"),
    ("http://169.254.169.254/latest/meta-data/", "link-local"),
    ("http://100.64.0.1/", "CGNAT"),
    ("http://0.0.0.0/", "unspecified"),
    ("http://224.0.0.251/", "multicast"),
    ("http://240.0.0.1/", "not a globally routable"),
    ("http://198.18.0.1/", "not a globally routable"),
    # Docker service names and other single labels: no lookup at all.
    ("http://finance_service:8005/", "single-label"),
    ("http://tool_service:8003/ops/exec", "single-label"),
    ("http://litellm:4000/", "single-label"),
    ("http://nginx/", "single-label"),
    ("http://localhost/", "single-label"),
    ("http://LOCALHOST:8123/", "single-label"),
    ("http://localhost./", "single-label"),
    # IPv6
    ("http://[::1]/", "loopback"),
    ("http://[::1]:8003/", "loopback"),
    ("http://[::]/", "unspecified"),
    ("http://[fc00::1]/", "unique local"),
    ("http://[fd12:3456::1]/", "unique local"),
    ("http://[fe80::1]/", "link-local"),
    ("http://[ff02::1]/", "multicast"),
    # IPv4 inside IPv6
    ("http://[::ffff:192.168.1.1]/", "RFC 1918"),
    ("http://[::ffff:c0a8:101]/", "RFC 1918"),
    ("http://[::ffff:127.0.0.1]/", "loopback"),
    ("http://[::ffff:169.254.169.254]/", "link-local"),
    ("http://[64:ff9b::a00:1]/", "RFC 1918"),          # NAT64 -> 10.0.0.1
    ("http://[2002:c0a8:101::]/", "RFC 1918"),         # 6to4 -> 192.168.1.1
    ("http://[::a00:1]/", "RFC 1918"),                 # IPv4-compatible
    # Numeric spellings the C resolver accepts for 127.0.0.1 / 10.0.0.1
    ("http://2130706433/", "loopback"),
    ("http://0x7f000001/", "loopback"),
    ("http://0x7f.1/", "loopback"),
    ("http://017700000001/", "loopback"),
    ("http://127.1/", "loopback"),
    ("http://10.1/", "RFC 1918"),
    ("http://167772161/", "RFC 1918"),
    # Hostnames that resolve to private addresses (resolver stubbed)
    ("http://router.home.test/admin", "resolves to 192.168.1.1"),
    ("http://mixed.home.test/", "resolves to 10.0.0.5"),
    ("http://rebind6.home.test/", "unique local"),
    ("https://meta.home.test/", "link-local"),
]


@pytest.mark.parametrize("url,why", REFUSED)
def test_private_targets_are_refused_before_any_request(url, why):
    out, client = _fetch(url)
    assert out["ok"] is False
    assert out["url"] == url
    assert out["error"].startswith("blocked")
    assert why in out["error"]
    assert client.calls == []


@pytest.mark.parametrize("url", ["http://0177.0.0.1/", "http://127.000.000.001/"])
def test_leading_zero_dotted_quads_are_refused(url):
    """httpx rejects these as invalid URLs; either way nothing is fetched."""
    out, client = _fetch(url)
    assert out["ok"] is False and out["error"].startswith("blocked")
    assert client.calls == []


def test_single_label_names_are_not_looked_up(stub_resolver):
    _fetch("http://finance_service:8005/")
    _fetch("http://localhost/")
    assert stub_resolver == []


def test_dns_failure_is_a_refusal_with_its_cause():
    out, client = _fetch("http://no-such-host.example.com/")
    assert out["ok"] is False
    assert "DNS lookup for no-such-host.example.com failed" in out["error"]
    assert client.calls == []


# ── A3: schemes and credentials ─────────────────────────────────────────────

@pytest.mark.parametrize("url,why", [
    ("file:///etc/passwd", "only http and https"),
    ("ftp://example.com/x", "only http and https"),
    ("gopher://example.com/", "only http and https"),
    ("example.com/page", "only http and https"),
    ("http://user:pass@example.com/", "credentials"),
    ("http://user@example.com/", "credentials"),
])
def test_other_schemes_and_credentials_are_refused(url, why):
    out, client = _fetch(url)
    assert out["ok"] is False and why in out["error"]
    assert client.calls == []


# ── A4: redirects by hand ───────────────────────────────────────────────────

def test_public_redirect_to_private_is_refused_and_names_the_hop():
    out, client = _fetch("https://example.com/go", {
        "https://example.com/go": _redirect("http://192.168.1.1/admin"),
    })
    assert out["ok"] is False
    assert "redirect 1, to http://192.168.1.1/admin" in out["error"]
    assert "RFC 1918" in out["error"]
    assert client.calls == [_key("https://example.com/go")]


def test_redirect_to_a_docker_name_at_hop_two_is_refused():
    out, client = _fetch("https://example.com/a", {
        "https://example.com/a": _redirect("https://www.example.org/b", 301),
        "https://www.example.org/b": _redirect("http://tool_service:8003/ops/exec", 307),
    })
    assert out["ok"] is False
    assert "redirect 2, to http://tool_service:8003/ops/exec" in out["error"]
    assert len(client.calls) == 2


def test_redirect_to_a_name_resolving_private_is_refused():
    out, _ = _fetch("https://example.com/a", {
        "https://example.com/a": _redirect("https://router.home.test/"),
    })
    assert out["ok"] is False and "resolves to 192.168.1.1" in out["error"]


def test_relative_redirect_stays_on_the_public_host():
    out, client = _fetch("https://example.com/old", {
        "https://example.com/old": _redirect("/new", 308),
        "https://example.com/new": _html("moved content " * 30),
    })
    assert out["ok"] is True and "moved content" in out["text"]
    assert client.calls == [_key("https://example.com/old"), _key("https://example.com/new")]


def test_five_redirects_are_followed():
    routes = {f"https://example.com/{i}": _redirect(f"/{i + 1}") for i in range(5)}
    routes["https://example.com/5"] = _html()
    out, client = _fetch("https://example.com/0", routes)
    assert out["ok"] is True
    assert len(client.calls) == 6


def test_more_than_five_redirects_is_refused():
    routes = {f"https://example.com/{i}": _redirect(f"/{i + 1}") for i in range(7)}
    out, client = _fetch("https://example.com/0", routes)
    assert out["ok"] is False
    assert out["error"] == "too many redirects (more than 5)"
    assert len(client.calls) == 6          # the first request plus five hops


# ── A5, A6: shapes and limits ────────────────────────────────────────────────

def test_public_page_fetches_as_before():
    out, client = _fetch("https://example.com/article", {
        "https://example.com/article": _html("The public article text. " * 20),
    })
    assert set(out) == {"url", "ok", "text"}
    assert out["ok"] is True and "public article text" in out["text"]
    # Same client settings as before M5, except redirects are now manual.
    assert client.init_kwargs == {"timeout": 15, "follow_redirects": False}
    assert client.request_kwargs[0]["headers"] is ts._BROWSER_HEADERS


def test_ipv6_public_host_resolving_to_two_public_addresses_fetches():
    out, _ = _fetch("https://www.example.org/", {"https://www.example.org/": _html()})
    assert out["ok"] is True


def test_upstream_errors_keep_their_shape():
    out, _ = _fetch("https://example.com/missing", {
        "https://example.com/missing": httpx.Response(404, text="nope"),
    })
    assert out == {"url": "https://example.com/missing", "ok": False, "error": "HTTP 404 Not Found"}
    out, _ = _fetch("https://example.com/slow", {
        "https://example.com/slow": httpx.ReadTimeout("slow"),
    })
    assert out == {"url": "https://example.com/slow", "ok": False, "error": "request timed out"}


def test_refusal_reaches_the_agent_as_an_ordinary_tool_failure():
    """The orchestrator's fetch_url handler turns {"ok": false} into its
    usual "Could not fetch" line; nothing new for the model to learn."""
    import os
    os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")
    import tools

    class C:
        async def get(self, url, params=None):
            return httpx.Response(200, json={"url": params["url"], "ok": False,
                                             "error": "blocked: 192.168.1.1 is a private (RFC 1918) address"},
                                  request=httpx.Request("GET", url))

    result = asyncio.run(tools._tool_fetch_url(C(), {"url": "http://192.168.1.1/"}))
    assert result.failed
    text = result.text
    assert text.startswith("[Could not fetch http://192.168.1.1/: blocked:")
    assert "RFC 1918" in text


# ── unit level ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111",
                                  "93.184.215.14", "::ffff:8.8.8.8"])
def test_public_addresses_pass(addr):
    assert ng.address_refusal(ipaddress.ip_address(addr)) is None


def test_literal_address_spellings():
    lo = ipaddress.ip_address("127.0.0.1")
    for s in ["127.0.0.1", "2130706433", "0x7f000001", "0x7f.1", "017700000001", "127.1"]:
        assert ng.literal_address(s) == lo, s
    assert ng.literal_address("[::1]") == ipaddress.ip_address("::1")
    assert ng.literal_address("example.com") is None
    assert ng.literal_address("finance_service") is None
