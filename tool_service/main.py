import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List
import httpx
import trafilatura
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# Dual-compat: flat layout in the container (import solar), package import in
# tests (from . import solar).
try:
    from . import solar
except ImportError:
    import solar

# Without this, INFO logs are silently dropped under uvicorn (only WARNING+
# escapes via the last-resort handler) — the /music "full error body goes to
# the log" story never actually logged. Matches health/finance services.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tool_service")

# ── home-location weather cache ──────────────────────────────────────────────
# Refreshed hourly by a background task so the orchestrator can inject fresh
# forecast data straight into the home agent's prompt — answering weather
# questions in ONE LLM round with zero tool calls. Part of the 2026-06
# response-time program (docs/REPORT_2026-06_response_time_program.md).
HOME_LOCATION = os.getenv("HOME_LOCATION", "Laurel, MD")
WEATHER_REFRESH_SEC = int(os.getenv("WEATHER_REFRESH_SEC", "3600"))
WEATHER_CACHE_FILE = Path("/data/weather_cache.json")

_weather_cache: dict = {}  # {"fetched_at": epoch, "location": ..., "data": {...}}


async def _refresh_weather_cache() -> None:
    global _weather_cache
    data = await _fetch_weather(HOME_LOCATION)
    _weather_cache = {"fetched_at": time.time(), "location": HOME_LOCATION, "data": data}
    try:
        WEATHER_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEATHER_CACHE_FILE.write_text(json.dumps(_weather_cache))
    except OSError as e:
        logger.warning("weather cache: could not persist: %s", e)


async def _weather_refresh_loop() -> None:
    while True:
        try:
            await _refresh_weather_cache()
            logger.info("weather cache refreshed for %s", HOME_LOCATION)
        except Exception as e:
            # Keep stale data; the cached endpoint reports its age and the
            # orchestrator falls back to the live tool past the staleness cap.
            logger.warning("weather cache refresh failed (keeping stale): %s", e)
        await asyncio.sleep(WEATHER_REFRESH_SEC)


_solar_last_total: dict = {}


async def _solar_poll_loop() -> None:
    """Poll the PVS every SOLAR_POLL_MIN minutes; record per-inverter
    readings and roll up + confirm multi-day failures
    (docs/plans/SOLAR_MONITOR_PLAN.md). Detection is sync/pure; this loop
    sends the HA alerts for the transitions it returns."""
    while True:
        try:
            inv = solar.parse_inverters(await solar._get_vars("inverter"))
            med = solar.array_median_power(inv)
            solar.record_poll(inv, med)
            _solar_last_total["kw"] = solar.parse_total_kw(await solar._get_vars("livedata"))
            for t in solar.rollup_and_confirm():
                if t["event"] == "confirmed_failing":
                    await solar.notify_ha_failing(t["sn"], t["days"], _solar_last_total.get("kw"))
                elif t["event"] == "recovered":
                    await solar.dismiss_ha(t["sn"])
        except solar.SolarError as e:
            logger.warning("solar poll failed (will retry): %s", e)
        except Exception as e:
            logger.error("solar poll loop error: %s", e)
        await asyncio.sleep(solar.POLL_MIN * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _weather_cache
    # Warm-start from disk so a restart doesn't lose the cache.
    try:
        if WEATHER_CACHE_FILE.exists():
            _weather_cache = json.loads(WEATHER_CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("weather cache: could not load persisted copy: %s", e)
    task = asyncio.create_task(_weather_refresh_loop())
    # Solar monitoring is optional — only wire it up when a serial is
    # configured (keeps it out of the way in tests / unconfigured installs).
    solar_task = None
    if solar.SOLAR_SERIAL:
        solar.init_db()
        solar_task = asyncio.create_task(_solar_poll_loop())
    yield
    task.cancel()
    if solar_task:
        solar_task.cancel()


app = FastAPI(title="Kronk Tool Service", lifespan=lifespan)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
LIST_FILE = Path("/data/shopping_list.json")
GENERATED_DIR = Path("/data/generated")


def load_list() -> dict:
    if LIST_FILE.exists():
        return json.loads(LIST_FILE.read_text())
    return {"items": [], "updated_at": None}


def save_list(data: dict):
    LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    LIST_FILE.write_text(json.dumps(data, indent=2))


class ItemsRequest(BaseModel):
    items: List[str]
FETCH_TOKEN_LIMIT = 4000  # ~16000 chars of page text passed to the model


def extract_page_text(html: str, token_limit: int = FETCH_TOKEN_LIMIT) -> str:
    """Extract the MAIN CONTENT from HTML, truncated to the token budget.

    Primary path is trafilatura (readability-style boilerplate removal):
    it scores DOM regions by link density / text density / semantic tags and
    keeps only the content subtree, with in-content links preserved as
    markdown. Replaces a keep-everything BeautifulSoup pass that spent 79%
    of the token budget on nav-link markdown and truncated an AllRecipes
    page before the ingredients (2026-06-12 incident).

    favor_recall: an LLM consumer tolerates extra noise far better than
    missing content, so bias toward keeping more.

    Fallback: if trafilatura returns nothing (unusual markup), fall back to
    the old whole-page text pass — degraded beats empty.
    """
    text = None
    try:
        text = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            favor_recall=True,
            output_format="markdown",
        )
    except Exception:
        text = None

    if not text or not text.strip():
        # Old subtractive pass — keeps everything except script/style/aside.
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "aside"]):
            tag.decompose()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            a_text = a.get_text(strip=True)
            if a_text and href:
                a.replace_with(f"[{a_text}]({href})")
            elif href:
                a.replace_with(href)
        text = soup.get_text(separator="\n", strip=True)

    text = re.sub(r'\n{3,}', '\n\n', text)
    # Truncate: ~4 chars per token
    max_chars = token_limit * 4
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated]"
    return text.strip()

NWS_HEADERS = {"User-Agent": "Kronk/1.0 (home assistant)", "Accept": "application/geo+json"}


def clean_location(location: str) -> str:
    """Strip US state abbreviations that confuse the geocoder."""
    return re.sub(r',\s*[A-Z]{2}\s*$', '', location).strip()


def fmt_period(p: dict) -> str:
    name = p.get("name", "")
    temp = p.get("temperature", "?")
    unit = p.get("temperatureUnit", "F")
    wind = p.get("windSpeed", "")
    short = p.get("shortForecast", "")
    detail = p.get("detailedForecast", "")
    body = detail if detail else short
    return f"{name}: {temp}°{unit}, {wind} — {body}"


def _check_upstream(resp: httpx.Response, what: str) -> httpx.Response:
    """502 with the failing call named + body snippet, instead of a silent
    empty forecast or a generic 500 (both happened with NWS/Open-Meteo 500s)."""
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"{what} failed (HTTP {resp.status_code}): {resp.text[:200]}",
        )
    return resp


async def _fetch_weather(location: str) -> dict:
    """Geocode + NWS forecast fetch. Shared by /weather and the hourly cache."""
    query = clean_location(location)
    async with httpx.AsyncClient(timeout=15, headers=NWS_HEADERS) as client:
        # Step 1: geocode via Open-Meteo (NWS has no geocoder)
        geo_resp = _check_upstream(await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
        ), "Open-Meteo geocoding")
        geo = geo_resp.json()
        if not geo.get("results"):
            raise HTTPException(status_code=404, detail=f"Location not found: {location}")

        place = geo["results"][0]
        lat = round(place["latitude"], 4)
        lon = round(place["longitude"], 4)

        # Step 2: get NWS grid point for this lat/lon
        points_resp = await client.get(f"https://api.weather.gov/points/{lat},{lon}")
        if points_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"NWS points lookup failed (HTTP {points_resp.status_code}) — "
                       f"location may be outside US coverage: {points_resp.text[:200]}",
            )
        points = points_resp.json()["properties"]

        nws_location = points.get("relativeLocation", {}).get("properties", {})
        city = nws_location.get("city", place["name"])
        state = nws_location.get("state", "")
        full_name = f"{city}, {state}" if state else city

        forecast_url = points["forecast"]
        hourly_url = points["forecastHourly"]
        alerts_url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"

        # Step 3: fetch period forecast, hourly forecast, and alerts in parallel
        period_resp, hourly_resp, alerts_resp = await asyncio.gather(
            client.get(forecast_url),
            client.get(hourly_url),
            client.get(alerts_url),
        )

    # NWS grid endpoints 500 routinely; unchecked, an error body parsed as
    # empty periods and the route returned 200 with no forecast.
    _check_upstream(period_resp, "NWS forecast fetch")
    _check_upstream(hourly_resp, "NWS hourly forecast fetch")
    _check_upstream(alerts_resp, "NWS alerts fetch")
    periods = period_resp.json().get("properties", {}).get("periods", [])
    hourly_periods = hourly_resp.json().get("properties", {}).get("periods", [])
    alerts = alerts_resp.json().get("features", [])

    # Current conditions = first hourly period
    current = hourly_periods[0] if hourly_periods else {}
    current_str = (
        f"{current.get('temperature', '?')}°F, "
        f"{current.get('shortForecast', '')}, "
        f"wind {current.get('windSpeed', '?')} {current.get('windDirection', '')}"
    ) if current else "unavailable"

    # Next 12 hourly periods
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    upcoming_hourly = []
    for p in hourly_periods[:12]:
        start = p.get("startTime", "")
        try:
            dt = datetime.fromisoformat(start)
            hour_label = dt.astimezone().strftime("%-I %p")
        except Exception:
            hour_label = start
        upcoming_hourly.append(
            f"{hour_label}: {p['temperature']}°F, {p['shortForecast']}"
        )

    # Named periods (Today, Tonight, Tomorrow, etc.) — first 6
    # NWS supplies ~14 periods (7 days, day/night). Use them all — "what
    # about next Tuesday?" must be answerable from cached/injected data
    # (2026-06-12 incident: 6 periods covered only ~3 days).
    named_periods = [fmt_period(p) for p in periods[:14]]

    # Active alerts
    alert_strs = []
    for a in alerts[:3]:
        props = a.get("properties", {})
        alert_strs.append(f"{props.get('event', 'Alert')}: {props.get('headline', '')}")

    summary_parts = [
        f"Current conditions in {full_name}: {current_str}",
        "\nHourly forecast:",
        "\n".join(upcoming_hourly),
        "\nExtended forecast:",
        "\n".join(named_periods),
    ]
    if alert_strs:
        summary_parts += ["\nActive weather alerts:", "\n".join(alert_strs)]

    return {
        "location": full_name,
        "current": current_str,
        "summary": "\n".join(summary_parts),
        "alerts": alert_strs,
    }


@app.get("/weather")
async def weather(location: str = Query(..., description="City name or city, state/country")):
    return await _fetch_weather(location)


@app.get("/weather/cached")
async def weather_cached():
    """Hourly-refreshed forecast for the home location.

    Returns the cached data plus its age so callers can apply their own
    staleness policy. 404 only if no fetch has ever succeeded.
    """
    if not _weather_cache.get("data"):
        raise HTTPException(status_code=404, detail="weather cache not yet populated")
    return {
        "location": _weather_cache["location"],
        "fetched_at": _weather_cache["fetched_at"],
        "age_s": round(time.time() - _weather_cache["fetched_at"]),
        **_weather_cache["data"],
    }


@app.get("/search")
async def search(q: str = Query(..., description="Search query"), count: int = 5):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": q, "format": "json", "categories": "general", "language": "en"},
            )
    except httpx.RequestError as e:
        # Network-level failure (container down, DNS, timeout) — used to
        # surface as a generic 500 with no cause.
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach SearXNG: {type(e).__name__}: {e}",
        )
    if resp.status_code != 200:
        logger.error("SearXNG returned HTTP %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=502,
            detail=f"SearXNG returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    try:
        data = resp.json()
    except ValueError:
        logger.error("SearXNG returned non-JSON body: %s", resp.text[:300])
        raise HTTPException(
            status_code=502,
            detail=f"SearXNG returned a non-JSON response: {resp.text[:200]}",
        )

    results = []
    for r in data.get("results", [])[:count]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        })

    if not results:
        raise HTTPException(status_code=404, detail="No results found")

    return {"query": q, "results": results}


_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # No "br": httpx only decompresses brotli with the optional brotli
    # package installed — advertising it without that yields mojibake
    # (community.frame.work served binary garbage, found 2026-06-12).
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}


@app.get("/fetch")
async def fetch(url: str = Query(..., description="URL to fetch and extract text from")):
    """Fetch and extract text from a URL.

    Upstream failures (403, 404, timeouts, DNS) are returned as 200 with
    `{"ok": false, "error": "..."}` so the calling agent sees the failure as
    a normal tool result and can choose a different URL from its search hits.
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=_BROWSER_HEADERS)
        except httpx.TimeoutException:
            return {"url": url, "ok": False, "error": "request timed out"}
        except httpx.RequestError as e:
            return {"url": url, "ok": False, "error": f"network error: {type(e).__name__}"}

    if resp.status_code >= 400:
        return {
            "url": url,
            "ok": False,
            "error": f"HTTP {resp.status_code} {resp.reason_phrase or ''}".strip(),
        }

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return {
            "url": url,
            "ok": False,
            "error": f"unsupported content type: {content_type or 'unknown'}",
        }

    text = extract_page_text(resp.text)
    return {"url": url, "ok": True, "text": text}


@app.get("/shopping_list")
async def get_shopping_list():
    return load_list()


@app.post("/shopping_list")
async def add_items(req: ItemsRequest):
    data = load_list()
    added = []
    for item in req.items:
        item = item.strip()
        if item and item.lower() not in [i.lower() for i in data["items"]]:
            data["items"].append(item)
            added.append(item)
    save_list(data)
    return {"added": added, "items": data["items"]}


@app.delete("/shopping_list/clear")
async def clear_shopping_list():
    data = {"items": [], "updated_at": None}
    save_list(data)
    return {"status": "cleared"}


@app.delete("/shopping_list/{item}")
async def remove_item(item: str):
    data = load_list()
    lower = item.lower()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.lower() != lower]
    if len(data["items"]) == before:
        raise HTTPException(status_code=404, detail=f"Item not found: {item}")
    save_list(data)
    return {"removed": item, "items": data["items"]}


class DiagramRequest(BaseModel):
    dot: str


@app.post("/diagram")
async def generate_diagram(req: DiagramRequest):
    """Render a Graphviz DOT string to PNG and return its URL path."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"diagram-{uuid.uuid4().hex[:8]}.png"
    output_path = GENERATED_DIR / filename

    try:
        result = subprocess.run(
            ["dot", "-Tpng", "-o", str(output_path)],
            input=req.dot.encode(),
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Diagram generation timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="graphviz 'dot' binary not found")

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"dot error: {result.stderr.decode()[:300]}")

    return {"url": f"/static/generated/{filename}"}


HOTTUB_STATUS_FILE = Path("/data/hottub/status.json")


@app.get("/solar/status")
async def solar_status():
    """Live snapshot for the solar_status skill. Detection is deterministic;
    the orchestrator's tool/agent narrates the summary."""
    try:
        return await solar.fetch_snapshot()
    except solar.SolarError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the solar system: {e}")


@app.get("/hottub")
async def hottub_status():
    if not HOTTUB_STATUS_FILE.exists():
        return {"online": None, "error": "no status file — monitor may not be running"}
    try:
        return json.loads(HOTTUB_STATUS_FILE.read_text())
    except Exception as e:
        return {"online": None, "error": str(e)}


# ── MagicMirror (Raspberry Pi over SSH) ──────────────────────────────────────
# Kronk's first cross-machine capability. Transport: `ssh` as user `kronk`
# with a forced-command key — the Pi runs /home/drew/kronk/mm-update.sh (as
# drew, via a sudoers grant pinned to that one script) no matter what the
# client sends; we pick an allowlisted verb via the SSH command field.
# Reference script + Pi-side setup: magicmirror/mm-update.sh. Design:
# docs/plans/MAGICMIRROR_PLAN.md.
#
# An update takes 1-5 min on a Pi (npm install), far past any voice budget,
# so POST /magicmirror/update does a fast preflight (status verb — proves
# reachability, auth, and the script itself), then runs the real update as
# a background task whose outcome lands in /data/mm_update_last.json and
# the log. GET /magicmirror/status reports live state + that last outcome.

MM_SSH_TARGET = os.getenv("MM_SSH_TARGET", "pi@mirror.local")
MM_SSH_KEY    = os.getenv("MM_SSH_KEY", "/keys/kronk-mm-update")
MM_SCRIPT     = os.getenv("MM_SCRIPT", "/magicmirror/mm-update.sh")
MM_REMOTE_DIR = os.getenv("MM_REMOTE_DIR", "kronk")  # ~/kronk on the Pi
MM_LAST_FILE  = Path("/data/mm_update_last.json")
MM_UPDATE_TIMEOUT_S = 600

# Proactive completion announcement via HA's assist_satellite.announce —
# the async half of the "walk away" flow (verified live 2026-07-11 against
# the kitchen Voice PE). Reusable primitive: timers/proactive alerts will
# call _ha_announce too (ROADMAP item 3). Non-fatal: the source of truth is
# always /magicmirror/status; a failed announce is a log line, nothing more.
ANNOUNCE_SATELLITE = os.getenv(
    "ANNOUNCE_SATELLITE",
    "assist_satellite.home_assistant_voice_0ac919_assist_satellite")


async def _ha_announce(message: str, satellite: str = ANNOUNCE_SATELLITE) -> bool:
    """Speak `message` on a satellite outside the conversation flow. Returns
    success; never raises — announcement is a notification layer, not truth."""
    if not HA_TOKEN:
        logger.warning("announce skipped: HA_TOKEN not configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HA_URL}/api/services/assist_satellite/announce",
                headers={"Authorization": f"Bearer {HA_TOKEN}",
                         "Content-Type": "application/json"},
                json={"entity_id": satellite, "message": message})
        if resp.status_code // 100 != 2:
            logger.error("announce failed (HTTP %s): %s",
                         resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.error("announce failed: %s", e)
        return False


def _mm_update_speech(ok: bool, fields: dict, detail: str) -> str:
    """One spoken sentence from the update result. Voice is the friendly
    register — clean wording; the gory detail stays in the status file/log.
    Decisions locked 2026-07-11: no auto-rollback; failure keeps the bad
    state and points at rollback-on-request."""
    if ok:
        # Prefer the friendly semver; the git rev (new=) is for the audit
        # trail, not for speaking aloud ("version 4b4a59534" is a hash).
        ver = fields.get("version") or fields.get("new") or "the latest version"
        n = fields.get("mods_ok")
        mods = f", {n} modules refreshed" if n and n != "0" else ""
        failed = fields.get("mods_failed")
        warn = (f" {failed} modules had trouble updating."
                if failed and failed != "0" else "")
        return f"The magic mirror updated to version {ver}{mods}.{warn}".strip()
    # Failure: name the step if the script gave one, keep the backup, wait
    # for an explicit rollback request.
    step = ""
    m = re.search(r"step=(\S+)", detail)
    if m:
        step = f" at the {m.group(1).replace('-', ' ')} step"
    return (f"The magic mirror update failed{step}. I kept a backup and left "
            "it as it is — ask me to roll it back when you want.")

# The key is a general key now (no forced command — see MAGICMIRROR_PLAN
# "Direction pivot"), so we stage the canonical script and run it by path.
_SSH_OPTS = [
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/data/mm_known_hosts",
    "-i", MM_SSH_KEY,
]


def _parse_kronk_line(raw: str) -> tuple[bool, str, dict]:
    """Find the script's machine-readable last line.
    Returns (ok, line, fields) — fields are the key=value pairs."""
    for line in reversed(raw.strip().splitlines()):
        if line.startswith(("KRONK-OK", "KRONK-FAIL")):
            parts = line.split()
            fields = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
            return line.startswith("KRONK-OK"), line, fields
    return False, "no KRONK status line in output", {}


async def _run(cmd: list, timeout_s: int) -> tuple[int | None, str]:
    """Run a subprocess, return (returncode, combined output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return None, f"timed out after {timeout_s}s"
    except OSError as e:
        return 255, f"could not exec: {e}"
    return proc.returncode, raw.decode(errors="replace")


async def _ssh_mm(verb: str, timeout_s: int) -> tuple[bool, str, dict]:
    """Stage the canonical mm-update.sh to the Pi (scp), then run it with
    the verb. (ok, detail_line, fields). The script is always the current
    repo copy — no manual drops, no version drift."""
    if not Path(MM_SSH_KEY).exists():
        return False, f"SSH key not found at {MM_SSH_KEY} — mount ./secrets/mm", {}
    if not Path(MM_SCRIPT).exists():
        return False, f"updater script not found at {MM_SCRIPT} — mount ./magicmirror", {}

    # 1. ensure ~/kronk exists and stage the script fresh.
    rc, out = await _run(
        ["ssh", *_SSH_OPTS, MM_SSH_TARGET, f"mkdir -p ~/{MM_REMOTE_DIR}"], 15)
    if rc == 255:
        logger.error("mm ssh transport failure: %s", out[:300])
        last = out.strip().splitlines()[-1] if out.strip() else "connection failed"
        return False, f"could not reach the mirror at {MM_SSH_TARGET}: {last}", {}
    remote = f"{MM_SSH_TARGET}:{MM_REMOTE_DIR}/mm-update.sh"
    rc, out = await _run(["scp", *_SSH_OPTS, MM_SCRIPT, remote], 20)
    if rc != 0:
        logger.error("mm scp failed: %s", out[:300])
        return False, f"could not stage the updater script: {out.strip()[:200]}", {}

    # 2. run it by path with the verb.
    rc, text = await _run(
        ["ssh", *_SSH_OPTS, MM_SSH_TARGET,
         f"chmod +x ~/{MM_REMOTE_DIR}/mm-update.sh && "
         f"~/{MM_REMOTE_DIR}/mm-update.sh {verb}"], timeout_s)
    if rc is None:
        return False, f"SSH to {MM_SSH_TARGET} timed out during '{verb}'", {}
    if rc == 255:
        logger.error("mm ssh transport failure: %s", text[:300])
        last = text.strip().splitlines()[-1] if text.strip() else "connection failed"
        return False, f"could not reach the mirror at {MM_SSH_TARGET}: {last}", {}
    ok, line, fields = _parse_kronk_line(text)
    if not ok:
        logger.error("mm verb %s failed (rc=%s): %s", verb, rc, text[-500:])
    return ok, line, fields


async def _run_mm_update() -> None:
    """Background task: the real update. Outcome → file + log (the voice
    reply already went out; this is where the truth lands — tenet 6 is
    served by GET /magicmirror/status reading it back)."""
    ok, line, fields = await _ssh_mm("update", MM_UPDATE_TIMEOUT_S)
    outcome = {"ok": ok, "detail": line, "fields": fields,
               "finished_at": time.time()}
    try:
        tmp = MM_LAST_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(outcome))
        os.replace(tmp, MM_LAST_FILE)
    except OSError as e:
        logger.error("could not persist mm update outcome: %s", e)
    (logger.info if ok else logger.error)("mm update finished: %s", line)
    # Close the loop: proactively announce the outcome on the Voice PE. The
    # synchronous "updating now" ack already went out at request time; this
    # is the walk-away completion notification.
    speech = _mm_update_speech(ok, fields, line)
    announced = await _ha_announce(speech)
    logger.info("mm update announce %s: %s",
                "sent" if announced else "FAILED", speech)


@app.post("/magicmirror/update")
async def magicmirror_update():
    ok, line, fields = await _ssh_mm("status", 20)
    if not ok:
        raise HTTPException(status_code=502,
                            detail=f"Mirror preflight failed: {line}")
    asyncio.get_running_loop().create_task(_run_mm_update())
    return {
        "status": "started",
        "current_version": fields.get("version"),
        "current_rev": fields.get("rev"),
        "message": (f"updating from version {fields.get('version', '?')} — "
                    "a full backup is taken first; this takes a few minutes"),
    }


@app.get("/magicmirror/status")
async def magicmirror_status():
    ok, line, fields = await _ssh_mm("status", 20)
    last = None
    try:
        last = json.loads(MM_LAST_FILE.read_text())
    except (OSError, ValueError):
        pass
    if not ok:
        raise HTTPException(status_code=502, detail=f"Mirror unreachable: {line}")
    return {"live": fields, "last_update": last}


# ── Home Assistant REST config ───────────────────────────────────────────────
# Shared by /music playback and the MagicMirror completion announce. Timers
# were decommissioned 2026-07-12 — HA Assist handles them natively on the
# Voice PE (local intent, on-device countdown; never reached Kronk). See
# ROADMAP item 3 / docs/incidents.

HA_URL   = os.getenv("HA_URL",   "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")


# ── Music Assistant proxy ────────────────────────────────────────────────────
# Calls HA's `music_assistant.play_media` action. MA runs its own fuzzy search
# across providers for the media string, so `media_id` is free text ("pink
# floyd", "wish you were here"). Player resolution is a fixed env-configured
# map of spoken name → HA entity_id of the *Music Assistant* player entity
# (platform music_assistant — NOT the native Sonos/Cast entity, which MA
# cannot drive).
#
# play_media returns 200 as soon as MA queues the request; provider failures
# (expired YouTube Music auth, etc.) happen asynchronously during stream
# start. So success here is defined as "the player actually reached
# `playing`", verified by polling — a 200 from HA alone is not success.

MUSIC_DEFAULT_PLAYER = os.getenv("MUSIC_DEFAULT_PLAYER", "")
# Format: "sonos move:media_player.a, kitchen:media_player.b"
MUSIC_PLAYERS = {
    name.strip().lower(): entity.strip()
    for name, _, entity in (
        pair.partition(":") for pair in os.getenv("MUSIC_PLAYERS", "").split(",") if ":" in pair
    )
}

MUSIC_VERIFY_TIMEOUT_S = 8   # how long to wait for the player to reach `playing`


class MusicRequest(BaseModel):
    query: str
    media_type: str | None = None   # artist | album | track | playlist | radio
    player: str | None = None       # spoken player name; None → default player


def _resolve_player(spoken: str | None) -> tuple[str, str] | None:
    """Resolve a spoken player name → (entity_id, speakable label)."""
    if not spoken:
        if not MUSIC_DEFAULT_PLAYER:
            return None
        for name, entity in MUSIC_PLAYERS.items():
            if entity == MUSIC_DEFAULT_PLAYER:
                return MUSIC_DEFAULT_PLAYER, f"the {name} speaker"
        return MUSIC_DEFAULT_PLAYER, "the default speaker"
    key = spoken.strip().lower()
    if key in MUSIC_PLAYERS:
        return MUSIC_PLAYERS[key], f"the {key} speaker"
    # tolerate partial names ("the sonos" / "sonos move speaker")
    for name, entity in MUSIC_PLAYERS.items():
        if name in key or key in name:
            return entity, f"the {name} speaker"
    return None


@app.post("/music")
async def play_music(req: MusicRequest):
    if not HA_TOKEN:
        raise HTTPException(status_code=500, detail="HA_TOKEN not configured")
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    resolved = _resolve_player(req.player)
    if resolved is None:
        known = ", ".join(sorted(MUSIC_PLAYERS)) or "none configured"
        raise HTTPException(
            status_code=400,
            detail=f"Unknown speaker '{req.player}'. Known speakers: {known}.",
        )
    entity, label = resolved

    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        # Pre-check: catch a powered-off / missing player with a clear message
        # instead of a misleading 200 from the service call.
        state_resp = await client.get(f"{HA_URL}/api/states/{entity}", headers=headers)
        if state_resp.status_code == 404:
            raise HTTPException(status_code=503, detail=f"Player entity '{entity}' does not exist in Home Assistant.")
        if state_resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"HA state check returned {state_resp.status_code}.")
        friendly = state_resp.json().get("attributes", {}).get("friendly_name", entity)
        if state_resp.json().get("state") == "unavailable":
            raise HTTPException(
                status_code=503,
                detail=f"The speaker '{friendly}' is unavailable — it may be powered off or asleep.",
            )

        payload = {"entity_id": entity, "media_id": req.query}
        if req.media_type:
            payload["media_type"] = req.media_type
        resp = await client.post(
            f"{HA_URL}/api/services/music_assistant/play_media",
            headers=headers, json=payload,
        )
        if resp.status_code >= 400:
            # Full body (often an HTML error page) goes to the log only —
            # the detail string ends up spoken aloud by the voice pipeline.
            logger.warning("play_media failed (%s): %s", resp.status_code, resp.text[:300])
            raise HTTPException(
                status_code=502,
                detail=f"Music Assistant rejected the request (HTTP {resp.status_code}).",
            )

        # Verify playback actually started (see header comment).
        deadline = asyncio.get_event_loop().time() + MUSIC_VERIFY_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            state = (await client.get(f"{HA_URL}/api/states/{entity}", headers=headers)).json()
            if state.get("state") == "playing":
                attrs = state.get("attributes", {})
                return {
                    "status": "playing",
                    "player": label,
                    "artist": attrs.get("media_artist"),
                    "title":  attrs.get("media_title"),
                }

    raise HTTPException(
        status_code=502,
        detail=(
            "Music Assistant accepted the request but playback did not start "
            f"on '{friendly}' within {MUSIC_VERIFY_TIMEOUT_S}s — the music "
            "provider may need re-authentication in Music Assistant."
        ),
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
