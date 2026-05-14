"""Specialist-agent registry + per-agent tool-calling loop.

The `AGENTS` dict is the single source of truth: the router prompt, the
valid-route set, and the /api/agents roster are all derived from it.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field

import llm
import metrics
import tools
from events import emit

logger = logging.getLogger(__name__)


def _tool_narration(name: str, args: dict) -> str:
    if name == "web_search":
        q = args.get("query", "")
        return f"searching the web for {q}" if q else "searching the web..."
    if name == "fetch_url":
        url = args.get("url", "")
        return f"reading {url}" if url else "reading that page..."
    if name == "get_weather":
        loc = args.get("location", "")
        return f"checking the weather for {loc}" if loc else "checking the weather..."
    if name == "query_health":
        metric = args.get("metric", "")
        return f"looking up your {metric} data" if metric else "checking your health data..."
    if name == "query_finances":
        q = args.get("query", "")
        return f"searching your financial documents for {q}" if q else "searching financial documents..."
    if name.startswith("shopping_list"):
        return "checking your shopping list..."
    if name == "get_kronk_context":
        return "reading Kronk's configuration..."
    if name == "generate_diagram":
        return "generating a diagram..."
    return f"running {name}..."


TALKIE_MODEL         = os.getenv("TALKIE_MODEL",         "talkie")
COORDINATOR_MODEL    = os.getenv("COORDINATOR_MODEL",    "mistral-nemo:12b")
HEALTH_AGENT_MODEL   = os.getenv("HEALTH_AGENT_MODEL",   COORDINATOR_MODEL)
RESEARCH_AGENT_MODEL = os.getenv("RESEARCH_AGENT_MODEL", COORDINATOR_MODEL)
FINANCE_AGENT_MODEL  = os.getenv("FINANCE_AGENT_MODEL",  COORDINATOR_MODEL)
CODING_AGENT_MODEL   = os.getenv("CODING_AGENT_MODEL",   "devstral-2512")
DEVOPS_AGENT_MODEL   = os.getenv("DEVOPS_AGENT_MODEL",   "devstral-2512")

MAX_TOOL_ROUNDS = 3


@dataclass
class AgentConfig:
    name: str
    # Shown to the roster / coordinator (plain-English capability description).
    description: str
    # One-line capability hint used by the phase-1 routing classifier.
    routing_hint: str
    # Emoji shown in the agent roster.
    icon: str
    # Which status probe represents this agent's upstream dependency.
    probe: str
    system_prompt: str
    tool_names: list[str]
    model: str = field(default="")

    def __post_init__(self):
        if not self.model:
            self.model = COORDINATOR_MODEL

    def tool_defs(self) -> list[dict]:
        return [t for t in tools.TOOL_DEFINITIONS if t["function"]["name"] in self.tool_names]


AGENTS: dict[str, AgentConfig] = {
    "health": AgentConfig(
        name="health",
        description="Garmin/Withings health data: sleep, HRV, weight, body composition, steps, activities, calories, stress, resting HR, body battery",
        routing_hint="personal health data, sleep, HRV, fitness, Garmin, Withings",
        icon="❤️",
        probe="health",
        system_prompt=(
            "You are Kronk's health specialist. Retrieve and interpret the user's personal health data using query_health.\n"
            "Always call query_health with the right metric and time window — never guess or fabricate numbers.\n"
            "Be specific: cite actual values, dates, and trends. Keep responses concise.\n"
            "Available metrics: sleep, hrv, activities, steps, calories, stress, resting_hr, "
            "body_battery, distance, weight, body_composition, all."
        ),
        tool_names=["query_health"],
        model=HEALTH_AGENT_MODEL,
    ),
    "research": AgentConfig(
        name="research",
        description="Web search, current events, news, online information, URL lookups",
        routing_hint="ONLY questions requiring live or real-time data: today's news, current prices, live scores, breaking events, recent releases — NOT for general knowledge or factual questions answerable from training data",
        icon="🔍",
        probe="tools",
        system_prompt=(
            "You are Kronk's research specialist. Answer questions requiring current information.\n"
            "For queries without a URL: call web_search, then call fetch_url on the single most "
            "relevant URL to get full page content. You may refine the search with a different "
            "query if the first results are weak. Skip fetch_url only if the snippets already "
            "fully answer a simple-fact question.\n"
            "For queries with a URL: call fetch_url on that URL first. "
            "If it doesn't contain the needed information, follow up with web_search.\n"
            "Always include the full URL for every source you reference — never omit links.\n"
            "Answer ONLY from tool results. Do not use training data. "
            "If the results do not contain the answer, say so plainly."
        ),
        tool_names=["web_search", "fetch_url"],
        model=RESEARCH_AGENT_MODEL,
    ),
    "home": AgentConfig(
        name="home",
        description="Weather lookups, shopping list management, and hot tub status",
        routing_hint="weather, forecast, shopping list, hot tub, spa",
        icon="🏠",
        probe="tools",
        system_prompt=(
            "You are Kronk's home specialist. Handle weather lookups, shopping list management, and home device status.\n"
            "Use get_weather for weather queries. Use shopping list tools for list management.\n"
            "Use query_hottub to check if the hot tub is online and report its temperature. "
            "If the hot tub is offline, clearly state that the breaker may have tripped and report how long it has been offline.\n"
            "Be brief and direct."
        ),
        tool_names=["get_weather", "shopping_list_view", "shopping_list_add", "shopping_list_remove", "shopping_list_clear", "query_hottub"],
    ),
    "assistant": AgentConfig(
        name="assistant",
        description="Kronk's own architecture, services, configuration, and generating diagrams",
        routing_hint="Kronk's own architecture or configuration",
        icon="🤖",
        probe="llm",
        system_prompt=(
            "You are Kronk's systems specialist. You know Kronk's architecture and can generate diagrams.\n"
            "Always call get_kronk_context before answering architecture questions or generating diagrams.\n"
            "After reading context, use generate_diagram with Graphviz DOT syntax — never write code blocks."
        ),
        tool_names=["get_kronk_context", "generate_diagram"],
    ),
    "finance": AgentConfig(
        name="finance",
        description="Bank statements, spending, income, tax returns, uploaded financial documents",
        routing_hint="bank statements, spending, taxes, investments",
        icon="💰",
        probe="finance",
        system_prompt=(
            "You are Kronk's finance specialist. Search uploaded financial documents using query_finances.\n"
            "Be precise about amounts, dates, and document sources."
        ),
        tool_names=["query_finances"],
        model=FINANCE_AGENT_MODEL,
    ),
    "coding": AgentConfig(
        name="coding",
        description="Writing code, debugging, explaining code, architecture questions, shell scripts, anything programming-related",
        routing_hint="writing or debugging code, programming questions",
        icon="💻",
        probe="llm",
        system_prompt=(
            "You are Kronk's coding specialist, powered by a model purpose-built for software engineering.\n"
            "Help with writing, debugging, refactoring, and explaining code across any language.\n"
            "Use web_search or fetch_url to look up documentation or APIs when needed.\n"
            "Be direct: provide working code, explain only what is non-obvious, avoid filler."
        ),
        tool_names=["web_search", "fetch_url"],
        model=CODING_AGENT_MODEL,
    ),
    "talkie": AgentConfig(
        name="talkie",
        description="Talkie-1930: a vintage language model trained on pre-1931 text. Answers in period-appropriate language with knowledge limited to December 31, 1930. Only invoked when explicitly requested by name.",
        routing_hint="explicitly requested by name only — e.g. 'ask talkie' or 'what does talkie think'",
        icon="📻",
        probe="llm",
        system_prompt=(
            "You are Talkie, a learned gentleman of letters circa 1930. Your knowledge extends only to "
            "December 31, 1930 — you know nothing of events, inventions, or persons that came after.\n"
            "Speak in the register of a well-educated Edwardian or early American: precise, measured, "
            "occasionally formal, but never stuffy. Draw on encyclopedias, reference works, and the "
            "great literature of the age.\n"
            "If asked about something beyond your knowledge — aeroplanes of a later era, television, "
            "the internet, events after 1930 — say plainly that such things lie beyond your acquaintance, "
            "and offer what relevant knowledge you do possess.\n"
            "You have no tools and require none. Your answers come from learning, not from wire or mechanism."
        ),
        tool_names=[],
        model=TALKIE_MODEL,
    ),
    "devops": AgentConfig(
        name="devops",
        description="SSH commands, server administration, Linux troubleshooting, infrastructure, Docker, systemd, networking, host automation",
        routing_hint="servers, SSH, Docker, Linux, nginx, web servers, systemd, networking, infrastructure",
        icon="🛠️",
        probe="llm",
        system_prompt=(
            "You are Kronk's DevOps specialist. You handle server administration, Linux systems, Docker, "
            "systemd, networking, SSH automation, and infrastructure tasks.\n"
            "Use web_search or fetch_url to look up documentation, man pages, or current best practices when needed.\n"
            "Provide complete, working commands and scripts. Be direct and concise — no filler, no action text."
        ),
        tool_names=["web_search", "fetch_url"],
        model=DEVOPS_AGENT_MODEL,
    ),
}


# ── Derived: router prompt + valid-route set ────────────────────────────────

VALID_ROUTES = set(AGENTS.keys()) | {"direct"}


def build_routing_prompt() -> str:
    width = max(len(k) for k in AGENTS) + 1
    lines = [
        "You are a request classifier for a home assistant. Output exactly one word — nothing else.",
        "",
        "Routes:",
        "",
    ]
    for key, agent in AGENTS.items():
        lines.append(f"  {key:<{width}} — {agent.routing_hint}")
    lines.append(
        f"  {'direct':<{width}} — factual questions, explanations, definitions, science, history, "
        "analysis, advice, math, opinions — anything answerable from general knowledge without live data"
    )
    lines += [
        "",
        "Key rule: Use 'research' ONLY when the answer genuinely requires live or current data that "
        "changes day to day. For all other questions — even complex or detailed ones — use 'direct'.",
        "When in doubt between 'research' and 'direct', choose 'direct'.",
        "",
        "Examples:",
        "  'What is the capital of France?' → direct",
        "  'Is zinc good for colds?' → direct",
        "  'How does TCP/IP work?' → direct",
        "  'What are the drawbacks of sitting on the floor?' → direct",
        "  'Why did the Roman Empire fall?' → direct",
        "  'What is the news today?' → research",
        "  'What are current mortgage rates?' → research",
        "  'Write a bash script to rename files' → coding",
        "  'How is my sleep this week?' → health",
        "",
        "Your entire response must be exactly one word from the list above. Do not explain. Do not add punctuation.",
    ]
    return "\n".join(lines)


ROUTING_PROMPT = build_routing_prompt()


# ── Agent loop ──────────────────────────────────────────────────────────────

def _args_key(name: str, args: dict) -> str:
    """Dedup key: tool name + canonical arg JSON. Different URLs / queries → different key."""
    try:
        return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
    except Exception:
        return f"{name}:{args!r}"


def _build_assistant_msg(content: str, tool_calls: list[dict]) -> dict:
    """Build an OpenAI-format assistant message from collected stream output."""
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id":   tc["id"],
                "type": "function",
                "function": {
                    "name":      tc["function"]["name"],
                    # OpenAI expects arguments as a JSON string, not an object.
                    "arguments": json.dumps(tc["function"]["arguments"] or {}),
                },
            }
            for tc in tool_calls
        ],
    }


async def run_stream(agent: AgentConfig, task: str, context: list[dict]):
    """Run a specialist agent's tool-calling loop with unified streaming.

    Async generator. Yielded events:
      {"type": "token",     "text": str}              — incremental content token
      {"type": "narration", "text": str}              — pre-tool status string
      {"type": "error",     "message": str}           — terminal; no more events follow
      {"type": "done",      "model": str, "ok": bool} — terminal

    One streaming LLM call per round. Tokens stream as they arrive; tool_calls
    are accumulated from the stream and executed at end-of-round. A round with
    no accumulated tool_calls terminates the loop.
    """
    agent_tool_defs = agent.tool_defs() or None

    system_content = agent.system_prompt
    if context:
        ctx_lines = [f"{m['role'].upper()}: {m['content']}" for m in context if m.get("content")]
        system_content += "\n\n[Recent conversation context]\n" + "\n".join(ctx_lines)

    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": task},
    ]

    seen_calls: set[str] = set()
    last_usage: dict = {}

    # Tool-using rounds: up to MAX_TOOL_ROUNDS streaming calls with tools enabled.
    # If a round ends without tool_calls, we're done — content already streamed.
    # If all rounds produce tool_calls, fall through to forced synthesis below.
    for round_idx in range(MAX_TOOL_ROUNDS):
        round_content: list[str] = []
        round_tool_calls: list[dict] = []
        t_llm = time.monotonic()

        try:
            async for chunk in llm.stream(messages, agent.model, agent_tool_defs):
                if "token" in chunk:
                    round_content.append(chunk["token"])
                    yield {"type": "token", "text": chunk["token"]}
                elif "tool_calls" in chunk:
                    round_tool_calls = chunk["tool_calls"]
                elif "usage" in chunk:
                    last_usage = chunk["usage"]
        except Exception as e:
            emit("agent_llm_error", agent=agent.name, model=agent.model, error=str(e))
            logger.error("Agent '%s' stream failed: %s", agent.name, e)
            yield {"type": "error", "message": f"[{agent.name} agent error: {e}]"}
            return

        phase = "synthesis" if not round_tool_calls else f"plan_{round_idx + 1}"
        emit(
            "agent_round",
            agent=agent.name,
            model=agent.model,
            phase=phase,
            duration_s=round(time.monotonic() - t_llm, 2),
        )
        metrics.record(
            agent=agent.name,
            model=agent.model,
            prompt_tokens=last_usage.get("prompt_tokens", 0),
            completion_tokens=last_usage.get("completion_tokens", 0),
            eval_duration_ns=0,
        )

        if not round_tool_calls:
            # Stream ended with no tool_calls → final answer. Content already streamed.
            if not round_content:
                yield {"type": "token", "text": f"[{agent.name} agent returned no response]"}
            yield {"type": "done", "model": agent.model, "ok": True}
            return

        # Execute tools, then loop for the next round.
        messages.append(_build_assistant_msg("".join(round_content), round_tool_calls))

        for call in round_tool_calls:
            fn_name = call["function"]["name"]
            fn_args = call["function"]["arguments"] or {}
            key = _args_key(fn_name, fn_args)
            if key in seen_calls:
                result = f"[{fn_name} was already called with these exact arguments this turn; use the earlier result]"
            else:
                yield {"type": "narration", "text": _tool_narration(fn_name, fn_args)}
                t_tool = time.monotonic()
                emit("tool_call", agent=agent.name, tool=fn_name, args=list(fn_args.keys()))
                result = await tools.execute(fn_name, fn_args)
                emit(
                    "tool_complete",
                    agent=agent.name,
                    tool=fn_name,
                    duration_s=round(time.monotonic() - t_tool, 2),
                )
                seen_calls.add(key)

            messages.append({
                "role":         "tool",
                "tool_call_id": call["id"],
                "content":      result,
            })

    # Tool budget exhausted: one final streaming call with tools disabled to force synthesis.
    t_llm = time.monotonic()
    final_content: list[str] = []
    try:
        async for chunk in llm.stream(messages, agent.model, tools=None):
            if "token" in chunk:
                final_content.append(chunk["token"])
                yield {"type": "token", "text": chunk["token"]}
            elif "usage" in chunk:
                last_usage = chunk["usage"]
    except Exception as e:
        emit("agent_llm_error", agent=agent.name, model=agent.model, error=str(e))
        logger.error("Agent '%s' forced-synthesis stream failed: %s", agent.name, e)
        yield {"type": "error", "message": f"[{agent.name} agent error: {e}]"}
        return

    emit(
        "agent_round",
        agent=agent.name,
        model=agent.model,
        phase="synthesis_forced",
        duration_s=round(time.monotonic() - t_llm, 2),
    )
    metrics.record(
        agent=agent.name,
        model=agent.model,
        prompt_tokens=last_usage.get("prompt_tokens", 0),
        completion_tokens=last_usage.get("completion_tokens", 0),
        eval_duration_ns=0,
    )

    if not final_content:
        yield {"type": "token", "text": f"[{agent.name} agent returned no response]"}
    yield {"type": "done", "model": agent.model, "ok": True}


async def run(agent: AgentConfig, task: str, context: list[dict]) -> str:
    """Non-streaming convenience wrapper — collects all tokens from run_stream."""
    parts: list[str] = []
    error: str | None = None
    async for ev in run_stream(agent, task, context):
        t = ev.get("type")
        if t == "token":
            parts.append(ev["text"])
        elif t == "error":
            error = ev["message"]
    if error and not parts:
        return error
    return "".join(parts) or error or f"[{agent.name} agent returned no response]"


def roster() -> list[dict]:
    return [
        {"name": "Coordinator", "icon": "🧭", "model": COORDINATOR_MODEL,
         "desc": "Direct answers and fallback synthesis when agent bypass is skipped",
         "probe": "llm"},
    ] + [
        {
            "name":  agent.name.title() + " Agent",
            "icon":  agent.icon,
            "model": agent.model,
            "desc":  agent.description,
            "probe": agent.probe,
        }
        for agent in AGENTS.values()
    ]
