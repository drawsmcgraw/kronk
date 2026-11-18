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
