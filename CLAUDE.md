# CLAUDE.md — session bootstrap for Kronk

Kronk is a fully-local, privacy-first home AI assistant: a chat UI + voice
pipeline in front of a router → specialist-agent → coordinator pipeline over
llama.cpp models, all on one machine (AMD Ryzen AI 375, Radeon 8060S iGPU,
122 GB RAM, hostname `kronk`).

## Read these, in this order

1. **`README.md`** — the architecture doc. Service map, agent-loop walkthrough,
   model/agent/tool inventory, operations runbook, design decisions.
   Read it fully before touching anything.
2. **`ROADMAP.md`** — the single source of truth for planned work. Any new
   feature idea, open item, or deferral belongs there (it also defines the
   docs lifecycle: plan doc → build → `docs/features/` doc → blog topic).
3. **`orchestrator/agents.py`** — the `AGENTS` dict is the single source of
   truth for agents, their tools, and the router prompt.
4. **`orchestrator/tools.py`** — tool definitions + dispatch.
5. **`orchestrator/main.py`** — the pipeline entrypoints: `/message` (chat UI,
   SSE), `/v1/chat/completions` (OpenAI shim), `/api/chat` (Ollama shim — this
   is what Home Assistant's voice pipeline calls).

Then, only when the task touches that area (shipped features each have a
distilled doc in `docs/features/` — start there, then the build journals):

- **Voice** (Voice PE, STT/TTS, HA pipeline): `docs/features/voice-pipeline.md`,
  then `docs/VOICE_SETUP.md` — the build journal with the final architecture
  diagram and open items.
- **Music** (Music Assistant, Sonos, players): `docs/features/voice-music-control.md`,
  then `docs/plans/MUSIC_ASSISTANT_PLAN.md` plus the header comment in
  `docker-compose.ma.yml`.
- **Telemetry / perf**: `docs/features/telemetry.md` (v1 is a prototype —
  Telemetry v2 is on the roadmap), then `docs/TELEMETRY_GUIDE.md`,
  `docs/PERF_FINDINGS_2026-06-10.md`, `docker-compose.langfuse.yml`.
- **Routing / agent loop internals**: `docs/features/agents-as-tools-routing.md`,
  `docs/plans/STREAMING_REFACTOR_PLAN.md`.
- **History / why-is-it-like-this**: `docs/HISTORY.md`, `docs/incidents/`,
  `docs/plans/` (every plan doc carries a status header — trust it over the
  body, which reflects the plan as written, not as built).

## Runtime topology (four compose stacks + host systemd)

| Stack | File | Project |
|---|---|---|
| Kronk app (orchestrator, nginx, tools, litellm, searxng, …) | `docker-compose.yml` | `kronk` |
| Home Assistant | `docker-compose.ha.yml` | `kronk-ha` |
| Music Assistant + YT PO-token helper | `docker-compose.ma.yml` | `kronk-ma` |
| Langfuse telemetry | `docker-compose.langfuse.yml` | (langfuse) |

LLM servers and GPU STT are **not** containers — they're user systemd units on
the host (`~/.config/systemd/user/llama-*.service`, `wyoming-whisper.service`).
Reference copies live in `systemd/`. Each compose file carries an
`# Operations:` comment block with its day-2 commands — read it before
running compose commands against that stack.

## Verify, don't trust the docs

The README and plan docs lag reality. Before asserting what's running:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
systemctl --user list-units 'llama-*' 'wyoming-*' 'kronk-*' --no-pager
```

(Example: the README lists six llama units; typically only a subset is
running.) Live HA state can be queried read-only with the token in `.env`:
`curl -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states`.

## Ground rules

- **Deploys**: code change → `docker compose up -d --build <service>` (never
  plain `restart` — it reuses the old image). nginx config → `docker compose
  restart nginx`. `litellm/config.yaml` is bind-mounted, hot-editable.
- **Never `docker compose down -v`** on the `kronk-ha` or `kronk-ma` stacks —
  their volumes hold all HA integrations / the MA library and auth tokens.
- **Git**: the operator runs `git commit` / `git push` themselves. Suggest a
  commit at milestones; don't run those commands.
- **Secrets**: `.env` (gitignored) holds `HA_TOKEN`. `searxng/settings.yml` is
  gitignored; its `.example` is the template. No other secrets exist.
- **New compose files** get an `# Operations:` comment block (up / restart /
  logs / down recipes) like the existing ones.
- **Docs lifecycle** (defined in `ROADMAP.md`, follow it): sizeable feature →
  plan doc in `docs/plans/` first → build → distill into
  `docs/features/<name>.md` (what/how/gotchas + a "Blog hooks" section — the
  operator blogs from these) → update the plan doc's status header and move
  the `ROADMAP.md` line to Shipped. Never add a roadmap section to any other
  file — `ROADMAP.md` is the single source of truth for planned work.
