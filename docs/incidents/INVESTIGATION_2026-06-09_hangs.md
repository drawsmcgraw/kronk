# Deep-dive: recurring silent hangs — 2026-06-09 investigation

Operator-requested investigation into the recurring full-system hangs
(no ping, no UI, journal stops mid-stream). Supersedes the working theories
from the 6/8 and 6/9 morning sessions (Bluetooth, Music Assistant).

## Full hang history (from journal boot records)

| Boot | Ran | Ended | Verdict |
|---|---|---|---|
| -7 | 04-16 → 05-16 (30 d) | 03:45 | **UNCLEAN — hang or power loss** |
| -6 | 05-17, 3 min | — | clean (operator reboot) |
| -5 | 05-17 → 05-21 (4 d) | 20:05 | **UNCLEAN — hang or power loss** |
| -4 | 05-23 → 05-31 (8 d) | 15:01 | **UNCLEAN — the documented memory incident** |
| -3 | 05-31 → 06-04 (4 d) | 20:15 | clean (operator reboot) |
| -2 | 06-04 → 06-08 (3.7 d) | 09:00 | **UNCLEAN — silent hang** |
| -1 | 06-08 → 06-09 (21 h) | 06:34 | **UNCLEAN — silent hang** |

**Operator confirmed (2026-06-09): the 5/16 and 5/21 endings were almost
certainly power outages**, not hangs. So the genuine hang timeline is:
5/31 (memory-driven, see INCIDENT_2026-05-31.md), 6/8, and 6/9 — all after
the late-May additions (voice stack 5/24, MA 5/31). The silent-hang signature
(6/8, 6/9) remains distinct from the 5/31 memory incident.

## Ruled out (with evidence)

- **`perf: interrupt took too long` as a leading indicator** — WRONG.
  Every boot shows the same ~4 warnings (2500→3100→3900→4900 ns); the
  kernel only logs each threshold crossing once. The 30-day stable April
  boot has the identical pattern, first warning at +2 h. Chronic, not
  predictive. (perfwatch service can stay — it's cheap — but its alerts
  are not hang-predictors.)
- **HA Bluetooth** — hangs predate the integration. The 1,856 scanner
  errors per boot were real but cosmetic. (Integration is now disabled
  anyway; the error spam was ugly.)
- **Music Assistant stack** — hangs predate it (5/16, 5/21).
- **Memory pressure** — only the 5/31 incident was memory-shaped. The
  silent hangs occur with ~90 GB free, flat fault rates.
- **Thermal CPU throttle** — acpitz 43–48 °C at idle, no throttle events.
- **Hidden request traffic** — llama server logs show zero requests while
  GPU reads 100% busy; "all slots are idle".
- **MCE / EDAC / ECC hardware errors** — none logged on any hang boot.
- **cgroup zombie buildup** — 121 dying memcgs; meaningful but an order of
  magnitude below pathological. Not the driver.

## The headline finding: idle GPU spin at 100% / ~35 W, 24/7

With all services idle (zero requests), the iGPU sits at **100% busy,
sclk pinned at max (2900 MHz), drawing ~35 W** — versus 0–1% / 6–7 W
genuinely idle. Confirmed by stop-one-at-a-time elimination:

| Process | Contribution to idle spin |
|---|---|
| llama-server gemma-3-4b (`-ngl 99`) | ~95% — the main spinner |
| llama-server gemma-4-E4B (`-ngl 99`) | ~5% |
| llama-server Bonsai-8B | none (1% / 6 W with only it running) |
| wyoming-whisper | none |

Differential tests (gemma-3-4b model):

| Config | Idle busy | Power |
|---|---|---|
| GPU (`-ngl 99`), flash-attn on | 100% | 35 W |
| GPU (`-ngl 99`), flash-attn off | 100% | 35 W |
| GPU via *other* llama.cpp build (bonsai rocm7) | 100% | 34 W |
| CPU only (`-ngl 0`) | 0% | 7 W |

So: **model-dependent, build-independent, flash-attn-independent.**
Something about the Gemma architecture graphs on ROCm/gfx1151 keeps the
GPU permanently busy even with no requests in flight. Bonsai (different
arch) idles correctly.

### Follow-up testing same day (b9585 + Vulkan)

- **llama.cpp b9585 (released 2026-06-09), ROCm backend: still spins.**
  100% / 35 W idle, identical to the April b8746 build. Not an old-build
  bug — implicates the ROCm/HIP runtime (or its interaction with amdgpu
  user-mode queues on gfx1151), not llama.cpp itself.
- **llama.cpp b9585, Vulkan (RADV) backend: clean.** 0% busy / 600 MHz /
  ~7 W idle with the same gemma-3-4b model and flags; returns to idle
  within seconds after inference. gemma-4-E4B verified clean too.
- Perf cost of Vulkan vs ROCm on gemma-3-4b: ~135/85 tok/s (prompt/gen)
  → ~110/75 tok/s. Acceptable.

### Resolution applied 2026-06-09

`llama-gemma3-4b.service` and `llama-gemma4-e4b.service` switched to the
b9585 **Vulkan** build (units updated in repo `systemd/` + installed
copies; `HSA_OVERRIDE_GFX_VERSION` env removed, `LD_LIBRARY_PATH` now
points at `llama-b9585-vulkan/`). Verified after restart: all three
llama servers healthy, inference OK, **system idle at 0% GPU / ~7 W**
(was 100% / ~35 W since mid-April). Bonsai stays on its rocm7 build —
it never exhibited the spin.

Notably, per-process `fdinfo` shows ~zero `drm-engine-gfx` time for the
spinning processes — the busy state is not attributable as normal job
execution, which smells like a driver/firmware-level wedge state rather
than honest work.

## Working theory

The gemma llama-server instances have kept the iGPU spinning at 100% /
max clock continuously since ~mid-April (matching service install dates).
This means the SMU/GPU firmware never idles, the package never enters
deep power states, and an extra ~30 W bakes the platform 24/7. Over days,
some amdgpu/SMU state degrades (consistent with the chronic NMI-latency
creep) until the platform wedges hard — full hang, journal mid-stream,
no panic. Accelerating cadence may correlate with rising ambient
temperatures (April → June) on top of the constant burn.

This is a theory, not a conviction. It is the only abnormal, chronic,
hardware-touching behavior found on the box, and it predates all hangs.

## Remaining actions

1. ~~Eliminate the idle spin~~ — **done 2026-06-09** (Vulkan switch, above).
2. **BIOS update.** Framework Desktop BIOS 03.03 (2025-09-16) is ~9 months
   old; Strix Halo SMU/firmware fixes have been shipping steadily. Do this
   next regardless of whether hangs stop.
3. **Watchdog stays.** sp5100_tco + `RuntimeWatchdogSec=2min` +
   boot-notify is the safety net (installed 2026-06-09).
4. ~~Confirm 5/16 / 5/21 endings~~ — **operator confirmed power outages.**
5. If hangs continue with the spin eliminated: next suspects are kernel
   (6.17.0-35 → try 6.17.0-29 still on disk) and the mt7925 WiFi driver.
6. Consider reporting the ROCm idle-spin upstream (ROCm or llama.cpp) —
   gfx1151 + Gemma graphs + HIP runtime keeps GPU 100% busy while idle.

## Verdict watch

The fix's success criterion: **no silent hang for 7+ days** (previous
gaps: 3.7 d and 21 h). If the box hangs again despite a 7 W idle, the
spin was a red herring and we move to BIOS/kernel. Quick health check:

    cat /sys/class/drm/card*/device/gpu_busy_percent   # expect ~0 idle

## Monitoring adjustments

- perfwatch alerts are NOT hang predictors (see above). **Retired
  2026-06-10** after a confirmed-noise overnight alert (the chronic
  staircase fired again even with the GPU idle at 7 W). Service disabled;
  script + unit remain in repo. To check the metric manually after any
  future wedge: `journalctl -k -b -1 --grep "perf:.*interrupt took too long"`.
  Re-arm with: `systemctl --user enable --now kronk-perfwatch.service`.
- The useful canary: GPU busy% + power at idle —
  `cat /sys/class/drm/card*/device/gpu_busy_percent` should be ~0 when
  the box is quiet. 100% with no traffic means the ROCm spin is back.
