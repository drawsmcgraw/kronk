"""Sprint M16: the config backup, the recordings archive, the stale check,
their units and GET /api/backups. Everything runs in tmp_path with a fake
Ops: no docker, no git, no socket, no notice leaves the test."""
import datetime as dt
import errno
import fcntl
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("m16_backups", ROOT / "scripts" / "backups.py")
B = importlib.util.module_from_spec(spec)
sys.modules["m16_backups"] = B
spec.loader.exec_module(B)

NOW = dt.datetime(2026, 12, 10, 1, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
SECRET = "HA_TOKEN=not-a-real-token-but-treat-it-as-one-7f3c"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Criterion 16: these tests reach no network. A connect fails the test."""
    import socket

    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", "/bin/false")     # belt and braces: never notify.sh


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            if data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                t.addfile(ti)
                continue
            ti.size = len(data)
            ti.mode = 0o644
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class FakeOps(B.Ops):
    def __init__(self, now=NOW):
        self.t = now
        self.notices: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.log = self.logs.append
        self.mounted: set[str] = set()
        self.notify_ok = True
        self.ha = tar_of({"./.storage/": None, "./.storage/core.entity_registry": b'{"data":{"entities":[]}}',
                          "./.storage/assist_pipeline.pipelines": b'{"data":{"items":[]}}',
                          "./configuration.yaml": b"homeassistant:\n"})
        self.ha_rc = 0
        self.keep_local: set[str] = set()     # recordings whose local delete "fails"
        self.removed: list[list[str]] = []
        self.commands: list[list[str]] = []

    def run(self, argv, timeout=300, stdin=None):
        self.commands.append(argv)
        if argv[0] == "git":
            if "rev-list" in argv:
                assert argv[-1] == "origin/0123abc..HEAD"     # no upstream in the fake: origin/<branch>
                return B.Result(0, b"3\n", "")
            if "--symbolic-full-name" in argv:
                return B.Result(128, b"", "fatal: no upstream configured")
            return B.Result(0, b"0123abc\n" if "rev-parse" in argv else b"", "")
        if argv[:2] in (["docker", "ps"], ["docker", "inspect"]):
            return B.Result(0, b"kronk-orchestrator-1\t/home/x/kronk-releases/sprint-M16\n", "")
        raise AssertionError(f"unexpected command in a test: {argv}")

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=s)

    def ismount(self, p):
        return str(p) in self.mounted

    def notify(self, title, body):
        self.notices.append((title, body))
        return self.notify_ok

    def container_tar(self, container, cwd, names):
        return B.Result(self.ha_rc, self.ha if self.ha_rc == 0 else b"", "" if self.ha_rc == 0 else "No such container: homeassistant")

    def container_cat(self, container, path):
        return B.Result(0, b'{"providers": {}}', "")

    def helper_tar(self, hostdir):
        files = {f"./{p.relative_to(hostdir)}": p.read_bytes() for p in hostdir.rglob("*") if p.is_file()}
        return B.Result(0, tar_of(files), "")

    def remove_recordings(self, src, rels, dirs):
        self.removed.append(list(rels))
        for r in rels:
            if r not in self.keep_local:
                os.unlink(src / r)
        for d in dirs:
            try:
                os.rmdir(src / d)
            except OSError:
                pass
        return B.Result(0, b"", "")


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A repo, a home, /etc, a destination and a state directory, all in tmp_path."""
    home = tmp_path / "home"
    repo = home / "git-repos" / "kronk"
    for d in ("data", "searxng", "secrets/mm", "ops", "scripts/lib"):
        (repo / d).mkdir(parents=True)
    (repo / ".env").write_text(SECRET + "\n")
    os.chmod(repo / ".env", 0o600)
    (repo / ".env.dev").write_text("MUSIC_DRY_RUN=1\n")
    (repo / ".env.telemetry").write_text("GF_SECURITY_ADMIN_PASSWORD=x\n")
    (repo / "searxng" / "settings.yml").write_text("server: {}\n")
    (repo / "secrets" / "empty").mkdir()
    (repo / "secrets" / "mm" / "kronk-mm-update").write_text("KEY\n")
    (repo / "data" / "music_taste.yaml").write_text("love: []\n")
    (repo / "data" / "kronk_state.md").write_text("# state\n")
    (repo / "data" / "mm_known_hosts").write_text("host key\n")
    (repo / "data" / "yt-music-cookie").write_text("cookie\n")
    import sqlite3
    for db in ("solar.db", "music_taste.db"):
        c = sqlite3.connect(repo / "data" / db)
        c.execute("create table t (x)")
        c.execute("insert into t values (42)")
        c.commit()
        c.close()
    (repo / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:1.29-alpine\n")
    live = home / "git-repos" / "kronk-config"
    for inst in ("prod", "dev"):
        (live / inst / ".git").mkdir(parents=True)
        (live / inst / "tuning.yaml").write_text(f"instance: {inst}\n")
        (live / inst / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    units = home / ".config" / "systemd" / "user"
    (units / "timers.target.wants").mkdir(parents=True)
    (units / "llama-x.service").write_text("[Service]\nExecStart=/bin/llama -m %h/models/x.gguf\n")
    (units / "timers.target.wants" / "x.timer").symlink_to(units / "x.timer")
    (home / ".config" / "kronk").mkdir()
    (home / ".config" / "kronk" / "upgrade.env").write_text("KRONK_UPGRADE_BRANCH=refactor\n")
    (home / "models").mkdir()
    (home / "models" / "x.gguf").write_bytes(b"GGUF" * 1000)
    (home / "models" / "PROVENANCE.sha256").write_text("abc  x.gguf\n")
    etc = tmp_path / "etc"
    (etc / "default").mkdir(parents=True)
    (etc / "default" / "grub").write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet ttm.pages_limit=1"\n')
    (etc / "kronk").mkdir()
    (etc / "kronk" / "nas-backup.cred").write_text("username=u\npassword=p\n")
    (etc / "fstab").write_text("UUID=x / ext4 defaults 0 1\n# the NAS\n//atlas.local/music /mnt/nas-music cifs ro,credentials=/etc/kronk/nas-music.cred 0 0\n")
    (etc / "os-release").write_text('PRETTY_NAME="Test OS"\n')
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cmdline").write_text("BOOT_IMAGE=/vmlinuz ttm.pages_limit=1\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    state = repo / "data" / "backups"
    monkeypatch.setattr(B, "HOME", home)
    s = B.Settings(dest=dest, require_mount=False, state=state, repo=repo, live_config=live,
                   upgrade_state=repo / "data" / "upgrades", model_dirs=(str(home / "models"),),
                   recordings=home / "kronk-voice-recordings", etc=etc, proc=proc, lock_wait_min=2)
    return s


def tar_names(p: Path) -> list[str]:
    with tarfile.open(p, "r:gz") as t:
        return t.getnames()


def read_member(p: Path, suffix: str) -> bytes:
    with tarfile.open(p, "r:gz") as t:
        for m in t:
            if m.name.endswith(suffix):
                return t.extractfile(m).read()
    raise KeyError(suffix)


def every_file(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


# ═══ Config backup ═════════════════════════════════════════════════════════

def test_one_dated_tar_mode_0600_with_every_item_and_a_manifest(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    files = list((house.dest / "config").iterdir())
    assert [f.name for f in files] == ["kronk-config-2026-12-10.tar.gz"]
    p = files[0]
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    names = tar_names(p)
    top = "kronk-config-2026-12-10/"
    for want in ("repo/.env", "repo/.env.dev", "repo/.env.telemetry", "repo/searxng/settings.yml",
                 "repo/secrets/mm/kronk-mm-update", "repo/data/music_taste.yaml", "repo/data/solar.db",
                 "repo/data/music_taste.db", "live-config/prod/tuning.yaml", "live-config/dev/.git/HEAD",
                 "systemd-user/llama-x.service", "systemd-user/timers.target.wants/x.timer",
                 "config-kronk/upgrade.env", "etc/default/grub", "etc/kronk/nas-backup.cred",
                 "ha/config/.storage/core.entity_registry", "ha/config/configuration.yaml",
                 "ma/settings.json", "repo-state.txt", "images.txt", "models.tsv", "host-facts.txt", "MANIFEST"):
        assert top + want in names, want
    assert names[-1] == top + "MANIFEST"
    # The live .env is byte-identical inside; the SQLite copies are real databases.
    assert read_member(p, "repo/.env") == (house.repo / ".env").read_bytes()
    assert read_member(p, "repo/data/solar.db").startswith(b"SQLite format 3")
    with tarfile.open(p, "r:gz") as t:
        link = t.getmember(top + "systemd-user/timers.target.wants/x.timer")
        assert link.issym()       # stored as a link, never followed
        assert t.getmember(top + "live-config/prod/.git").isdir()      # directories kept, empty ones too
        assert t.getmember(top + "ha/config/.storage").isdir()
    man = read_member(p, "MANIFEST").decode()
    env_sha = __import__("hashlib").sha256((house.repo / ".env").read_bytes()).hexdigest()
    assert f"{env_sha}\t{len(SECRET) + 1}\tfile\trepo/.env\t<- {house.repo / '.env'}" in man
    assert "sqlite backup\trepo/data/solar.db" in man
    assert "docker exec homeassistant tar\tha/config/.storage/core.entity_registry" in man
    assert "ABSENT\t" + str(house.repo / "ops" / "hosts.json") in man
    assert "directory\t0\tdirectory\trepo/secrets/empty\t" in man
    assert "ABSENT\t" + str(B.HOME / "kronk-voice-labels") in man
    state = read_member(p, "repo-state.txt").decode()
    assert "not pushed: 3 commits ahead of origin/0123abc" in state
    assert "commit: 0123abc" in state and "pushed:" in state and "production tag: sprint-M16 (1 containers)" in state
    assert "docker-compose.yml\tnginx:1.29-alpine" in read_member(p, "images.txt").decode()
    models = read_member(p, "models.tsv").decode()
    gguf = B.HOME / "models" / "x.gguf"
    assert __import__("hashlib").sha256(gguf.read_bytes()).hexdigest() + f"\t4000\t" in models
    assert "llama-x.service" in models          # named by the unit
    assert "models" + str(B.HOME / "models" / "PROVENANCE.sha256") in "\n".join(names)
    facts = read_member(p, "host-facts.txt").decode()
    assert "ttm.pages_limit=1" in facts and "//atlas.local/music /mnt/nas-music cifs" in facts and "# the NAS" in facts
    assert "UUID=x" not in facts


def test_no_secret_reaches_a_log_line_a_notice_or_the_status_file(house):
    ops = FakeOps()
    B.cmd_config(house, ops, dry_run=False)
    B.cmd_config(house, ops, dry_run=True)
    text = "\n".join(ops.logs) + "\n".join(t + b for t, b in ops.notices) + (house.state / "status.json").read_text()
    for secret in (SECRET, "password=p", "KEY", "GF_SECURITY_ADMIN_PASSWORD"):
        assert secret not in text


def test_what_cannot_be_read_is_listed_in_the_manifest_and_the_notice(house):
    ops = FakeOps()
    ops.ha_rc = 1
    (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o000)
    try:
        assert B.cmd_config(house, ops, dry_run=False) == 0
    finally:
        (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o600)
    p = next((house.dest / "config").iterdir())
    man = read_member(p, "MANIFEST").decode()
    assert "UNREADABLE\thomeassistant:/config (.storage, YAML, blueprints)\tdocker exec failed (1)" in man
    if os.geteuid() != 0:
        assert "UNREADABLE\t" + str(house.repo / "secrets" / "mm" / "kronk-mm-update") in man
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup OK" and "NOT READ" in body and "homeassistant" in body
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last"]["unreadable"]


def test_start_and_end_notices_with_sizes_and_totals(house):
    ops = FakeOps()
    (house.dest / "recordings" / "d" / "p").mkdir(parents=True)
    (house.dest / "recordings" / "d" / "p" / "old.wav").write_bytes(b"x" * 2_500_000)
    B.cmd_config(house, ops, dry_run=False)
    assert ops.notices[0] == ("Kronk backup: config backup started",
                              f"Writing the 2026-12-10 config backup to {house.dest}/config.")
    title, body = ops.notices[1]
    assert title == "Kronk backup: config backup OK"
    assert re.match(r"kronk-config-2026-12-10\.tar\.gz, \d+\.\d KB, \d+ entries\. Repo: 3 commits not pushed "
                    r"\(they exist only on this machine; the tar does not hold the code\)\.", body), body
    assert re.search(r"On the destination: config backups \d+\.\d KB \(1 file\), recordings archive 2\.5 MB "
                     r"\(1 file\), together 2\.5 MB\.$", body), body


def test_a_notice_that_fails_does_not_fail_the_job(house):
    ops = FakeOps()
    ops.notify_ok = False
    assert B.cmd_config(house, ops, dry_run=False) == 0
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last"]["result"] == "ok" and st["config"]["last"]["notice_sent"] is False


def test_the_real_notifier_runs_the_stub_command_with_tag_title_body(tmp_path, monkeypatch):
    out = tmp_path / "notices.txt"
    stub = tmp_path / "stub.sh"
    stub.write_text(f'#!/bin/sh\nprintf "%s|%s|%s\\n" "$1" "$2" "$3" >> {out}\n')
    stub.chmod(0o755)
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", str(stub))
    assert B.Ops(log=lambda m: None).notify("Kronk backup: config backup OK", "a b; c") is True
    assert out.read_text() == "kronk-backup|Kronk backup: config backup OK|a b; c\n"
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", str(tmp_path / "missing"))
    logs = []
    assert B.Ops(log=logs.append).notify("t", "b") is False and "notice not sent" in logs[0]


def test_written_to_a_temporary_name_then_verified_then_renamed(house, monkeypatch):
    ops = FakeOps()
    seen = {}
    real = B.verify_tar

    def spy(p, sha, n):
        seen["name"] = p.name
        seen["final_exists"] = any(f.name.startswith("kronk-config") for f in p.parent.iterdir())
        return real(p, sha, n)
    monkeypatch.setattr(B, "verify_tar", spy)
    B.cmd_config(house, ops, dry_run=False)
    assert seen == {"name": ".kronk-config-2026-12-10.tar.gz.tmp", "final_exists": False}


def test_a_failed_verify_leaves_no_file_and_says_so(house, monkeypatch):
    ops = FakeOps()
    monkeypatch.setattr(B, "verify_tar", lambda p, sha, n: "read back differs (test)")
    assert B.cmd_config(house, ops, dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []
    assert ops.notices[-1][0] == "Kronk backup: config backup FAILED"
    assert "read back differs (test); the temporary file was removed" in ops.notices[-1][1]


def test_verify_reads_the_tar_to_the_end(tmp_path):
    p = tmp_path / "t.tar.gz"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
    sha, _ = B.write_tar(fd, "top", [B.Member("a", "s", "file", b"x" * 100_000)])
    os.close(fd)
    assert B.verify_tar(p, sha, 1) is None
    assert "members" in B.verify_tar(p, sha, 2)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    assert "read back differs" in B.verify_tar(p, sha, 1)
    assert "cannot read it to the end" in B.verify_tar(p, B.sha256_file(p, False), 1)


def test_an_interrupted_run_leaves_only_a_temporary_name_which_the_next_run_removes(house, monkeypatch):
    ops = FakeOps()

    def killed(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(B, "verify_tar", killed)
    with pytest.raises(KeyboardInterrupt):
        B._config(house, ops, {"at": "x"}, "2026-12-10", "kronk-config-2026-12-10", True)
    # The kill cleaned its own file here; a SIGKILL would not: leave one by hand.
    cfg = house.dest / "config"
    (cfg / ".kronk-config-2026-11-10.tar.gz.tmp").write_bytes(b"half")
    (cfg / ".kronk-config-notes.tmp").write_bytes(b"not ours")
    (cfg / ".kronk-config-2026-11-09.tar.gz.tmp").mkdir()          # a directory: not ours
    assert not any(CONFIG_FINAL(f.name) for f in cfg.iterdir())
    monkeypatch.undo()
    monkeypatch.setattr(B, "HOME", house.repo.parent.parent)
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert sorted(f.name for f in cfg.iterdir()) == [".kronk-config-2026-11-09.tar.gz.tmp", ".kronk-config-notes.tmp",
                                                     "kronk-config-2026-12-10.tar.gz"]


def CONFIG_FINAL(name):
    return B.CONFIG_RE.fullmatch(name) is not None


def test_a_second_run_the_same_day_never_overwrites(house):
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_config(house, FakeOps(), dry_run=False)
    assert sorted(f.name for f in (house.dest / "config").iterdir()) == [
        "kronk-config-2026-12-10.2.tar.gz", "kronk-config-2026-12-10.tar.gz"]


def test_retention_keeps_the_newest_n_of_its_own_files_only(house):
    cfg = house.dest / "config"
    cfg.mkdir()
    old = [f"kronk-config-2025-{m:02d}-10.tar.gz" for m in range(1, 13)] + ["kronk-config-2026-11-10.tar.gz"]
    for n in old:
        (cfg / n).write_bytes(b"old")
    foreign = ["notes.txt", "kronk-config-2020-01-01.tar.gz.keep", "kronk-config-latest.tar.gz"]
    for n in foreign:
        (cfg / n).write_bytes(b"mine")
    (cfg / "kronk-config-2019-01-01.tar.gz").mkdir()          # a directory with the name: never removed
    (house.dest / "kronk-config-2018-01-01.tar.gz").write_bytes(b"outside config/")
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    left = sorted(f.name for f in cfg.iterdir())
    kept = sorted(n for n in left if CONFIG_FINAL(n) and (cfg / n).is_file())
    assert len(kept) == 12 and kept[-1] == "kronk-config-2026-12-10.tar.gz"
    assert "kronk-config-2025-01-10.tar.gz" not in left and "kronk-config-2025-02-10.tar.gz" not in left
    for n in foreign + ["kronk-config-2019-01-01.tar.gz"]:
        assert n in left
    assert (house.dest / "kronk-config-2018-01-01.tar.gz").exists()
    assert "Removed by retention: kronk-config-2025-01-10.tar.gz, kronk-config-2025-02-10.tar.gz." in ops.notices[-1][1]


def test_retention_never_runs_when_the_run_failed(house, monkeypatch):
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 15):
        (cfg / f"kronk-config-2025-{m % 12 + 1:02d}-{m:02d}.tar.gz").write_bytes(b"old")
    before = sorted(f.name for f in cfg.iterdir())
    monkeypatch.setattr(B, "verify_tar", lambda *a: "broken (test)")
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert sorted(f.name for f in cfg.iterdir()) == before


def test_over_the_ceiling_is_a_failure_naming_the_largest_members(house):
    (house.repo / "data" / "music_taste.yaml").write_bytes(os.urandom(300_000))
    house.max_bytes = 100_000
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []
    body = ops.notices[-1][1]
    assert "over the 100.0 KB ceiling" in body and "largest members: repo/data/music_taste.yaml 300.0 KB" in body


def test_dry_run_lists_with_sizes_and_writes_and_sends_nothing(house):
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=True) == 0
    assert list(house.dest.iterdir()) == [] and not house.state.exists() and ops.notices == []
    out = "\n".join(ops.logs)
    assert re.search(r"\s+\d+  repo/\.env$", out, re.M)
    assert "nothing written, nothing sent" in out


def test_an_unmounted_destination_is_a_failure_and_gets_nothing(house):
    house.require_mount = True
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 2
    assert list(house.dest.iterdir()) == []
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup FAILED" and "is not a mounted filesystem" in body
    assert json.loads((house.state / "status.json").read_text())["config"]["last"]["result"] == "failed"
    ops.mounted.add(str(house.dest))
    assert B.cmd_config(house, ops, dry_run=False) == 0


def test_the_default_destination_always_requires_a_mount(monkeypatch):
    monkeypatch.delenv("KRONK_BACKUP_DIR", raising=False)
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    a = type("A", (), {"dest": None, "require_mount": False})()
    assert B.Settings.load(a).require_mount is True and str(B.Settings.load(a).dest) == "/mnt/nas-backup"
    a.dest = "/tmp/x"
    assert B.Settings.load(a).require_mount is False
    monkeypatch.setenv("KRONK_BACKUP_REQUIRE_MOUNT", "1")
    assert B.Settings.load(a).require_mount is True


def test_waits_for_the_other_job_then_declines(house):
    house.state.mkdir(parents=True)
    fd = os.open(house.state / "lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ops = FakeOps()
        assert B.cmd_config(house, ops, dry_run=False) == 3
    finally:
        os.close(fd)
    assert ops.t - NOW == dt.timedelta(minutes=2)
    assert ops.notices[-1][0] == "Kronk backup: config backup DECLINED"
    assert list(house.dest.iterdir()) == []


def test_declines_while_an_upgrade_run_holds_its_lock_and_never_creates_it(house):
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    assert not (house.upgrade_state / "lock").exists()
    house.upgrade_state.mkdir(parents=True)
    fd = os.open(house.upgrade_state / "lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ops = FakeOps()
        assert B.cmd_archive(house, ops, dry_run=False) == 3
        assert "an upgrade run holds" in ops.notices[-1][1]
    finally:
        os.close(fd)


# ═══ Recordings archive ════════════════════════════════════════════════════

def rec(house, device, pipeline, run, name="01_stt-stt.faster_whisper.wav", age_days=10, data=None):
    d = house.recordings / device / pipeline / run
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(data if data is not None else os.urandom(3200))
    t = (NOW - dt.timedelta(days=age_days)).timestamp()
    os.utime(p, (t, t))
    return p


def archived(house, p: Path) -> Path:
    rel = p.relative_to(house.recordings).parts
    month = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m")
    return house.dest / "recordings" / rel[0] / rel[1] / month / rel[2] / rel[3]


def test_old_recordings_move_keeping_names_and_times_young_ones_stay(house):
    old = rec(house, "dev1", "kronk", "1203688800703734", age_days=10)
    young = rec(house, "dev1", "kronk", "1299062379733951", age_days=1)
    data, mtime, target = old.read_bytes(), old.stat().st_mtime_ns, archived(house, old)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert target == house.dest / "recordings/dev1/kronk/2026-11/1203688800703734/01_stt-stt.faster_whisper.wav"
    assert target.read_bytes() == data and target.stat().st_mtime_ns == mtime
    for d in (target.parent, target.parent.parent, house.dest / "recordings" / "dev1"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    assert not old.exists() and not old.parent.exists()      # the emptied run directory goes too
    assert young.exists() and (house.recordings / "dev1" / "kronk").is_dir()
    assert ops.notices[0] == ("Kronk backup: recordings archive started",
                              f"Moving recordings older than 7 days from {house.recordings} to {house.dest}/recordings.")
    title, body = ops.notices[-1]
    assert title == "Kronk backup: recordings archive OK"
    assert body.startswith("Moved 1 recording(s) (3.2 KB); 1 remain local (3.2 KB). Written this run: 3.2 KB. "
                           "On the destination: config backups 0 B (0 files), recordings archive 3.2 KB (1 file), together 3.2 KB.")


def test_a_second_run_with_nothing_new_copies_and_deletes_nothing(house):
    rec(house, "dev1", "kronk", "1", age_days=10)
    rec(house, "dev1", "kronk", "2", age_days=1)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    before = every_file(house.dest)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert every_file(house.dest) == before and ops.removed == []
    assert ops.notices[-1][1].startswith("Moved 0 recording(s) (0 B); 1 remain local (3.2 KB). Written this run: 0 B.")


def test_same_hash_on_the_destination_is_not_copied_again_and_is_deleted_locally(house):
    """Also the state a run killed between copy and delete leaves behind."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(p.read_bytes())
    os.utime(t, (1, 1))           # a different mtime: the hash decides, and the copy is not touched
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert not p.exists() and t.stat().st_mtime == 1
    assert "Moved 1 recording(s)" in ops.notices[-1][1] and "Written this run: 0 B" in ops.notices[-1][1]


def test_same_name_different_hash_is_never_overwritten_both_kept_and_reported(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(b"something else")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"something else" and p.exists()
    assert ops.notices[-1][0] == "Kronk backup: recordings archive FAILED"
    assert "a different file of the same name (both kept)" in ops.notices[-1][1]


def test_fault_destination_not_mounted(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    house.require_mount = True
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert p.exists() and list(house.dest.iterdir()) == [] and ops.removed == []
    assert "is not a mounted filesystem" in ops.notices[-1][1]


def test_fault_destination_full_mid_copy(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", age_days=10, data=os.urandom(3 << 20))
    b = rec(house, "dev1", "kronk", "2", age_days=10)
    calls = {"n": 0}
    real = B._write_all

    def full(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd, data)
    monkeypatch.setattr(B, "_write_all", full)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.exists() and b.exists() and ops.removed == []
    assert not any(f.name == B.ARCHIVE_TMP for f in house.dest.rglob("*"))
    assert [f for f in house.dest.rglob("*") if f.is_file()] == []
    assert "destination full: No space left on device" in ops.notices[-1][1]


def test_fault_the_copy_reads_back_different(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    real = B.sha256_file
    monkeypatch.setattr(B, "sha256_file", lambda q, drop=True: "0" * 64 if q.name == B.ARCHIVE_TMP else real(q, drop))
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "the copy read back different; kept locally" in ops.notices[-1][1]


def test_fault_the_local_delete_fails_then_the_next_run_finishes_it(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    ops = FakeOps()
    ops.keep_local.add("dev1/kronk/1/01_stt-stt.faster_whisper.wav")
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and archived(house, p).exists()
    assert "local delete failed" in ops.notices[-1][1]
    ops2 = FakeOps()
    assert B.cmd_archive(house, ops2, dry_run=False) == 0
    assert not p.exists() and "Written this run: 0 B" in ops2.notices[-1][1]


def test_fault_killed_mid_copy_the_next_run_replaces_its_own_temporary_file(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    (t.parent / B.ARCHIVE_TMP).write_bytes(b"half a copy")
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 0
    assert sorted(f.name for f in t.parent.iterdir()) == [t.name] and not p.exists()


def test_fault_a_recording_being_written_is_left_for_the_next_run(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    real = B._write_all
    once = []

    def growing(fd, data):
        real(fd, data)
        if not once:                      # HA appends once while the archive reads
            once.append(1)
            with open(p, "ab") as f:
                f.write(b"more audio")
    monkeypatch.setattr(B, "_write_all", growing)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "Left for the next run: 1 (dev1/kronk/1/01_stt-stt.faster_whisper.wav: changed while it was read" in ops.notices[-1][1]


def test_fault_symlinks_are_not_followed(house, tmp_path):
    outside = tmp_path / "outside"
    (outside / "kronk" / "9").mkdir(parents=True)
    victim = outside / "kronk" / "9" / "x.wav"
    victim.write_bytes(b"not a recording")
    os.utime(victim, (1, 1))
    house.recordings.mkdir(parents=True)
    (house.recordings / "linkdev").symlink_to(outside)                  # a symlinked device directory
    rec(house, "dev1", "kronk", "1", age_days=10)
    (house.recordings / "dev1" / "kronk" / "1" / "02_link.wav").symlink_to(victim)   # a symlinked file
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1        # reported as a problem: a person looks
    assert victim.read_bytes() == b"not a recording"
    assert (house.recordings / "dev1" / "kronk" / "1" / "02_link.wav").is_symlink()
    files = [f.name for f in house.dest.rglob("*") if f.is_file()]
    assert files == ["01_stt-stt.faster_whisper.wav"]
    assert "linkdev: symlink, not followed" in ops.notices[-1][1]


def test_fault_names_with_spaces_and_newlines(house):
    p = rec(house, "dev 1", "kronk dev", "run\nwith newline", name="a b\nc.wav", age_days=10)
    data, target = p.read_bytes(), archived(house, p)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert target.read_bytes() == data and not p.exists()
    assert ops.removed == [["dev 1/kronk dev/run\nwith newline/a b\nc.wav"]]


def test_the_delete_runs_by_literal_path_in_a_container_that_sees_only_the_recordings(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    monkeypatch.delenv("KRONK_BACKUP_HELPER_IMAGE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"", "")
    src = tmp_path / "rec"
    Capture().remove_recordings(src, ["d/p/r 1/a\nb.wav"], ["d/p/r 1"])
    argv = seen[0]
    assert argv[:9] == ["docker", "run", "--rm", "--pull=never", "--network", "none", "-v", f"{src}:/rec", "alpine:3.20"]
    assert argv[-3:] == ["1", "d/p/r 1/a\nb.wav", "d/p/r 1"]       # arguments, never part of the script
    assert "d/p" not in argv[11]
    assert Capture().remove_recordings(tmp_path / "a,b", ["x"], []).rc == -1   # a path -v cannot carry
    assert len(seen) == 1


def test_nothing_outside_the_recordings_and_nothing_on_the_destination_is_deleted(house, tmp_path, monkeypatch):
    """Criterion 10: a tree around both, through every fault above."""
    around = tmp_path / "around"
    (around / "kronk-voice-recordings-old").mkdir(parents=True)
    (around / "kronk-voice-recordings-old" / "keep.wav").write_bytes(b"k")
    (house.dest / "config").mkdir()
    (house.dest / "config" / "kronk-config-2026-01-01.tar.gz").write_bytes(b"c")
    (house.dest / "other").mkdir()
    (house.dest / "other" / "photo.jpg").write_bytes(b"p")
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5").mkdir(parents=True)
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5" / "01_stt.wav").write_bytes(b"archived earlier")
    (house.dest / "recordings" / "dev1" / "kronk" / "2026-01" / "5" / ".kronk-archive.tmp.keep").write_bytes(b"x")
    rec(house, "dev1", "kronk", "1", age_days=10)
    rec(house, "dev1", "kronk", "2", age_days=10)
    house.recordings.joinpath("stray.txt").write_bytes(b"not at the recording depth")
    outside_before = every_file(around)
    dest_before = every_file(house.dest)
    ops = FakeOps()
    ops.keep_local.add("dev1/kronk/2/01_stt-stt.faster_whisper.wav")
    B.cmd_archive(house, ops, dry_run=False)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    assert every_file(around) == outside_before
    after = every_file(house.dest)
    assert all(after.get(k) == v for k, v in dest_before.items())          # nothing removed or changed
    assert house.recordings.joinpath("stray.txt").exists()


def test_archive_dry_run_writes_deletes_and_sends_nothing(house):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=True) == 0
    assert p.exists() and list(house.dest.iterdir()) == [] and ops.notices == [] and ops.removed == []
    assert not house.state.exists()
    assert "would move" in "\n".join(ops.logs) and "recordings/dev1/kronk/2026-11/1/" in "\n".join(ops.logs)


# ═══ The stale check ═══════════════════════════════════════════════════════

def test_stale_check_with_a_clock(house):
    rec(house, "dev1", "kronk", "1", age_days=10)
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    # The tar's mtime is the real clock; pin it to the fake run time.
    tarp = next((house.dest / "config").iterdir())
    os.utime(tarp, (NOW.timestamp(), NOW.timestamp()))
    ops = FakeOps(NOW + dt.timedelta(days=1))
    assert B.cmd_check(house, ops) == 0 and ops.notices == []
    ops = FakeOps(NOW + dt.timedelta(days=10))
    assert B.cmd_check(house, ops) == 1
    assert ops.notices == [("Kronk backup: needs a look", "the recordings archive last finished 10 days ago (limit 9).")]
    ops = FakeOps(NOW + dt.timedelta(days=11))          # same warnings the next day: no repeat
    B.cmd_check(house, ops)
    assert ops.notices == []
    ops = FakeOps(NOW + dt.timedelta(days=36))          # changed (config now stale too): a notice
    B.cmd_check(house, ops)
    assert ops.notices and "the newest config backup, kronk-config-2026-12-10.tar.gz, is 36 days old (limit 35)" in ops.notices[0][1]
    ops = FakeOps(NOW + dt.timedelta(days=43))          # a week on, unchanged: repeated
    B.cmd_check(house, ops)
    assert len(ops.notices) == 1
    st = json.loads((house.state / "status.json").read_text())
    assert len(st["check"]["warnings"]) == 2 and st["limits"] == {"config_days": 35, "archive_days": 9}


def test_stale_check_never_ran_and_not_mounted(house):
    ops = FakeOps()
    assert B.cmd_check(house, ops) == 1
    body = ops.notices[0][1]
    assert f"no config backup on {house.dest}/config" in body and "never finished a run" in body
    house.require_mount = True
    ops = FakeOps()
    B.cmd_check(house, ops)
    assert "is not a mounted filesystem" in ops.notices[0][1]
    assert list(house.dest.iterdir()) == []


# ═══ Units and wrappers (criterion 12) ═════════════════════════════════════

UNITS = ROOT / "systemd"


@pytest.mark.parametrize("name,calendar", [("kronk-backup.timer", "*-*-10 01:30"),
                                           ("kronk-recordings-archive.timer", "Mon *-*-* 01:00"),
                                           ("kronk-backup-check.timer", "*-*-* 09:30")])
def test_timers(name, calendar):
    text = (UNITS / name).read_text()
    assert f"OnCalendar={calendar}\n" in text and "Persistent=true" in text and "WantedBy=timers.target" in text


def test_the_backup_misses_the_upgrade_window():
    """Not 02:00-05:00 on days 1-7 (the upgrade run), not the 8th's 04:30 report."""
    backup = (UNITS / "kronk-backup.timer").read_text()
    m = re.search(r"OnCalendar=\*-\*-(\d+) (\d\d):(\d\d)", backup)
    day, hour = int(m.group(1)), int(m.group(2))
    assert day > 8 and (hour < 2 or hour >= 5)
    archive = re.search(r"OnCalendar=Mon \*-\*-\* (\d\d):(\d\d)", (UNITS / "kronk-recordings-archive.timer").read_text())
    assert int(archive.group(1)) < 2       # Mondays include days 1-7: before the window


@pytest.mark.parametrize("unit,script", [("kronk-backup.service", "backup.sh"),
                                         ("kronk-recordings-archive.service", "archive_recordings.sh"),
                                         ("kronk-backup-check.service", "backup_check.sh")])
def test_services_run_the_repo_scripts_and_wrappers_call_the_one_script(unit, script):
    text = (UNITS / unit).read_text()
    assert f"ExecStart=%h/git-repos/drawsmcgraw/kronk/scripts/{script}\n" in text and "Type=oneshot" in text
    w = ROOT / "scripts" / script
    assert os.access(w, os.X_OK)
    assert re.search(r'exec /usr/bin/python3 "\$\(dirname "\$0"\)/backups.py" (config|archive|check) "\$@"', w.read_text())


def test_the_operations_block_has_install_run_and_remove():
    text = (UNITS / "kronk-backup.service").read_text()
    for word in ("# Operations", "install:", "enable --now", "dry run:", "run now:", "pause:", "log:", "remove:"):
        assert word in text


# ═══ GET /api/backups (criterion 13) ═══════════════════════════════════════

sys.path.insert(0, str(ROOT / "orchestrator"))
import backups_api  # noqa: E402


def api(tmp_path, raw=None, text=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    p = tmp_path / "status.json"
    if raw is not None:
        p.write_text(json.dumps(raw))
    if text is not None:
        p.write_text(text)
    backups_api.STATUS_FILE = str(p)
    app = FastAPI()
    app.include_router(backups_api.router)
    return TestClient(app).get("/api/backups").json()


def test_api_without_a_status_file(tmp_path):
    r = api(tmp_path)
    assert r["present"] is False and r["warnings"] == []


def test_api_unreadable_is_a_warning(tmp_path):
    assert "backup status file unreadable" in api(tmp_path, text="{nope")["warnings"][0]
    assert "not a JSON object" in api(tmp_path, text="[1]")["warnings"][0]


def test_api_warns_on_a_failed_run_a_stale_success_and_the_checks_findings(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    raw = {"limits": {"config_days": 35, "archive_days": 9},
           "config": {"last": {"at": (now - dt.timedelta(days=40)).isoformat(), "result": "ok", "size": 6_000_000},
                      "last_ok_at": (now - dt.timedelta(days=40)).isoformat()},
           "archive": {"last": {"at": now.isoformat(), "result": "failed", "detail": "destination full"},
                       "last_ok_at": now.isoformat()},
           "check": {"at": now.isoformat(), "warnings": ["destination /mnt/nas-backup is not a mounted filesystem"]}}
    w = api(tmp_path, raw)["warnings"]
    assert any("the last good config backup was 40 days ago (limit 35)" in x for x in w)
    assert any("the last recordings archive failed" in x and "destination full" in x for x in w)
    assert "destination /mnt/nas-backup is not a mounted filesystem" in w


def test_api_quiet_when_all_is_well(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    raw = {"config": {"last": {"at": now, "result": "ok", "unreadable": []}, "last_ok_at": now},
           "archive": {"last": {"at": now, "result": "ok", "moved": 3, "remaining": 4}, "last_ok_at": now},
           "check": {"at": now, "warnings": []}}
    r = api(tmp_path, raw)
    assert r["warnings"] == [] and r["jobs"]["archive"]["last"]["moved"] == 3


def test_api_only_reads():
    assert [(r.path, sorted(r.methods)) for r in backups_api.router.routes] == [("/api/backups", ["GET"])]
    src = (ROOT / "orchestrator" / "backups_api.py").read_text()
    assert "write_" not in src and "open(" not in src
    assert "backups_api.router" in (ROOT / "orchestrator" / "main.py").read_text()
    assert "/api/backups" in (ROOT / "orchestrator" / "static" / "services.html").read_text()
