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
DEFAULT_TIMER_LABEL = "Timer"

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
            "name": "set_timer",
            "description": (
                "Set a countdown timer on Home Assistant. When the timer expires, "
                "HA will announce it through the voice speaker. "
                "Use whenever the user asks to set, start, or begin a timer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {
                        "type": "number",
                        "description": "Timer duration in minutes (decimals allowed, e.g. 0.5 for 30 seconds).",
                    },
                    "label": {
                        "type": "string",
                        "description": f"Optional label describing what the timer is for. Default: {DEFAULT_TIMER_LABEL}",
                    },
                },
                "required": ["duration_minutes"],
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
    "set_timer": 10,
    "query_finances": 10,
}


async def _tool_get_weather(client: httpx.AsyncClient, args: dict) -> str:
    location = args.get("location", DEFAULT_LOCATION)
    resp = await client.get(f"{TOOL_SERVICE_URL}/weather", params={"location": location})
    if resp.status_code == 200:
        wx = resp.json()
        return f"[Weather for {wx['location']}]\n{wx['summary']}"
    return f"[Weather unavailable for {location}]"


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
    return "[Web search failed]"


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
    return "[Could not retrieve shopping list]"


async def _tool_shopping_list_add(client: httpx.AsyncClient, args: dict) -> str:
    resp = await client.post(
        f"{TOOL_SERVICE_URL}/shopping_list",
        json={"items": args.get("items", [])},
    )
    if resp.status_code == 200:
        added = resp.json().get("added", args.get("items", []))
        return f"[Added to shopping list: {', '.join(added)}]"
    return "[Could not add to shopping list]"


async def _tool_shopping_list_remove(client: httpx.AsyncClient, args: dict) -> str:
    item = args.get("item", "")
    resp = await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/{item}")
    if resp.status_code == 200:
        return f"[Removed '{item}' from shopping list]"
    if resp.status_code == 404:
        return f"['{item}' was not found on the shopping list]"
    return "[Could not remove from shopping list]"


async def _tool_shopping_list_clear(client: httpx.AsyncClient, args: dict) -> str:
    await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/clear")
    return "[Shopping list cleared]"


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
    return f"[Health search error: {resp.status_code}]"


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
    return f"[Health service error: {resp.status_code}]"


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
    return f"[Bloodwork query error: {resp.status_code}]"


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


async def _tool_set_timer(client: httpx.AsyncClient, args: dict) -> str:
    duration = args.get("duration_minutes")
    if duration is None:
        return "[set_timer error: duration_minutes is required]"
    label = args.get("label") or DEFAULT_TIMER_LABEL
    resp = await client.post(
        f"{TOOL_SERVICE_URL}/timer",
        json={"duration_minutes": float(duration), "label": label},
    )
    if resp.status_code == 200:
        info = resp.json()
        return f"[Timer set: {info['duration']} ({label})]"
    detail = resp.text[:200] if resp.text else f"status {resp.status_code}"
    return f"[Could not set timer: {detail}]"


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
    return "[Finance service unavailable]"


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
    "set_timer":            _tool_set_timer,
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
