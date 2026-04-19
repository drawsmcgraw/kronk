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
            "name": "query_health",
            "description": (
                "Query personal health and fitness data from Garmin and Withings. "
                "Choose the metric that matches the question and set days to cover the relevant period. "
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
                "Examples: 'how did I sleep last week' → metric=sleep days=7; "
                "'HRV trend this year' → metric=hrv days=365; "
                "'my weight this month' → metric=weight days=30; "
                "'body composition trend' → metric=body_composition days=90."
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
                            "Default 30. Max 3650. If the user asks for more data than exists, "
                            "the oldest available records will be returned."
                        ),
                    },
                    "end_date": {
                        "type": "string",
                        "description": (
                            "ISO date (YYYY-MM-DD) to use as the end of the window instead of today. "
                            "Use when asking about a specific past period."
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
]


# ── Dispatch ─────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict) -> str:
    """Execute a named tool and return a string result for LLM context."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:

            if name == "get_weather":
                location = args.get("location", DEFAULT_LOCATION)
                resp = await client.get(f"{TOOL_SERVICE_URL}/weather", params={"location": location})
                if resp.status_code == 200:
                    wx = resp.json()
                    return f"[Weather for {wx['location']}]\n{wx['summary']}"
                return f"[Weather unavailable for {location}]"

            if name == "web_search":
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

            if name == "fetch_url":
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

            if name == "shopping_list_view":
                resp = await client.get(f"{TOOL_SERVICE_URL}/shopping_list")
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        return f"[Shopping list: {', '.join(items)}]"
                    return "[Shopping list is empty]"
                return "[Could not retrieve shopping list]"

            if name == "shopping_list_add":
                resp = await client.post(
                    f"{TOOL_SERVICE_URL}/shopping_list",
                    json={"items": args.get("items", [])},
                )
                if resp.status_code == 200:
                    added = resp.json().get("added", args.get("items", []))
                    return f"[Added to shopping list: {', '.join(added)}]"
                return "[Could not add to shopping list]"

            if name == "shopping_list_remove":
                item = args.get("item", "")
                resp = await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/{item}")
                if resp.status_code == 200:
                    return f"[Removed '{item}' from shopping list]"
                if resp.status_code == 404:
                    return f"['{item}' was not found on the shopping list]"
                return "[Could not remove from shopping list]"

            if name == "shopping_list_clear":
                await client.delete(f"{TOOL_SERVICE_URL}/shopping_list/clear")
                return "[Shopping list cleared]"

            if name == "query_health":
                params: dict = {"metric": args.get("metric", "all")}
                if "days" in args:
                    params["days"] = int(args["days"])
                if "end_date" in args:
                    params["end_date"] = args["end_date"]
                resp = await client.get(
                    f"{HEALTH_SERVICE_URL}/api/query", params=params, timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "no_data":
                        return f"[Health data: {data.get('note', 'no data available')}]"
                    return f"[Health data — metric={params['metric']} days={params.get('days', 30)}]\n{json.dumps(data, indent=2)}"
                return f"[Health service error: {resp.status_code}]"

            if name == "get_kronk_context":
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

            if name == "generate_diagram":
                dot = args.get("dot", "")
                resp = await client.post(
                    f"{TOOL_SERVICE_URL}/diagram",
                    json={"dot": dot},
                    timeout=30,
                )
                if resp.status_code == 200:
                    url = resp.json().get("url", "")
                    return f"[Diagram generated: {url}]\n![diagram]({url})"
                return f"[Diagram generation failed: {resp.status_code} {resp.text[:200]}]"

            if name == "query_finances":
                query = args.get("query", "")
                resp = await client.get(f"{FINANCE_SERVICE_URL}/api/query", params={"q": query}, timeout=10)
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

    except Exception as e:
        emit("tool_error", tool=name, error=str(e))
        logger.warning("Tool %s failed: %s", name, e)
        return f"[Tool {name} error: {e}]"

    return f"[Unknown tool: {name}]"
