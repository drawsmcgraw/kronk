"""Shared fixtures for Kronk tests.

Import policy (2026-06-12): services are imported as PACKAGES
(`tool_service.main`, `voice_tracer.main`, …) so the flat container layout
keeps working. Only `orchestrator/` goes on sys.path directly: its modules
import each other by bare name (`import agents`) and have no cross-service
collisions. (A per-service sys.path-juggling fixture once poisoned
sys.modules across test files; don't bring one back.)
"""
import os
import sys
import tempfile

# Household taste store (tool_service/taste.py) — never the container's
# /data paths from a test run. Read at import, so set before any import.
_TASTE_TMP = tempfile.mkdtemp(prefix="kronk-taste-")
os.environ.setdefault("MUSIC_TASTE_DB", os.path.join(_TASTE_TMP, "music_taste.db"))
os.environ.setdefault("MUSIC_TASTE_FILE", os.path.join(_TASTE_TMP, "music_taste.yaml"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# config/ (sprint M21): the services read /config in the container; tests
# read the repo's own files, so the pins prove the shipped files.
os.environ.setdefault("KRONK_CONFIG_DIR", os.path.join(REPO_ROOT, "config"))

_ORCH = os.path.join(REPO_ROOT, "orchestrator")
if _ORCH not in sys.path:
    sys.path.append(_ORCH)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ── no_network: fail any real lookup or connection (sprint M5 change 1) ──────
# The /fetch and /http_check tests feed the code private and placeholder
# hosts. A fake HTTP client serves them, but a bug or a mutant that builds
# its own client would reach the real network: in the M5 end-to-end test a
# mutant sent ARP and mDNS traffic for placeholder hosts. Modules that test
# outbound fetches opt in with `pytestmark = pytest.mark.usefixtures(
# "no_network")`. Every name lookup and every IPv4/IPv6 connect or send
# raises NetworkBlocked before a packet leaves; AF_UNIX sockets (and the
# event loop's socketpair) are unaffected.
import socket as _socket

import pytest as _pytest


class NetworkBlocked(AssertionError):
    """A test tried to use the real network."""


_INET = (_socket.AF_INET, _socket.AF_INET6)


def _blocked(what):
    def refuse(*a, **kw):
        raise NetworkBlocked(f"test tried to use the network: {what}{a!r}")
    return refuse


def _sock_guard(name):
    real = getattr(_socket.socket, name)

    def guarded(self, *a, **kw):
        if self.family in _INET:
            raise NetworkBlocked(f"test tried to use the network: socket.{name}{a!r}")
        return real(self, *a, **kw)
    guarded.__name__ = name
    return guarded


NO_NETWORK_PATCHES = {
    "getaddrinfo": "lookup", "gethostbyname": "lookup", "gethostbyname_ex": "lookup",
    "gethostbyaddr": "lookup", "getnameinfo": "lookup", "create_connection": "connect",
}
NO_NETWORK_SOCKET_METHODS = ("connect", "connect_ex", "sendto", "sendmsg")


@_pytest.fixture
def no_network(monkeypatch):
    for fn in NO_NETWORK_PATCHES:
        monkeypatch.setattr(_socket, fn, _blocked(f"socket.{fn}"))
    for m in NO_NETWORK_SOCKET_METHODS:
        monkeypatch.setattr(_socket.socket, m, _sock_guard(m))
    yield
