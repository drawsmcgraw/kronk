#!/usr/bin/env python3
"""Per-model GPU memory exporter for the /resources page.

Runs ON THE HOST (systemd user timer `gpu-mem-export.timer`, every 30 s) —
the orchestrator container has its own PID namespace and cannot read host
/proc, so this is the bridge: it measures each model server's real GPU
memory via DRM fdinfo and writes data/gpu_mem.json (production) and
data/dev/gpu_mem.json (the dev pair, since M25) for each orchestrator
(`servers.gpu_mem_source()`) to join into /api/servers by port.

Why fdinfo: on this iGPU (Radeon 8060S) model weights + KV cache live in
GTT, which RSS does NOT reflect for the ROCm/HIP servers — devstral's
33.6 GB footprint showed 0.8 GB RSS. The kernel's per-process DRM
accounting (/proc/PID/fdinfo/*, drm-memory-gtt/vram) is authoritative.
Full write-up: docs/features/resources-live-memory.md.

No dependencies beyond the stdlib. Read-only on the host apart from the
files it writes, each atomically (temp file in the same directory, rename).

    gpu_mem_export.py [--out FILE ...] [--print | --once]

Default: the repo's data/gpu_mem.json and data/dev/gpu_mem.json, or the one
path in GPU_MEM_OUT when that is set. --print (or the older --once) prints
the JSON instead of writing.

The keys are read by every deployed orchestrator, older releases included:
add keys, never rename or repurpose one (M25).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ([Path(os.environ["GPU_MEM_OUT"])] if os.getenv("GPU_MEM_OUT")
               else [REPO / "data" / "gpu_mem.json", REPO / "data" / "dev" / "gpu_mem.json"])
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


def write(path: Path, data: dict) -> None:
    """Atomic: the orchestrator never sees a torn file, and a run killed
    mid-write leaves the previous file whole (and a stray temp file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, action="append", help="file to write (repeatable)")
    ap.add_argument("--print", "--once", dest="print", action="store_true",
                    help="print the JSON instead of writing")
    a = ap.parse_args(argv)
    payload = collect()
    if a.print:
        print(json.dumps(payload, indent=2))
        return 0
    for p in a.out or DEFAULT_OUT:
        write(p, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
