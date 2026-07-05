# Kronk Tech Debt

## Considered and rejected (2026-06-12 codebase review)

Decisions made deliberately — revisit only if the stated assumption breaks:

- **Splitting tool_service into per-domain services** — imperfect cohesion,
  but at single-operator scale container sprawl costs more than it buys.
- **SQLite connection pooling** (orchestrator metrics/sessions) — per-call
  connects measure as negligible while `_llm_lock` serialises the pipeline.
- **events.py schema enforcement** — ceremony without payoff at this size.
- **Removing `_execute_tool` / per-request `model` field** — kept as
  documented API/test tolerances.
- **`_llm_lock` serialisation** — intentional: one GPU, one llama.cpp slot
  budget. The lock is the scheduler.

### [HOTTUB-01] Hot tub monitor non-functional

**Status:** Parked (operator decision 2026-06-12)

The geckolib monitor can't reach the spa pack (connection retry loop), and
alerting was never implemented (dead ntfy.sh stubs removed). Revisit when
the spa-pack connectivity story is sorted; when it is, alert via the shared
`scripts/lib/notify.sh` HA path like the other watchers.

### Garmin / Withings sync stubs

**Status:** Intentional — pending Infisical rebuild. `/api/sync` and the
Withings flow are no-op stubs in health_service until secrets management
returns.

## Open Issues

### [LITELLM-01] LiteLLM `async_pre_call_hook` not firing in proxy mode — RESOLVED 2026-07-03

**Status:** Resolved. The suspected root cause below was wrong.

**Actual root cause:** the hook *was* being invoked all along — but for async
proxy calls LiteLLM passes `call_type="acompletion"`, and the hook body only
matched `call_type == "completion"`, so `_normalize` never ran and the hook
was a silent no-op. Fixed in `litellm/hooks.py` (`call_type in ("completion",
"acompletion")`) while chasing the voice-path router 400 (HA's local-intent
fallback sends non-alternating history — see `docs/VOICE_SETUP.md` timeline,
2026-07-03). Verified: a `[user, user]` message array sent directly to
LiteLLM 400'd before the fix, 200s after.

**Leftover to retire deliberately:** the permissive Jinja template override
at `/opt/models/mistralai/devstral-template-permissive.jinja` (referenced by
`--chat-template-file` in both devstral systemd units) predates the fix and
is now redundant in principle. Removing it means the stock devstral template
must tolerate whatever the now-working hook produces — test the Zed
Q8↔Q4-switch scenario before deleting. Until then it's harmless, but it's a
snapshot that won't track upstream template changes.

---

### [ROUTING-01] Gate-based router can't handle queries that are partially research-flavored

**Status:** Mitigated 2026-06-12 — the "better approach" below was implemented
as **agents-as-tools** (the hybrid variant): gate routing stays for clear-cut
cases, and the direct path's coordinator now carries `ask_<agent>` tools
(`agents.COORDINATOR`), so a router miss becomes an ordinary delegation
instead of a dead end ("I need to search…" incident, trace `bb8bd8b7`).
Verified: misroutes self-heal via ask_research; spurious-delegation rate on
pure-knowledge questions 1/5; delegation rate observable in Langfuse
(`agent.*` spans under direct-routed traces). Remaining gap: a multi-domain
query routed to a *specialist* still gets a single-domain answer — peer
handoffs are the next step if that bites in practice.

**Problem:**  
The current router classifies at the gate before any LLM reasoning occurs. For queries that are geopolitical, medical, or otherwise factual-but-ambiguous ("Why is the US at war with Iran?", "What does X policy mean for Y?"), the router can't reliably distinguish "needs live data" from "answerable from training knowledge." These tend to route to research even when a direct coordinator answer would be more useful.

**Better approach:**  
A two-phase design where the coordinator sees the query first and decides whether a tool call is actually warranted, rather than routing at the gate. The coordinator would attempt an answer and only invoke research if it determines current data is genuinely needed. This is more expensive per request but eliminates the gate classification problem entirely for ambiguous queries.

**To investigate:**  
- Evaluate whether the coordinator model (gemma-4-e4b) is capable enough to make this tool-use decision reliably
- Assess latency impact of coordinator-first vs. current router-first approach
- Consider a hybrid: keep gate routing for clear-cut cases (health, home, finance, coding, devops) and use coordinator-first only for the research/direct ambiguity
