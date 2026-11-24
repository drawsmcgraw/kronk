# M25 fix round 2

Fix agent (Opus 5.5), 2026-11-24, 17:15 to 17:30 local (EST), on
`08214f8`. Contract: the last ruling in `docs/sprint/M25-criteria.md`
(B1, S1). Sources: `docs/sprint/M25-e2e2.md` B1 and S1.

## What changed

- `orchestrator/static/resources.html`
  - `MEM_BAR_LABELS.fresh.note` (B1), was: "Per-process segments are GPU
    memory (GTT + VRAM). Other used RAM is used RAM minus them: the
    system, caches, and each process's own RAM (the RAM figure on its
    card)." Now: "Per-process segments are GPU memory (GTT + VRAM). Other
    used RAM is used RAM minus them. A card's RAM figure includes
    memory-mapped model files, which the kernel counts as available, so
    the cards' RAM need not fit inside Other used RAM."
  - `MEM_BAR_LABELS.withheld.note` (job 2), was: "... so it is not split
    out: the model servers' GPU memory is inside used RAM." Now: "... so
    it is not split out. GTT is system RAM: the GTT in use (below) is
    inside used RAM." Why below.
  - The comment above `MEM_BAR_LABELS`: "which holds each process's own
    RAM" removed; says the segment is used RAM minus the listed GPU
    memory, that a card's RAM is RSS and counts mapped model files, which
    the kernel counts as available, and gives Talkie's numbers.
  - S1: new `MEM_STAT_WORD` (JSON literal: stale, missing, unreadable,
    each its own word) and `memStatWord(src)` (falls back to "not known"
    when the payload has no source). The stats line prints it in place of
    the fixed "stale".
- `docs/features/resources-live-memory.md`: the memory bar paragraph
  carries the same sentence as the page, and says the stats line names
  the state.
- `tests/test_m25_fix1_pages.py`: the fresh note and the withheld note
  are pinned word for word; a test asserts the fresh note carries none of
  "each process's own RAM", "caches", "holds", "the system,"; two tests
  for S1 (the table, its use in `renderMemBar`, no fixed
  `<span class="stale">stale</span>` left; every non-fresh state
  `read_source` produces, built through `servers.build_servers`, has a
  word in the table, and nothing else does).

No figure added or removed, no payload field touched, no arithmetic
changed, nothing on `/services`, no exporter.

## Measurements

Five GPU processes, `/proc/<pid>/status` at 17:23:12, GiB (1024³). GPU
memory is `gtt_bytes + vram_bytes` from `data/dev/gpu_mem.json`, which is
the page's `gpu_bytes`; "RSS" is `VmRSS`, which is the card's RAM figure
(the page's `rss_bytes`: devstral 8.2, gemma 9.87, talkie 14.9,
whisper-dev 0.63, whisper 0.62, the same).

| process | GPU memory | RSS (card RAM) | anonymous | file-mapped | shared |
|---|---|---|---|---|---|
| llama-devstral-q4 | 34.01 | 8.20 | 8.02 | 0.17 | 0.00 |
| llama-gemma4-e4b | 5.10 | 9.87 | 8.39 | 1.48 | 0.00 |
| llama-talkie | 0.01 | 14.90 | 1.62 | 13.21 | 0.07 |
| wyoming-whisper-dev | 1.93 | 0.63 | 0.30 | 0.16 | 0.16 |
| wyoming-whisper | 1.93 | 0.62 | 0.30 | 0.15 | 0.16 |
| sum | 42.98 | 34.22 | 18.64 | 15.18 | 0.40 |

The same table at 17:19:34 (before the rebuild): identical to two
places.

`/proc/<pid>/smaps`, largest Rss by mapping: Talkie 13.14 GiB
`talkie-1930-13b-it-Q8_0.gguf`, 1.57 GiB anonymous; gemma 7.77 anonymous,
1.41 `gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf`; devstral 7.91
anonymous, 0.11 its `.gguf`. `VmLck` 0 kB and smaps `Locked` 0 on all
five; `/proc/meminfo` `Mlocked` 0 kB, `Unevictable` 0.19 GiB.

`/proc/meminfo` at 17:23:12: MemTotal 122.84, MemAvailable 51.21, used
(MemTotal − MemAvailable) 71.63, Cached 40.64, MemFree 10.51, Shmem
1.20, Active(file) + Inactive(file) 40.70, Mapped 16.70. GTT used
(`mem_info_gtt_used`) 43.50. VRAM: `mem_info_vram_total` 512 MiB; kernel
log "amdgpu: VRAM: 512M ... (512M used)", the firmware carve-out.

Page payloads at 17:23:12 (`/dev/api/system`, `/dev/api/servers`):
mem_total 122.84, mem_available 51.21, used 71.63, listed GPU memory
42.98, so Other used RAM 28.65; `gtt_used` 43.50; both sources fresh.

Two accountings from the same `/proc/meminfo` read:

- MemTotal − MemFree − Buffers − Cached − SwapCached − AnonPages − Slab
  − KernelStack − PageTables − SecPageTables − VmallocUsed − Percpu =
  43.77 GiB: memory in none of free, page cache, anonymous or slab. GTT
  used is 43.50 of it (0.27 left over).
- MemFree + file LRU + SReclaimable = 52.38 against MemAvailable 51.21:
  the kernel's estimate is free memory plus the file LRU (40.70) and
  reclaimable slab, less 1.17 GiB of watermarks.

## Each sentence and the numbers behind it

Fresh note:

1. "Per-process segments are GPU memory (GTT + VRAM)." `servers._memory`:
   `gpu_bytes = gtt_bytes + vram_bytes`; the segments are `gpu_bytes` of
   each row. 42.98 summed from the file's gtt + vram equals the page's
   42.98.
2. "Other used RAM is used RAM minus them." `memBarSegments`:
   `total − available − gpuBytes`. 122.84 − 51.21 − 42.98 = 28.65, the
   payload's figure; the render (17:23:11) showed 29.0 (the browser's own
   ~0.35 GiB was resident while it rendered; with it closed one second
   later, 28.65).
3. "A card's RAM figure includes memory-mapped model files". The card
   shows `rss_bytes` = `VmRSS` (14.90 = 14.90 for Talkie) = RssAnon +
   RssFile + RssShmem. RssFile is 13.21 GiB for Talkie, and smaps puts
   13.14 GiB of it on its `.gguf`; gemma 1.41 GiB on its `.gguf`,
   devstral 0.11.
4. "which the kernel counts as available". Those pages are file LRU: no
   lock (`VmLck` 0, `Mlocked` 0), and `Unevictable` is 0.19 GiB, smaller
   than Talkie's 13.14 alone. MemAvailable is MemFree + file LRU +
   SReclaimable less watermarks (52.38 − 1.17 = 51.21, to the hundredth),
   so the file LRU, mapped pages included, is in it.
5. "so the cards' RAM need not fit inside Other used RAM". Cards' RAM
   34.22 against a 28.65 segment.

The note says nothing about what the segment holds. One thing the numbers
would bear out, left unsaid to keep the line short: the listed processes'
anonymous and shared RAM (19.04) is inside it, since GTT sits in no
meminfo category and so is not counted twice with AnonPages.

Withheld note (rendered missing, unreadable and stale; the same in all
three):

1. "Per-process GPU memory is not known (gpu_mem.json, below), so it is
   not split out." Rendered: one used segment, no per-process segment;
   the source line below names the file and the reason.
2. "GTT is system RAM: the GTT in use (below) is inside used RAM." The
   first accounting: 43.77 GiB is in none of free, page cache, anonymous
   or slab, and GTT used (43.50) fits in it; none of it is in the
   MemAvailable estimate (second accounting), so it is inside used RAM
   (71.63). "(below)" is the stats line's GTT figure, 43.5 GiB.

   Changed from "the model servers' GPU memory is inside used RAM": GPU
   memory is GTT + VRAM, and VRAM is the 512 MiB firmware carve-out,
   which is not part of MemTotal. The listed processes' VRAM is 0.01 GiB
   of 42.98, so the old sentence was wrong by 13 MiB; the new one says
   only what the numbers show.

Other labels the last round added, checked the same way:

- "<name> GPU memory": each segment is that process's gtt + vram. True.
- "Other used RAM": used RAM minus the listed GPU memory. True; the name
  claims nothing more.
- "Used RAM, all of it (per-process GPU memory not known)": the segment
  is `total − available − 0` = used (72.0 rendered, the stats line's Used
  72.0). True.
- "Available": `mem_available`. True.
- `/services`' tags and dots (`SERVER_TAG`, `HEALTH_DOT`): read from the
  code only. They name payload states (`state`, `health`) and claim no
  measurement; the brief says nothing on `/services`, and `/dev/services`
  was not loaded. Not changed.

Found, not changed (arithmetic is outside this round): the bar draws the
listed processes' VRAM (0.01 GiB) inside MemTotal, where it is not. Below
the bar's resolution. The feature doc's gotcha "whether they overlap was
not measured" (RSS and GPU memory) is now answered for the system as a
whole by the first accounting (GTT is not counted in AnonPages); left as
written, outside the sentence this round fixes.

## Renders

Headless snap Firefox (WebDriver on loopback port 4445, profile
`~/snap/firefox/common/m25-fix2`). Every non-loopback request the browser
made went to a proxy address with nothing listening (127.0.0.1:9), so it
failed; loopback was direct. The page fetches only `api/system`,
`api/servers`, `api/activity` and `static/*`, relative. One load per
render, DOM read after the page's "updated" stamp, then `about:blank`.

Dev rebuilt at 17:22:20 (`docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev`: orchestrator-dev recreated,
tool_service-dev healthy and untouched). `resources.html` in the
container and in the tree: SHA-256 `b5f345a1b33d0ef8...`, the same.

Fresh, 17:22:59:

- Legend: "devstral-2512-q4 GPU memory — 34.0 GiB", "gemma-4-e4b GPU
  memory — 5.1 GiB", "talkie GPU memory — 0.01 GiB", "wyoming-whisper-dev
  GPU memory — 1.9 GiB", "wyoming-whisper GPU memory — 1.9 GiB", "Other
  used RAM — 28.9 GiB".
- Note: "Per-process segments are GPU memory (GTT + VRAM). Other used RAM
  is used RAM minus them. A card's RAM figure includes memory-mapped
  model files, which the kernel counts as available, so the cards' RAM
  need not fit inside Other used RAM."
- Stats: "Total 122.8 GiB Used 71.9 GiB (59%) Available 50.9 GiB Free
  10.2 GiB GPU memory, listed processes 43.0 GiB GTT 43.5 GiB / 101.6 GiB
  (43%) GPU busy 0 % (instant reading)".

A second fresh render at 17:23:11, paired with the measurement at
17:23:12: Other used RAM 29.0 GiB, note and stats line the same words.

Override (17:23:26): `docker compose -f docker-compose.yml -f
<scratch>/override.yml --profile dev up -d --no-deps --no-build
orchestrator-dev`, the override setting `GPU_MEM_PATH=/m25/gpu_mem.json`
and mounting a scratch directory read-only at `/m25`. Dev orchestrator
only.

- Missing (17:23:34, the directory empty). Legend: "Used RAM, all of it
  (per-process GPU memory not known) — 72.0 GiB". Note: "Per-process GPU
  memory is not known (gpu_mem.json, below), so it is not split out. GTT
  is system RAM: the GTT in use (below) is inside used RAM." Stats:
  "Total 122.8 GiB Used 72.0 GiB (59%) Available 50.9 GiB Free 10.1 GiB
  GPU memory, listed processes missing GTT 43.5 GiB / 101.6 GiB (43%) GPU
  busy 0 % (instant reading)". Source: "gpu_mem.json does not exist: the
  host exporter (systemd/gpu-mem-export.timer) is not installed or has
  not run".
- Unreadable (17:23:38, the first 300 bytes of the dev file). Legend and
  note the same. Stats: "... GPU memory, listed processes unreadable GTT
  43.5 GiB / 101.6 GiB (43%) ...". Source: "gpu_mem.json cannot be read:
  JSONDecodeError: Expecting property name enclosed in double quotes:
  line 17 column 2 (char 300)".
- Stale (17:23:51, the dev file with `ts` 200 s back). Legend and note
  the same. Stats: "... GPU memory, listed processes stale ...". Source:
  "gpu_mem.json STALE, written 202 s ago; figures from it are withheld".

Restored at 17:23:53 by the plain build line. `docker inspect
kronk-orchestrator-dev-1`: mounts `data/dev -> /data`, `kronk-context.md`,
`litellm/config.yaml`, `kronk-config/dev -> /config`; no `/m25`; no
`GPU_MEM_PATH` in its environment.

After the last render: `pgrep -a 'firefox|geckodriver'` empty;
`~/snap/firefox/common` holds `.cache` and `.mozilla` (both there
before), the profile removed. Journals of `llama-gemma4-e4b`,
`llama-devstral-q4`, `llama-talkie` from 17:22:00 to 17:24:30: 0 lines
each.

## Suite

`./scripts/run_tests.sh`:

```
===== 7504 passed, 3 skipped, 54 xfailed, 6 warnings in 103.08s (0:01:43) ======
```

Before: 7501 passed. Three tests added. With the round-1 sentences and
the fixed "stale" put back into the page (new tests kept): 4 failed, 12
passed in `tests/test_m25_fix1_pages.py` (both note pins, the
old-sentence check, the stats-line test). With the round-1 page entire:
collection error, `MEM_STAT_WORD is not a literal in the page`.

## Not done

- S2 of the second test (the pin reads the page's text and does not run
  it; eight mutants survive) and N1 to N4: left over by the ruling. The
  new pins have the same limit: they assert the table and that
  `renderMemBar` calls `memStatWord`, not what the DOM shows. The renders
  above are the evidence for the DOM.
- The VRAM-in-the-bar point and the feature doc's "overlap was not
  measured" gotcha, above: found, not changed.
- No push, no tag, no deploy.
