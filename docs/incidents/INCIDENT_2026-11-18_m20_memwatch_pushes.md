# Incident 2026-11-18: the M20 TTS trial set off two memwatch pushes to Drew's phone

**Symptom, as Drew saw it:** two push notifications from Kronk on the phone,
about four minutes apart, Wednesday 2026-11-18 afternoon:

- 15:49:32 EST (20:49:32 UTC): "Kronk: CRITICAL free RAM"
- 15:53:33 EST (20:53:33 UTC): "Kronk: GPU idle-spin (ROCm wedge?)"

Neither was a real danger to the host at that moment (MemAvailable stayed
above 42 GB; the GPU spin was a trial process that was unloaded within
minutes), but both were sent, and the M20 brief says no notification goes to
anyone's phone. Both were caused by the M20 build agent's trial
(`docs/sprint/M20-build.md`).

## Timeline (UTC)

- 18:41 to 20:11: a sampler (`scripts/tts_bench.py sample --drop-cache`)
  drops the page cache of the trial's own files whenever MemFree falls under
  6 GB. It was started for the downloads with a 90-minute duration and
  expired at about 20:11.
- 19:58 to 20:02: Chatterbox, loaded and idle, holds the GPU at 100 % /
  2900 MHz / 37 W (the idle-spin of `INVESTIGATION_2026-06-09_hangs.md`). The
  trial's guard unloads it after 140 s of spin, before memwatch's fifth
  consecutive poll; memwatch polls at 15:02:29 and 15:03:29 EST see the GPU
  idle and reset its count. No push.
- 20:04 to 20:44: Chatterbox measured in eight short sessions, each loaded
  for at most 170 s, with 70 s of idle GPU between sessions. No push.
- 20:47:56: Chatterbox-Turbo started for its cold-start measurement. Loading
  its 4 GB of weights and the ROCm libraries takes MemFree from about 10 GB
  to 1.47 GB (sampler, 20:48:21) and it stays under 2 GB until about 20:50:40.
  The cache-drop sampler is no longer running.
- 20:48:32 and 20:49:32: memwatch polls read MemFree under 2 GB, its floor
  (an alert regardless of memory pressure; PSI was 0.00 to 0.16 %).
  **20:49:32: push 1, "CRITICAL free RAM".**
- 20:48:05 to 20:50:44: Turbo, idle after the cold-start requests, spins
  (100 %, 2900 MHz, 37.5 W, no model-server engine time). The guard unloads
  it at 20:50:44, after memwatch's polls at 20:49:32 and 20:50:32 had both
  counted the spin.
- About 20:50:55: the build agent starts the Turbo session driver right
  away. The driver waited for idle GPU between its own sessions but not
  before its first. The next memwatch polls (20:51:32, 20:52:32, 20:53:32)
  all fall inside session 1, which is loading or synthesizing with the GPU
  busy, and ROCm work is invisible to memwatch's fdinfo discriminator.
  **20:53:33: push 2, "GPU idle-spin (ROCm wedge?)"**, five consecutive
  polls counted across the end of one load and the start of the next.
- 20:53:42 onward: the driver waits 70 s of idle GPU between sessions; no
  further push. Found at 21:24 while summarizing the sampler files
  (`memfree_gb_min 1.471`) and confirmed in memwatch's journal.

## Root cause

Two guard gaps, both the build agent's:

1. The memory guard did not cover the whole trial. Its duration was sized
   for the downloads; the GPU candidates' loads, which read several GB of
   weights through the page cache, came later. On this host the kernel's
   high watermark is about 340 MB, so ordinary page-cache growth can take
   MemFree under memwatch's 2 GB floor with tens of GB still available.
2. The idle-poll wait (let one memwatch poll see an idle GPU before the next
   load) was applied between the driver's sessions but not between the
   cold-start measurement and the driver's first session.

## Hypotheses checked

- Real memory pressure: no. PSI some avg60 0.00 to 0.16 %, MemAvailable
  42.8 GB at the minimum, no OOM line in the kernel or user journal, no unit
  restarted.
- A host-level GPU wedge: no. GPU back to 0 % / 600 MHz / about 9 W within
  seconds of each unload; llama units healthy, `NRestarts=0` before and after.

## Fix

- The trial is over; everything it started is stopped (`M20-build.md`,
  section 9). No change to memwatch.
- `~/kronk-tts-trial/tools/session_driver.py` (scratch, for the cloning run)
  now waits for 70 s of idle GPU before its first session too.
- For any later run on this host: start the cache-drop sampler with a
  duration longer than the whole run, and keep it running through every
  load; treat "unload, then wait for one idle memwatch poll" as a step of
  every load-to-load transition, not only inside a loop.

## What would have caught it sooner

- A pre-flight in the trial plan listing memwatch's alert rules as hard
  stops, each with the guard that covers it and that guard's time span.
  The rules were found and written down at check-first (`M20-build.md`
  1.4); the time spans were not checked against the run order.
- Watching memwatch's journal for `notified` lines during the run, not only
  at the end.
