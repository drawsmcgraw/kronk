# Feature: /resources shows measured facts only

**Shipped:** 2026-07-06 (measured GPU memory), rewritten by M25 on
2026-11-24 (every figure measured; activity added). **Origin:** operator
question "is /resources accurate?" (2026-07-06: it wasn't, twice over);
Drew's order of 2026-11-24 ("It should represent real usage information")
and `docs/incidents/INVESTIGATION_2026-11-08_resources_page.md`.
Criteria and evidence: `docs/sprint/M25-criteria.md`, `M25-build.md`.

## What it does

`/resources` shows only facts read from the running system:

- **System memory**: RAM total, used, available and free from
  `/proc/meminfo`; GTT total and used and GPU busy from sysfs, all read
  at the request. GPU busy is one instant reading, labelled as such. The
  memory bar has one segment per GPU process (its GTT + VRAM), then
  "System / other" and "Available".
- **Model files on disk**: the total size of the model and draft files
  the running llama servers loaded, and the size and free space of each
  filesystem holding one.
- **Model servers**, one card per catalog entry, grouped as running,
  state unknown, and configured, not running: GPU memory, RAM, context
  size and slots as the server reports them (`n_ctx`, `total_slots`, not
  divided), the model file's base name and size (the quantization is in
  the name), the draft file and its size, health, agents.
- **Other GPU processes**: the Whisper units, and any running `llama-*`
  unit that is not in the catalog (by unit name, with its model file).
- **Calls from this Kronk, last 24 hours**: per model, calls, prompt
  tokens and completion tokens since now minus 24 hours, and when the
  model was last used at any time. A model outside the catalog with
  calls in the window is listed apart, "not in the catalog".

Each group names its source and its age, and says STALE when the source
is stale. Sizes are GiB. Nothing reads "(est.)": there is no estimate.

What it replaced: context, quantization, parameter count and `vram_gb`
were hand-written in `kronk:` blocks of `litellm/config.yaml`, and wrong
(gemma-4-e4b "128k, Q4_K_M" while it ran 32,768 tokens of a
Q4_K_XL file with an MTP drafter). The timing row (TTFT, tokens a second)
had been frozen since 2026-06-12 and is gone. `/performance` is untouched.

## How it works

```
host: gpu-mem-export.timer (30 s)
  └─ scripts/gpu_mem_export.py
       • systemctl --user list-units llama-*, wyoming-whisper → MainPID
       • /proc/PID/fdinfo/* → drm-memory-gtt / drm-memory-vram, deduped
         by drm-client-id; VmRSS; --port from the cmdline (the join key)
       • atomic write → data/gpu_mem.json and data/dev/gpu_mem.json

host: llama-props-export.timer (60 s)
  └─ scripts/llama_props_export.py
       • each running llama-* unit: its flags, GET 127.0.0.1:<port>/props
         (n_ctx, total_slots, sampling)
       • stat on the -m and --model-draft paths → model_bytes,
         model_draft_bytes (only base names go in the file)
       • statvfs on each file's directory → "disk": model_files_bytes and
         the filesystems (mount, size, used, avail)
       • atomic write → data/llama_props.json and data/dev/llama_props.json

container: orchestrator (production reads data/, dev reads data/dev/)
  └─ servers.build_servers()  → /api/servers
       catalog (litellm/config.yaml: name and port only)
       ⋈ gpu_mem.json by port  ⋈ llama_props.json by port
  └─ /api/system   meminfo + sysfs now, disk block from llama_props.json
  └─ /api/activity metrics.activity(): llm_metrics in this instance's
                   metrics.db
  └─ static/resources.html, static/services.html (relative URLs)
```

**Running has one definition.** A catalog server is running when its port
is in a fresh `gpu_mem.json`; absent from a fresh file, it is configured,
not running; with the file stale (older than 120 s), from the future, or
missing, its state is unknown. **Health** is the props exporter's: the
server answered `GET /props` when `llama_props.json` was written; stale
(older than 300 s), health is unknown. A running server absent from a
fresh props file shows its memory and "not answering" for context, slots
and files.

**No default.** Every fact with no fresh source is null, and the server's
`reasons` map says why (`stale: gpu_mem.json was written 407 s ago …`,
`configured, not running: port 11499 is not in gpu_mem.json …`). The page
shows the reason in place of the figure.

**`/api/servers` calls nothing.** Before M25 it called LiteLLM's
`/health`, which runs a chat completion on every backend: one completion
per server per page poll, 0.7 s a request, and Talkie's cached prompt
reset each time. Now it reads two files: about 2 ms.

**Activity** counts rows with `timestamp >= now - 24 h` (the boundary row
counts). Timestamps are stored as UTC text with no zone mark
(`2026-11-24 19:03:20`); the API sends ISO 8601 with `Z` and the browser
shows its own local time. Each orchestrator counts its own calls: the
model servers are shared, so production's and dev's pages differ.

## Gotchas

- **The exporters run from the working tree and production reads their
  files** (`data/` in the repo is production's `/data`). An edit to
  either script is live within a minute. Keys are additive only: never
  rename or repurpose one, since an older release reads the same file.
  Develop against a scratch copy with `--print` or `--out <scratch>`,
  then move it into place with a rename.
- **Both pages use relative URLs** (`api/servers`, `static/kronk.css`),
  so `/dev/resources` and `/dev/services` show dev's data. The probes and
  `/api/upgrades`, `/api/backups` on `/services` stay absolute on purpose.
- **`/services`' orchestrator dot still costs completions.** Its
  `/probe/orchestrator` is production's `/api/status`, which calls
  LiteLLM's `/health`. Out of M25's scope; noted in `M25-build.md`.
- `gpu_busy_percent` is an instant. On an idle box it reads 0 nearly
  every time.
- RSS and GPU memory are separate kernel counters, shown side by side and
  never summed: whether they overlap was not measured. gemma's RSS is
  mostly its host prompt cache; Talkie's (CPU only) is its weights.

## Operations

- Install (both units, once): `cp systemd/{gpu-mem-export,llama-props-export}.{service,timer}
  ~/.config/systemd/user/ && systemctl --user daemon-reload &&
  systemctl --user enable --now gpu-mem-export.timer llama-props-export.timer`
- Eyeball: `python3 scripts/gpu_mem_export.py --print`,
  `python3 scripts/llama_props_export.py --print`
- Health: `systemctl --user list-timers gpu-mem-export.timer llama-props-export.timer`;
  `curl -s localhost/api/servers | jq .sources`
- A new model server is covered when its unit name starts with `llama-`
  and its command line has `--port`. Add it to `litellm/config.yaml`
  (name and `api_base`) to get a card; until then it is listed under
  other GPU processes.

## Blog hooks

- "Your dashboard is lying twice": hand-written capacity numbers that
  ignore KV cache, and a health join dead since a dependency bump
  (2026-07-06).
- The health check that wasn't free: a status page whose every poll ran
  a completion on three model servers, and the CPU-only one lost its
  cache each time.
- Per-process GPU memory on an iGPU: why RSS lies and DRM fdinfo doesn't.
- "State unknown" is not "not running": one definition of running, and
  stale data that says so instead of falling back to a guess.
- The container/host seam: two small exporters instead of privileged
  mounts, and why their keys may only grow.
