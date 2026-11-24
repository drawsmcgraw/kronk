"""The two host exporters /resources reads (M25 criterion 11).

scripts/gpu_mem_export.py and scripts/llama_props_export.py run from the
working tree on host timers, and every deployed orchestrator, older
releases included, reads the files they write. So: every key an older
release reads keeps its name and meaning (the key sets of item-M24's
output are pinned below), both write data/ and data/dev/, and a run killed
in the middle of a write leaves the previous file whole.

No socket is opened: the parts that ask systemd, /proc or a server are
replaced by fakes.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"m25_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gme(monkeypatch):
    monkeypatch.delenv("GPU_MEM_OUT", raising=False)
    return _load("gpu_mem_export")


@pytest.fixture
def lpe():
    return _load("llama_props_export")


# The keys item-M24's orchestrator reads (servers.load_gpu_mem,
# servers.load_llama_props, settings_view.models) and its exporters wrote.
OLD_GPU_TOP = {"ts", "processes"}
OLD_GPU_PROC = {"unit", "pid", "port", "gtt_bytes", "vram_bytes", "rss_bytes"}
OLD_PROPS_TOP = {"ts", "servers"}
OLD_PROPS_SERVER = {"unit", "flags", "port", "n_ctx", "total_slots", "model_alias", "sampling"}


def _fake_gpu(gme, monkeypatch):
    monkeypatch.setattr(gme, "running_units", lambda: ["llama-a.service", "wyoming-whisper.service"])
    monkeypatch.setattr(gme, "main_pid", lambda u: {"llama-a.service": 11, "wyoming-whisper.service": 12}[u])
    monkeypatch.setattr(gme, "port_of", lambda pid: 11438 if pid == 11 else None)
    monkeypatch.setattr(gme, "drm_mem", lambda pid: (5 * 2**30, 4096))
    monkeypatch.setattr(gme, "rss_bytes", lambda pid: 2**30)


def test_gpu_mem_keys_are_a_superset_of_item_m24s(gme, monkeypatch):
    _fake_gpu(gme, monkeypatch)
    out = gme.collect()
    assert OLD_GPU_TOP <= set(out)
    assert isinstance(out["ts"], float)
    for p in out["processes"]:
        assert OLD_GPU_PROC <= set(p)
    a, w = out["processes"]
    assert (a["unit"], a["pid"], a["port"], a["gtt_bytes"], a["vram_bytes"], a["rss_bytes"]) == \
        ("llama-a", 11, 11438, 5 * 2**30, 4096, 2**30)
    assert w["unit"] == "wyoming-whisper" and w["port"] is None


def _fake_props(lpe, monkeypatch, tmp_path, draft=True):
    model = tmp_path / "models" / "m-Q4_K_M.gguf"
    model.parent.mkdir()
    model.write_bytes(b"x" * 1000)
    drafter = tmp_path / "models" / "d-Q8_0.gguf"
    drafter.write_bytes(b"y" * 30)
    raw = {"model": str(model), "port": "11438", "ctx_size": "32768"}
    if draft:
        raw["model_draft"] = str(drafter)
    monkeypatch.setattr(lpe, "units", lambda: [("llama-a.service", 11)])
    monkeypatch.setattr(lpe, "raw_flags", lambda pid: dict(raw))
    monkeypatch.setattr(lpe, "props", lambda port: {"n_ctx": 32768, "total_slots": 4,
                                                     "model_alias": "m", "sampling": {"top_k": 64}})
    return model, drafter


def test_props_keys_are_a_superset_and_paths_stay_out(lpe, monkeypatch, tmp_path):
    model, drafter = _fake_props(lpe, monkeypatch, tmp_path)
    out = lpe.collect()
    assert OLD_PROPS_TOP <= set(out)
    (s,) = out["servers"]
    assert OLD_PROPS_SERVER <= set(s)
    # Unchanged meaning: flags carry base names, port an int.
    assert s["flags"]["model"] == "m-Q4_K_M.gguf"
    assert s["flags"]["model_draft"] == "d-Q8_0.gguf"
    assert s["port"] == 11438 and s["n_ctx"] == 32768 and s["total_slots"] == 4
    # Added: sizes from stat.
    assert s["model_bytes"] == 1000 and s["model_draft_bytes"] == 30
    # No directory name anywhere in the file.
    assert str(tmp_path) not in json.dumps(out)


def test_props_no_draft_means_no_draft_size(lpe, monkeypatch, tmp_path):
    _fake_props(lpe, monkeypatch, tmp_path, draft=False)
    (s,) = lpe.collect()["servers"]
    assert s["model_bytes"] == 1000
    assert "model_draft_bytes" not in s


def test_props_missing_model_file_is_null_with_reason(lpe, monkeypatch, tmp_path):
    model, _ = _fake_props(lpe, monkeypatch, tmp_path)
    model.unlink()
    out = lpe.collect()
    (s,) = out["servers"]
    assert s["model_bytes"] is None
    assert s["model_bytes_error"] == "stat failed: FileNotFoundError"
    assert out["disk"]["model_files_bytes"] == 30


def test_relative_model_path_resolves_against_the_process_cwd(lpe, tmp_path, monkeypatch):
    (tmp_path / "rel.gguf").write_bytes(b"z" * 7)
    monkeypatch.chdir(tmp_path)
    sizes, found = lpe.file_sizes(os.getpid(), {"model": "rel.gguf"})
    assert sizes == {"model_bytes": 7}
    assert found[0][4] == "rel.gguf"


def test_disk_counts_a_shared_file_once_and_matches_statvfs(lpe, tmp_path):
    f = tmp_path / "shared.gguf"
    f.write_bytes(b"q" * 500)
    g = tmp_path / "other.gguf"
    g.write_bytes(b"q" * 20)
    _, a = lpe.file_sizes(os.getpid(), {"model": str(f)})
    _, b = lpe.file_sizes(os.getpid(), {"model": str(f), "model_draft": str(g)})
    d = lpe.disk(a + b)
    assert d["model_files_bytes"] == 520
    (fs,) = d["filesystems"]
    v = os.statvfs(tmp_path)
    assert fs["size_bytes"] == v.f_blocks * v.f_frsize
    # avail moves as other processes write; within 1 % of a fresh read.
    assert abs(fs["avail_bytes"] - v.f_bavail * v.f_frsize) <= 0.01 * fs["size_bytes"]
    assert sorted(fs["files"]) == ["other.gguf", "shared.gguf"]
    assert os.path.ismount(fs["mount"])
    assert str(tmp_path).startswith(fs["mount"])


def test_disk_with_no_files_is_zero_and_empty(lpe):
    assert lpe.disk([]) == {"model_files_bytes": 0, "filesystems": []}


# ── outputs ───────────────────────────────────────────────────────────────

def test_both_write_data_and_data_dev_by_default(gme, lpe):
    for mod, name in ((gme, "gpu_mem.json"), (lpe, "llama_props.json")):
        assert mod.DEFAULT_OUT == [REPO / "data" / name, REPO / "data" / "dev" / name]


def test_gpu_mem_out_env_still_names_one_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GPU_MEM_OUT", str(tmp_path / "x.json"))
    assert _load("gpu_mem_export").DEFAULT_OUT == [tmp_path / "x.json"]


def test_main_writes_every_out_and_print_writes_nothing(gme, lpe, monkeypatch, tmp_path, capsys):
    _fake_gpu(gme, monkeypatch)
    _fake_props(lpe, monkeypatch, tmp_path)
    for mod in (gme, lpe):
        a, b = tmp_path / f"{mod.__name__}-a" / "f.json", tmp_path / f"{mod.__name__}-b" / "f.json"
        assert mod.main(["--out", str(a), "--out", str(b)]) == 0
        assert json.loads(a.read_text()) == json.loads(b.read_text())
        assert oct(a.stat().st_mode & 0o777) == "0o644"
        c = tmp_path / f"{mod.__name__}-c.json"
        assert mod.main(["--print", "--out", str(c)]) == 0
        assert not c.exists()
        assert json.loads(capsys.readouterr().out)["ts"]
    gme.main(["--once"])  # the older flag still prints
    assert "processes" in json.loads(capsys.readouterr().out)


def test_failed_write_leaves_the_old_file_and_no_temp(gme, lpe, monkeypatch, tmp_path):
    for mod in (gme, lpe):
        target = tmp_path / f"{mod.__name__}.json"
        target.write_text('{"ts": 1, "old": true}')

        def boom(data, f, **kw):
            f.write('{"ts": 2, "half')
            raise OSError("disk full")

        monkeypatch.setattr(mod.json, "dump", boom)
        with pytest.raises(OSError):
            mod.write(target, {"ts": 2})
        monkeypatch.undo()
        assert json.loads(target.read_text()) == {"ts": 1, "old": True}
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


KILL_CHILD = r"""
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("m", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
target, marker = Path(sys.argv[2]), Path(sys.argv[3])
def slow(data, f, **kw):
    f.write('{"ts": 2, "half')
    f.flush()
    marker.write_text("mid-write")
    time.sleep(60)
m.json.dump = slow
m.write(target, {"ts": 2})
"""


@pytest.mark.parametrize("name", ["gpu_mem_export", "llama_props_export"])
def test_kill_in_the_middle_of_a_write_leaves_the_old_file_whole(name, tmp_path):
    target = tmp_path / "out.json"
    old = '{"ts": 1, "processes": [], "servers": []}'
    target.write_text(old)
    marker = tmp_path / "marker"
    child = subprocess.Popen([sys.executable, "-I", "-c", KILL_CHILD,
                              str(SCRIPTS / f"{name}.py"), str(target), str(marker)])
    try:
        deadline = time.time() + 20
        while not marker.exists():
            assert child.poll() is None, "child exited before the write"
            assert time.time() < deadline, "child never reached the write"
            time.sleep(0.02)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
    assert child.returncode == -signal.SIGKILL
    assert target.read_text() == old
    # The torn half is in the temp file, never in the target.
    temps = [p for p in tmp_path.iterdir() if p.name.startswith(".out.json.")]
    assert len(temps) == 1 and temps[0].read_text().startswith('{"ts": 2, "half')
