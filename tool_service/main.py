import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import List
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Kronk Tool Service")

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


@app.get("/weather")
async def weather(location: str = Query(..., description="City name or city, state/country")):
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
    named_periods = [fmt_period(p) for p in periods[:6]]

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


@app.get("/health")
async def health():
    return {"status": "ok"}
