"""Outbound address checks for tool_service (sprint M5).

Two consumers:

- `/fetch` (the research and coding agents' `fetch_url`) may reach only
  public addresses. `check_public_url` refuses loopback, RFC 1918,
  link-local, CGNAT, unspecified, multicast and every other address that is
  not globally routable, in IPv4 and IPv6, plus single-label hostnames
  (Docker service names) and anything that is not plain http(s). The caller
  turns redirects off and runs this check again on every hop.
- `/http_check` (the devops agent's reachability probe) is exempt from that
  block, because reaching LAN hosts is its purpose. It is limited instead
  by an allow-list file beside the ops host registry
  (`load_http_check_allowlist`). Absent or empty file: every target refused.

Accepted limit: DNS rebinding. The hostname is resolved here, then httpx
resolves it again when it connects. A name that answers public for the
first lookup and private for the second gets through. This is a home LAN,
not a hostile multi-tenant host; the window is accepted (plan, M5 "Limit").
"""
import asyncio
import ipaddress
import json
import os
import socket
from pathlib import Path

import httpx

HTTP_CHECK_HOSTS_FILE = Path(os.getenv("OPS_HTTP_CHECK_HOSTS",
                                       "/ops/http_check_hosts.json"))
DNS_TIMEOUT_S = 5.0
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# NAT64 well-known prefix: the low 32 bits are an IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
# IPv4-compatible IPv6 (deprecated ::a.b.c.d). Python calls these global.
_V4_COMPAT = ipaddress.ip_network("::/96")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_RFC1918 = [ipaddress.ip_network(n) for n in
            ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


class Blocked(Exception):
    """The URL or one of its addresses is refused. The message is the cause."""


def _embedded_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if addr.ipv4_mapped:
        return addr.ipv4_mapped
    if addr.sixtofour:
        return addr.sixtofour
    if addr in _NAT64 or (addr in _V4_COMPAT and int(addr) > 1):
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def address_refusal(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why `addr` may not be fetched, or None when it is public."""
    if isinstance(addr, ipaddress.IPv6Address):
        inner = _embedded_v4(addr)
        if inner is not None:
            why = address_refusal(inner)
            return f"{why} (IPv4 {inner} inside IPv6)" if why else None
    if addr.is_unspecified:
        return "an unspecified address"
    if addr.is_loopback:
        return "a loopback address"
    if addr.is_link_local:
        return "a link-local address"
    if addr.is_multicast:
        return "a multicast address"
    if isinstance(addr, ipaddress.IPv4Address):
        if any(addr in n for n in _RFC1918):
            return "a private (RFC 1918) address"
        if addr in _CGNAT:
            return "a CGNAT (100.64.0.0/10) address"
    elif addr in ipaddress.ip_network("fc00::/7"):
        return "a private (unique local, fc00::/7) address"
    if addr.is_private or addr.is_reserved or not addr.is_global:
        return "not a globally routable address"
    return None


def literal_address(host: str):
    """The address a literal host means, in any spelling the C resolver
    accepts (dotted quad, decimal, octal, hex, short forms, IPv6 with or
    without brackets and zone). None when `host` is a name."""
    h = host.strip("[]")
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    try:
        # inet_aton is what getaddrinfo uses for numeric hosts: 2130706433,
        # 0x7f.1, 0177.0.0.1 and 127.1 all mean 127.0.0.1. Pure parsing,
        # no lookup.
        return ipaddress.IPv4Address(socket.inet_aton(h))
    except (OSError, ValueError):
        return None


def parse_url(url: str) -> httpx.URL:
    """Parse with httpx's own parser, so the host checked is the host the
    client will connect to. Refuses non-http(s) schemes and credentials."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as e:
        raise Blocked(f"invalid URL: {e}")
    if u.scheme not in ("http", "https"):
        raise Blocked(f"only http and https URLs are allowed, not {u.scheme or 'a URL without a scheme'!r}")
    if u.userinfo:
        raise Blocked("URLs with credentials (user:password@) are not allowed")
    if not u.host:
        raise Blocked("URL has no host")
    return u


async def _resolve(host: str, port: int) -> list:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
        timeout=DNS_TIMEOUT_S,
    )
    out = []
    for info in infos:
        ip = info[4][0]
        try:
            out.append(ipaddress.ip_address(ip.split("%", 1)[0]))
        except ValueError:
            continue
    return out


async def check_public_url(url: str) -> httpx.URL:
    """Refuse `url` unless every address it names or resolves to is public.
    Raises Blocked with the cause; returns the parsed URL."""
    u = parse_url(url)
    host = u.host.rstrip(".").lower()
    lit = literal_address(host)
    if lit is not None:
        why = address_refusal(lit)
        if why:
            raise Blocked(f"{host} is {why}")
        return u
    if "." not in host:
        raise Blocked(f"{host!r} is a single-label hostname (a local or "
                      "Docker service name), not a public site")
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        addrs = await _resolve(host, port)
    except asyncio.TimeoutError:
        raise Blocked(f"DNS lookup for {host} timed out")
    except socket.gaierror as e:
        raise Blocked(f"DNS lookup for {host} failed: {e.strerror or e}")
    if not addrs:
        raise Blocked(f"DNS lookup for {host} returned no addresses")
    for a in addrs:
        why = address_refusal(a)
        if why:
            raise Blocked(f"{host} resolves to {a}, {why}")
    return u


def redirect_target(resp: httpx.Response, current: httpx.URL) -> httpx.URL | None:
    """The next URL of a redirect response, or None when it is not one."""
    if resp.status_code not in REDIRECT_STATUSES:
        return None
    loc = resp.headers.get("location")
    if not loc:
        return None
    return current.join(loc)


# ── http_check allow-list ─────────────────────────────────────────────────────

def load_http_check_allowlist() -> tuple[set[str], str | None]:
    """(hosts, problem). problem is None when the file has at least one host;
    otherwise it says why nothing is allowed, naming the file."""
    path = HTTP_CHECK_HOSTS_FILE
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return set(), f"the allow-list {path} does not exist"
    except OSError as e:
        return set(), f"the allow-list {path} could not be read ({e.strerror or e})"
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        return set(), f"the allow-list {path} is not valid JSON ({e})"
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, list):
        hosts = []
    clean = {normalize_host(h) for h in hosts if isinstance(h, str) and h.strip()}
    if not clean:
        return set(), f"the allow-list {path} names no hosts"
    return clean, None


def normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()
