# Verbose error reporting — Plan (ROADMAP item 2)

**Status:** shipped 2026-07-05 (branch `p1-verbose-errors`) — see
`../features/verbose-errors.md`. Operator validation of spoken-failure
wording in the kitchen still pending.

The audit this item called for was done as part of the 2026-07-05 codebase
review — `docs/REVIEW_2026-07-05_codebase.md` P1 section (plus P0.3/P0.5,
already shipped). This doc records the design decisions; the review holds
the per-finding detail.

## Goal

Every user-facing failure carries the most specific cause available at the
layer that failed, and every failure is findable in Langfuse in one step
(filter traces by level=ERROR, or search the rid included in the message).
"An unexpected error occurred" is a bug (tenet 7).

## Verbosity policy ("as verbose as reasonable")

> **Superseded 2026-07-05 (same day):** policy is now runtime-configurable —
> the `ERROR_STYLE` toggle in `orchestrator/errors.py` (debug | friendly,
> with `ERROR_STYLE_VOICE` per-transport override; rendering only, capture
> always full). Default is debug, which matches the policy below. See
> `../features/verbose-errors.md`.

- **Chat UI**: full detail — error type, cause, rid. Operators troubleshoot
  here.
- **Voice**: the same text is spoken; keep tool-level detail to one clear
  sentence (the tool_service `detail` strings are already written for this).
  The rid is spoken too — mildly awkward, accepted: this is a single-operator
  debugging aid, and voice failures are exactly when you want the handle.
- **Model-facing** (tool results): specific enough that the model can relay
  or adapt (e.g. retry a different URL), with explicit instructions when the
  failure must be reported rather than worked around.

## Design decisions

1. **Specialist failure → coordinator (P1.1):** keep the coordinator
   fallback (an agent-infrastructure error like an LLM 5xx still deserves a
   best-effort answer), but relabel the context block honestly:
   `[The {agent} specialist FAILED: {error}. If you can answer the user's
   question yourself, do so; otherwise report the failure and its cause
   plainly. Never invent an answer the specialist failed to produce.]`
   Rationale: pure pass-through (skip the coordinator) would turn every
   transient LLM hiccup into a spoken stack trace; the reworded block keeps
   the recovery path while forbidding the "pretend it worked" failure mode.
   The trace is marked ERROR either way, so nothing hides.
2. **Tool handler failures (P1.2):** one `_fail(action, resp)` helper in
   tools.py — extracts `detail` from JSON bodies (FastAPI convention across
   all three services), falls back to `resp.text[:200]`, always includes the
   HTTP status. Handlers keep their per-tool result *shapes*; only the
   error branch is unified. `shopping_list_clear` gains a status check
   (tenet 6 — it asserted success without looking).
3. **Trace marking (P1.3):** `pipeline_error` (added in P0.5) is now set on
   every failure path: router failure, specialist failure (even when the
   coordinator recovers — metadata records the specialist error), and
   coordinator error events.
4. **Terminal-speech fallback (P1.4):** unrecognized terminal-tool results
   are no longer spoken raw; they get a clean sentence + the raw text goes
   to the log. Full formatter registry deferred to the MagicMirror
   prerequisite (review P2.2) — not needed while play_music is the only
   terminal tool.
5. **Out of scope here:** P2.2 formatter registry, P2.4 metrics, the
   review's P2 refactors. Single-purpose branch.

## Work list (from review P1, in implementation order)

- tool_service: `/search` error detail (P1.6); `logging.basicConfig` (P1.7)
- health_service: `/api/query` 422s + days cap (P1.9); import-loop
  `sample_errors` (P1.10); vector-store failure surfaced in responses (P1.11)
- orchestrator/tools.py: `_fail()` + six handlers + clear-status-check (P1.2)
- orchestrator/agents.py: terminal-speech fallback hygiene (P1.4); terminal
  turns set span output (review P2.5 — one line, pairs with P1.3)
- orchestrator/main.py: specialist-FAILED block (P1.1); ERROR marking on all
  paths (P1.3); router message (P1.5); shopping-list page error state (P1.8)

Each lands with the test that would have caught it. Definition-of-done tier
3 applies on deploy (pipeline touched): run the suite + live shim checks.
