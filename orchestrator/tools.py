"""Tool schema catalog + HTTP dispatch to sub-services."""
import json
import logging
import os

import httpx

from events import emit

logger = logging.getLogger(__name__)

TOOL_SERVICE_URL    = os.getenv("TOOL_SERVICE_URL",    "http://localhost:8003")
HEALTH_SERVICE_URL  = os.getenv("HEALTH_SERVICE_URL",  "http://localhost:8004")
FINANCE_SERVICE_URL = os.getenv("FINANCE_SERVICE_URL", "http://localhost:8005")
DEFAULT_LOCATION    = os.getenv("LOCATION", "Laurel, MD")

# ── Tool schema catalog ──────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Get current weather and forecast for a location. "
                "Use this any time the user asks about weather, temperature, rain, snow, wind, "
                "or whether to bring an umbrella."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": f"City and state, e.g. 'Baltimore, MD'. Default: {DEFAULT_LOCATION}",
                    }
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Use for news, facts, recent events, "
                "product info, local businesses, or any question that requires up-to-date data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "count": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and read the content of a specific web page or URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shopping_list_view",
            "description": "View the current shopping list.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shopping_list_add",
            "description": "Add one or more items to the shopping list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Items to add",
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shopping_list_remove",
            "description": "Remove an item from the shopping list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "Item name to remove"},
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shopping_list_clear",
            "description": "Clear all items from the shopping list.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_health_data",
            "description": (
                "Semantically search personal health data using natural language. "
                "Use for qualitative or exploratory questions that span multiple metrics or time periods: "
                "'how was my recovery during high-stress weeks', "
                "'days with low body battery', "
                "'sleep quality after hard workouts'. "
                "Returns the most relevant daily snapshots as readable text. "
                "For precise aggregation (averages, trends, extremes over time), use query_health instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what to find",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 6, max 20)",
                        "default": 6,
                    },
                    "start_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) to filter results from (inclusive)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) to filter results until (inclusive)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_health",
            "description": (
                "Query personal health and fitness data from Garmin and Withings. "
                "Choose the metric that matches the question, the time window, and the resolution. "
                "Available metrics: "
                "sleep (duration, stages, score), "
                "hrv (last-night HRV, weekly avg, baseline), "
                "activities (workouts: type, duration, distance, HR), "
                "steps (daily step count), "
                "calories (total and active calories), "
                "stress (avg and max stress score), "
                "resting_hr (resting heart rate), "
                "body_battery (daily high and low), "
                "distance (daily distance in meters), "
                "weight (Withings scale — daily weight in kg), "
                "body_composition (Withings — weight, fat %, muscle mass, bone mass), "
                "all (compact snapshot of everything — use for general health questions). "
                "Resolution guide — choose based on the question: "
                "raw = every daily record, best for short windows (≤30 days); "
                "weekly = 7-day averages, good for 1–6 month trends; "
                "monthly = calendar-month averages, good for 6+ month trends; "
                "summary = single aggregate (min/max/avg with dates), best for extremum questions "
                "('what was my lowest HRV?', 'when did I sleep most?') over any window. "
                "Examples: "
                "'how did I sleep last week' → metric=sleep days=7 resolution=raw; "
                "'HRV trend this year' → metric=hrv days=365 resolution=monthly; "
                "'when was my lowest HRV this year' → metric=hrv days=365 resolution=summary; "
                "'my weight this month' → metric=weight days=30 resolution=raw; "
                "'step count trend over 6 months' → metric=steps days=180 resolution=weekly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": [
                            "sleep", "hrv", "activities",
                            "steps", "calories", "stress",
                            "resting_hr", "body_battery", "distance",
                            "weight", "body_composition",
                            "all",
                        ],
                        "description": "Which health metric to retrieve.",
                    },
                    "days": {
                        "type": "integer",
                        "description": (
                            "How many days back from today (or end_date) to include. "
                            "Default 30. Max 3650."
                        ),
                    },
                    "end_date": {
                        "type": "string",
                        "description": (
                            "ISO date (YYYY-MM-DD) to use as the end of the window instead of today. "
                            "Use when asking about a specific past period."
                        ),
                    },
                    "resolution": {
                        "type": "string",
                        "enum": ["raw", "weekly", "monthly", "summary"],
                        "description": (
                            "How to aggregate the data. "
                            "raw = every daily record (default, use for ≤30 days); "
                            "weekly = 7-day averages (use for 1–6 months); "
                            "monthly = calendar-month averages (use for 6+ months); "
                            "summary = single row with min/max/avg and dates (use for extremum questions over any window)."
                        ),
                    },
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_bloodwork",
            "description": (
                "Query structured bloodwork / lab results from LabCorp reports. "
                "Use for questions about specific lab markers over time: cholesterol, LDL, HDL, "
                "glucose, HbA1c, creatinine, TSH, vitamin D, CBC components, etc. "
                "Omit marker to retrieve all results from a recent draw. "
                "For open-ended questions ('anything concerning in my labs?') use search_health_data instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "marker": {
                        "type": "string",
                        "description": "Lab marker to filter by (partial match, e.g. 'LDL', 'Glucose', 'TSH'). Omit for all markers.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "How many days back to include (default 730 = 2 years).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kronk_context",
            "description": (
                "Read the Kronk system context file. Call this when you need detailed information "
                "about your own architecture, services, ports, data flows, or configuration. "
                "Always call this before generating an architecture diagram."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_diagram",
            "description": (
                "Generate a diagram image from Graphviz DOT language and return a URL to display it inline. "
                "ALWAYS use this tool when the user asks for a diagram — never write Mermaid, DOT, or any "
                "diagram code as a code block. The tool renders it server-side and returns an image URL. "
                "Use 'digraph' for directed graphs. Use subgraph cluster_X for grouped boxes. "
                "Keep node labels concise. After the tool returns a URL, include it in your response "
                "as markdown: ![title](url)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dot": {
                        "type": "string",
                        "description": "Complete, valid Graphviz DOT language string",
                    },
                },
                "required": ["dot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_hottub",
            "description": (
                "Check the current status of the hot tub. Returns whether it is online or offline, "
                "the current water temperature, the target set temperature, and how long it has been "
                "offline if applicable. Use when the user asks about the hot tub, spa, or whether the "
                "hot tub breaker has tripped."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solar_status",
            "description": (
                "Quick solar panel system health summary: current total power output "
                "in kW, how many inverters are underperforming right now, and any inverters "
                "confirmed failing over several days. Use for a simple status check "
                "('how's my solar?', 'is the solar okay?')."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solar_detail",
            "description": (
                "Detailed per-inverter solar data for ANALYTICAL questions — which "
                "specific inverters are underperforming, their power/voltage/temperature, "
                "how many consecutive days each has been failing, and a short daily "
                "history (power and ratio-to-peers) for the troubled ones. Use when the "
                "user asks WHY something changed, which inverters are affected, whether one "
                "is getting worse, or anything needing per-inverter or trend detail. "
                "Reason over the numbers to answer; note that the 'underperforming right "
                "now' set is momentary (a marginal inverter dips in and out) while the "
                "consecutive bad-days count is what indicates a real, sustained failure."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_finances",
            "description": (
                "Search personal financial documents: bank statements, investment summaries, "
                "tax returns, budgets. Use for questions about spending, income, accounts, "
                "investments, or any financial data the user has uploaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for in the financial documents",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "Play music through Music Assistant on a home speaker. Use "
                "whenever the user asks to play, put on, or listen to music — "
                "an artist, album, song, playlist, or genre. Music Assistant "
                "searches for the query itself, so pass the artist/album/song "
                "name as plain text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to play — artist, album, song, playlist, or genre (e.g. 'Pink Floyd', 'Wish You Were Here').",
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["artist", "album", "track", "playlist", "radio"],
                        "description": "What kind of thing the query names, if the user said so (album/song/etc). Omit when unsure.",
                    },
                    "player": {
                        "type": "string",
                        "description": "Which speaker to play on, as the user named it (e.g. 'sonos move', 'kitchen'). Omit for the default speaker.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_magicmirror",
            "description": (
                "Update the MagicMirror software on the hallway Raspberry Pi. "
                "Use when the user asks to update/upgrade the magic mirror. "
                "A full backup is taken first; the update runs in the "
                "background and takes a few minutes."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_exec",
            "description": (
                "Run a READ-ONLY shell command on a managed host to inspect "
                "it, then read the output. Use for diagnostics — uptime, "
                "service status, logs, disk/memory, process list. The magic "
                "mirror is host 'magicmirror'. Only read-only commands are "
                "permitted (uptime, systemctl status, journalctl, ps, df, "
                "git log, …); anything that changes the system is refused. "
                "Compose one command; you may pipe between read commands "
                "(e.g. 'ps aux | grep node'). Iterate: run, read output, run "
                "another if needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "The read-only command to run."},
                    "host": {"type": "string",
                             "description": "Managed host name. Defaults to 'magicmirror'."},
                },
                "required": ["command"],
            },
        },
    },
]


# ── Dispatch ─────────────────────────────────────────────────────────────────
#
# One small handler per tool, registered in _HANDLERS. Adding a tool =
# definition in TOOL_DEFINITIONS + one handler function + one dict entry
# (+ tool_names on whichever agents get it).

# Per-tool HTTP timeout policy (seconds). Everything else uses the default.
TOOL_TIMEOUT_DEFAULT = 15
TOOL_TIMEOUTS = {
    "generate_diagram": 30,  # graphviz render can be slow on big graphs
    "query_hottub": 5,       # local file read — fail fast
    "query_finances": 10,
    "play_music": 20,        # tool_service polls up to 8s to confirm playback
    "remote_exec": 35,         # tool_service caps the exec at 30s + SSH setup
    "update_magicmirror": 30,  # SSH preflight to the Pi (~5-20s); the update
                               # itself runs as a tool_service background task
}


def _fail(action: str, resp: httpx.Response) -> str:
    """Uniform tool-failure string that keeps the sub-service's detail.

    All three services put the specific cause in a JSON `detail` field
    (FastAPI convention); fall back to the raw body. Handlers used to
    flatten this into strings like "[Web search failed]" — the model (and
    therefore the user) never saw why (2026-07-05 review P1.2, tenet 7)."""
    try:
        detail = resp.json().get("detail") or resp.text[:200]
    except ValueError:
        detail = resp.text[:200]
    return f"[{action} failed (HTTP {resp.status_code}): {detail}]"


async def _tool_get_weather(client: httpx.AsyncClient, args: dict) -> str:
    location = args.get("location", DEFAULT_LOCATION)
    resp = await client.get(f"{TOOL_SERVICE_URL}/weather", params={"location": location})
    if resp.status_code == 200:
        wx = resp.json()
        return f"[Weather for {wx['location']}]\n{wx['summary']}"
    return _fail(f"Weather lookup for {location}", resp)


async def _tool_web_search(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.get(
        f"{TOOL_SERVICE_URL}/search",
        params={"q": args.get("query", ""), "count": args.get("count", 5)},
    )
    if resp.status_code == 200:
        sr = resp.json()
        snippets = "\n\n".join(
            f"[{r['title']}]({r['url']})\n{r['snippet']}"
            for r in sr.get("results", [])
        )
        return f"[Web search results for '{args.get('query')}']\n\n{snippets}"
    return _fail("Web search", resp)


async def _tool_fetch_url(client: httpx.AsyncClient, args: dict) -> str:
    url = args.get("url", "")
    resp = await client.get(f"{TOOL_SERVICE_URL}/fetch", params={"url": url})
    if resp.status_code == 200:
        page = resp.json()
        if page.get("ok"):
            return f"[Page content from {url}]\n\n{page['text']}"
        reason = page.get("error", "unknown error")
        return (
            f"[Could not fetch {url}: {reason}. "
            "Try a different URL from the search results.]"
        )
    return f"[Could not fetch {url}: tool service returned {resp.status_code}]"


async def _tool_shopping_list_view(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.get(f"{TOOL_SERVICE_URL}/shopping_list")
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            return f"[Shopping list: {', '.join(items)}]"
        return "[Shopping list is empty]"
    return _fail("Shopping list view", resp)


async def _tool_shopping_list_add(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.post(
        f"{TOOL_SERVICE_URL}/shopping_list",
        json={"items": args.get("items", [])},
    )
    if resp.status_code == 200:
        added = resp.json().get("added", args.get("items", []))
        return f"[Added to shopping list: {', '.join(added)}]"
    return _fail("Shopping list add", resp)


async def _tool_shopping_list_remove(client: httpx.AsyncClient, args: dict) -> str:
    item = args.get("item", "")
    resp = await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/{item}")
    if resp.status_code == 200:
        return f"[Removed '{item}' from shopping list]"
    if resp.status_code == 404:
        return f"['{item}' was not found on the shopping list]"
    return _fail("Shopping list remove", resp)


async def _tool_shopping_list_clear(client: httpx.AsyncClient, args: dict) -> str:
    # Used to ignore the response entirely and claim success (tenet 6).
    resp = await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/clear")
    if resp.status_code == 200:
        return "[Shopping list cleared]"
    return _fail("Shopping list clear", resp)


async def _tool_search_health_data(client: httpx.AsyncClient, args: dict) -> str:
    params: dict = {"q": args.get("query", "")}
    if "n_results" in args:
        params["n"] = int(args["n_results"])
    if "start_date" in args:
        params["start_date"] = args["start_date"]
    if "end_date" in args:
        params["end_date"] = args["end_date"]
    resp = await client.get(f"{HEALTH_SERVICE_URL}/api/search", params=params)
    if resp.status_code == 200:
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return f"[Health search: no indexed data found for '{params['q']}'. Data may not have been ingested yet.]"
        chunks = "\n\n".join(
            f"[{r['metadata'].get('date','?')} | {r['metadata'].get('type','?')} | score={r['score']}] {r['text']}"
            for r in results
        )
        return f"[Health search results for '{params['q']}']\n\n{chunks}"
    return _fail("Health search", resp)


async def _tool_query_health(client: httpx.AsyncClient, args: dict) -> str:
    days = int(args.get("days", 30))
    resolution = args.get("resolution", "raw")
    # Enforce sane resolution for longer windows to prevent context bloat
    if days > 365 and resolution in ("raw", "weekly"):
        resolution = "monthly"
    elif days > 90 and resolution == "raw":
        resolution = "weekly"
    params: dict = {"metric": args.get("metric", "all"), "resolution": resolution, "days": days}
    if "end_date" in args:
        params["end_date"] = args["end_date"]
    resp = await client.get(f"{HEALTH_SERVICE_URL}/api/query", params=params)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("status") == "no_data":
            return f"[Health data: {data.get('note', 'no data available')}]"
        return f"[Health data — metric={params['metric']} days={days}]\n{json.dumps(data, indent=2)}"
    return _fail("Health query", resp)


async def _tool_query_bloodwork(client: httpx.AsyncClient, args: dict) -> str:
    params: dict = {}
    if "marker" in args:
        params["marker"] = args["marker"]
    if "days" in args:
        params["days"] = int(args["days"])
    resp = await client.get(f"{HEALTH_SERVICE_URL}/api/bloodwork", params=params)
    if resp.status_code == 200:
        data = resp.json()
        if not data.get("results"):
            return "[Bloodwork: no lab results found. Upload a LabCorp PDF at /api/health/import/bloodwork.]"
        dates = data.get("dates", [])
        rows = data["results"]
        lines = [f"Lab draws on file: {', '.join(dates)}"]
        for r in rows:
            flag = f" [{r['flag']}]" if r.get("flag") else ""
            ref = f" (ref {r['raw_ref']})" if r.get("raw_ref") else ""
            lines.append(f"{r['date']} | {r['panel']} | {r['marker']}: {r['value']} {r.get('unit','')}{flag}{ref}")
        return "[Bloodwork results]\n" + "\n".join(lines)
    return _fail("Bloodwork query", resp)


async def _tool_get_kronk_context(client: httpx.AsyncClient, args: dict) -> str:
    try:
        with open("/kronk-context.md") as f:
            content = f.read()
        return (
            f"[Kronk system context]\n{content}\n\n"
            "---\n"
            "You now have the full system context. "
            "If the user asked for a diagram, your next action MUST be to call "
            "generate_diagram with Graphviz DOT syntax — do not write diagram "
            "code as text or in a code block."
        )
    except Exception as e:
        return f"[Could not read context file: {e}]"


async def _tool_generate_diagram(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.post(f"{TOOL_SERVICE_URL}/diagram", json={"dot": args.get("dot", "")})
    if resp.status_code == 200:
        url = resp.json().get("url", "")
        return f"[Diagram generated: {url}]\n![diagram]({url})"
    return f"[Diagram generation failed: {resp.status_code} {resp.text[:200]}]"


async def _tool_solar_status(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.get(f"{TOOL_SERVICE_URL}/solar/status")
    if resp.status_code != 200:
        return _fail("Solar status", resp)
    d = resp.json()
    total = d.get("total_kw")
    live = d.get("live_underperforming") or []
    confirmed = d.get("confirmed_failing") or []
    # Hand the model structured facts; it renders a 1-2 sentence summary.
    parts = [f"total output {total} kW" if total is not None else "output unknown",
             f"{d.get('inverter_count', '?')} inverters",
             f"{len(live)} underperforming right now"]
    if confirmed:
        parts.append(f"{len(confirmed)} confirmed failing for days: " +
                     ", ".join(f"…{c['sn'][-6:]} ({c['days']}d)" for c in confirmed))
    healthy = not live and not confirmed
    tag = "HEALTHY" if healthy else "ISSUES"
    return f"[Solar {tag}] " + "; ".join(parts) + (
        ". Summarize for the user in one or two short sentences.")


async def _tool_solar_detail(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.get(f"{TOOL_SERVICE_URL}/solar/detail")
    if resp.status_code != 200:
        return _fail("Solar detail", resp)
    d = resp.json()
    thr = int(d.get('fail_ratio', 0.4) * 100)
    lines = [
        f"[Solar detail — total {d.get('total_kw')} kW, {d.get('inverter_count')} inverters, "
        f"array median {d.get('array_median_kw')} kW. status per inverter: "
        f"'underperforming' = below {thr}% of median (counts as failing right now); "
        f"'marginal' = just above {thr}%, so it flickers in and out of the failing set "
        f"as sunlight/median shifts (this is why the live count changes); "
        f"'healthy' = well above. {d.get('confirm_days')} consecutive bad days confirms a "
        f"real sustained failure.]",
    ]
    # Lead with the troubled inverters (already sorted worst-first by the service).
    for iv in d.get("inverters", []):
        base = (f"inv …{iv['sn'][-6:]}: {iv.get('status', '?').upper()} — "
                f"{iv.get('power_kw')} kW (ratio {iv.get('ratio_to_median')}), "
                f"{iv.get('voltage_v')} V, {iv.get('temp_c')}°C; bad_days={iv.get('bad_days')}"
                + (", CONFIRMED FAILING" if iv.get('confirmed_failing') else ""))
        if iv.get("history"):
            hist = " | ".join(f"{h['day'][5:]}:{h['avg_kw']}kW(r{h['ratio']})"
                              for h in iv["history"])
            base += f"; daily history: {hist}"
        lines.append(base)
    lines.append("Reason over this to answer the user. If asked why the failing COUNT "
                 "changed, it's the MARGINAL inverters crossing the threshold as "
                 "conditions change — not recovery; the sustained failures are the ones "
                 "with a high bad_days count and consistently low daily history.")
    return "\n".join(lines)


async def _tool_query_hottub(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.get(f"{TOOL_SERVICE_URL}/hottub")
    if resp.status_code == 200:
        d = resp.json()
        if d.get("online") is None:
            return f"[Hot tub status unknown: {d.get('error', 'no data')}]"
        if d["online"]:
            return (
                f"[Hot tub ONLINE]\n"
                f"Temperature: {d.get('temperature_f')}°F (set: {d.get('set_temperature_f')}°F)\n"
                f"Spa: {d.get('spa_name')} ({d.get('spa_ip')})\n"
                f"Last checked: {d.get('last_check')}"
            )
        offline_since = d.get("offline_since", "unknown")
        return (
            f"[Hot tub OFFLINE — breaker may have tripped]\n"
            f"Offline since: {offline_since}\n"
            f"Last seen: {d.get('last_seen')}\n"
            f"Last checked: {d.get('last_check')}"
        )
    return "[Hot tub status unavailable]"


async def _tool_play_music(client: httpx.AsyncClient, args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "[play_music error: query is required]"
    payload: dict = {"query": query}
    if args.get("media_type"):
        payload["media_type"] = args["media_type"]
    if args.get("player"):
        payload["player"] = args["player"]
    resp = await client.post(f"{TOOL_SERVICE_URL}/music", json=payload)
    if resp.status_code == 200:
        info = resp.json()
        what = " by ".join(p for p in (info.get("title"), info.get("artist")) if p) or query
        return f"[Music playing: {what} on {info.get('player')}]"
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        detail = resp.text[:200]
    return (
        f"[Could not play music: {detail}]\n"
        "Playback FAILED — tell the user it failed and why. "
        "Do NOT claim music is playing. Do NOT call play_music again."
    )


async def _tool_remote_exec(client: httpx.AsyncClient, args: dict) -> str:
    command = (args.get("command") or "").strip()
    if not command:
        return "[remote_exec error: command is required]"
    host = args.get("host") or "magicmirror"
    resp = await client.post(f"{TOOL_SERVICE_URL}/ops/exec",
                             json={"host": host, "command": command})
    if resp.status_code == 200:
        info = resp.json()
        out = info.get("output", "").strip() or "(no output)"
        return (f"[remote_exec on {host}: `{command}` exit={info.get('exit_code')}]\n"
                f"{out}")
    detail = _fail(f"remote_exec on {host}", resp)
    # A refusal (422) is a normal outcome the model should adapt to, not a
    # hard failure — tell it plainly so it picks a read-only alternative.
    if resp.status_code == 422:
        return (f"{detail}\nThat command was refused (read-only mode). Try a "
                "read-only command instead, or tell the user it can't be done "
                "without a change they'd need to approve.")
    return detail


async def _tool_update_magicmirror(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.post(f"{TOOL_SERVICE_URL}/magicmirror/update")
    if resp.status_code == 200:
        info = resp.json()
        return f"[Magic mirror update started: {info.get('message', 'in progress')}]"
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        detail = resp.text[:200]
    return (
        f"[Could not update the magic mirror: {detail}]\n"
        "The update did NOT start — tell the user it failed and why. "
        "Do NOT claim the mirror is updating. Do NOT call update_magicmirror again."
    )


async def _tool_query_finances(client: httpx.AsyncClient, args: dict) -> str:
    query = args.get("query", "")
    resp = await client.get(f"{FINANCE_SERVICE_URL}/api/query", params={"q": query})
    if resp.status_code == 200:
        data = resp.json()
        if data.get("status") == "no_documents":
            return "[Financial documents: none uploaded yet. User can upload via /finances.]"
        results = data.get("results", [])
        if not results:
            return f"[No financial documents matched '{query}']"
        excerpts = "\n\n".join(
            f"[{r['doc_name']}, page {r['page']}]\n{r['excerpt']}"
            for r in results
        )
        return f"[Financial document results for '{query}']\n\n{excerpts}"
    return _fail("Finance query", resp)


_HANDLERS = {
    "get_weather":          _tool_get_weather,
    "web_search":           _tool_web_search,
    "fetch_url":            _tool_fetch_url,
    "shopping_list_view":   _tool_shopping_list_view,
    "shopping_list_add":    _tool_shopping_list_add,
    "shopping_list_remove": _tool_shopping_list_remove,
    "shopping_list_clear":  _tool_shopping_list_clear,
    "search_health_data":   _tool_search_health_data,
    "query_health":         _tool_query_health,
    "query_bloodwork":      _tool_query_bloodwork,
    "get_kronk_context":    _tool_get_kronk_context,
    "generate_diagram":     _tool_generate_diagram,
    "query_hottub":         _tool_query_hottub,
    "solar_status":         _tool_solar_status,
    "solar_detail":         _tool_solar_detail,
    "play_music":           _tool_play_music,
    "update_magicmirror":   _tool_update_magicmirror,
    "remote_exec":          _tool_remote_exec,
    "query_finances":       _tool_query_finances,
}


async def execute(name: str, args: dict) -> str:
    """Execute a named tool and return a string result for LLM context."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"[Unknown tool: {name}]"
    try:
        timeout = TOOL_TIMEOUTS.get(name, TOOL_TIMEOUT_DEFAULT)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await handler(client, args)
    except Exception as e:
        emit("tool_error", tool=name, error=str(e))
        logger.warning("Tool %s failed: %s", name, e)
        return f"[Tool {name} error: {e}]"
