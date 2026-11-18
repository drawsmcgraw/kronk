# M21 step 2 build report: the `/settings` page

Build agent, 2026-11-18, branch `refactor`. Criteria:
`docs/sprint/M21-step2-criteria.md`. This report is written step by step
with the build; the last commit completes it.

## 1. Check first (read only)

### 1a. Everything still defined in code or env, and where the page shows it

Searched: `orchestrator/` and `tool_service/` (every `os.getenv`, every
module-level constant, every string sent to a model as a system or user
message), `docker-compose.yml`, `.env.dev.example`, `litellm/config.yaml`,
the llama and Whisper units in `systemd/` and the running ones
(`systemctl --user list-units`, read only), and each llama server's
`GET /props` on loopback (read only). The step 1 inventory
(`docs/sprint/M21-step1-build.md`, "Inventory" and both addenda) is the
base; the rows below add what the sprint added since (M8, M9, M11, M13,
M14, M19) and say for each where `/settings` shows it. "Env table" is the
page's read-only section "Environment settings"; "Code text" is "Model-facing
text in code"; "Routing" is "Routing rules"; "Models" is "Model servers".

| What | Where it is defined | On the page |
|---|---|---|
| Agent and coordinator prompts | `config/prompts/*.md` (live directory) | Editable, shown whole |
| Loop budgets, nudge, weather age, tool timeouts, messy gate, model-lock overtakes | `config/tuning.yaml` | Editable, per key |
| Lead-in words | `config/lead_in.yaml` | Editable, per key |
| Spoken-reply line, stream hold, voice deadline | `config/voice.yaml` | Editable, per key |
| Verify window, resolver, parser words, decision policy | `config/music.yaml` (read by tool_service) | Editable, per key |
| `WEATHER_CTX_MAX_AGE_S` (env wins over `tuning.yaml`) | `config_loader.ENV_OVERRIDES` | Per key, source "environment" when set |
| Routing rules (17, in order) | `orchestrator/routing.py` `RULES` | Routing, read only: name, route, the words, the pattern |
| Model assignments: `ROUTER_MODEL`, `COORDINATOR_MODEL`, `RESEARCH_AGENT_MODEL`, `CODING_AGENT_MODEL`, `DEVOPS_AGENT_MODEL`, `TALKIE_MODEL` | compose / `.env.dev` → `agents.py`, `servers.py` | Env table and the per-agent model column |
| `VOICE_STREAM`, `MODEL_LOCK`, `ERROR_STYLE`, `ERROR_STYLE_VOICE`, `TELEMETRY_ENABLED`, `TELEMETRY_RECORD_TEXT`, `LOCATION`, `HISTORY_MAX_MESSAGES`, `ASSISTANT_PROMPT_CHARS`, `SESSION_IDLE_PRUNE_DAYS`, `OTEL_SERVICE_NAME`, `LLM_SERVICE_URL`, `TOOL_SERVICE_URL` | compose → orchestrator | Env table (orchestrator) |
| `MUSIC_DRY_RUN`, `MUSIC_SHADOW`, `MUSIC_SHADOW_TIMEOUT_S`, `MUSIC_DEFAULT_PLAYER`, `MUSIC_MODEL_URL`, `MA_LIKE_TIMEOUT_S`, `HOME_LOCATION`, `WEATHER_REFRESH_SEC`, `ANNOUNCE_SATELLITE`, `HA_NOTIFY_SERVICE`, `SOLAR_*` thresholds (8), `NEWS_*` (8), `MUSIC_TASTE_*` (6), `MUSIC_GENRE_ARTIST_RADIO`, `SOLAR_POLL_ENABLED`, `NEWS_ENABLED`, `MUSIC_TASTE_ENABLED`, `TELEMETRY_RECORD_TEXT` | compose / `.env.dev` → tool_service | Env table (tool_service) |
| Secrets: `HA_TOKEN`, `MA_TOKEN`, `SOLAR_SERIAL`, `SETTINGS_PASSPHRASE_HASH`, the MagicMirror key path, `MM_SSH_TARGET` | `.env`, compose | Never shown. The env tables are allow-lists; a secret name is not on them. `SETTINGS_PASSPHRASE_HASH` shows only as "editing on/off" |
| Tool descriptions and parameter schemas (22 tools), the `ask_*` delegation tools, `escalate` | `orchestrator/tools.py` `TOOL_DEFINITIONS`, `agents.py` | Code text: each tool's description and parameters, and which agent is offered it |
| `ESCALATION_GUIDANCE`; loop guardrail texts (tool rejected, duplicate call, repeat-call note, budget exhausted, no such specialist) | `orchestrator/agents.py` | Code text. The five loop texts were inline f-strings in `run_stream`; they become named templates in the same file so the page shows the text the loop uses (same bytes, a test compares) |
| Friendly error sentences (`errors.py`, 12), `FRIENDLY_TOOL_PHRASING`, news failure lines (`tools.py`) | `orchestrator/errors.py`, `tools.py` | Code text |
| `_terminal_speech` / `_news_speech` result templates | `orchestrator/agents.py` | Code text: listed by location (they are code paths, not one string) |
| `kronk_facts()`, weather-context block, pipeline `system_extra` blocks | `agents.py`, `main.py` | Code text: listed by location (built per request from live values) |
| Music model question (`_MODEL_SYSTEM`, M11) | `tool_service/main.py` | Code text (tool_service) |
| News brief prompt (`news.build_prompt`), genre-tagging prompt (`taste.tag_artists`) | `tool_service/news.py`, `taste.py` | Code text (tool_service): location and the fixed instruction lines |
| Taste weights, genre picks, feedback windows, resolver search limits | `tool_service/taste.py`, `main.py` | Not shown one by one; listed under "Other constants in code" by file (step 1 inventory rows) |
| Safety bounds: exec classifier, fetch block, netguard limits, `config/` caps, mirror pin | `tool_service/ops.py`, `netguard.py`, `main.py`, `config_files.py`, `routing.py` | Not editable and not configurable; the page names them under "Structural guards (code)" so nobody looks for them in a file |
| llama unit flags: `--ctx-size`, `--reasoning-budget`, `--cache-reuse`, `--swa-full`, draft model, `--parallel`; sampling (no unit sets any; llama.cpp and GGUF defaults) | `~/.config/systemd/user/llama-*.service` | Models: read from each running server's `GET /props` (`n_ctx`, sampling parameters, slots) by a host-side exporter, see 1c |
| `litellm/config.yaml` (model names, ports, display `ctx_k`) | `litellm/config.yaml` | Models: name, port, `ctx_k` beside the measured `n_ctx`, so the known disagreement (`gemma-4-e4b` 128 vs 32k, `gemma-3-4b` 128 vs 8k) is visible |
| Whisper vocabulary prompt (M13, dev Whisper only) | `~/services/wyoming-whisper/dev-prompt.env`, built nightly by `kronk-stt-prompt.timer` | Named under "Host-side settings" with its location; the containers cannot read the host file |
| `data/music_taste.yaml` | `./data` | Named under "Host-side settings"; it has its own writer (`taste.py`) and is household data, not config |

No prompt or tuning value was found half moved. No value is moved by this
step: the page shows code and env values read only, as criterion 2 asks.

### 1b. Git from the service (criterion 16)

- The orchestrator image is `python:3.12-slim` (Debian 13.4); it has no
  `git`. The host has git 2.51.0.
- The containers run as uid 1000 (`kronk`), the same uid as the host user
  `drew`, with a home directory. A repository owned by uid 1000 is used by
  a uid 1000 process without git's `safe.directory` complaint.
- Choice: git in the orchestrator image (`apt-get install git`, Debian's
  package, version pinned to the 2.47 series in the Dockerfile). The
  alternative, a host-side helper, needs a channel from the container to
  the host (a socket or a watched spool directory) plus a unit to run it,
  and a second place where a save can fail half way; git in the image is
  one process call next to the write it records. Only the orchestrator
  writes and commits; tool_service only reads the live directory and gets
  no git.
- Every git call runs with `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`,
  hooks off (`core.hooksPath=/dev/null`) and the identity on the command
  line, so no user or system git config takes part and no hook in the live
  repository can run.

### 1c. llama flags "as read from the running servers"

The llama servers listen on `127.0.0.1` on the host. The containers are on
the compose bridge and cannot reach them (only LiteLLM, on host networking,
can, and LiteLLM proxies only the OpenAI routes). Each server answers
`GET /props` with `n_ctx`, the sampling parameters in effect and
`total_slots`. The page reads them from a JSON file written by a small
host-side exporter (`scripts/llama_props_export.py`, standard library),
the same pattern as `gpu_mem_export.py` and `data/gpu_mem.json`. The unit
files are in `systemd/`; installing the timer is a host change and is in
the migration runbook, not done by this build (the build writes nothing
under `./data`).
