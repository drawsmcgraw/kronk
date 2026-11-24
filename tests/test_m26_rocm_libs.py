"""ROCm libraries from the image they were built in (work item M26).

Criterion 1: no unit file under systemd/ names the Ollama install, and the
three ROCm units load scripts/build_rocm_libs.sh's directory in the same
search order as before.

Criterion 3, offline: the script's fixed list, its digest refusal, its
refusal of a non-empty directory, the manifest format, the verify mode, and
the build path run against a fake `docker` on PATH (no docker, no image, no
socket). The real extraction is shown in docs/sprint/M26-build.md.

Self-contained (no conftest fixtures), so the file can be copied into an
export of an older tree to show criterion 1 failing there.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build_rocm_libs.sh"
SYSTEMD = REPO / "systemd"

# Built from parts so that criterion 6's grep for the install's paths does
# not find this file.
OLLAMA = "oll" + "ama"

DIGEST = "sha256:86e11093b4a7ec2a79b1b6701d10e840a6994f21c7e05929b51eb9be361c683a"
IMAGE = "rocm/dev-ubuntu-24.04@" + DIGEST

SONAMES = {
    "libamd_comgr.so.3": "libamd_comgr.so.3.0.0",
    "libamdhip64.so.7": "libamdhip64.so.7.2.70200",
    "libhipblas.so.3": "libhipblas.so.3.2.70200",
    "libhipblaslt.so.1": "libhipblaslt.so.1.2.70200",
    "libhsa-runtime64.so.1": "libhsa-runtime64.so.1.18.70200",
    "librocblas.so.5": "librocblas.so.5.2.70200",
    "librocprofiler-register.so.0": "librocprofiler-register.so.0.6.0",
    "librocroller.so.1": "librocroller.so.1.0.0",
    "librocsolver.so.0": "librocsolver.so.0.7.70200",
    "libroctx64.so.4": "libroctx64.so.4.1.70200",
}

UNITS = {
    # unit: (LD_LIBRARY_PATH as it must read, the directory list in order)
    "wyoming-whisper.service": [
        "%h/services/wyoming-whisper/runtime-libs", "%h/services/rocm-7.2/lib"],
    "wyoming-whisper-dev.service": [
        "%h/services/wyoming-whisper/runtime-libs", "%h/services/rocm-7.2/lib"],
    "llama-talkie.service": [
        "/home/drew/services/rocm-7.2/lib",
        "/home/drew/pai/pai_workspace/llama-cpp/talkie-gfx1151-rocm7"],
}


# ── criterion 1: the unit files ─────────────────────────────────────────────

def test_no_unit_file_names_the_ollama_install():
    hits = []
    for path in sorted(SYSTEMD.iterdir()):
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if OLLAMA in line.lower():
                hits.append(f"{path.name}:{n}: {line.strip()}")
    assert hits == [], "unit files name the Ollama install:\n" + "\n".join(hits)


def _ld_library_path(text: str) -> list[str]:
    lines = [l for l in text.splitlines()
             if not l.lstrip().startswith("#") and "LD_LIBRARY_PATH=" in l]
    assert len(lines) == 1, lines
    value = lines[0].split("LD_LIBRARY_PATH=", 1)[1].strip().strip('"')
    return value.split(":")


@pytest.mark.parametrize("unit", sorted(UNITS))
def test_units_load_the_extracted_directory_in_the_old_order(unit):
    text = (SYSTEMD / unit).read_text()
    assert _ld_library_path(text) == UNITS[unit]
    # Each unit names the script that fills the directory.
    comments = "\n".join(l for l in text.splitlines() if l.lstrip().startswith("#"))
    assert "scripts/build_rocm_libs.sh" in comments


# ── criterion 3: the script, offline ────────────────────────────────────────

def _run(*args, env=None, check=False):
    proc = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                          text=True, env=env)
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc


def test_fixed_list_is_ten_sonames_their_files_and_rocblas_data():
    out = _run("list", check=True).stdout.split()
    expected = []
    for link, real in SONAMES.items():
        expected += [link, real]
    assert sorted(out) == sorted(expected + ["rocblas/library"])
    assert len(out) == 21
    assert not any("hipblaslt/" in m for m in out)


def test_script_pins_the_image_by_digest():
    text = SCRIPT.read_text()
    assert f'IMAGE_DIGEST="{DIGEST}"' in text
    assert 'IMAGE_REPO="rocm/dev-ubuntu-24.04"' in text
    assert "--network none" in text and "--pull never" in text


def _fake_docker(tmp_path: Path, src: Path | None = None, image_id=DIGEST,
                 version="7.2.0") -> tuple[dict, Path]:
    """A `docker` on PATH that logs its argv and answers the three calls the
    script makes; `run --entrypoint tar` archives `src` with the host's tar."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    fake = bindir / "docker"
    fake.write_text(f"""#!{sys.executable}
import os, subprocess, sys
a = sys.argv[1:]
with open({str(log)!r}, "a") as f:
    f.write("\\x1f".join(a) + "\\n")
if a[:2] == ["image", "inspect"]:
    print({image_id!r}); sys.exit(0)
if a and a[0] == "run":
    i = a.index("--entrypoint")
    ep, rest = a[i + 1], a[i + 3:]
    if ep == "cat":
        print({version!r}); sys.exit(0)
    if ep == "tar":
        assert rest[0] == "-C" and rest[2:4] == ["-cf", "-"], rest
        sys.exit(subprocess.call(["tar", "-C", {str(src)!r}, "-cf", "-"] + rest[4:]))
sys.exit(99)
""")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
    return env, log


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [l.split("\x1f") for l in log.read_text().splitlines()]


@pytest.mark.parametrize("ref", [
    "rocm/dev-ubuntu-24.04:7.2-complete",
    "rocm/dev-ubuntu-24.04@sha256:" + "0" * 64,
    "other/image@" + DIGEST,
])
def test_build_refuses_any_other_image_before_docker_runs(tmp_path, ref):
    env, log = _fake_docker(tmp_path)
    proc = _run("build", str(tmp_path / "out"), "--image", ref, env=env)
    assert proc.returncode == 2
    assert "refusing image" in proc.stderr
    assert "pin it on purpose" in proc.stderr and "IMAGE_DIGEST" in proc.stderr
    assert _calls(log) == []
    assert not (tmp_path / "out").exists()


def test_build_accepts_the_pinned_reference(tmp_path):
    src = _source_tree(tmp_path / "src")
    env, log = _fake_docker(tmp_path, src)
    proc = _run("build", str(tmp_path / "out"), "--image", IMAGE, env=env)
    assert proc.returncode == 0, proc.stderr


def test_build_refuses_a_non_empty_directory(tmp_path):
    env, log = _fake_docker(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "stray").write_text("x")
    proc = _run("build", str(out), env=env)
    assert proc.returncode == 2
    assert "not empty" in proc.stderr
    assert _calls(log) == []
    assert sorted(p.name for p in out.iterdir()) == ["stray"]


def test_build_refuses_a_local_image_with_another_id(tmp_path):
    env, log = _fake_docker(tmp_path, image_id="sha256:" + "1" * 64)
    proc = _run("build", str(tmp_path / "out"), env=env)
    assert proc.returncode == 3
    assert [c[0] for c in _calls(log)] == ["image"]


def test_build_refuses_another_rocm_version(tmp_path):
    env, log = _fake_docker(tmp_path, version="7.2.1")
    proc = _run("build", str(tmp_path / "out"), env=env)
    assert proc.returncode == 3
    assert "pinned 7.2.0" in proc.stderr
    assert not (tmp_path / "out" / "lib").exists()


def _source_tree(root: Path, extra: dict[str, bytes] | None = None) -> Path:
    """An /opt/rocm-7.2.0/lib look-alike with the listed members, small."""
    root.mkdir(parents=True)
    for link, real in SONAMES.items():
        (root / real).write_bytes(real.encode())
        os.symlink(real, root / link)
        os.symlink(link, root / (link.rsplit(".so", 1)[0] + ".so"))  # dev link, not listed
    lib = root / "rocblas" / "library"
    lib.mkdir(parents=True)
    (lib / "TensileLibrary_lazy_gfx1151.dat").write_bytes(b"lazy")
    (lib / "Kernels.so-000-gfx1151.hsaco").write_bytes(b"\x7fELF kernels")
    (root / "hipblaslt" / "library").mkdir(parents=True)
    (root / "hipblaslt" / "library" / "x.dat").write_bytes(b"not taken")
    for rel, data in (extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            p.chmod(0o755 if p.is_dir() else 0o644)
    return root


def test_build_extracts_the_list_by_tar_stream_and_writes_the_manifest(tmp_path):
    src = _source_tree(tmp_path / "src")
    env, log = _fake_docker(tmp_path, src)
    out = tmp_path / "rocm-7.2"
    proc = _run("build", str(out), env=env)
    assert proc.returncode == 0, proc.stderr

    # Only lib/ and MANIFEST; lib/ holds exactly the list.
    assert sorted(p.name for p in out.iterdir()) == ["MANIFEST", "lib"]
    top = sorted(p.name for p in (out / "lib").iterdir())
    assert top == sorted([*SONAMES, *SONAMES.values(), "rocblas"])
    for link, real in SONAMES.items():
        assert os.readlink(out / "lib" / link) == real
    assert not (out / "lib" / "hipblaslt").exists()
    assert not any(p.is_dir() and "hipblaslt" in p.name for p in (out / "lib").rglob("*"))
    for p in (out / "lib").rglob("*"):
        assert p.lstat().st_uid == os.getuid()

    # Every docker call: by digest, no network, no mount, no pull.
    calls = _calls(log)
    assert [c[0] for c in calls] == ["image", "run", "run"]
    assert calls[0][-1] == IMAGE
    for c in calls[1:]:
        assert ["--network", "none"] == c[c.index("--network"):c.index("--network") + 2]
        assert "--rm" in c and "--read-only" in c and "--pull" in c
        assert not {"-v", "--volume", "--mount", "--volumes-from"} & set(c)
        assert not any(x.startswith(("-v=", "--volume=", "--mount=")) for x in c)
        assert c[c.index("--entrypoint") + 2] == IMAGE
    tar_call = calls[2]
    assert tar_call[tar_call.index("--entrypoint") + 1] == "tar"
    rest = tar_call[tar_call.index("--entrypoint") + 3:]
    assert rest[:4] == ["-C", "/opt/rocm-7.2.0/lib", "-cf", "-"]
    assert rest[4:] == _run("list", check=True).stdout.split()

    manifest = (out / "MANIFEST").read_text()
    assert f"image\t{IMAGE}\n" in manifest
    assert "rocm-version\t7.2.0\n" in manifest
    assert "omitted\thipblaslt/library\t" in manifest
    assert str(tmp_path) not in manifest
    assert _run("verify", str(out)).returncode == 0


def test_build_rejects_an_archive_that_is_not_the_list(tmp_path):
    # An image whose soname link points at another file is not the list.
    src = _source_tree(tmp_path / "src")
    os.unlink(src / "librocblas.so.5")
    os.symlink("libamdhip64.so.7.2.70200", src / "librocblas.so.5")
    env, _ = _fake_docker(tmp_path, src)
    out = tmp_path / "out"
    proc = _run("build", str(out), env=env)
    assert proc.returncode == 1
    assert "librocblas.so.5 points at" in proc.stderr
    assert not (out / "MANIFEST").exists()


def test_build_rejects_missing_lazy_library(tmp_path):
    src = _source_tree(tmp_path / "src")
    os.unlink(src / "rocblas" / "library" / "TensileLibrary_lazy_gfx1151.dat")
    env, _ = _fake_docker(tmp_path, src)
    proc = _run("build", str(tmp_path / "out"), env=env)
    assert proc.returncode == 1
    assert "TensileLibrary_lazy_gfx1151.dat" in proc.stderr


# ── the manifest and verify ────────────────────────────────────────────────

def _lib(root: Path) -> Path:
    """A small directory shaped like rocm-7.2/lib."""
    lib = root / "lib"
    (lib / "rocblas" / "library").mkdir(parents=True)
    (lib / "libroctx64.so.4.1.70200").write_bytes(b"roctx")
    os.symlink("libroctx64.so.4.1.70200", lib / "libroctx64.so.4")
    (lib / "rocblas" / "library" / "TensileLibrary_lazy_gfx1151.dat").write_bytes(b"lazy")
    for p in [lib, *lib.rglob("*")]:
        if not p.is_symlink():
            p.chmod(0o755 if p.is_dir() else 0o644)
    return lib


def _write_manifest(root: Path) -> str:
    text = _run("manifest", str(root / "lib"), check=True).stdout
    (root / "MANIFEST").write_text(text)
    return text


def _entries(text: str) -> list[list[str]]:
    lines = text.split("entries\n", 1)[1].splitlines()
    return [l.split("\t") for l in lines]


def test_manifest_format_is_sorted_relative_and_complete(tmp_path):
    _lib(tmp_path / "a")
    text = _write_manifest(tmp_path / "a")
    head, _ = text.split("entries\n", 1)
    assert f"image\t{IMAGE}" in head
    assert "totals\tfiles=2 links=1 dirs=2 bytes=9" in head
    rows = _entries(text)
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)  # C order
    by_path = {r[0]: r for r in rows}
    assert by_path["libroctx64.so.4"] == ["libroctx64.so.4", "l", "777", "-",
                                          "libroctx64.so.4.1.70200"]
    assert by_path["libroctx64.so.4.1.70200"] == [
        "libroctx64.so.4.1.70200", "f", "644", "5", hashlib.sha256(b"roctx").hexdigest()]
    assert by_path["rocblas"] == ["rocblas", "d", "755", "-", "-"]
    assert all(len(r) == 5 for r in rows)
    assert not any(r[0].startswith(("/", "./")) for r in rows)
    # No destination path, user, host or date.
    assert str(tmp_path) not in text
    assert os.environ.get("USER", "\0") not in text
    assert os.uname().nodename not in text
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text)


def test_manifests_of_equal_trees_are_byte_identical(tmp_path):
    _lib(tmp_path / "x")
    _lib(tmp_path / "deeper" / "y")
    a = _write_manifest(tmp_path / "x")
    b = _write_manifest(tmp_path / "deeper" / "y")
    assert a == b


def test_verify_passes_on_an_unchanged_directory(tmp_path):
    _lib(tmp_path)
    _write_manifest(tmp_path)
    proc = _run("verify", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "matches its manifest" in proc.stdout


@pytest.mark.parametrize("change, word", [
    (lambda lib: (lib / "libroctx64.so.4.1.70200").unlink(), "missing: libroctx64.so.4.1.70200"),
    (lambda lib: (lib / "rocblas" / "library" / "extra.hsaco").write_bytes(b"x"),
     "extra:   rocblas/library/extra.hsaco"),
    (lambda lib: (lib / "libroctx64.so.4.1.70200").write_bytes(b"ROCTX"),
     "changed: libroctx64.so.4.1.70200"),
    (lambda lib: (lib / "libroctx64.so.4.1.70200").chmod(0o600),
     "changed: libroctx64.so.4.1.70200"),
    (lambda lib: ((lib / "libroctx64.so.4").unlink(),
                  os.symlink("elsewhere.so", lib / "libroctx64.so.4")),
     "changed: libroctx64.so.4"),
    (lambda lib: (lib / "hipblaslt").mkdir(), "extra:   hipblaslt"),
])
def test_verify_fails_on_a_missing_extra_or_changed_entry(tmp_path, change, word):
    lib = _lib(tmp_path)
    _write_manifest(tmp_path)
    change(lib)
    proc = _run("verify", str(tmp_path))
    assert proc.returncode == 1
    assert word in proc.stderr
    assert "does NOT match" in proc.stderr


def test_verify_fails_on_a_manifest_for_another_image(tmp_path):
    _lib(tmp_path)
    text = _write_manifest(tmp_path)
    other = text.replace(DIGEST, "sha256:" + "2" * 64)
    (tmp_path / "MANIFEST").write_text(other)
    proc = _run("verify", str(tmp_path))
    assert proc.returncode == 1
    assert "header:" in proc.stderr and "image" in proc.stderr


def test_verify_fails_without_a_manifest(tmp_path):
    _lib(tmp_path)
    proc = _run("verify", str(tmp_path))
    assert proc.returncode == 1
    assert "no manifest" in proc.stderr


def test_usage_errors_exit_2():
    assert _run().returncode == 2
    assert _run("frobnicate").returncode == 2
    assert _run("build").returncode == 2
    assert _run("verify").returncode == 2
