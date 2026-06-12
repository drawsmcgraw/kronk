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
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

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
    yield
    task.cancel()


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
    """Extract readable text from HTML, preserving hyperlinks, truncate to token limit."""
    soup = BeautifulSoup(html, "lxml")
    # Only strip tags that are pure noise — preserve nav, header, footer for links
    for tag in soup(["script", "style", "aside"]):
        tag.decompose()
    # Convert <a href> to markdown links so the model sees actual URLs
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if text and href:
            a.replace_with(f"[{text}]({href})")
        elif href:
            a.replace_with(href)
    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive blank lines
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


async def _fetch_weather(location: str) -> dict:
    """Geocode + NWS forecast fetch. Shared by /weather and the hourly cache."""
    query = clean_location(location)
    async with httpx.AsyncClient(timeout=15, headers=NWS_HEADERS) as client:
        # Step 1: geocode via Open-Meteo (NWS has no geocoder)
        geo_resp = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
        )
        geo = geo_resp.json()
        if not geo.get("results"):
            raise HTTPException(status_code=404, detail=f"Location not found: {location}")

        place = geo["results"][0]
        lat = round(place["latitude"], 4)
        lon = round(place["longitude"], 4)

        # Step 2: get NWS grid point for this lat/lon
        points_resp = await client.get(f"https://api.weather.gov/points/{lat},{lon}")
        if points_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="NWS points lookup failed — location may be outside US coverage")
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
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={"q": q, "format": "json", "categories": "general", "language": "en"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Search service unavailable")
        data = resp.json()

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
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}


@app.get("/fetch")
async def fetch(url: str = Query(..., description="URL to fetch and extract text from")):
    """Fetch and extract text from a URL.

    Upstream failures (403, 404, timeouts, DNS) are returned as 200 with
    `{"ok": false, "error": "..."}` so the calling agent sees the failure as
    a normal tool result and can choose a different URL from its search hits.
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, verify=False) as client:
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


@app.get("/hottub")
async def hottub_status():
    if not HOTTUB_STATUS_FILE.exists():
        return {"online": None, "error": "no status file — monitor may not be running"}
    try:
        return json.loads(HOTTUB_STATUS_FILE.read_text())
    except Exception as e:
        return {"online": None, "error": str(e)}


# ── Home Assistant timer proxy ───────────────────────────────────────────────
# Calls HA's REST `timer.start` service. HA fires `timer.finished` when the
# countdown expires; a separate HA automation handles the announcement via
# the Voice PE media player (configured operator-side after device adoption).
# Requires a HA timer helper entity to exist (default: `timer.voice_timer`).

HA_URL          = os.getenv("HA_URL",          "http://localhost:8123")
HA_TOKEN        = os.getenv("HA_TOKEN",        "")
HA_TIMER_ENTITY = os.getenv("HA_TIMER_ENTITY", "timer.voice_timer")


class TimerRequest(BaseModel):
    duration_minutes: float
    label: str | None = None


@app.post("/timer")
async def set_timer(req: TimerRequest):
    if not HA_TOKEN:
        raise HTTPException(status_code=500, detail="HA_TOKEN not configured")
    if req.duration_minutes <= 0:
        raise HTTPException(status_code=400, detail="duration_minutes must be > 0")

    total = int(round(req.duration_minutes * 60))
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    duration_str = f"{h:02d}:{m:02d}:{s:02d}"

    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=5) as client:
        # Pre-check: HA's timer.start silently accepts unknown entity_ids and
        # returns 200, which masks "the helper isn't set up yet" as success.
        state_resp = await client.get(f"{HA_URL}/api/states/{HA_TIMER_ENTITY}", headers=headers)
        if state_resp.status_code == 404:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Timer entity '{HA_TIMER_ENTITY}' does not exist in Home "
                    "Assistant. Create a Timer helper named 'voice_timer' "
                    "(Settings → Devices & Services → Helpers → Create Helper → Timer)."
                ),
            )
        if state_resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"HA state check returned {state_resp.status_code}: {state_resp.text[:200]}",
            )

        resp = await client.post(
            f"{HA_URL}/api/services/timer/start",
            headers=headers,
            json={"entity_id": HA_TIMER_ENTITY, "duration": duration_str},
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"HA returned {resp.status_code}: {resp.text[:200]}",
        )
    return {
        "status":           "timer_set",
        "duration_minutes": req.duration_minutes,
        "duration":         duration_str,
        "entity_id":        HA_TIMER_ENTITY,
        "label":            req.label,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
