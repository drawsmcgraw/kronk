"""Unlocking the /settings page (sprint M21 step 2, criteria 5 and 6).

Editing is off unless SETTINGS_PASSPHRASE_HASH is set (by
scripts/settings_passphrase.sh, which prompts for the passphrase; the
sprint never sets one). The hash is the standard library's scrypt:

    scrypt:<log2 N>:<r>:<p>:<salt, base64url>:<key, base64url>
    N = 2**15, r = 8, p = 1, 16-byte random salt, 32-byte key

That costs 32 MiB and about 0.1 s per check on this host; checks run one
at a time, off the event loop. The comparison is hmac.compare_digest. The
format has no `$`, so docker compose's .env interpolation leaves it alone.

A correct passphrase starts a session: a random 32-byte id in an HttpOnly,
SameSite=Strict cookie (named per instance, path per URL prefix), kept in
memory only, so a restart ends every session. It ends after IDLE_S without
a request, after ABSOLUTE_S in any case, or on Lock. Every state-changing
request needs the cookie, the session's second token in X-Settings-Token
(the page holds it; another site's form cannot send a header) and an
Origin equal to the page's own; a Sec-Fetch-Site other than same-origin is
refused too.

Wrong passphrases: at most FAILS_PER_CLIENT per client address in
FAIL_WINDOW_S, and FAILS_GLOBAL from all addresses together; past either,
unlocking is refused for LOCKOUT_S (per address, or for everyone). Each
failure and each lockout is logged and written to the audit record with the
address only; the attempted text is never logged, traced or recorded.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import config_files

logger = logging.getLogger(__name__)

SCRYPT_LOG2_N = 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
KEY_BYTES = 32
MAX_PASSPHRASE_CHARS = 1024

IDLE_S = 15 * 60
ABSOLUTE_S = 2 * 60 * 60
MAX_SESSIONS = 20

FAIL_WINDOW_S = 15 * 60
FAILS_PER_CLIENT = 5
FAILS_GLOBAL = 20
LOCKOUT_S = 15 * 60

TOKEN_HEADER = "x-settings-token"


# ── the hash ─────────────────────────────────────────────────────────────────

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_passphrase(passphrase: str, *, salt: bytes | None = None, log2_n: int = SCRYPT_LOG2_N) -> str:
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** log2_n, r=SCRYPT_R, p=SCRYPT_P,
                         maxmem=SCRYPT_MAXMEM, dklen=KEY_BYTES)
    return f"scrypt:{log2_n}:{SCRYPT_R}:{SCRYPT_P}:{_b64(salt)}:{_b64(key)}"


def parse_hash(stored: str):
    """(log2_n, r, p, salt, key) or None when stored is not a hash this
    module wrote (or the cost is outside 2**14 .. 2**20)."""
    try:
        kind, log2_n, r, p, salt, key = stored.strip().split(":")
        log2_n, r, p = int(log2_n), int(r), int(p)
        salt, key = _unb64(salt), _unb64(key)
    except (ValueError, AttributeError):
        return None
    if kind != "scrypt" or not (14 <= log2_n <= 20) or not (1 <= r <= 16) or not (1 <= p <= 4) \
            or len(salt) < 16 or len(key) != KEY_BYTES:
        return None
    return log2_n, r, p, salt, key


def verify(passphrase: str, stored: str) -> bool:
    parsed = parse_hash(stored)
    if parsed is None or not isinstance(passphrase, str):
        return False
    log2_n, r, p, salt, key = parsed
    try:
        got = hashlib.scrypt(passphrase.encode("utf-8", "surrogatepass"), salt=salt, n=2 ** log2_n, r=r, p=p,
                             maxmem=max(SCRYPT_MAXMEM, 256 * r * 2 ** log2_n), dklen=len(key))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(got, key)


def stored_hash() -> str:
    return os.getenv("SETTINGS_PASSPHRASE_HASH", "").strip()


def hash_problem() -> str | None:
    """None when a usable hash is set; else the sentence for the page."""
    h = stored_hash()
    if not h:
        return "Editing is off: SETTINGS_PASSPHRASE_HASH is not set."
    if parse_hash(h) is None:
        return ("Editing is off: SETTINGS_PASSPHRASE_HASH is set but is not a hash "
                "scripts/settings_passphrase.sh wrote.")
    return None


# ── sessions ─────────────────────────────────────────────────────────────────

@dataclass
class Session:
    sid: str
    token: str
    client: str
    created: float = field(default_factory=lambda: time.monotonic())
    last: float = field(default_factory=lambda: time.monotonic())


_sessions: dict[str, Session] = {}
_fails: dict[str, list[float]] = {}       # client -> failure times in the window
_fails_all: list[float] = []
_locked_until: dict[str, float] = {}      # client (or "*" for everyone) -> monotonic time
_verify_lock = asyncio.Lock()


def reset() -> None:
    """Forget every session and failure (tests; a restart does the same)."""
    _sessions.clear()
    _fails.clear()
    _fails_all.clear()
    _locked_until.clear()


def instance() -> str:
    return os.getenv("SETTINGS_INSTANCE", "production")


def cookie_name() -> str:
    return f"kronk_settings_{'dev' if instance() == 'dev' else 'production'}"


def cookie_path() -> str:
    prefix = os.getenv("SETTINGS_URL_PREFIX", "").rstrip("/")
    return f"{prefix}/" if prefix else "/"


def _alive(s: Session, now: float) -> bool:
    return now - s.last <= IDLE_S and now - s.created <= ABSOLUTE_S


def session_of(request: Request) -> Session | None:
    sid = request.cookies.get(cookie_name())
    if not sid:
        return None
    s = _sessions.get(sid)
    now = time.monotonic()
    if s is None:
        return None
    if not _alive(s, now):
        _sessions.pop(sid, None)
        return None
    return s


def session_view(request: Request) -> dict:
    s = session_of(request) if hash_problem() is None else None
    if s is None:
        return {"unlocked": False, "idle_s": IDLE_S, "absolute_s": ABSOLUTE_S}
    s.last = time.monotonic()
    return {"unlocked": True, "token": s.token, "idle_s": IDLE_S, "absolute_s": ABSOLUTE_S}


def _new_session(client: str) -> Session:
    now = time.monotonic()
    for sid in [k for k, v in _sessions.items() if not _alive(v, now)]:
        _sessions.pop(sid, None)
    while len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions.values(), key=lambda x: x.created)
        _sessions.pop(oldest.sid, None)
    s = Session(sid=secrets.token_urlsafe(32), token=secrets.token_urlsafe(32), client=client)
    _sessions[s.sid] = s
    return s


def end_session(request: Request) -> Session | None:
    sid = request.cookies.get(cookie_name())
    return _sessions.pop(sid, None) if sid else None


# ── rate limit ───────────────────────────────────────────────────────────────

def _prune(now: float) -> None:
    for c in list(_fails):
        _fails[c] = [t for t in _fails[c] if now - t <= FAIL_WINDOW_S]
        if not _fails[c]:
            del _fails[c]
    _fails_all[:] = [t for t in _fails_all if now - t <= FAIL_WINDOW_S]
    for c in [c for c, until in _locked_until.items() if until <= now]:
        del _locked_until[c]


def locked_out(client: str) -> float:
    """Seconds left of a lockout that applies to client (0 when none)."""
    now = time.monotonic()
    _prune(now)
    until = max(_locked_until.get(client, 0.0), _locked_until.get("*", 0.0))
    return max(0.0, until - now)


def _record_failure(client: str) -> str | None:
    """Count a failure; the lockout it started ("client" or "global"), if any."""
    now = time.monotonic()
    _prune(now)
    _fails.setdefault(client, []).append(now)
    _fails_all.append(now)
    if len(_fails[client]) >= FAILS_PER_CLIENT and client not in _locked_until:
        _locked_until[client] = now + LOCKOUT_S
        return "client"
    if len(_fails_all) >= FAILS_GLOBAL and "*" not in _locked_until:
        _locked_until["*"] = now + LOCKOUT_S
        return "global"
    return None


async def unlock(passphrase, client: str, audit) -> Session:
    """A new session, or HTTPException 401 (wrong), 429 (locked out), 403
    (editing off). audit(event, **fields) records each outcome."""
    problem = hash_problem()
    if problem:
        raise HTTPException(status_code=403, detail=problem)
    left = locked_out(client)
    if left > 0:
        audit("unlock_refused_locked_out", client=client, seconds_left=round(left))
        raise HTTPException(status_code=429, detail=f"Too many wrong passphrases: unlocking is refused for "
                                                    f"{max(1, round(left / 60))} more minutes.")
    if not isinstance(passphrase, str) or not passphrase or len(passphrase) > MAX_PASSPHRASE_CHARS:
        ok = False
    else:
        async with _verify_lock:
            ok = await asyncio.to_thread(verify, passphrase, stored_hash())
    if not ok:
        started = _record_failure(client)
        n = len(_fails.get(client, []))
        logger.warning("settings: wrong passphrase from %s (%d in the last %d min)", client, n, FAIL_WINDOW_S // 60)
        audit("unlock_failed", client=client, failures_in_window=n)
        if started:
            logger.warning("settings: lockout (%s) for %d min after a failure from %s", started,
                           LOCKOUT_S // 60, client)
            audit("lockout", client=client, scope=started, minutes=LOCKOUT_S // 60)
            raise HTTPException(status_code=429, detail=f"Wrong passphrase. Too many wrong passphrases: "
                                                        f"unlocking is refused for {LOCKOUT_S // 60} minutes.")
        raise HTTPException(status_code=401, detail="Wrong passphrase.")
    s = _new_session(client)
    logger.info("settings: unlocked from %s", client)
    audit("unlock", client=client)
    return s


def set_cookie(response, s: Session) -> None:
    response.set_cookie(cookie_name(), s.sid, httponly=True, samesite="strict", path=cookie_path(),
                        max_age=ABSOLUTE_S)


def clear_cookie(response) -> None:
    response.delete_cookie(cookie_name(), path=cookie_path(), httponly=True, samesite="strict")


# ── the check on every state-changing request ────────────────────────────────

def check_origin(request: Request) -> None:
    """Refuse a request whose Origin is not the page's own (403)."""
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="refused: no Origin header (state-changing requests "
                                                    "come from the /settings page)")
    host = request.headers.get("host", "")
    try:
        o = urlsplit(origin)
    except ValueError:
        o = None
    netloc = (o.netloc if o else "") or ""
    if o is None or o.scheme not in ("http", "https") or not host or \
            netloc.lower() not in {host.lower(), f"{host.lower()}:80"}:
        raise HTTPException(status_code=403, detail=f"refused: Origin {config_files.quote(origin)} is not "
                                                    "this page's origin")
    site = request.headers.get("sec-fetch-site")
    if site and site != "same-origin":
        raise HTTPException(status_code=403, detail=f"refused: Sec-Fetch-Site {config_files.quote(site)}")


def require(request: Request, *, origin: bool = True) -> Session:
    """The unlocked session of a state-changing request, or 403. origin=False
    only for a GET that changes nothing but needs the session (browsers
    send no Origin on a same-origin GET)."""
    if origin:
        check_origin(request)
    problem = hash_problem()
    if problem:
        raise HTTPException(status_code=403, detail=problem)
    s = session_of(request)
    if s is None:
        raise HTTPException(status_code=403, detail="locked: unlock with the passphrase first (the session "
                                                    "ended, or never started)")
    token = request.headers.get(TOKEN_HEADER, "")
    if not hmac.compare_digest(token.encode(), s.token.encode()):
        raise HTTPException(status_code=403, detail="refused: missing or wrong X-Settings-Token")
    s.last = time.monotonic()
    return s


# ── instance to instance ("apply to dev first") ─────────────────────────────
# Production's orchestrator sends a candidate to dev's over the compose
# network. Dev checks a signature made with a key derived from the shared
# SETTINGS_PASSPHRASE_HASH (both instances read it from .env), within
# PEER_SKEW_S, once (nonce). The route also refuses anything nginx proxied.

PEER_SKEW_S = 60
_nonces: dict[str, float] = {}


def _peer_key() -> bytes | None:
    h = stored_hash()
    if parse_hash(h) is None:
        return None
    return hmac.new(h.encode(), b"kronk-settings-peer-v1", hashlib.sha256).digest()


def peer_sign(body: bytes, ts: int, nonce: str) -> str | None:
    key = _peer_key()
    if key is None:
        return None
    return hmac.new(key, f"{ts}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()


def peer_verify(body: bytes, ts: str, nonce: str, sig: str) -> str | None:
    """None when the request is genuine; else why not."""
    key = _peer_key()
    if key is None:
        return "editing is off on this instance (no usable SETTINGS_PASSPHRASE_HASH)"
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return "no timestamp"
    now = time.time()
    if abs(now - t) > PEER_SKEW_S:
        return "timestamp outside the allowed window"
    if not nonce or len(nonce) > 64 or nonce in _nonces:
        return "nonce missing or already used"
    want = hmac.new(key, f"{t}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig or ""):
        return "bad signature (the instances must share SETTINGS_PASSPHRASE_HASH)"
    for n in [n for n, at in _nonces.items() if now - at > 2 * PEER_SKEW_S]:
        _nonces.pop(n, None)
    _nonces[nonce] = now
    return None
