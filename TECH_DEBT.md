# Kronk Tech Debt

## Considered and rejected (2026-06-12 codebase review)

Decisions made deliberately — revisit only if the stated assumption breaks:

- **Splitting tool_service into per-domain services** — imperfect cohesion,
  but at single-operator scale container sprawl costs more than it buys.
- **SQLite connection pooling** (orchestrator metrics/sessions) — per-call
  connects measure as negligible next to a generation. (Until sprint M9
  `_llm_lock` serialised the pipeline; requests now run concurrently and
  session writes take their own lock, `sessions.append_exchange`.)
- **events.py schema enforcement** — ceremony without payoff at this size.
- **Removing `_execute_tool` / per-request `model` field** — kept as
  documented API/test tolerances.
- **`_llm_lock` serialisation** — superseded 2026-11-17 (sprint M9). The
  assumption broke: a household and guests on several pucks, and each
  llama.cpp server runs 4 slots with the full context each. Now one
  priority lock per model server, held for one generation, voice first
  (`docs/features/model-locks.md`); `MODEL_LOCK=pipeline` restores the old
  lock for rollback.

## Open Issues

### [HOTTUB-01] Hot tub monitor: no alerting, stale readings served as current

**Status:** Open. Corrected 2026-11-16; the entry as written on 2026-06-12
("non-functional: can't reach the spa pack") stopped being true on
2026-10-24 and was never updated (see
`docs/incidents/INVESTIGATION_2026-11-16_hottub_removal.md`).

The monitor works. `kronk-hottub-monitor.service` (reference copy in
`systemd/`) runs `hottub_monitor/monitor.py` from its own virtualenv,
connects to the spa pack, and rewrites `data/hottub/status.json` every
5 minutes; `query_hottub` reads it through `tool_service`'s `/hottub`.
The journal shows the spa module unreachable until 2026-10-24 13:50 and
connected from then on (stopped only for the M1b removal, 2026-11-15 22:31
to 2026-11-16 07:33).

What remains open:

- **No alerting.** `_on_online` / `_on_offline` only log. When it is built,
  alert via the shared `scripts/lib/notify.sh` HA path like the other
  watchers.
- **Stale data looks current.** `/hottub` returns the file as it is; if the
  monitor stops, the last reading is served as live. `query_hottub` passes
  `last_check` to the model but nothing compares it with the clock (seen
  2026-11-15: with the unit stopped, production's `/hottub` returned the
  frozen file, `online: true`, 101.0°F, as if current).
- **The virtualenv is not reproducible from the repo.** `hottub_monitor/.venv`
  is untracked, holds `geckolib==1.0.15`, and links to the host's
  `/usr/bin/python3` (3.13.7). No requirements file is tracked; an OS Python
  upgrade means rebuilding it by hand (`python3 -m venv`, then
  `pip install geckolib==1.0.15`). A copy is in
  `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`.

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

**Leftover:** the permissive Jinja template override at
`/opt/models/mistralai/devstral-template-permissive.jinja` predates the fix.
No unit uses it since the Devstral Q4 server was retired (M31, 2026-11-24;
the Q8 unit went in M24). It stays on disk beside the Devstral model file as
part of the way back (the backed-up unit names it) until Drew deletes both.

---

### [ROUTING-01] Gate-based router can't handle queries that are partially research-flavored

**Status:** Resolved 2026-08-18 — superseded by **coordinator-default
routing** (`docs/features/coordinator-default-routing.md`): the LLM gate
was deleted outright; everything a narrow shortcut doesn't pin goes to the
coordinator. The 2026-06-12 mitigation below was the intermediate step.

*(Mitigated 2026-06-12)* — the "better approach" below was implemented
as **agents-as-tools** (the hybrid variant): gate routing stays for clear-cut
cases, and the direct path's coordinator now carries `ask_<agent>` tools
(`agents.COORDINATOR`), so a router miss becomes an ordinary delegation
instead of a dead end ("I need to search…" incident, trace `bb8bd8b7`).
Verified: misroutes self-heal via ask_research; spurious-delegation rate on
pure-knowledge questions 1/5; delegation rate observable in Grafana (Tempo)
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
- Consider a hybrid: keep gate routing for clear-cut cases (home, coding, devops) and use coordinator-first only for the research/direct ambiguity
