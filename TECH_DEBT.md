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

### [LITELLM-01] LiteLLM `async_pre_call_hook` not firing in proxy mode

**Status:** Workaround in place  
**Workaround:** Permissive Jinja chat template override at `/opt/models/mistralai/devstral-template-permissive.jinja`, referenced by `--chat-template-file` in both devstral systemd units.

**Problem:**  
When switching between devstral Q8 and Q4 in Zed without clearing conversation history, the devstral Jinja template raises an exception because prior messages don't strictly alternate user/assistant roles.

The correct fix is a LiteLLM pre-call hook (`litellm/hooks.py`) that normalizes the message array before it reaches llama.cpp — merging consecutive same-role messages and appending a trailing user turn if the conversation ends on an assistant message. The hook is implemented and mounted into the container, but `async_pre_call_hook` is never invoked.

**Root cause (suspected):**  
`litellm.callbacks` is empty inside the running container despite `callbacks: ["hooks.proxy_handler_instance"]` in `litellm_settings`. The `initialize_callbacks_on_proxy()` startup path does not appear to wire custom `CustomLogger` subclasses into `proxy_logging_obj`'s pre-call pipeline.

**To investigate:**  
- Check LiteLLM proxy source for how `general_settings` vs `litellm_settings` callbacks differ
- Try registering via `custom_logger` key or another config path
- Check if `proxy_logging_obj` has a separate registration mechanism from `litellm.callbacks`

**Risk of workaround:**  
External template file is a snapshot. If a devstral model update changes the embedded chat template, the override file won't auto-update and must be re-extracted and re-patched.

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
