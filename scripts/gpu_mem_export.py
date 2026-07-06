#!/usr/bin/env python3
"""Per-model GPU memory exporter for the /resources page.

Runs ON THE HOST (systemd user timer `gpu-mem-export.timer`, every 30 s) —
the orchestrator container has its own PID namespace and cannot read host
/proc, so this is the bridge: it measures each model server's real GPU
memory via DRM fdinfo and writes /data/gpu_mem.json for the orchestrator
(`servers.load_gpu_mem()`) to join into /api/servers by port.

Why fdinfo: on this iGPU (Radeon 8060S) model weights + KV cache live in
GTT, which RSS does NOT reflect for the ROCm/HIP servers — devstral's
33.6 GB footprint showed 0.8 GB RSS. The kernel's per-process DRM
accounting (/proc/PID/fdinfo/*, drm-memory-gtt/vram) is authoritative.
Full write-up: docs/features/resources-live-memory.md.

No dependencies beyond the stdlib. Stdout on --once for eyeballing.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(os.getenv(
    "GPU_MEM_OUT",
    "/home/drew/git-repos/drawsmcgraw/kronk/data/gpu_mem.json",
))
# User units worth measuring: model servers + GPU STT.
UNIT_PREFIXES = ("llama-", "wyoming-whisper")


def running_units() -> list[str]:
    res = subprocess.run(
        ["systemctl", "--user", "list-units", "--type=service",
         "--state=running", "--no-legend", "--plain"],
        capture_output=True, text=True,
    )
    units = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service") and parts[0].startswith(UNIT_PREFIXES):
            units.append(parts[0])
    return units


def main_pid(unit: str) -> int | None:
    res = subprocess.run(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", unit],
        capture_output=True, text=True,
    )
    try:
        return int(res.stdout.strip()) or None
    except ValueError:
        return None


def port_of(pid: int) -> int | None:
    """The --port argument from the process cmdline — the join key the
    orchestrator uses against the LiteLLM catalog's api_base ports."""
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    for i, tok in enumerate(argv):
        if tok == b"--port" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
    return None


def drm_mem(pid: int) -> tuple[int, int]:
    """(gtt_bytes, vram_bytes) summed across the process's unique DRM clients.

    A process holds several fds on the DRM device; fds dup'd from the same
    open share a drm-client-id and report identical counters — summing raw
    fdinfo files would double-count, so key by client id first."""
    clients: dict[str, tuple[int, int]] = {}
    fdinfo = Path(f"/proc/{pid}/fdinfo")
    try:
        fds = list(fdinfo.iterdir())
    except OSError:
        return 0, 0
    for fd in fds:
        try:
            text = fd.read_text()
        except OSError:
            continue
        if "drm-client-id" not in text:
            continue
        cid = None
        gtt = vram = 0
        for line in text.splitlines():
            if line.startswith("drm-client-id"):
                cid = line.split(":", 1)[1].strip()
            elif line.startswith("drm-memory-gtt"):
                gtt = int(line.split(":", 1)[1].strip().split()[0]) * 1024
            elif line.startswith("drm-memory-vram"):
                vram = int(line.split(":", 1)[1].strip().split()[0]) * 1024
        if cid is not None:
            clients[cid] = (gtt, vram)
    return (sum(g for g, _ in clients.values()),
            sum(v for _, v in clients.values()))


def rss_bytes(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def collect() -> dict:
    procs = []
    for unit in running_units():
        pid = main_pid(unit)
        if not pid:
            continue
        gtt, vram = drm_mem(pid)
        procs.append({
            "unit":       unit.removesuffix(".service"),
            "pid":        pid,
            "port":       port_of(pid),
            "gtt_bytes":  gtt,
            "vram_bytes": vram,
            "rss_bytes":  rss_bytes(pid),
        })
    return {"ts": time.time(), "processes": procs}


def main() -> int:
    payload = collect()
    if "--once" in sys.argv:
        print(json.dumps(payload, indent=2))
        return 0
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    os.replace(tmp, OUT)  # atomic — the orchestrator never sees a torn file
    return 0


if __name__ == "__main__":
    sys.exit(main())
