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

# Dual-compat: flat layout in the container (import x), package import in
# tests (from . import x). The relative form also avoids the repo-root ops/
# registry dir shadowing the ops module on the test sys.path.
try:
    from . import news
    from . import ops
    from . import solar
except ImportError:
    import news
    import ops
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
            live = await solar._get_vars("livedata")   # one fetch: power + energy
            _solar_last_total["kw"] = solar.parse_total_kw(live)
            # Snapshot ALL lifetime counters for past-period queries — the
            # PVS keeps no history of its own (site_load/net recorded as
            # baseline even while the install mirrors them; SOLAR_VIZ_PLAN).
            counters = solar.parse_counters(live)
            solar.record_energy(counters["pv_en"],
                                site_load_en=counters["site_load_en"],
                                net_en=counters["net_en"])
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
    # News editions (docs/plans/NEWS_BRIEF_PLAN.md) — gated the same way
    # tests expect: no loop unless explicitly enabled (default on in the
    # container via compose env).
    news_task = None
    if os.getenv("NEWS_ENABLED", "1") == "1":
        news_task = asyncio.create_task(news.refresh_loop())
    yield
    task.cancel()
    if solar_task:
        solar_task.cancel()
    if news_task:
        news_task.cancel()


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


@app.get("/solar/detail")
async def solar_detail():
    """Per-inverter breakdown + short history for analytical questions
    ("which inverters, why did the count change, is one getting worse")."""
    try:
        return await solar.fetch_detail()
    except solar.SolarError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the solar system: {e}")


@app.get("/solar/energy")
async def solar_energy(period: str = "lifetime"):
    """Energy produced. period='lifetime' (live counter, system + per-inverter)
    or a past period ('today', 'yesterday', 'week', 'month', 'since:YYYY-MM-DD')
    computed by diffing stored counter snapshots."""
    try:
        if period.strip().lower() == "lifetime":
            return await solar.fetch_lifetime()
        return await solar.energy_for_period(period)
    except solar.SolarError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the solar system: {e}")


@app.get("/solar/series")
async def solar_series(days: int = 7, inverter: str | None = None):
    """Aggregated history for the /solar dashboard (SOLAR_VIZ_PLAN).
    Read-only; heavy lifting is SQL-side so the payload stays small."""
    days = max(1, min(days, 365))
    payload = {
        "window_days":      days,
        "power":            solar.power_series(days, inverter),
        "daily":            solar.daily_energy(days),
        "consumption_real": solar.consumption_data_real(),
    }
    if inverter:
        payload["inverter"] = inverter
    else:
        payload["heatmap"] = solar.heatmap(days)
    return payload


# ── News brief (docs/plans/NEWS_BRIEF_PLAN.md) ──────────────────────────────
# The cached edition, served verbatim by the coordinator's terminal
# news_brief tool. Generation happens in news.refresh_loop(); this endpoint
# never generates — a request must stay a cache read (voice budget).

@app.get("/news/brief")
async def news_brief_get():
    rec = news.load_record()
    if not rec:
        raise HTTPException(
            status_code=503,
            detail="no news brief has been generated yet — "
                   "check tool_service logs for feed or LLM errors")
    return {**rec, "age_min": int((time.time() - rec["generated_ts"]) // 60)}


@app.post("/news/refresh")
async def news_refresh():
    """Force a regeneration (deploy verification, operator use)."""
    try:
        rec = await news.generate()
    except news.NewsError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "ok", "edition": rec["edition"],
            "generated_at_local": rec["generated_at_local"]}


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
        # Dirty-skipped modules are spoken by NAME — a bare skip count is how
        # a pinned active module stayed invisible for a month (RAIN-MAP,
        # INVESTIGATION_2026-08-14_mm_banner.md).
        dirty = fields.get("mods_dirty")
        if dirty:
            names = dirty.split(",")
            verb = "was" if len(names) == 1 else "were"
            warn += (f" {' and '.join(names)} {verb} skipped — "
                     "local changes need a look.")
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


# ── General ops on managed hosts (Phase A: read-only) ───────────────────────
# The devops agent's remote_exec tool lands here. The classifier (ops.py) is
# the safety core — deterministic, server-side. Mutations are refused until
# phase B adds the confirmation gate. docs/plans/MAGICMIRROR_PLAN.md.

OPS_EXEC_TIMEOUT_S = 30


class OpsExecRequest(BaseModel):
    host: str
    command: str


@app.post("/ops/exec")
async def ops_exec(req: OpsExecRequest):
    entry = ops.get_host(req.host)
    if not entry:
        known = ", ".join(ops.load_registry()) or "(none configured)"
        raise HTTPException(status_code=404,
                            detail=f"unknown host {req.host!r} — known hosts: {known}")
    allowed, reason = ops.classify_readonly(req.command)
    if not allowed:
        ops.audit_exec(req.host, req.command, None, 0, allowed=False, note=reason)
        raise HTTPException(status_code=422,
                            detail=f"refused: {reason}")
    if not Path(entry["key"]).exists():
        raise HTTPException(status_code=500,
                            detail=f"SSH key not found at {entry['key']}")

    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=/data/mm_known_hosts",
           "-i", entry["key"], entry["ssh_target"], req.command]
    rc, text = await _run(cmd, OPS_EXEC_TIMEOUT_S)
    ops.audit_exec(req.host, req.command, rc, len(text), allowed=True)
    if rc is None:
        raise HTTPException(status_code=504,
                            detail=f"command timed out after {OPS_EXEC_TIMEOUT_S}s on {req.host}")
    if rc == 255:
        logger.error("ops exec transport failure on %s: %s", req.host, text[:300])
        last = text.strip().splitlines()[-1] if text.strip() else "connection failed"
        raise HTTPException(status_code=502,
                            detail=f"could not reach {req.host}: {last}")
    return {"host": req.host, "command": req.command, "exit_code": rc,
            "output": text[:8000]}


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
# floyd", "wish you were here").
#
# Players are discovered from HA at request time (docs/plans/
# MUSIC_PLAYERS_FROM_HA_PLAN.md): one template call lists every media_player
# the Music Assistant integration registered — exactly the set MA can drive
# (the native Sonos/Cast entities are not in it) — with friendly name, area
# and state. Resolution follows the MA voice blueprint's own order so the
# fast local tier and this tier agree: spoken player name → spoken area →
# origin area → MUSIC_DEFAULT_PLAYER. Membership is by integration, not by
# the `mass_player_type` attribute: an unavailable player drops its
# attributes (observed 2026-09-04). `area_name(entity)` resolves through the
# device when the entity has no area of its own — how the satellites are set.
#
# play_media returns 200 as soon as MA queues the request; provider failures
# (expired YouTube Music auth, etc.) happen asynchronously during stream
# start. So success here is defined as "a target actually reached
# `playing`", verified by polling — a 200 from HA alone is not success.

# The one preference discovery can't answer: where music goes when the
# request names no speaker/room and has no origin (the web UI).
MUSIC_DEFAULT_PLAYER = os.getenv("MUSIC_DEFAULT_PLAYER", "")

MUSIC_VERIFY_TIMEOUT_S = 8   # how long to wait for a player to reach `playing`

_PLAYERS_TEMPLATE = (
    "{% set ns = namespace(out=[]) %}"
    "{% for e in integration_entities('music_assistant') if e.startswith('media_player.') %}"
    "{% set ns.out = ns.out + [{'entity_id': e, 'name': state_attr(e, 'friendly_name'), "
    "'area': area_name(e), 'state': states(e)}] %}"
    "{% endfor %}{{ ns.out | to_json }}"
)


class MusicRequest(BaseModel):
    query: str
    media_type: str | None = None   # artist | album | track | playlist | radio
    player: str | None = None       # spoken speaker OR room, as the user said it
    origin_area: str | None = None  # area the request came from (voice satellite); reserved


def parse_players(raw: str) -> list[dict]:
    """HA template output → [{entity_id, name, area, state}] (ValueError if not a list)."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("player list is not a JSON array")
    out = []
    for p in data:
        if not isinstance(p, dict) or not p.get("entity_id"):
            continue
        out.append({
            "entity_id": p["entity_id"],
            "name": p.get("name") or p["entity_id"],
            "area": p.get("area") or None,
            "state": p.get("state") or "unknown",
        })
    return out


def _norm(s: str | None) -> str:
    """Case/punctuation-insensitive key: 'the Office speaker' → 'office'."""
    s = " ".join((s or "").lower().replace("-", " ").replace("_", " ").split())
    s = re.sub(r"^(the|my)\s+", "", s)
    s = re.sub(r"\s+(speakers?|players?|room)$", "", s)
    return s


def _label(targets: list[dict], area: str | None) -> str:
    if area:
        return f"the {area} speaker{'s' if len(targets) > 1 else ''}"
    return f"the {targets[0]['name']} speaker"


def _by_area(players: list[dict], area: str) -> list[dict]:
    return [p for p in players if p["area"] and _norm(p["area"]) == _norm(area)]


def resolve_players(spoken: str | None, origin_area: str | None,
                    players: list[dict], default_entity: str) -> tuple[list[dict], str]:
    """Blueprint order: player name → area name → origin area → default.

    Returns (targets, speakable label). Raises HTTPException carrying the
    sentence the voice pipeline will speak.
    """
    if not players:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant lists no Music Assistant players — is Music Assistant running and connected?",
        )
    if spoken and spoken.strip():
        key = _norm(spoken)
        areas = sorted({p["area"] for p in players if p["area"]}, key=str.lower)

        def _pick_name(cands: list[dict]) -> tuple[list[dict], str] | None:
            if len(cands) > 1:
                names = ", ".join(p["name"] for p in cands)
                raise HTTPException(
                    status_code=400,
                    detail=f"'{spoken}' matches more than one speaker: {names}. Say which one.",
                )
            return (cands, _label(cands, None)) if cands else None

        def _pick_area(hit: list[str]) -> tuple[list[dict], str] | None:
            if len(hit) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{spoken}' matches more than one room: {', '.join(hit)}. Say which one.",
                )
            if not hit:
                return None
            targets = _by_area(players, hit[0])
            return targets, _label(targets, hit[0])

        # The blueprint matches exactly, name before area. Substring tolerance
        # ("the sonos", "sonos move speaker") comes AFTER both exact rungs so
        # a room name inside a device name ("kitchen" in "kitchen-voice-pe-ma")
        # never steals a room request from the area.
        if picked := _pick_name([p for p in players if _norm(p["name"]) == key]):
            return picked                                                   # 1. exact player name
        if picked := _pick_area([a for a in areas if _norm(a) == key]):
            return picked                                                   # 2. exact area
        if key and (picked := _pick_name([p for p in players
                                          if key in _norm(p["name"]) or _norm(p["name"]) in key])):
            return picked                                                   # 3. substring player name
        if key and (picked := _pick_area([a for a in areas
                                          if key in _norm(a) or _norm(a) in key])):
            return picked                                                   # 4. substring area
        known_names = ", ".join(sorted(p["name"] for p in players))
        known_areas = ", ".join(areas) or "none assigned"
        raise HTTPException(
            status_code=400,
            detail=(f"I don't know a speaker or room called '{spoken}'. "
                    f"Speakers: {known_names}. Rooms with a speaker: {known_areas}."),
        )
    # 3. origin area (the satellite that heard the request)
    if origin_area:
        targets = _by_area(players, origin_area)
        if targets:
            return targets, _label(targets, targets[0]["area"])
    # 4. default
    if not default_entity:
        raise HTTPException(status_code=400, detail="No speaker named and no default speaker is configured.")
    dflt = [p for p in players if p["entity_id"] == default_entity]
    if not dflt:
        raise HTTPException(
            status_code=503,
            detail=f"The default speaker '{default_entity}' is not registered with Music Assistant in Home Assistant.",
        )
    return dflt, _label(dflt, dflt[0]["area"])


async def _fetch_players(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    try:
        resp = await client.post(f"{HA_URL}/api/template", headers=headers,
                                 json={"template": _PLAYERS_TEMPLATE})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Home Assistant is unreachable ({type(e).__name__}).")
    if resp.status_code >= 400:
        logger.warning("player list template failed (%s): %s", resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=502,
            detail=f"Home Assistant could not list the music players (HTTP {resp.status_code}).",
        )
    try:
        return parse_players(resp.text)
    except ValueError:
        logger.warning("player list template returned non-JSON: %s", resp.text[:300])
        raise HTTPException(status_code=502, detail="Home Assistant returned an unreadable player list.")


@app.post("/music")
async def play_music(req: MusicRequest):
    if not HA_TOKEN:
        raise HTTPException(status_code=500, detail="HA_TOKEN not configured")
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        players = await _fetch_players(client, headers)
        targets, label = resolve_players(req.player, req.origin_area, players, MUSIC_DEFAULT_PLAYER)

        # The list carries live state, so a powered-off player is caught here
        # with a clear sentence instead of a misleading 200 from the service call.
        live = [t for t in targets if t["state"] != "unavailable"]
        if not live:
            names = " and ".join(t["name"] for t in targets)
            verb = "is" if len(targets) == 1 else "are"
            raise HTTPException(
                status_code=503,
                detail=f"{names} {verb} unavailable — it may be powered off or asleep.",
            )
        entities = [t["entity_id"] for t in live]

        payload = {"entity_id": entities, "media_id": req.query}
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

        # Verify playback actually started (see header comment): any target
        # reaching `playing` is success.
        deadline = asyncio.get_event_loop().time() + MUSIC_VERIFY_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            for entity in entities:
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
            f"on {label} within {MUSIC_VERIFY_TIMEOUT_S}s — the music "
            "provider may need re-authentication in Music Assistant."
        ),
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
