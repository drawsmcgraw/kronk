"""HTTP surface of config/ (sprint M21 step 1): POST /config/reload and
GET /api/config. Mounted by main.py with app.include_router.

/config/reload is for the host and the compose network only. nginx refuses
/config/ and /dev/config/ (nginx.conf). Behind that, refuse_if_proxied()
turns away anything nginx forwarded: nginx sets X-Real-IP on every request
it proxies (`location /` and `location /dev/`), so a request carrying it, or
X-Forwarded-For, came from the LAN. That second check covers the window
before an nginx restart loads the deny rule, and any proxy added later.
"""
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import config_loader
import tools

router = APIRouter()


def refuse_if_proxied(request: Request, what: str) -> None:
    if request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for"):
        raise HTTPException(status_code=403,
                            detail=f"{what} is not served through nginx; call it from the "
                                   "host (scripts/config_reload.sh) or the compose network")


@router.post("/config/reload")
async def config_reload(request: Request):
    """Re-read config/. 200 with what changed, or 422 with the errors (file
    and line) and the last good values still in effect."""
    refuse_if_proxied(request, "/config/reload")
    result = config_loader.reload()
    return JSONResponse(result, status_code=200 if result["ok"] else 422)


@router.get("/api/config")
async def config_status():
    """config/ load status of the orchestrator and tool_service, for the
    /services page: source in effect, files, and the last error."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{tools.TOOL_SERVICE_URL}/config/status")
            resp.raise_for_status()
            tool = resp.json()
    except Exception as e:
        tool = {"service": "tool_service", "ok": False, "event": "status",
                "errors": [{"file": None, "line": None,
                            "message": f"tool_service config status unavailable: {e}"}]}
    return {"services": [config_loader.status(), tool]}
