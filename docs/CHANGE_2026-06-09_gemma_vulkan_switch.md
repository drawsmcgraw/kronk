# Change record: gemma llama-servers switched from ROCm to Vulkan backend

Date: 2026-06-09
Operator: drew (executed by Claude Code)
Motivation: docs/incidents/INVESTIGATION_2026-06-09_hangs.md — the ROCm/HIP
backend kept the iGPU spinning at 100% busy / max clock / ~35 W around the
clock with zero requests, since ~mid-April. Prime suspect for the recurring
silent system hangs (6/8, 6/9), and a constant heat/power burn regardless.

## TL;DR

`llama-gemma3-4b.service` and `llama-gemma4-e4b.service` now run the
**llama.cpp b9585 Vulkan (RADV)** build instead of the **b8746 ROCm** build.
System idle went from **100% GPU busy / 2900 MHz / ~35 W** to
**0% / 600 MHz / ~7 W**. Token throughput cost ~15-20%.
`llama-bonsai.service` unchanged (its build never exhibited the spin).

## Evidence chain that led here

1. With every service idle (zero requests in any llama-server log, "all
   slots are idle"), `gpu_busy_percent` read 100%, sclk pinned 2900 MHz,
   ~35 W package draw. True idle on this box is 0-1% / ~7 W.
2. Stop-one-at-a-time elimination: gemma-3-4b server ≈95% of the spin,
   gemma-4-E4B ≈5%, Bonsai-8B none, wyoming-whisper none.
3. Differentials with the gemma-3-4b model:
   | Config | Idle busy | Power |
   |---|---|---|
   | ROCm b8746, flash-attn on | 100% | 35 W |
   | ROCm b8746, flash-attn off | 100% | 35 W |
   | ROCm bonsai-rocm7 build (older, different build) | 100% | 34 W |
   | ROCm b9585 (released 2026-06-09) | 100% | 35 W |
   | CPU only (`-ngl 0`) | 0% | 7 W |
   | **Vulkan b9585** | **0%** | **7 W** |
4. Conclusion: model-architecture-dependent (Gemma graphs trigger it,
   Bonsai doesn't), backend-dependent (ROCm/HIP yes, Vulkan no),
   build-version-independent, flash-attn-independent. The fault is in the
   ROCm/HIP runtime layer (or its interaction with amdgpu user-mode queues
   on gfx1151), not in llama.cpp.
   Supporting oddity: the spinning processes showed ~zero
   `drm-engine-gfx` time in `/proc/<pid>/fdinfo` — the GPU was "busy"
   without attributable submitted work.

## Performance measured (gemma-3-4b, Q4_K_M, -ngl 99, single short request)

| Backend | Prompt eval | Generation |
|---|---|---|
| ROCm b9585 | ~135 tok/s | ~85 tok/s |
| Vulkan b9585 | ~110 tok/s | ~75 tok/s |

(Indicative only — single short requests, includes warm-up noise.)

## What exactly changed

### New artifacts on disk

- `/home/drew/pai/pai_workspace/llama-cpp/llama-b9585/` — ROCm build of
  b9585, downloaded for testing only. **Not used by any service.** Can be
  deleted, or kept for future A/B tests.
- `/home/drew/pai/pai_workspace/llama-cpp/llama-b9585-vulkan/` — Vulkan
  build of b9585. **Production for the two gemma services.**
- Tarballs alongside: `llama-b9585-bin-ubuntu-rocm-7.2-x64.tar.gz`,
  `llama-b9585-vulkan.tar.gz` (deletable).
- Source: https://github.com/ggml-org/llama.cpp/releases/tag/b9585

### Unit file changes (both repo `systemd/` and installed
`~/.config/systemd/user/` copies; `daemon-reload` + restart applied)

`llama-gemma3-4b.service` and `llama-gemma4-e4b.service`, identical shape
of change:

```diff
-Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
-Environment="LD_LIBRARY_PATH=/home/drew/pai/pai_workspace/llama-cpp/llama-b8746:/usr/local/lib/ollama/rocm"
-ExecStart=/home/drew/pai/pai_workspace/llama-cpp/llama-b8746/llama-server \
+Environment="LD_LIBRARY_PATH=/home/drew/pai/pai_workspace/llama-cpp/llama-b9585-vulkan"
+ExecStart=/home/drew/pai/pai_workspace/llama-cpp/llama-b9585-vulkan/llama-server \
```

Notes:
- `HSA_OVERRIDE_GFX_VERSION` is a ROCm/HSA-only knob (it spoofed gfx1151
  as a supported target). Meaningless under Vulkan — removed.
- The `/usr/local/lib/ollama/rocm` LD path (borrowed ROCm libs) is no
  longer needed — Vulkan uses the system Mesa/RADV stack that GNOME
  already exercises.
- All model flags unchanged: same `.gguf` files, `-ngl 99`,
  `--flash-attn on`, ctx sizes (8192 / 32768), `--cache-reuse 256` on E4B.
- Ports unchanged: 11439 (gemma3-4b), 11438 (gemma4-E4B). Nothing
  downstream (litellm, orchestrator, HA) needed touching.

### Unchanged

- `llama-bonsai.service` — still the `bonsai-gfx1151-rocm7` ROCm build,
  port 11437. It idles correctly; no reason to disturb it.
- wyoming-whisper, ollama, all Docker stacks.

## Verification performed (2026-06-09 ~16:07 EDT)

1. All three servers report healthy on 11437/11438/11439.
2. Both gemma models answer `/v1/chat/completions` correctly.
3. With the full production stack running and idle, sampled 8× over 24 s:
   `gpu_busy_percent` = 0%, sclk 600 MHz, package ~7 W.
4. Post-inference, GPU returns to idle within ~3-6 s.

## Rollback

If Vulkan misbehaves (crashes, bad output, unacceptable latency):

```bash
# 1. restore the ROCm config in both unit copies
git -C ~/git-repos/drawsmcgraw/kronk checkout systemd/llama-gemma3-4b.service systemd/llama-gemma4-e4b.service   # (after this change is committed)
cp ~/git-repos/drawsmcgraw/kronk/systemd/llama-gemma{3-4b,4-e4b}.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart llama-gemma3-4b.service llama-gemma4-e4b.service
```

The b8746 ROCm build is still on disk at
`/home/drew/pai/pai_workspace/llama-cpp/llama-b8746/` — rollback needs no
downloads. Note rolling back also brings the idle spin back.

## Watch criteria

- **Success**: no silent hang for 7+ days (previous gaps: 3.7 d, then 21 h).
- **Spot check**: `cat /sys/class/drm/card*/device/gpu_busy_percent`
  should read ~0 when idle. If it's pinned at 100 with no traffic, the
  spin is back (e.g., a service got reverted or a new ROCm consumer
  appeared).
- If a hang recurs despite a clean 7 W idle: the spin was a red herring;
  next steps per the investigation doc are BIOS update (03.03 is 9 months
  old), then kernel 6.17.0-29 rollback, then mt7925 WiFi driver scrutiny.
