"""Shared fixtures for the M21 step 2 tests: a temporary live config
directory that is its own git repository (identity set on the repository,
never the global config), both loaders pointed at it, and the orchestrator
app wired to tool_service's app in-process (httpx ASGI transports: no
sockets)."""
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
ORIGIN = "http://kronk.test"
HOST = "kronk.test"


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                       capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    if check and r.returncode != 0:
        raise AssertionError(r.stderr)
    return r.stdout


def make_live(root: Path) -> Path:
    d = root / "live"
    shutil.copytree(REPO / "config", d)
    git(d, "init", "-q", "-b", "main")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "Seed from test")
    return d


def tree(d: Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def ts_transport():
    import tool_service.main as ts_main
    return httpx.ASGITransport(app=ts_main.app)


def orch_transport():
    import main
    return httpx.ASGITransport(app=main.app, client=("192.0.2.10", 5555))


@asynccontextmanager
async def orch_client(**headers):
    async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}",
                                 headers={"x-real-ip": "192.0.2.10", **headers}) as c:
        yield c
