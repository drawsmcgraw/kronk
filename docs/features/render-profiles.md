# Feature: Render profiles (markdown for screens, plain text for speakers)

**Shipped:** 2026-08-25 · **Plan:** `../plans/RENDER_PROFILES_PLAN.md` ·
**Motivating problem:** every response serves two surfaces — the web UI
wants markdown, TTS reads asterisks aloud — and per-skill fixes (the news
prompt's "no markdown" line) were a tax on every future skill.

## What it does

All content inside the pipeline is canonical markdown — agents, tools,
cached briefs, history. Rendering happens once, at the transport boundary:

- **display** (default, every surface): passthrough.
- **speech** (`/voice` mount only): `render.to_speech()` — a ~40-line
  deterministic scrub (headers→spoken titles, bold/italic/links/fences/
  bullets/tables flattened, snake_case preserved, every line normalized
  to end at a punctuation boundary). Idempotent, no LLM, same input →
  same speech.

**Prosody, measured not guessed** (live piper, 2026-08-25, max internal
silence): `"Tech & AI."` pauses 200 ms — identical to any sentence
boundary, which sounds jarring after a title; a blank line adds *nothing*
(120 ms — TTS ignores newlines); `"Tech & AI:"` pauses **420 ms**. So
headers get a colon, not a period, and lines that lose their trailing
`**` (bold story leads) get a period appended so they can't run into the
next sentence with no boundary at all.

Speech is a **client declaration, not an inference**: the `/voice/api/*`
mount mirrors the Ollama protocol surface (chat/tags/version/show) with
the speech profile; the root mount stays display. A future Ollama-dialect
client gets markdown unless it's deliberately pointed at `/voice` —
fail-safe in both directions.

## How voice traffic gets there

All voice devices funnel through HA, and HA's Ollama integration base URL
now points at `http://localhost/voice` — so every satellite, present and
future, rides the speech profile with zero per-device work. The flip was a
storage edit (`.storage/core.config_entries`, `data.url`) with HA stopped,
because this HA version's ollama entry reports `supports_reconfigure:
false` — the URL can't be changed via the flow API. Backup at
`~/ha-backups/core.config_entries.bak-2026-08-25`; rollback = restore the
file (or re-edit the URL back) and restart HA. Downtime for the flip:
~90 s.

**Streaming:** the speech profile buffers the full reply and answers a
streaming request with one protocol-valid content chunk + terminal chunk —
markdown markers can straddle token boundaries, and TTS needs complete
text anyway, so buffering costs nothing perceived. Display mounts stream
untouched.

## Verified behavior (live, 2026-08-25)

- Same cached news brief, both mounts: web `### World\n**Trump's
  Economic War…**`, voice `World.\nTrump's Economic War…` — assert:
  no `#`/`**` in speech output.
- HA arrived on the voice mount immediately after the flip
  (`GET /voice/api/tags`, `POST /voice/api/chat` in orchestrator logs);
  ollama entry `state: loaded`.
- 16 unit/endpoint tests: every markdown construct, idempotency,
  plain-text no-op, display-vs-speech mount behavior, buffered
  single-chunk streaming, protocol-surface mirroring.
- The news prompt's markdown suppression is deleted — the brief is now
  properly formatted in the web UI and clean over voice, from one cache.

## Gotchas

- The entry's *title* in HA still reads "http://localhost" — titles are
  set at creation and cosmetic; the live URL is in `data.url`.
- `to_speech` runs only at the speech boundary — `_terminal_speech` still
  crafts spoken sentences from structural tool results; the two compose
  (the scrub is a no-op on plain sentences).
- Ollama-protocol non-stream responses on the voice mount also scrub —
  don't assume streaming is the only path.
- If a future speech client speaks another protocol, mount it under
  `/voice/` too (nginx's catch-all already forwards the prefix).

## Blog hooks

- One cache, two renders: separating content from surface in a voice
  assistant.
- Speech as a declared client property — why "it's the Ollama endpoint"
  must not mean "it's a speaker."
- Editing HA's config entry storage when the flow API says no: backup,
  stop, edit, start.
