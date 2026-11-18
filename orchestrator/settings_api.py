"""HTTP surface of the /settings page (sprint M21 step 2;
docs/features/config-directory.md). Mounted by main.py.

Read routes (no cookie, change nothing): GET /settings, /api/settings,
/api/settings/file, /api/settings/history, /api/settings/diff,
/api/settings/digests. Everything is JSON; the page puts every string on
the screen as text.

Each instance edits its own live directory, mounted at /config:
/settings is production's, /dev/settings dev's (nginx strips /dev/).
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config_files
import config_loader
import settings_git
import settings_store
import settings_view
import tools

logger = logging.getLogger(__name__)
router = APIRouter()

STATIC = Path(os.getenv("STATIC_DIR", "/app/static"))


def base_dir() -> Path:
    return config_loader.config_dir()


def client_of(request: Request) -> str:
    """The client's address: nginx's X-Real-IP, else the socket peer."""
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    return config_files.safe(ip, 64)


def editing_status(base: Path) -> dict:
    """Whether this instance can edit, and if not, why and how to turn it on."""
    import settings_auth
    reasons = []
    hash_problem = settings_auth.hash_problem()
    if hash_problem:
        reasons.append(hash_problem)
    git = settings_git.health(base)
    if git:
        reasons.append(f"Editing is off: {git}.")
    if not os.access(base, os.W_OK):
        reasons.append(f"Editing is off: the live config directory {base} is mounted read-only.")
    how = ("To turn editing on: run scripts/settings_passphrase.sh on kronk (it asks for the passphrase "
           "twice and writes SETTINGS_PASSPHRASE_HASH to .env), then redeploy: "
           + ("scripts/deploy_tag.sh deploy <tag>." if settings_view.instance() == "production"
              else "the dev up line (docker compose --profile dev up -d tool_service-dev orchestrator-dev)."))
    return {"enabled": not reasons, "reason": " ".join(reasons), "how": how if reasons else ""}


def ts_client(timeout: float = 5) -> httpx.AsyncClient:
    """The client for tool_service over the compose network (tests swap in
    an in-process transport)."""
    return httpx.AsyncClient(base_url=tools.TOOL_SERVICE_URL, timeout=timeout)


async def tool_service_get(path: str, timeout: float = 5) -> dict | None:
    try:
        async with ts_client(timeout) as client:
            r = await client.get(path)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("settings: tool_service %s unavailable: %s", path, config_files.safe(e, 200))
        return None


def _status_problems(statuses: list[dict]) -> tuple[list[dict], list[dict]]:
    errors, warnings = [], []
    for st in statuses:
        svc = st.get("service", "?")
        for e in st.get("errors") or []:
            errors.append({"service": svc, **e, "kept": st.get("kept")})
        for w in st.get("warnings") or []:
            warnings.append({"service": svc, **w})
    return errors, warnings


@router.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse((STATIC / "settings.html").read_text())


@router.get("/api/settings")
async def settings_data(request: Request):
    import settings_auth
    base = base_dir()
    ts_eff, ts_status = await asyncio.gather(tool_service_get("/config/effective"),
                                             tool_service_get("/config/status"))
    git_msg = await asyncio.to_thread(settings_git.health, base)
    git = {"ok": git_msg is None, "message": git_msg, "head": None}
    if git_msg is None:
        try:
            git["head"] = (await asyncio.to_thread(settings_git.head, base))[:7]
        except settings_git.GitError as e:
            git = {"ok": False, "message": str(e), "head": None}
    statuses = [config_loader.status(), ts_status or {
        "service": "tool_service", "errors": [{"file": None, "line": None,
                                               "message": "tool_service config status unavailable"}]}]
    errors, warnings = _status_problems(statuses)
    files = await asyncio.to_thread(settings_view.file_entries, base, ts_eff, git["ok"])
    dev = await dev_digests()
    for f in files:
        if dev is None:
            continue
        if "error" in dev:
            f["dev"] = {"error": dev["error"]}
        else:
            theirs = (dev.get("files") or {}).get(f["name"])
            f["dev"] = {"blob": theirs, "differs": theirs != f["blob"]}
    out = {
        "instance": settings_view.instance(),
        "release": settings_view.release(),
        "dir": config_files.safe(base, 200),
        "git": git,
        "editing": editing_status(base),
        "session": settings_auth.session_view(request),
        "config_errors": errors,
        "config_warnings": warnings,
        "files": files,
        "dev_url": bool(os.getenv("SETTINGS_DEV_URL")) and settings_view.instance() == "production",
        "readonly": settings_view.readonly(ts_eff),
    }
    return JSONResponse(config_files.renderable(out, lambda s: {"detail": s}))


def _name(name: str) -> str:
    try:
        settings_store.check_name(name)
    except settings_store.StoreError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return name


@router.get("/api/settings/file")
async def settings_file(name: str):
    """The file's text as on disk, and its version (git blob id) for the
    editor's conflict check."""
    _name(name)
    base = base_dir()
    try:
        raw = settings_store.read_bytes(base, name)
    except settings_store.StoreError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if raw is None:
        return {"name": name, "text": "", "blob": settings_git.blob_sha(b""), "exists": False}
    head = None
    if settings_git.health(base) is None:
        head = settings_git.head(base)
    return {"name": name, "text": raw.decode("utf-8", "replace"), "blob": settings_git.blob_sha(raw),
            "exists": True, "head": head}


@router.get("/api/settings/digests")
async def settings_digests():
    """Each file's version (git blob id) on disk, for a peer instance's
    "dev differs" mark."""
    base = base_dir()
    out = {}
    for name in settings_store.EDITABLE:
        try:
            raw = settings_store.read_bytes(base, name)
        except settings_store.StoreError:
            raw = None
        out[name] = settings_git.blob_sha(raw) if raw is not None else None
    return {"instance": settings_view.instance(), "files": out}


@router.get("/api/settings/history")
async def settings_history(name: str):
    _name(name)
    base = base_dir()
    msg = settings_git.health(base)
    if msg:
        raise HTTPException(status_code=409, detail=f"no history: {msg}")
    commits = await asyncio.to_thread(settings_git.file_log, base, name)
    for c in commits:
        c["revertable"] = settings_git.parent(base, c["commit"]) is not None
    return {"name": name, "commits": commits}


@router.get("/api/settings/diff")
async def settings_diff(name: str, commit: str):
    _name(name)
    base = base_dir()
    msg = settings_git.health(base)
    if msg:
        raise HTTPException(status_code=409, detail=f"no history: {msg}")
    try:
        diff = await asyncio.to_thread(settings_git.commit_diff, base, commit, name)
    except settings_git.GitError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Decoded with "replace", so it holds no lone surrogate and encodes as JSON.
    return {"name": name, "commit": commit, "diff": diff[:200_000]}


# ── unlock and lock (criteria 5, 6) ──────────────────────────────────────────

def audit(event: str, **fields) -> dict:
    import settings_audit
    return settings_audit.record(event, **fields)


@router.post("/api/settings/unlock")
async def settings_unlock(request: Request):
    """{"passphrase": ...} -> a session cookie and the page's second token.
    401 wrong, 429 locked out, 403 editing off or wrong Origin."""
    import settings_auth
    settings_auth.check_origin(request)
    client = client_of(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    passphrase = body.get("passphrase") if isinstance(body, dict) else None
    session = await settings_auth.unlock(passphrase, client, audit)
    del passphrase, body
    response = JSONResponse({"ok": True, "token": session.token, "idle_s": settings_auth.IDLE_S,
                             "absolute_s": settings_auth.ABSOLUTE_S})
    settings_auth.set_cookie(response, session)
    return response


@router.post("/api/settings/lock")
async def settings_lock(request: Request):
    """End this browser's session (no token needed: locking only takes
    rights away)."""
    import settings_auth
    settings_auth.check_origin(request)
    s = settings_auth.end_session(request)
    if s is not None:
        audit("lock", client=client_of(request))
    response = JSONResponse({"ok": True, "message": "locked"})
    settings_auth.clear_cookie(response)
    return response


# ── save (criteria 7, 8, 10, 24) ─────────────────────────────────────────────

def who(request: Request) -> str:
    return f"{settings_view.instance()} page, client {client_of(request)}"


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="the request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the request body must be a JSON object")
    return body


def _refused(e) -> JSONResponse:
    return JSONResponse(config_files.renderable(e.body, lambda s: {"ok": False, "detail": s}), status_code=e.status)


@router.post("/api/settings/check")
async def settings_check(request: Request):
    """Would this text save? Validation, the keys it changes and (music
    policy, lead-in) the corpus check. Writes nothing."""
    import settings_auth
    import settings_save
    settings_auth.require(request)
    body = await _json(request)
    _name(body.get("name"))
    result = await settings_save.check(body["name"], body.get("text"))
    return JSONResponse(config_files.renderable(result, lambda s: {"ok": False, "detail": s}),
                        status_code=200 if result["ok"] else 422)


@router.post("/api/settings/save")
async def settings_save_route(request: Request):
    """{"name", "text", "base" (the version the editor loaded), "overrides"
    (corpus row ids the editor confirmed)} -> saved, or refused with why."""
    import settings_auth
    import settings_save
    settings_auth.require(request)
    body = await _json(request)
    overrides = body.get("overrides") or []
    if not isinstance(overrides, list):
        raise HTTPException(status_code=400, detail="overrides must be a list of corpus row ids")
    try:
        result = await settings_save.save(body.get("name"), body.get("text"), body.get("base"),
                                          who(request), client_of(request), overrides=overrides)
    except settings_save.SaveRefused as e:
        return _refused(e)
    return JSONResponse(config_files.renderable(result, lambda s: {"ok": True, "detail": s}))



# ── production and dev (criterion 13) ───────────────────────────────────────
# Production's page reaches dev's orchestrator over the compose network
# (SETTINGS_DEV_URL). Reads (digests, a file) need nothing on dev's side;
# the one write, "apply to dev first", is signed (settings_auth.peer_sign)
# and goes through dev's own save path. "Bring over from dev" fetches dev's
# file into production's editor; saving it is production's own save.

PEER_HEADERS = ("x-kronk-peer-ts", "x-kronk-peer-nonce", "x-kronk-peer-sig")


def dev_url() -> str | None:
    if settings_view.instance() != "production":
        return None
    return (os.getenv("SETTINGS_DEV_URL") or "").rstrip("/") or None


def dev_client(timeout: float = 5) -> httpx.AsyncClient:
    """The client for dev's orchestrator (tests swap in a transport)."""
    return httpx.AsyncClient(base_url=dev_url(), timeout=timeout)


async def dev_digests() -> dict | None:
    if not dev_url():
        return None
    try:
        async with dev_client(3) as c:
            r = await c.get("/api/settings/digests")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": f"dev did not answer ({config_files.safe(e, 120)})"}


@router.get("/api/settings/dev_file")
async def settings_dev_file(request: Request, name: str):
    """Dev's copy of a file, for "bring over from dev" (production only)."""
    import settings_auth
    settings_auth.require(request, origin=False)
    _name(name)
    if not dev_url():
        raise HTTPException(status_code=404, detail="this instance has no dev instance to bring a file from")
    try:
        async with dev_client(5) as c:
            r = await c.get("/api/settings/file", params={"name": name})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"dev did not answer ({config_files.safe(e, 120)})")


@router.post("/api/settings/apply_dev")
async def settings_apply_dev(request: Request):
    """Send a candidate to the dev instance only (production unchanged).
    Dev validates, writes, reloads, verifies and commits it through its own
    save path, and answers as its save does."""
    import json as _json_mod
    import secrets
    import time as _time
    import settings_auth
    settings_auth.require(request)
    body = await _json(request)
    name = _name(body.get("name"))
    if not dev_url():
        raise HTTPException(status_code=404, detail="this instance has no dev instance to apply to")
    payload = _json_mod.dumps({"name": name, "text": body.get("text"), "overrides": body.get("overrides") or [],
                               "who": who(request), "client": client_of(request)}).encode()
    ts, nonce = int(_time.time()), secrets.token_hex(16)
    sig = settings_auth.peer_sign(payload, ts, nonce)
    if sig is None:
        raise HTTPException(status_code=403, detail=settings_auth.hash_problem() or "editing is off")
    try:
        async with dev_client(180) as c:
            r = await c.post("/api/settings/peer/save", content=payload, headers={
                "content-type": "application/json", PEER_HEADERS[0]: str(ts), PEER_HEADERS[1]: nonce,
                PEER_HEADERS[2]: sig})
        answer = r.json()
    except Exception as e:
        audit("apply_dev_failed", client=client_of(request), file=name, reason=f"dev unreachable: {e}")
        raise HTTPException(status_code=502, detail=f"dev did not answer ({config_files.safe(e, 120)}); "
                                                    "nothing changed on production")
    audit("apply_dev" if r.status_code == 200 else "apply_dev_refused", client=client_of(request), file=name,
          keys=answer.get("keys"), commit=answer.get("commit"), outcome=str(r.status_code),
          reason=None if r.status_code == 200 else answer.get("message"))
    if r.status_code == 200:
        answer["message"] = f"dev: {answer.get('message')}. Production is unchanged; dev now differs for {name}."
    else:
        answer["message"] = f"dev refused it: {answer.get('message') or answer.get('detail')}. Production is unchanged."
    return JSONResponse(config_files.renderable(answer, lambda s: {"ok": False, "detail": s}),
                        status_code=r.status_code)


@router.post("/api/settings/peer/save")
async def settings_peer_save(request: Request):
    """Dev's side of "apply to dev first": a signed candidate from
    production's orchestrator over the compose network, never through nginx."""
    import config_api
    import settings_auth
    import settings_save
    config_api.refuse_if_proxied(request, "/api/settings/peer/save")
    raw = await request.body()
    why = settings_auth.peer_verify(raw, *(request.headers.get(h) for h in PEER_HEADERS))
    if why:
        audit("peer_refused", client=client_of(request), reason=why)
        raise HTTPException(status_code=403, detail=f"refused: {why}")
    import json as _json_mod
    try:
        body = _json_mod.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="the request body must be JSON")
    name = _name(body.get("name"))
    try:
        current = settings_store.read_bytes(base_dir(), name)
    except settings_store.StoreError as e:
        raise HTTPException(status_code=409, detail=str(e))
    base = settings_git.blob_sha(current if current is not None else b"")
    try:
        result = await settings_save.save(
            name, body.get("text"), base, f"{config_files.safe(body.get('who'), 120)}, applied to dev first",
            config_files.safe(body.get("client"), 64), overrides=body.get("overrides") or [],
            event="apply_from_production")
    except settings_save.SaveRefused as e:
        return _refused(e)
    return JSONResponse(config_files.renderable(result, lambda s: {"ok": True, "detail": s}))
