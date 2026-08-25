# Render profiles — Plan (markdown for screens, plain text for speakers)

Status: **shipped 2026-08-25** — all steps executed including the HA URL
flip (storage-edit method; backup at
`~/ha-backups/core.config_entries.bak-2026-08-25`). Distilled into
`docs/features/render-profiles.md` — read that first. Pending: the
operator's spoken verification (step 6), the plan's only operator task.

## Problem

Every Kronk response serves two masters: the web UI (wants markdown) and
the voice path (asterisks and pound signs read aloud by TTS, or garbled).
Today this is handled nowhere-in-particular: the news-brief prompt
suppresses markdown, other agents emit it freely, and voice output quality
depends on which tool answered. Per-skill fixes are a tax on every future
skill.

## Operator decisions (2026-08-25)

1. **Global filter, not per-skill** — all return traffic passes through a
   render seam; skill authors never think about surfaces.
2. **Key on render profile, not protocol.** `display` (markdown
   passthrough) and `speech` (deterministic markdown strip). The Ollama
   *protocol* endpoint must not imply speech — future Ollama-dialect
   clients may be displays.
3. **Display is the default; speech is an explicit client declaration**
   via a dedicated voice mount. Fail-safe in both directions: an
   undeclared speech client reads asterisks (loud, obvious), a display
   client is never silently flattened.
4. **Speech profile buffers the response** and scrubs once — no stateful
   streaming scrubber. No user-visible cost: HA needs complete text
   before TTS anyway.

## Design

- **Canonical content inside.** Agents, tools, cached briefs, and
  persisted history keep one form, markdown allowed. Rendering exists
  only at the transport boundary in `orchestrator/main.py`.
- **`to_speech(text)`** — ~30 deterministic lines: headers, bold/italic
  markers, bullets → sentences, `[text](url)` → text, code-fence markers
  stripped, blank-line runs collapsed. Idempotent; no-op on plain text.
  Applied to reply text, narration, and error strings on the speech
  profile.
- **Voice mount:** nginx serves `/voice/` → the same shim handlers with
  `profile=speech` (implementation: prefix route passes a header or route
  flag; the shim resolves profile per request, default `display`). The
  mount covers the shim's full protocol surface (`/api/chat`,
  `/api/tags`) because HA validates connectivity against it.
- **All voice traffic funnels through HA**, so one base-URL change
  enrolls every satellite, present and future.

## The HA URL flip (Claude executes)

Discovery 2026-08-25: the Ollama config entry (id `01KSC27VCMSADHG9NH149BP0CR`,
title `http://localhost`) reports `supports_reconfigure: false` on the
main entry — the URL cannot be changed via the config-flow API in this HA
version; only the conversation/ai_task subentries are reconfigurable.

**Primary method — storage edit with HA stopped** (preserves the entry
id, subentries, conversation entity id, and therefore the assist
pipeline's `conversation_engine` reference):

1. Back up `.storage/core.config_entries` from the `kronk_ha-config`
   volume (tenet 11 — backup before the destructive op).
2. `docker stop homeassistant` (voice outage ~60 s, scheduled with the
   operator).
3. Edit the ollama entry's `data.url`: `http://localhost` →
   `http://localhost/voice`. (Exact field shape inspected in the backup
   before editing.)
4. Start HA; verify the entry loads (`state: loaded`), the conversation
   entity is unchanged, and the shim logs show HA's requests arriving on
   the voice mount.

**Fallback — delete + re-add the integration via the config-flow API.**
Works, but creates a new conversation entity id, which orphans the
`kronk` assist pipeline's `conversation_engine` reference — that would
then need a pipeline update via websocket. More moving parts; only if the
storage edit hits a surprise.

**Rollback:** restore the backed-up file (or re-edit the URL back) and
restart HA. Kronk-side rollback is git revert; the root mount never
changes behavior, so pointing HA back at it restores today's exact state.

## Build steps + tests (each lands green)

1. `to_speech()` + unit tests against every markdown construct the house
   emits (headers, bold story names, bullets, links, fences, tables →
   flattened), idempotency, plain-text no-op.
2. Profile resolution in the shim + buffering on speech profile + tests
   (canned markdown reply → speech response contains no markdown; display
   paths byte-identical to today; narration/error strings scrubbed too).
3. nginx voice location; deploy shim + nginx. Both mounts now live and
   identical except profile. Verify root mount unchanged via the bench
   shim prompts or targeted curls.
4. Pre-flip check of HA's current behavior: one deliberately-bold test
   response through the root mount, observe what TTS receives (confirms
   we're fixing a real class, documents the before-state).
5. The HA URL flip (procedure above), scheduled with the operator.
6. **Operator verification (the only operator step):** spoken tests — a
   news brief and one markdown-heavy answer via "Okay Nabu"; confirm the
   web UI still renders markdown.
7. Cleanup pass: remove per-skill markdown suppressions (news prompt's
   "no markdown" line goes; regenerate an edition; the brief gets real
   formatting in the web UI). Feature doc + ROADMAP Shipped entry.

## Latency budget

None. Speech buffering adds no perceived latency (TTS requires the full
text); display paths keep streaming exactly as today.
