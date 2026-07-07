# Incident 2026-07-06 — coding/devops agents had no model for ~4 weeks

**Symptom:** none observed — that's the incident. Discovered while setting
up the devops model bench: `curl 127.0.0.1:11440/health` → connection
refused.

**Timeline:**
- 2026-05-31 15:32 — `llama-devstral-q4.service` stopped cleanly (journal;
  13.6 GB peak noted). Unit is **disabled**, so this was presumably a
  deliberate stop — likely freeing memory during that day's voice work.
- 2026-06-09 06:37 — host reboot. Disabled unit did not return. Every other
  needed llama unit is also disabled-but-manually-started, so nothing
  contradicted the pattern.
- 2026-06-09 → 2026-07-06 — `coding` and `devops` agents route to a model
  that isn't running. Any request would have hit LiteLLM with a dead
  backend → agent error → coordinator fallback (pre-2026-07-05, labeled as
  a "specialist result"; post, a FAILED report). Nobody noticed because
  these agents are rarely invoked from voice/chat.
- 2026-07-06 09:30 — unit **started** (not enabled) for the bench; healthy;
  left running.

**Root cause:** no reconciliation between "what the AGENTS dict assigns"
and "what's actually running." The README's own "verify, don't trust the
docs" warning exists because units drift — but nothing *checks*.

**Fixes / follow-ups:**
1. Left running for now. Operator to decide: enable the unit (survives
   reboots, costs ~14 GB resident) or accept manual starts.
2. The planned `scripts/check_all.sh` (review finding, feeds ROADMAP items
   8/9) should assert every model referenced by `AGENTS`/env answers its
   LiteLLM health probe — this incident is the test case for it.
3. The voice smoke test won't catch this class (no battery utterance routes
   to coding/devops); the health sweep is the right layer.

**What would have caught it sooner:** a per-agent backend health line on
the `/resources` page already exists in spirit (LiteLLM `/health` is
authoritative) — the gap is that nothing looks at it unprompted.
