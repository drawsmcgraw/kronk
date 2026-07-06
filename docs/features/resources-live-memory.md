# Feature: Measured GPU memory on /resources

**Shipped:** 2026-07-06 · **Origin:** operator question "is /resources accurate?" — it wasn't, twice over.

## What it does

The `/resources` page now shows each model server's **real** GPU memory —
measured by the kernel, refreshed every 30 s — instead of a hand-written
estimate, and its health dots work again. Cards show "GPU mem (measured)"
when fresh data exists, falling back to "model size (est.)" otherwise. The
memory bar uses measured values and only counts running servers.

Why it mattered — day-one measurements vs the old static numbers:

| Server | Page claimed | Measured |
|---|---|---|
| devstral-2512-q4 | 14 GB | **36.1 GB** (13.3 GB model + ~20.5 GB KV cache for its 128k ctx) |
| bonsai-8b | 1.1 GB | **6.6 GB** |
| gemma-4-e4b | 3.5 GB | 5.5 GB (incl. MTP drafter) |

The estimates were model-file sizes; they ignored the KV cache, which for
devstral is *bigger than the model*. (Standing note: devstral's 128k ctx is
likely wasted for devops Q&A — trimming to 32k would free ~15 GB.)

## How it works

```
host: gpu-mem-export.timer (30 s)
  └─ scripts/gpu_mem_export.py
       • systemctl --user list-units llama-*, wyoming-whisper → MainPID
       • /proc/PID/fdinfo/* → drm-memory-gtt / drm-memory-vram,
         deduped by drm-client-id (fds dup'd from one open repeat the
         same counters — summing raw files would double-count)
       • --port from /proc/PID/cmdline  ← the join key
       • atomic write → data/gpu_mem.json  (tmp + os.replace)

container: orchestrator
  └─ servers.load_gpu_mem()  (data/ is already bind-mounted)
       • rejects files older than 120 s OR from the future (bad clock)
       • keys by port
  └─ /api/servers joins by port → "measured_gb" per server +
     top-level "measured_age_s"
  └─ static/resources.html prefers measured_gb, labels honestly either way
```

**Why a host-side exporter:** the orchestrator container reads `/sys`
(GTT totals — that part always worked) but has its own PID namespace, so
per-process `/proc/PID/fdinfo` is invisible to it. The 30-second file is
the bridge. **Why fdinfo:** on this iGPU, model weights + KV live in GTT,
which RSS does not reflect for ROCm/HIP processes — devstral's 36 GB
showed as 0.8 GB RSS. DRM fdinfo is the kernel's own per-process GPU
accounting.

**Staleness degrades visibly:** if the timer dies, measurements age out in
120 s and every card flips back to "(est.)" — a broken exporter can't
masquerade as live data.

## The health-dot fix (same session)

The page's "live" health dots had been silently dead: LiteLLM v1.88's
`/health` reports `api_base: null` and identifies backends as
`model: "openai/<name>"`, so `fetch_health`'s api_base join matched
nothing and every server rendered `healthy=None`. Now keyed by model name
(api_base fallback for older shapes), pinned by a regression test. Lesson
logged for update day (ROADMAP item 9): a proxy version bump can silently
change payload shapes that only humans look at.

## Operations

- Install/enable: `cp systemd/gpu-mem-export.{service,timer}
  ~/.config/systemd/user/ && systemctl --user daemon-reload &&
  systemctl --user enable --now gpu-mem-export.timer`
- Eyeball: `python3 scripts/gpu_mem_export.py --once`
- Health: `systemctl --user list-timers gpu-mem-export.timer`
- New model servers are covered automatically if their unit name starts
  with `llama-` and the cmdline has `--port`; the `kronk.vram_gb` value in
  `litellm/config.yaml` is now only the cold-start fallback label.

## Blog hooks

- "Your dashboard is lying twice": hand-written capacity numbers that
  ignore KV cache, and a health join dead since a dependency bump.
- Per-process GPU memory on an iGPU: why RSS lies and DRM fdinfo doesn't.
- The container/host seam: a 100-line exporter instead of privileged
  mounts.
