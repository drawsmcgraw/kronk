"""Specialist-agent registry + per-agent tool-calling loop.

The `AGENTS` dict is the single source of truth: the router prompt, the
valid-route set, and the /api/agents roster are all derived from it.
"""
import asyncio
import json
import logging
import os
import re
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

import config_loader
import errors
import llm
import metrics
import origin
import speech_stream
import telemetry
import tools
from events import emit

logger = logging.getLogger(__name__)

# Inject the hourly-cached home forecast into the home agent's prompt so
# weather questions resolve in one LLM round with no tool call (2026-06
# response-time program). Past config/tuning.yaml weather_context.max_age_s
# (env WEATHER_CTX_MAX_AGE_S wins if set) we omit the injection and the
# agent falls back to the live get_weather tool.


async def weather_context() -> str | None:
    """Fresh cached forecast as a prompt block, or None (never raises)."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{tools.TOOL_SERVICE_URL}/weather/cached")
        if resp.status_code != 200:
            return None
        wx = resp.json()
        if wx.get("age_s", 1e9) > config_loader.current().tuning.weather_context.max_age_s:
            return None
        age_min = wx["age_s"] // 60
        return (
            f"[Weather data for {wx['location']}, fetched {age_min} minutes ago — "
            "answer weather questions directly from this data. Call get_weather only "
            "if the user asks about a DIFFERENT location, or about a date beyond "
            "what this data covers. If a requested date is beyond both, say so "
            "plainly — never invent forecast values]\n"
            f"{wx['summary']}"
        )
    except Exception as e:
        logger.debug("weather context unavailable: %s", e)
        return None


def _tool_narration(name: str, args: dict) -> str:
    if name.startswith("ask_"):
        return f"asking the {name[4:]} agent..."
    if name == "web_search":
        q = args.get("query", "")
        return f"searching the web for {q}" if q else "searching the web..."
    if name == "fetch_url":
        url = args.get("url", "")
        return f"reading {url}" if url else "reading that page..."
    if name == "get_weather":
        loc = args.get("location", "")
        return f"checking the weather for {loc}" if loc else "checking the weather..."
    if name.startswith("shopping_list"):
        return "checking your shopping list..."
    if name == "get_kronk_context":
        return "reading Kronk's configuration..."
    if name == "generate_diagram":
        return "generating a diagram..."
    if name == "play_music" and args.get("kind") == "another":
        return "trying something else..."
    if name == "play_music" and args.get("kind") == "genre":
        q = args.get("query", "")
        return f"picking some {q}..." if q and q != "music" else "picking something..."
    if name == "play_music":
        q = args.get("query", "")
        verb = "shuffling" if args.get("shuffle") else "putting on"
        return f"{verb} {q}" if q else "starting the music..."
    if name == "now_playing":
        return "checking what's playing..."
    if name == "remember_music_preference":
        return "noting that..."
    if name == "rate_music":
        return "thumbs down, skipping..." if args.get("thumb") == "down" else "thumbs up..."
    if name == "control_music":
        return {"pause": "pausing the music...", "stop": "pausing the music...",
                "resume": "resuming the music...", "next": "skipping this track...",
                "volume_up": "turning it up...", "volume_down": "turning it down..."
                }.get(args.get("action", ""), "adjusting playback...")
    if name == "update_magicmirror":
        return "checking the mirror and starting the update..."
    return f"running {name}..."


def _news_speech(result: str, style: str) -> str:
    """news_brief's result, which is display-ready prose, success or failure —
    the whole point of the tool is that nothing rephrases it
    (NEWS_BRIEF_PLAN). Its failure prose carries tool_service's detail and
    tools.execute's exception line, so on the speech mount those go through
    errors.failure_text like every other code-written failure (M2 fix 1)."""
    if result.startswith("[Tool news_brief error: "):
        return errors.failure_text(result, style, errors.friendly_tool_failure("news_brief"))
    if result.startswith(tools.NEWS_UNAVAILABLE):
        return errors.failure_text(result, style, errors.FRIENDLY_NEWS_UNAVAILABLE)
    if result.startswith(tools.NEWS_REFRESH_FAILED) and "\n\n" in result:
        # A failed refresh, then the cached brief: only the note is failure text.
        note, brief = result.split("\n\n", 1)
        return (errors.failure_text(note, style, errors.FRIENDLY_NEWS_REFRESH_FAILED)
                + "\n\n" + brief)
    return result


# tools.py's argument refusals for terminal tools, e.g. "play_music error:
# query is required" (brackets already stripped). No "Tool " prefix: that
# one is tools.execute's exception line.
_TOOL_BAD_REQUEST = re.compile(r"^(?!Tool )\S+ error: ")


# tool_service's refusal of a stripped request (tool_service/main.py
# STRIPPED_SENTENCE, sprint M23): /music got no terms but the user's sentence
# named something. Spoken as tool_service wrote it, with no lead-in.
STRIPPED_REFUSAL = "I didn't catch what to play."


def _terminal_speech(result: str, style: str = errors.DEBUG,
                     tool: str | None = None) -> str:
    """Speakable sentence from a terminal tool's structural result line.

    Success lines map to their sentence in both styles. Every failure line
    leaves through errors.failure_text: the music failures with their own
    sentence are the same in both styles (criterion 8); the rest are the
    friendly sentence in friendly style and today's text in debug."""
    if tool == "news_brief":
        return _news_speech(result, style)
    line = result.split("\n", 1)[0].strip().strip("[]")
    if line.startswith("Music playing: "):
        return f"Now playing {line[len('Music playing: '):]}."
    if line.startswith("Music dry run: "):
        return line[len("Music dry run: "):]      # tool_service's sentence, as-is
    if line.startswith("Station playing: "):
        return line[len("Station playing: "):]    # tools.py's sentence, as-is
    if line.startswith("Could not play music: "):
        # The detail here is already a human sentence ("The speaker may be
        # powered off") — spoken in both styles.
        detail = line[len('Could not play music: '):]
        if detail.startswith(STRIPPED_REFUSAL):
            # Already says what failed; "I couldn't play that." before it
            # would say it twice.
            return errors.failure_text(detail, style, None)
        return errors.failure_text(f"I couldn't play that. {detail}", style, None)
    if line.startswith("Music control: "):
        # tool_service's detail is already the sentence ("Paused on the
        # Office speaker.") — ends with a period so HA closes the mic.
        return line[len("Music control: "):]
    if line.startswith("Could not control music: "):
        return errors.failure_text(
            f"I couldn't do that. {line[len('Could not control music: '):]}", style, None)
    if line.startswith("Music rated: "):
        return line[len("Music rated: "):]
    if line.startswith("Could not rate music: "):
        return errors.failure_text(
            f"I couldn't rate that. {line[len('Could not rate music: '):]}", style, None)
    if line.startswith("Music taste: "):
        return line[len("Music taste: "):]        # tool_service's sentence, as-is
    if line.startswith("Could not save music taste: "):
        return errors.failure_text(
            f"I couldn't save that. {line[len('Could not save music taste: '):]}", style, None)
    if line.startswith("Now playing: "):
        return line[len("Now playing: "):]        # tool_service's sentence, as-is
    if line.startswith("Could not read the player: "):
        return errors.failure_text(
            f"I couldn't tell. {line[len('Could not read the player: '):]}", style, None)
    if line.startswith("Magic mirror update started: "):
        return f"The magic mirror is {line[len('Magic mirror update started: '):]}."
    if line.startswith("Magic mirror update refused: "):
        # A refusal, not a failure: the fixed sentence, spoken unchanged.
        return line[len("Magic mirror update refused: "):]   # tools.MM_UPDATE_REFUSAL
    if line.startswith("Could not update the magic mirror: "):
        # The detail is tool_service's preflight text (key paths, ssh
        # targets, ssh output): debug text, never spoken (M2 fix 1).
        return errors.failure_text(
            "I couldn't update the magic mirror. "
            f"{line[len('Could not update the magic mirror: '):]}",
            style, errors.FRIENDLY_MIRROR_NOT_STARTED)
    # Unrecognized shape — e.g. "Tool play_music error: ReadTimeout(...)" from
    # tools.execute on a transport failure. Spoken raw, that's a stack trace
    # read aloud; log the internals, speak a clean sentence with the cause
    # kept short in debug and dropped in friendly (2026-07-05 review P1.4).
    logger.warning("terminal tool result had no speech mapping (%d chars): %r", len(result),
                   telemetry.text(result, 300))
    if line.lower().startswith("tool ") and " error: " in line:
        cause = line.split(" error: ", 1)[1]
        return errors.failure_text(f"That didn't work — the tool failed with {cause[:120]}.",
                                   style, errors.friendly_tool_failure(tool))
    if _TOOL_BAD_REQUEST.match(line):
        return errors.failure_text(line, style, errors.friendly_tool_bad_request(tool))
    return errors.failure_text(line, style, errors.friendly_tool_failure(tool))


TALKIE_MODEL         = os.getenv("TALKIE_MODEL",         "talkie")
COORDINATOR_MODEL    = os.getenv("COORDINATOR_MODEL",    "mistral-nemo:12b")
RESEARCH_AGENT_MODEL = os.getenv("RESEARCH_AGENT_MODEL", COORDINATOR_MODEL)
CODING_AGENT_MODEL   = os.getenv("CODING_AGENT_MODEL",   COORDINATOR_MODEL)
DEVOPS_AGENT_MODEL   = os.getenv("DEVOPS_AGENT_MODEL",   COORDINATOR_MODEL)

# Round budgets and the repeat-call nudge threshold live in config/tuning.yaml
# (agent_loop); apply_config copies the budgets onto the registry.


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
    # Set from config/prompts/<name>.md by apply_config for every agent in
    # AGENTS and the coordinator; given inline only by agents built in tests.
    system_prompt: str = field(default="")
    tool_names: list[str] = field(default_factory=list)
    model: str = field(default="")
    # Tool-use round budget: config/tuning.yaml agent_loop.max_rounds, set by
    # apply_config for every registry agent; agents built in tests get the
    # configured default.
    max_rounds: int = field(default_factory=lambda: (
        config_loader.current().tuning.agent_loop.max_rounds.default))
    # Tools whose result IS the final answer: run_stream speaks the result
    # verbatim and ends the turn instead of handing it back to the model.
    # Structural guardrail — small models otherwise re-call the tool or
    # contradict its result (hallucinated "now playing" after a failure).
    terminal_tools: frozenset = field(default=frozenset())
    # False: when this agent was routed to and fails, its failure is the
    # reply (errors.render, M2 wording), instead of the coordinator being
    # told and answering. For an agent asked for by name whose answer no
    # other model can give (talkie, M24): on dev the coordinator answered
    # "ask talkie ..." itself 2 times in 10 and named the failure in 4.
    coordinator_fallback: bool = True

    def __post_init__(self):
        if not self.model:
            self.model = COORDINATOR_MODEL

    def tool_defs(self) -> list[dict]:
        return [t for t in tools.TOOL_DEFINITIONS if t["function"]["name"] in self.tool_names]


AGENTS: dict[str, AgentConfig] = {
    "research": AgentConfig(
        name="research",
        description="Web search, current events, news, online information, URL lookups, and current prices or rates (utility, market, retail) — anything needing live external data",
        routing_hint="Lookups against the live web: news, prices, scores, events, current officeholders or other currently-held positions, but ALSO any factual lookup where verbatim precision matters (quotes, statistics, specific dates, lyrics, biographies, technical definitions). NOT for operations on text the user already provided (translation, summarization of a pasted paragraph, rewriting, math) — those go direct.",
        icon="🔍",
        probe="tools",
        # system prompt: config/prompts/research.md ({{max_rounds}} in it is max_rounds)
        # round budget: config/tuning.yaml agent_loop.max_rounds.research
        tool_names=["web_search", "fetch_url"],
        model=RESEARCH_AGENT_MODEL,
    ),
    "home": AgentConfig(
        name="home",
        description="Weather lookups, shopping list management, hot tub status, solar panel health and energy production (kWh), playing music and controlling playback (stop, pause, resume, skip, volume), and updating or upgrading the magic mirror",
        routing_hint="weather, forecast, shopping list, hot tub, spa, solar, solar panels, inverters, play music, songs, albums, speakers, stop, pause, resume, skip, volume, updating the magic mirror",
        icon="🏠",
        probe="tools",
        # system prompt: config/prompts/home.md
        tool_names=["get_weather", "shopping_list_view", "shopping_list_add", "shopping_list_remove", "shopping_list_clear", "query_hottub", "solar_status", "solar_detail", "solar_energy", "play_music", "control_music", "now_playing", "rate_music", "remember_music_preference", "update_magicmirror"],
        terminal_tools=frozenset({"play_music", "control_music", "now_playing", "rate_music", "remember_music_preference", "update_magicmirror"}),
    ),
    "assistant": AgentConfig(
        name="assistant",
        description="Kronk's own architecture, services, configuration, and generating diagrams",
        routing_hint="Kronk's own architecture or configuration",
        icon="🤖",
        probe="llm",
        # system prompt: config/prompts/assistant.md
        tool_names=["get_kronk_context", "generate_diagram"],
    ),
    "coding": AgentConfig(
        name="coding",
        description="Writing code, debugging, explaining code, architecture questions, shell scripts, anything programming-related",
        routing_hint="writing or debugging code, programming questions",
        icon="💻",
        probe="llm",
        # system prompt: config/prompts/coding.md
        tool_names=["web_search", "fetch_url"],
        model=CODING_AGENT_MODEL,
    ),
    "talkie": AgentConfig(
        name="talkie",
        description="Talkie-1930: a vintage language model trained on pre-1931 text. Answers in period-appropriate language with knowledge limited to December 31, 1930. Only invoked when explicitly requested by name.",
        routing_hint="explicitly requested by name only — e.g. 'ask talkie' or 'what does talkie think'",
        icon="📻",
        probe="llm",
        # system prompt: config/prompts/talkie.md
        tool_names=[],
        model=TALKIE_MODEL,
        coordinator_fallback=False,   # M24: a failure is said, not answered around
    ),
    "devops": AgentConfig(
        name="devops",
        # The coordinator reads this as ask_devops's description. The mirror
        # clause is why "check the uptime on the mirror" reaches devops rather
        # than home (whose description ends "updating or upgrading the magic
        # mirror"). Its "never updating or upgrading it" half keeps upgrade
        # requests on home's update_magicmirror: without it, "please upgrade
        # the mirror to the latest version" went to ask_devops 9/10; with it
        # but without "upgrading" in home's description, the coordinator
        # refused upgrades itself 5/15 (docs/sprint/M4-fix1.md). The last
        # sentence is how documentation reaches a devops task (M4).
        description="SSH commands, server administration, Linux troubleshooting, infrastructure, Docker, systemd, networking, host automation, and read-only checks on the magic mirror (uptime, status, logs), never updating or upgrading it: that is ask_home. It has no web access: when a task also needs documentation, ask research first and put its findings in the query.",
        routing_hint="servers, SSH, Docker, Linux, nginx, web servers, systemd, networking, infrastructure, the magic mirror (status/logs/uptime/diagnostics — anything other than updating it)",
        icon="🛠️",
        probe="llm",
        # system prompt: config/prompts/devops.md
        # No web tools (sprint M4, decided by Drew 2026-11-09): web text
        # and command execution never share one loop, so a fetched page
        # cannot steer the next command. Documentation comes in through the
        # coordinator (ask_research, then ask_devops with the findings).
        # http_check (sprint M5) can GET any address the host reaches (no
        # allow-list, Drew 2026-11-16), so it lives here too and never on an
        # agent with web tools.
        # tests/test_agent_tool_separation.py fails if they are recombined.
        tool_names=["remote_exec", "http_check"],
        model=DEVOPS_AGENT_MODEL,
    ),
}


# ── Agents-as-tools: specialists callable by the coordinator ────────────────
#
# Each specialist is exposed as an `ask_<name>` tool so the coordinator can
# delegate mid-answer — a router miss onto the direct path becomes an
# ordinary tool call instead of a dead end ("I need to search…", 2026-06-12
# incident / TECH_DEBT ROUTING-01), and multi-domain questions compose.
#
# Depth is structurally capped at 2: ONLY the coordinator carries ask_*
# tools; specialists keep their own tool lists, so a sub-agent can never
# delegate further. talkie is excluded (explicit-invocation persona; the
# router shortcut owns it).

_AGENT_TOOL_EXCLUDE = {"talkie"}


def agent_tool_defs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"ask_{a.name}",
                "description": f"Consult Kronk's {a.name} specialist. {a.description}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "A complete, self-contained question for the specialist — include all context it needs.",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
        for a in AGENTS.values()
        if a.name not in _AGENT_TOOL_EXCLUDE
    ]


# ── Escalation terminal (Phase 2, COORDINATOR_ROUTING_PLAN) ─────────────────
# A shortcut-pinned specialist can't answer the out-of-domain half of a
# composite ("how much money did my SOLAR PANELS make" pins to home, which
# has no rates — trace 02e8b817). The escalate terminal lets it hand the
# whole request back: the loop yields an `escalated` event and the pipeline
# re-enters at the coordinator, note attached. NOT a peer tool — the
# specialist never learns other agents exist; it only declines. Offered
# ONLY on top-level pinned runs (run_stream allow_escalation=True): the
# coordinator and ask_*-delegated runs never see it, so escalation is
# structurally once-per-request.

ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": (
            "End your turn and hand this request to the Kronk coordinator "
            "because part of it needs data or tools outside your domain. "
            "Use ONLY when your own tools cannot cover part of the request "
            "— never for requests you can fully answer yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": (
                        "One sentence: what you CAN provide, and which part "
                        "you cannot."
                    ),
                },
            },
            "required": ["note"],
        },
    },
}

ESCALATION_GUIDANCE = (
    "ESCALATION: you also have an `escalate` tool. If part of this request "
    "needs data or tools outside your domain (prices, rates, web facts, "
    "another system's state), call escalate with a one-sentence note of "
    "what you CAN provide and what you cannot — instead of guessing, using "
    "placeholders, or answering only half the question. If your tools fully "
    "cover the request, do NOT escalate — just answer."
)


COORDINATOR = AgentConfig(
    name="coordinator",
    description="Direct answers; delegates to specialists for live or personal data",
    routing_hint="(not routable — coordinator is the direct-path handler)",
    icon="🧭",
    probe="llm",
    # system prompt: config/prompts/coordinator.md
    # news_brief is the coordinator's one service tool, and it's TERMINAL:
    # a briefing's text must reach the user without a synthesis round
    # compressing it (rid 99ce926c: a brief re-summarized to 754 chars).
    terminal_tools=frozenset({"news_brief"}),
    tool_names=[],  # filled below — ask_* names aren't in tools.TOOL_DEFINITIONS
)


def _coordinator_tool_defs() -> list[dict]:
    """ask_* agent-tools plus the coordinator's own service tools."""
    own = [d for d in tools.TOOL_DEFINITIONS
           if d["function"]["name"] in ("news_brief",)]
    return agent_tool_defs() + own


COORDINATOR.tool_names = [d["function"]["name"] for d in _coordinator_tool_defs()]
# AgentConfig.tool_defs() only knows tools.TOOL_DEFINITIONS; give the
# coordinator its combined menu directly.
COORDINATOR.tool_defs = _coordinator_tool_defs  # type: ignore[method-assign]


# ── config/ (sprint M21) ─────────────────────────────────────────────────────
# The prompts live in config/prompts/<name>.md and the round budgets in
# config/tuning.yaml. apply_config copies them onto the registry above at
# import (config_loader's first load) and after every successful POST
# /config/reload, for the roster and anything that reads the registry. A run
# reads them through prompt_and_rounds() (below), from the config its
# request started with.

def apply_config(cfg: "config_loader.Config") -> None:
    for agent in [*AGENTS.values(), COORDINATOR]:
        agent.max_rounds = cfg.tuning.agent_loop.max_rounds.for_agent(agent.name)
    for name, text in cfg.prompts.items():
        agent = COORDINATOR if name == "coordinator" else AGENTS[name]
        agent.system_prompt = config_loader.fill(text, max_rounds=agent.max_rounds)


config_loader.on_change(apply_config)


def prompt_and_rounds(agent: AgentConfig) -> tuple[str, int]:
    """(system prompt, round budget) for one run of `agent`. For the registry
    agents and the coordinator they come from config_loader.current(), the
    config the request started with (sprint M21 step 2, reload isolation):
    a reload during the request does not change them part way. An agent
    built elsewhere (tests) keeps its own fields."""
    registry = COORDINATOR if agent.name == "coordinator" else AGENTS.get(agent.name)
    cfg = config_loader.current()
    if registry is not agent or agent.name not in cfg.prompts:
        return agent.system_prompt, agent.max_rounds
    rounds = cfg.tuning.agent_loop.max_rounds.for_agent(agent.name)
    return config_loader.fill(cfg.prompts[agent.name], max_rounds=rounds), rounds


# ── Loop texts (model-facing; shown read-only on /settings) ─────────────────
# What run_stream tells the model when a guardrail fires. Named here, beside
# the loop, so the /settings page shows the exact text the loop sends; each
# is filled with str.format at its one call site. Structural guardrails
# (tenet 5): the code path decides, the text only explains.
TOOL_REJECTED_TEXT = ("[TOOL: {tool} — REJECTED: not available to the {agent} agent. "
                      "It was not run. Use only the tools you were given.]")
DUPLICATE_CALL_TEXT = "[{tool} was already called with these exact arguments this turn; use the earlier result]"
NO_SUCH_SPECIALIST_TEXT = "[no such specialist: {name}]"
REPEAT_CALL_TEXT = ("\n\n[NOTE: that was call #{n} to {tool} this turn. "
                    "Do not call {tool} again — answer from the results "
                    "above, or use a different tool if one fits better.]")
BUDGET_EXHAUSTED_TEXT = (
    "[Your tool budget for this question is exhausted — no more tool "
    "calls are possible. Using ONLY the information gathered above, "
    "give your best final answer now. If parts are missing, say which "
    "parts you could not look up. Do not write tool-call syntax.]"
)


# The LLM router (ROUTING_PROMPT / VALID_ROUTES / build_routing_prompt) was
# removed 2026-08-18: routing is deterministic shortcuts or the coordinator
# (docs/plans/COORDINATOR_ROUTING_PLAN.md). The per-agent `routing_hint`
# fields above remain as documentation of each agent's intended domain.


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


# The house's zone for the date and time in kronk_facts() (work item M35).
# The containers run in UTC and stay there (log and store timestamps); the
# prompt line alone is told the house's time. The same zone is also written
# as NEWS_TZ, SOLAR_TZ and MUSIC_TASTE_TZ (tool_service) and as TZ in
# docker-compose.ha.yml and docker-compose.ma.yml.
HOUSE_TZ_DEFAULT = "America/New_York"

# Zone names already warned about, so a bad KRONK_TZ logs once per process,
# not once per request. Tests reset it.
_tz_warned: set[str] = set()


def _utc_now() -> datetime:
    """The instant kronk_facts() states, as an aware UTC datetime. The one
    seam for the clock: tests patch this, never the process TZ."""
    return datetime.now(timezone.utc)


def house_zone():
    """The house's zone: KRONK_TZ, else America/New_York. A name that does
    not load (unknown, empty, a path) falls back to the default with one
    warning; if the default itself does not load (no zone database in the
    image), UTC, with the error logged once. Never raises."""
    name = os.getenv("KRONK_TZ") or HOUSE_TZ_DEFAULT
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        if name != HOUSE_TZ_DEFAULT:
            if name not in _tz_warned:
                _tz_warned.add(name)
                logger.warning("KRONK_TZ=%r does not load (%s); using %s",
                               name, e, HOUSE_TZ_DEFAULT)
            try:
                return ZoneInfo(HOUSE_TZ_DEFAULT)
            except (ZoneInfoNotFoundError, ValueError) as e2:
                e = e2
        if HOUSE_TZ_DEFAULT not in _tz_warned:
            _tz_warned.add(HOUSE_TZ_DEFAULT)
            logger.error("default zone %s does not load (%s); the ambient "
                         "facts state the time in UTC", HOUSE_TZ_DEFAULT, e)
        return timezone.utc


def kronk_facts() -> str:
    """Ambient facts every Kronk path should know — appended to system prompts.

    Single source of truth so a new fact (timezone, household names, …) is
    added once and reaches the router-bypass coordinator path AND every
    specialist agent. Re-evaluated per request so the date/time stays live.
    """
    location = os.getenv("LOCATION", "Laurel, MD")
    # Without this the model guesses the date from training data — observed
    # confidently claiming "next Tuesday" was in October (2026-06-12 incident).
    # The house's zone, not the container's (UTC): M35.
    now = _utc_now().astimezone(house_zone())
    today = now.strftime("%A, %B %-d, %Y")
    clock = now.strftime("%-I:%M %p %Z")
    return (
        "[Kronk ambient facts — assume these unless the user says otherwise]\n"
        f"- Today is {today}; the local time is {clock}.\n"
        f"- Home location: {location}\n"
        "- Default to this location for weather, news, traffic, time-of-day, etc.\n"
        "  When a location-taking tool is available and the user did not specify "
        "one, call the tool without the location argument; it will use the home default.\n"
        "- Resolve relative dates (tomorrow, next Tuesday, this weekend) against "
        "today's date above — never guess the date from memory."
    )


def _cached_tokens(usage: dict) -> int | None:
    """Prompt tokens llama.cpp took from its cache for this generation, as
    LiteLLM passes them through (usage.prompt_tokens_details.cached_tokens;
    prompt_tokens minus this is what the server evaluated). None when the
    server did not say (sprint M9: cache reuse is measured per generation)."""
    details = usage.get("prompt_tokens_details") or {}
    n = details.get("cached_tokens") if isinstance(details, dict) else None
    return n if isinstance(n, int) else None


def _usage(usage: dict) -> dict:
    """Token counts for a generation span: input, output, cached."""
    return {
        "input":  usage.get("prompt_tokens", 0),
        "output": usage.get("completion_tokens", 0),
        "cached": _cached_tokens(usage),
    }


def _empty_reply(agent: "AgentConfig", style: str) -> str:
    """The model streamed no token and no tool call: say so (M2 fix 1)."""
    emit("agent_empty_reply", agent=agent.name, model=agent.model)
    return errors.failure_text(f"[{agent.name} agent returned no response]",
                               style, errors.FRIENDLY_EMPTY_REPLY)


async def run_stream(agent: AgentConfig, task: str, context: list[dict],
                     system_extra: str | None = None,
                     history_messages: list[dict] | None = None,
                     error_style: str | None = None,
                     allow_escalation: bool = False,
                     delegated: bool = False):
    """Run an agent's tool-calling loop with unified streaming.

    Used by specialists AND the coordinator (which carries ask_* agent-tools
    instead of service tools). system_extra: caller-supplied additions to the
    system prompt (shared state, uploaded files, weather context, …).
    history_messages: prior turns inserted as real chat messages (coordinator
    path — keeps the prompt prefix append-only for llama.cpp cache reuse);
    `context` embeds turns as system text instead (specialist style).

    Async generator. Yielded events:
      {"type": "token",     "text": str}              — incremental content token
      {"type": "narration", "text": str}              — pre-tool status string
      {"type": "delegating","agent": str}             — ask_* call starting
      {"type": "error",     "message": str}           — terminal; no more events follow
      {"type": "escalated", "note": str}              — terminal; specialist handed the
                                                        request back (allow_escalation only)
      {"type": "done",      "model": str, "ok": bool} — terminal

    One streaming LLM call per round. Tokens stream as they arrive; tool_calls
    are accumulated from the stream and executed at end-of-round. A round with
    no accumulated tool_calls terminates the loop.
    """
    if error_style is None:
        # Not handed one (a specialist run via ask_*): the running request's
        # style, from origin.voice — so a delegated failure on the speech
        # mount is phrased for a speaker too.
        error_style = errors.current_style()
    # Who reads this run's non-terminal text. A delegated run (run_delegated)
    # hands it to the coordinator model, so an empty reply stays the debug
    # marker the coordinator has always seen; its terminal results are
    # relayed to the user verbatim, so those keep error_style.
    user_style = errors.DEBUG if delegated else error_style
    agent_tool_defs = agent.tool_defs() or None
    if allow_escalation:
        # Top-level pinned specialist runs only (see ESCALATE_TOOL comment) —
        # ask_*-delegated runs and the coordinator never get this tool.
        agent_tool_defs = (agent_tool_defs or []) + [ESCALATE_TOOL]
    # The tools this run offered the model. A call to anything else is
    # rejected below without running it: the tool list sent to the LLM is a
    # request, not a guarantee, and a model can emit any name (sprint M4 —
    # the devops loop must never run web_search or fetch_url).
    offered = {d["function"]["name"] for d in agent_tool_defs or []}

    system_prompt, max_rounds = prompt_and_rounds(agent)
    system_content = system_prompt + "\n\n" + kronk_facts()
    if allow_escalation:
        system_content += "\n\n" + ESCALATION_GUIDANCE
    if system_extra:
        system_content += "\n\n" + system_extra
    if agent.name == "home":
        wx_ctx = await weather_context()
        if wx_ctx:
            system_content += "\n\n" + wx_ctx
    if context:
        ctx_lines = [f"{m['role'].upper()}: {m['content']}" for m in context if m.get("content")]
        system_content += "\n\n[Recent conversation context]\n" + "\n".join(ctx_lines)

    messages: list[dict] = [{"role": "system", "content": system_content}]
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": task})

    seen_calls: set[str] = set()
    tool_call_counts: dict[str, int] = {}
    last_usage: dict = {}
    # The reply-length cap on the speech mount (M24, config/voice.yaml
    # max_reply_tokens): Talkie generates about 8 tokens a second on the
    # CPU, so an uncapped long answer passes the voice deadline. Sent only
    # when set, so a call without one is unchanged.
    max_tokens = (config_loader.current().voice.max_reply_tokens.for_agent(agent.name)
                  if origin.voice.get() else None)
    cap = {"max_tokens": max_tokens} if max_tokens is not None else {}

    # Parent: the current span (an ask_* tool span when delegated, the
    # pipeline root otherwise) so a delegation nests under its ask_* call.
    agent_span = telemetry.current().child_span(
        f"agent.{agent.name}", input=task, metadata={"model": agent.model, "agent": agent.name},
    )
    last_round_text = ""

    try:
        # Tool-using rounds: up to max_rounds streaming calls with tools enabled.
        # If a round ends without tool_calls, we're done — content already streamed.
        # If all rounds produce tool_calls, fall through to forced synthesis below.
        for round_idx in range(max_rounds):
            round_content: list[str] = []
            round_tool_calls: list[dict] = []
            finish_reason: str | None = None
            t_llm = time.monotonic()
            gen = agent_span.child_generation(
                f"llm.{agent.model}", model=agent.model, input=messages,
                metadata={"round": round_idx + 1},
            )

            try:
                with gen.current():   # the LLM HTTP call hangs under this round
                    async with aclosing(llm.stream(messages, agent.model, agent_tool_defs, **cap)) as chunks:
                        async for chunk in chunks:
                            if "token" in chunk:
                                gen.first_token()
                                round_content.append(chunk["token"])
                                yield {"type": "token", "text": chunk["token"]}
                            elif "tool_calls" in chunk:
                                # NOTE: llm.stream yields tool_calls once at end-of-stream,
                                # so this is NOT a first-token signal — don't mark TTFT here
                                # or pure-tool rounds report TTFT ≈ full round duration.
                                round_tool_calls = chunk["tool_calls"]
                            elif "usage" in chunk:
                                last_usage = chunk["usage"]
                            elif "finish_reason" in chunk:
                                finish_reason = chunk["finish_reason"]
            except (asyncio.CancelledError, GeneratorExit):
                gen.end(status_message="stopped: the request ended (deadline or client gone)")   # sprint M9: close it, then go
                raise
            except Exception as e:
                gen.end(level="ERROR", status_message=str(e)[:200])
                emit("agent_llm_error", agent=agent.name, model=agent.model, error=str(e))
                logger.error("Agent '%s' stream failed: %s", agent.name, e)
                agent_span.end(level="ERROR", status_message=str(e)[:200])
                yield {"type": "error", "message": f"[{agent.name} agent error: {e}]"}
                return

            phase = "synthesis" if not round_tool_calls else f"plan_{round_idx + 1}"
            gen.end(
                output={
                    "content":    "".join(round_content),
                    "tool_calls": [tc["function"]["name"] for tc in round_tool_calls],
                },
                usage=_usage(last_usage),
                metadata={"phase": phase},
            )
            emit(
                "agent_round",
                agent=agent.name,
                model=agent.model,
                phase=phase,
                duration_s=round(time.monotonic() - t_llm, 2),
                prompt_tokens=last_usage.get("prompt_tokens"),
                cached_tokens=_cached_tokens(last_usage),
                completion_tokens=last_usage.get("completion_tokens"),
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
                last_round_text = "".join(round_content)
                if max_tokens is not None and finish_reason == "length":
                    # Stopped at the cap: the unfinished last sentence is
                    # dropped (still held on the speech mount, so it is
                    # never spoken); a reply with no complete sentence is
                    # kept as it is.
                    tail = speech_stream.unfinished_tail(last_round_text)
                    emit("reply_capped", agent=agent.name, model=agent.model,
                         max_tokens=max_tokens, dropped_chars=len(tail))
                    if tail:
                        last_round_text = last_round_text[:-len(tail)]
                        yield {"type": "retract", "chars": len(tail), "preview": tail[:160]}
                if not round_content:
                    # "empty" tells run_delegated the text is a failure marker.
                    yield {"type": "token", "text": _empty_reply(agent, user_style)}
                    yield {"type": "done", "model": agent.model, "ok": True, "empty": True}
                    return
                yield {"type": "done", "model": agent.model, "ok": True}
                return

            # Execute tools, then loop for the next round.
            #
            # Content produced in a round that ends with tool calls is never the
            # answer — the loop continues after the tool result, or a terminal
            # tool speaks — so it is RETRACTED before anything speaks or stores
            # it. In practice it is leaked thinking: the server's reasoning
            # budget closes the channel mid-thought and the model finishes out
            # loud (2026-09-14, "Pause" answered with "5. Construct the tool
            # call…"; TOOL_ROUND_RETRACT_PLAN). The model still gets its own
            # text back in the transcript below; only the user-facing copy goes.
            # Streaming clients that can't retract keep it; the event log keeps
            # a preview so leaks stay findable (tenet 7).
            retracted = "".join(round_content)
            if retracted:
                emit("round_content_retracted", agent=agent.name,
                     chars=len(retracted), preview=telemetry.text(retracted, 160))
                yield {"type": "retract", "chars": len(retracted), "preview": retracted[:160]}
            messages.append(_build_assistant_msg("".join(round_content), round_tool_calls))

            for call in round_tool_calls:
                fn_name = call["function"]["name"]
                fn_args = call["function"]["arguments"] or {}

                # Escalation terminal: the specialist is handing the whole
                # request back — end the turn immediately; the pipeline
                # re-enters at the coordinator with the note.
                if fn_name == "escalate" and allow_escalation:
                    note = str(fn_args.get("note") or "").strip() or (
                        f"the {agent.name} specialist could not fully answer this"
                    )
                    emit("escalate", agent=agent.name, note=telemetry.text(note, 120))
                    agent_span.end(output=f"[escalated] {note}")
                    yield {"type": "escalated", "note": note}
                    return

                if fn_name not in offered:
                    emit("tool_rejected", agent=agent.name, tool=fn_name)
                    agent_span.child_span(f"tool.{fn_name}", input=fn_args).end(
                        level="ERROR",
                        status_message=f"rejected: not available to the {agent.name} agent")
                    logger.warning("Agent '%s' called %s, which it does not hold; rejected",
                                   agent.name, fn_name)
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": call["id"],
                        "content":      TOOL_REJECTED_TEXT.format(tool=fn_name, agent=agent.name),
                    })
                    continue

                key = _args_key(fn_name, fn_args)
                # failed: set by the code that produced the result (a tool
                # handler via tools.execute, or the delegation below), never
                # read off the text (M2 fix 2).
                failed = False
                if key in seen_calls:
                    result = DUPLICATE_CALL_TEXT.format(tool=fn_name)
                else:
                    yield {"type": "narration", "text": _tool_narration(fn_name, fn_args)}
                    if fn_name.startswith("ask_"):
                        # Structured delegation signal — the pipeline maps
                        # this to a labeled UI stage entry. Narration alone
                        # went dark in the UI after the Aug-18 routing
                        # collapse moved delegation inside this loop.
                        yield {"type": "delegating", "agent": fn_name[4:]}
                    t_tool = time.monotonic()
                    emit("tool_call", agent=agent.name, tool=fn_name, args=list(fn_args.keys()))
                    tool_span = agent_span.child_span(f"tool.{fn_name}", input=fn_args)
                    delegated_terminal = False
                    try:
                        with tool_span.current():   # tool_service joins via traceparent
                            if fn_name.startswith("ask_"):
                                # Agent-as-tool: delegate to a specialist. The
                                # sub-agent's own span/metrics come from its run.
                                sub = AGENTS.get(fn_name[4:])
                                if sub is None:
                                    result = NO_SUCH_SPECIALIST_TEXT.format(name=fn_name[4:])
                                    failed = True
                                else:
                                    result, delegated_terminal, failed = await run_delegated(
                                        sub, fn_args.get("query") or task, [])
                            else:
                                outcome = await tools.execute(fn_name, fn_args)
                                result, failed = outcome.text, outcome.failed
                    except (asyncio.CancelledError, GeneratorExit):
                        tool_span.end(status_message="stopped: the request ended (deadline or client gone)")
                        raise
                    except Exception as e:
                        tool_span.end(level="ERROR", status_message=str(e)[:200])
                        raise
                    tool_span.end(output=result[:2000] if isinstance(result, str) else result)
                    emit(
                        "tool_complete",
                        agent=agent.name,
                        tool=fn_name,
                        duration_s=round(time.monotonic() - t_tool, 2),
                    )
                    seen_calls.add(key)

                    # A specialist that ended on a TERMINAL tool has already
                    # spoken the answer ("Now playing …", "I couldn't play
                    # that. Playback failed for …"). Relay it verbatim and
                    # end this turn — a synthesis round here is where the
                    # cause got reworded into "can't play music right now"
                    # (ERROR_SURFACING_PLAN; the news_brief rule generalized).
                    if delegated_terminal:
                        emit("terminal_passthrough", agent=agent.name, tool=fn_name)
                        last_round_text = result
                        yield {"type": "token", "text": result}
                        yield {"type": "done", "model": agent.model, "ok": True, "terminal": fn_name}
                        return

                    # Terminal tool: the result IS the answer — speak it and
                    # end the turn (see AgentConfig.terminal_tools).
                    if fn_name in agent.terminal_tools:
                        # Set the span output too — terminal turns otherwise
                        # end the agent span with output=None and the spoken
                        # answer is invisible in the trace (review P2.5).
                        last_round_text = _terminal_speech(result, error_style,
                                                           tool=fn_name)
                        yield {"type": "token", "text": last_round_text}
                        # `terminal` lets a delegating coordinator relay this
                        # verbatim instead of synthesizing (run_delegated).
                        yield {"type": "done", "model": agent.model, "ok": True, "terminal": fn_name}
                        return

                    # Repeat-call guardrail: models re-issue the same tool
                    # with reworded args (exact-dup dedup never fires) until
                    # the budget dies. Prompt instructions don't stop it —
                    # append the stop order to the result itself.
                    tool_call_counts[fn_name] = tool_call_counts.get(fn_name, 0) + 1
                    n = tool_call_counts[fn_name]
                    nudge_at = config_loader.current().tuning.agent_loop.repeat_tool_nudge_at
                    if n >= nudge_at and isinstance(result, str):
                        result += REPEAT_CALL_TEXT.format(n=n, tool=fn_name)

                # Friendly style: the phrasing rule rides on a result its code
                # flagged as failed, never on the system prompt and never on a
                # successful result, so successful requests see the same input
                # in both styles. The result keeps its full detail
                # (errors.FRIENDLY_TOOL_PHRASING).
                if error_style == errors.FRIENDLY and failed:
                    result += "\n\n" + errors.FRIENDLY_TOOL_PHRASING

                messages.append({
                    "role":         "tool",
                    "tool_call_id": call["id"],
                    "content":      result,
                })

        # Tool budget exhausted: one final call with tools disabled to force
        # synthesis. Two guardrails from the 2026-06-12 budget-cliff incident
        # (a mid-plan model, silently stripped of tools, emitted raw
        # tool-call syntax as its "answer"):
        #   1. Tell the model what just happened and what to do instead.
        #   2. Buffer the output (this path is rare; streaming matters less
        #      than correctness) and scrub leaked tool-call syntax.
        messages.append({
            "role": "user",
            "content": BUDGET_EXHAUSTED_TEXT,
        })
        t_llm = time.monotonic()
        final_content: list[str] = []
        gen = agent_span.child_generation(
            f"llm.{agent.model}", model=agent.model, input=messages,
            metadata={"round": max_rounds + 1, "phase": "synthesis_forced"},
        )
        try:
            with gen.current():
                async with aclosing(llm.stream(messages, agent.model, tools=None)) as chunks:
                    async for chunk in chunks:
                        if "token" in chunk:
                            gen.first_token()
                            final_content.append(chunk["token"])
                        elif "usage" in chunk:
                            last_usage = chunk["usage"]
        except (asyncio.CancelledError, GeneratorExit):
            gen.end(status_message="stopped: the request ended (deadline or client gone)")
            raise
        except Exception as e:
            gen.end(level="ERROR", status_message=str(e)[:200])
            emit("agent_llm_error", agent=agent.name, model=agent.model, error=str(e))
            logger.error("Agent '%s' forced-synthesis stream failed: %s", agent.name, e)
            agent_span.end(level="ERROR", status_message=str(e)[:200])
            yield {"type": "error", "message": f"[{agent.name} agent error: {e}]"}
            return

        final_text = "".join(final_content)
        if re.search(r"<\|?/?tool_call|<start_of_function_call", final_text):
            emit("tool_syntax_leak", agent=agent.name, fragment=telemetry.text(final_text, 120))
            logger.warning("Agent '%s' leaked tool-call syntax in forced synthesis; scrubbed", agent.name)
            final_text = (
                "I ran out of research steps before completing every lookup. "
                "Here is what I confirmed so far — ask me to continue for the rest."
            )
        if final_text:
            yield {"type": "token", "text": final_text}
        final_content = [final_text] if final_text else []

        gen.end(
            output=final_text,
            usage=_usage(last_usage),
        )
        emit(
            "agent_round",
            agent=agent.name,
            model=agent.model,
            phase="synthesis_forced",
            duration_s=round(time.monotonic() - t_llm, 2),
            prompt_tokens=last_usage.get("prompt_tokens"),
            cached_tokens=_cached_tokens(last_usage),
            completion_tokens=last_usage.get("completion_tokens"),
        )
        metrics.record(
            agent=agent.name,
            model=agent.model,
            prompt_tokens=last_usage.get("prompt_tokens", 0),
            completion_tokens=last_usage.get("completion_tokens", 0),
            eval_duration_ns=0,
        )

        last_round_text = "".join(final_content)
        if not final_content:
            yield {"type": "token", "text": _empty_reply(agent, user_style)}
            yield {"type": "done", "model": agent.model, "ok": True, "empty": True}
        else:
            yield {"type": "done", "model": agent.model, "ok": True}
    finally:
        # Covers normal returns, error returns above, and client disconnects
        # (GeneratorExit) — _Obs.end() is idempotent via the .ended flag.
        agent_span.end(output=last_round_text or None)


async def run_delegated(agent: AgentConfig, task: str,
                        context: list[dict]) -> tuple[str, bool, bool]:
    """Run a specialist for a delegating agent:
    (text, ended_on_terminal_tool, failed).

    The terminal flag is how a terminal result (spoken verbatim by the
    specialist) survives the hop back to the coordinator without a
    synthesis round. failed is True when the specialist produced no answer
    — it errored before any text, or returned nothing — so the delegating
    loop treats the text as a failed tool result (M2 fix 2)."""
    text = ""
    error: str | None = None
    terminal = False
    empty = False
    async with aclosing(run_stream(agent, task, context, delegated=True)) as run:
        async for ev in run:
            t = ev.get("type")
            if t == "token":
                text += ev["text"]
            elif t == "retract":
                # tool-round content (usually leaked thinking) — never the answer
                text = text[:-ev["chars"]] if ev["chars"] <= len(text) else ""
            elif t == "error":
                error = ev["message"]
            elif t == "done":
                terminal = terminal or bool(ev.get("terminal"))
                empty = empty or bool(ev.get("empty"))
    if error and not text:
        return error, False, True
    if not text:
        return f"[{agent.name} agent returned no response]", False, True
    return text, terminal, empty


async def run(agent: AgentConfig, task: str, context: list[dict]) -> str:
    """Non-streaming convenience wrapper — collects all tokens from run_stream."""
    text, _, _ = await run_delegated(agent, task, context)
    return text


def roster() -> list[dict]:
    return [
        {"name": "Coordinator", "icon": "🧭", "model": COORDINATOR_MODEL,
         "desc": "Default lane: answers directly or delegates to specialists via ask_* tools",
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
