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
