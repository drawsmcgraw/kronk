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
    monkeypatch.setenv("KRONK_ARCHIVE_DELETE", "direct")           # the real delete script, the host's sh


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
        self.no_notify = False
        self.ha = tar_of({"./.storage/": None, "./.storage/core.entity_registry": b'{"data":{"entities":[]}}',
                          "./.storage/assist_pipeline.pipelines": b'{"data":{"items":[]}}',
                          "./configuration.yaml": b"homeassistant:\n"})
        self.ha_rc = 0
        self.ha_names = [".storage", "configuration.yaml", "home-assistant_v2.db", "tts"]
        self.keep_local: set[str] = set()     # recordings whose local delete "fails" (the container never ran)
        self.removed: list[list[str]] = []
        self.commands: list[list[str]] = []
        self.bundle = b"# v2 git bundle\n0123abc HEAD\n\nPACK"
        self.unpushed = 3

    def run(self, argv, timeout=300, stdin=None):
        self.commands.append(argv)
        if argv[0] == "git":
            if "rev-list" in argv:
                assert argv[-1] in ("origin/0123abc..HEAD", "--remotes")   # no upstream in the fake: origin/<branch>
                return B.Result(0, f"{self.unpushed}\n".encode(), "")
            if "bundle" in argv:
                assert argv[-2:] == ["--not", "--remotes"]
                Path(argv[argv.index("--quiet") + 1]).write_bytes(self.bundle)
                return B.Result(0, b"", "")
            if "--symbolic-full-name" in argv:
                return B.Result(128, b"", "fatal: no upstream configured")
            return B.Result(0, b"0123abc\n" if "rev-parse" in argv else b"", "")
        if argv[:2] in (["docker", "ps"], ["docker", "inspect"]):
            return B.Result(0, b"kronk-orchestrator-1\t/home/x/kronk-releases/sprint-M16\n", "")
        if argv[:2] == ["sh", "-c"] and argv[2] == B.DELETE_SCRIPT:
            return super().run(argv, timeout, stdin)        # the real delete script on the test's own tree
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

    def container_ls(self, container, path):
        if self.ha_rc:
            return B.Result(self.ha_rc, b"", "No such container: homeassistant")
        return B.Result(0, "\n".join(self.ha_names).encode() + b"\n", "")

    def container_tar(self, container, cwd, names):
        self.ha_tar_names = names
        return B.Result(self.ha_rc, self.ha if self.ha_rc == 0 else b"", "" if self.ha_rc == 0 else "No such container: homeassistant")

    def container_cat(self, container, path):
        return B.Result(0, b'{"providers": {}}', "")

    def helper_tar(self, hostdir):
        files = {f"./{p.relative_to(hostdir)}": p.read_bytes() for p in hostdir.rglob("*") if p.is_file()}
        return B.Result(0, tar_of(files), "")

    def remove_recordings(self, src, dst, items):
        self.removed.append([v.rec.path for v in items])
        if any(v.rec.path in self.keep_local for v in items):
            return {}, "No such image: alpine:3.20"        # the helper container did not run
        return super().remove_recordings(src, dst, items)


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A repo, a home, /etc, a destination and a state directory, all in tmp_path."""
    home = tmp_path / "home"
    repo = home / "git-repos" / "kronk"
    for d in ("data", "searxng", "secrets/mm", "ops", "scripts/lib", "docs/runbooks", "docs/features"):
        (repo / d).mkdir(parents=True)
    (repo / "docs" / "runbooks" / "restore.md").write_text("# Runbook\n")
    (repo / "docs" / "features" / "backups.md").write_text("# Backups\n")
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
                 "ma/settings.json", "repo-state.txt", "images.txt", "models.tsv", "host-facts.txt", "MANIFEST",
                 "repo-unpushed.bundle"):
        assert top + want in names, want
    assert names[0] == "kronk-config-2026-12-10" and names[-1] == top + "MANIFEST"
    # The live .env is byte-identical inside; the SQLite copies are real databases.
    assert read_member(p, "repo/.env") == (house.repo / ".env").read_bytes()
    assert read_member(p, "repo/data/solar.db").startswith(b"SQLite format 3")
    with tarfile.open(p, "r:gz") as t:
        assert t.getmember("kronk-config-2026-12-10").isdir() and t.getmember("kronk-config-2026-12-10").mode == 0o700
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
    assert "unpushed bundle: repo-unpushed.bundle: 3 commit(s) no remote-tracking branch has" in state
    assert ("production tag sprint-M16 on the remote: no: its commit 0123abc is on no remote-tracking branch"
            in state)
    assert read_member(p, "repo-unpushed.bundle") == ops.bundle
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
    """An optional item unreadable: still OK, and said."""
    ops = FakeOps()
    (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o000)
    try:
        assert B.cmd_config(house, ops, dry_run=False) == 0
    finally:
        (house.repo / "secrets" / "mm" / "kronk-mm-update").chmod(0o600)
    p = next((house.dest / "config").iterdir())
    man = read_member(p, "MANIFEST").decode()
    if os.geteuid() != 0:
        assert "UNREADABLE\t" + str(house.repo / "secrets" / "mm" / "kronk-mm-update") in man
        title, body = ops.notices[-1]
        assert title == "Kronk backup: config backup OK" and "NOT READ (1)" in body and "kronk-mm-update" in body
        # Finding 5: the unpushed sentence survives a gap (gaps += , not gaps =).
        assert "Repo: 3 commits not pushed (the tar holds them as repo-unpushed.bundle)." in body
        st = json.loads((house.state / "status.json").read_text())
        assert st["config"]["last"]["unreadable"] and st["config"]["last"]["result"] == "ok"


@pytest.mark.parametrize("break_it", ["ha", "ma", "env", "live-prod", "live-dev", "units", "env-missing"])
def test_a_missing_required_item_makes_the_tar_incomplete(house, monkeypatch, break_it):
    """Finding 5: HA, MA, .env, the live config directories, the units."""
    ops = FakeOps()
    if break_it == "ha":
        ops.ha_rc = 1
    elif break_it == "ma":
        monkeypatch.setattr(ops, "container_cat", lambda c, p: B.Result(1, b"", "No such container"))
    elif break_it == "env":
        (house.repo / ".env").chmod(0o000)
    elif break_it == "env-missing":
        (house.repo / ".env").unlink()
    elif break_it == "live-prod":
        (house.live_config / "prod" / "tuning.yaml").chmod(0o000)
    elif break_it == "live-dev":
        import shutil
        shutil.rmtree(house.live_config / "dev")
    elif break_it == "units":
        (B.HOME / ".config" / "systemd" / "user" / "llama-x.service").chmod(0o000)
    if os.geteuid() == 0 and break_it in ("env", "live-prod", "units"):
        pytest.skip("root reads a 0000 file")
    cfg = house.dest / "config"
    cfg.mkdir()
    complete = [f"kronk-config-2025-{m:02d}-10.tar.gz" for m in range(1, 13)] + ["kronk-config-2026-01-10.tar.gz"]
    for n in complete:
        (cfg / n).write_bytes(b"a complete tar")
    try:
        assert B.cmd_config(house, ops, dry_run=False) == 4
    finally:
        for f in (house.repo / ".env", house.live_config / "prod" / "tuning.yaml",
                  B.HOME / ".config" / "systemd" / "user" / "llama-x.service"):
            if f.exists():
                f.chmod(0o600)
    names = sorted(f.name for f in cfg.iterdir())
    assert names == sorted(complete + ["kronk-config-2026-12-10.incomplete.tar.gz"])   # retention never ran
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE"
    assert "MISSING REQUIRED (1)" in body and "does not count as a backup" in body
    assert "Repo: 3 commits not pushed" in body
    st = json.loads((house.state / "status.json").read_text())
    assert st["config"]["last"]["result"] == "incomplete" and "last_ok_at" not in st["config"]
    p = cfg / "kronk-config-2026-12-10.incomplete.tar.gz"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    man = read_member(p, "MANIFEST").decode()
    assert "\nREQUIRED\t" in man
    if break_it == "env-missing":      # mutant n06: a missing required file is a gap, not "absent"
        assert "ABSENT\t" + str(house.repo / ".env") not in man and "missing: no such file" in man
    # The stale check does not count it as a backup.
    assert B.newest_config(house)[0] == "kronk-config-2026-01-10.tar.gz"
    assert any("the last config backup incomplete" in w for w in B.stale_warnings(house, FakeOps(), st))


def test_an_incomplete_tar_never_counts_toward_retention_and_a_later_good_run_keeps_twelve(house):
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 13):
        (cfg / f"kronk-config-2025-{m:02d}-10.tar.gz").write_bytes(b"complete")
    for m in range(1, 4):
        (cfg / f"kronk-config-2026-0{m}-11.incomplete.tar.gz").write_bytes(b"incomplete")
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    names = sorted(f.name for f in cfg.iterdir())
    assert "kronk-config-2025-01-10.tar.gz" not in names           # 12 complete kept: 11 old + today
    assert sum(1 for n in names if B.CONFIG_RE.fullmatch(n)) == 12
    assert sum(1 for n in names if B.INCOMPLETE_RE.fullmatch(n)) == 3


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
                    r"\(the tar holds them as repo-unpushed\.bundle\)\.", body), body
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
    blob, sha = B.build_tar("top", [B.Member("a", "s", "file", b"x" * 100_000)])
    p.write_bytes(blob)
    assert B.verify_tar(p, sha, 2) is None          # the top directory and the member
    assert "members" in B.verify_tar(p, sha, 3)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    assert "read back differs" in B.verify_tar(p, sha, 2)
    assert "cannot read it to the end" in B.verify_tar(p, B.sha256_file(p, False), 2)


def test_the_read_back_drops_the_page_cache_first(tmp_path, monkeypatch):
    """Finding 12: posix_fadvise DONTNEED before the first read of the read-back."""
    calls = []
    real_read = os.read
    monkeypatch.setattr(B.os, "posix_fadvise", lambda fd, a, b, adv: calls.append(("fadvise", adv)))
    monkeypatch.setattr(B.os, "read", lambda fd, n: (calls.append(("read",)), real_read(fd, n))[1])
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    B.sha256_file(p, drop_cache=True)
    assert calls[0] == ("fadvise", os.POSIX_FADV_DONTNEED) and ("read",) in calls


def test_an_interrupted_run_leaves_only_a_temporary_name_which_the_next_run_removes(house, monkeypatch):
    ops = FakeOps()

    def killed(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(B, "verify_tar", killed)
    with pytest.raises(KeyboardInterrupt):
        B._config(house, ops, {"at": "x"}, "2026-12-10", True)
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
    assert "nothing was written to the destination" in body


def test_the_size_guard_checks_before_anything_reaches_the_destination(house, monkeypatch):
    """Note 19: an oversize tar is never opened on the destination."""
    (house.repo / "data" / "music_taste.yaml").write_bytes(os.urandom(300_000))
    house.max_bytes = 100_000
    opened = []
    real = B.os.open
    monkeypatch.setattr(B.os, "open", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert not any(".tar.gz" in p for p in opened)


def test_the_size_guard_default_and_unit(monkeypatch):
    """Mutants g02, g03: 50 MB by default, KRONK_BACKUP_MAX_MB in decimal megabytes."""
    monkeypatch.delenv("KRONK_BACKUP_MAX_MB", raising=False)
    a = type("A", (), {"dest": "/tmp/x", "require_mount": False})()
    assert B.Settings.load(a).max_bytes == 50_000_000
    monkeypatch.setenv("KRONK_BACKUP_MAX_MB", "2")
    assert B.Settings.load(a).max_bytes == 2_000_000


def test_an_exception_mid_write_leaves_no_temporary_file(house, monkeypatch):
    """Mutant t15."""
    def broken(fd, b):
        os.write(fd, b[:10])
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(B, "_write_all", broken)
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 1
    assert list((house.dest / "config").iterdir()) == []


def test_keep_zero_removes_nothing_and_retention_touches_only_config(house):
    """Mutants r06 (retention's directory) and r08 (KEEP=0)."""
    cfg = house.dest / "config"
    cfg.mkdir()
    for m in range(1, 13):
        (cfg / f"kronk-config-2025-{m:02d}-10.tar.gz").write_bytes(b"old")
    for d in (house.dest, house.dest / "recordings"):
        d.mkdir(exist_ok=True)
        (d / "kronk-config-2000-01-01.tar.gz").write_bytes(b"not in config/")
    assert B.retention(cfg, 0, lambda m: None) == []
    assert len(list(cfg.iterdir())) == 12
    assert B.retention(cfg, 10, lambda m: None) == ["kronk-config-2025-01-10.tar.gz", "kronk-config-2025-02-10.tar.gz"]
    for d in (house.dest, house.dest / "recordings"):
        assert (d / "kronk-config-2000-01-01.tar.gz").read_bytes() == b"not in config/"


def test_a_same_day_second_tar_unpacks_into_its_own_directory(house):
    """Note 23."""
    B.cmd_config(house, FakeOps(), dry_run=False)
    B.cmd_config(house, FakeOps(), dry_run=False)
    names = tar_names(house.dest / "config" / "kronk-config-2026-12-10.2.tar.gz")
    assert names[0] == "kronk-config-2026-12-10.2" and all(n.startswith("kronk-config-2026-12-10.2") for n in names)


def test_the_tar_carries_the_runbook_and_the_feature_doc(house):
    """Finding 9: the way back travels with the tar."""
    B.cmd_config(house, FakeOps(), dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert read_member(p, "/RESTORE.md") == b"# Runbook\n" and read_member(p, "/BACKUPS.md") == b"# Backups\n"


def test_no_bundle_when_nothing_is_unpushed_and_none_over_the_cap(house):
    ops = FakeOps()
    ops.unpushed = 0
    B.cmd_config(house, ops, dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert not any(n.endswith("repo-unpushed.bundle") for n in tar_names(p))
    assert "unpushed bundle: none needed" in read_member(p, "repo-state.txt").decode()
    assert not any("bundle" in argv for argv in ops.commands)
    ops = FakeOps()
    ops.bundle = b"x" * 2_000_000
    house.bundle_max_bytes = 1_000_000
    B.cmd_config(house, ops, dry_run=False)
    p = house.dest / "config" / "kronk-config-2026-12-10.2.tar.gz"
    assert not any(n.endswith("repo-unpushed.bundle") for n in tar_names(p))
    state = read_member(p, "repo-state.txt").decode()
    assert "unpushed bundle: OMITTED: 3 commit(s) unpushed, but the bundle is 2.0 MB, over the 1.0 MB cap" in state
    assert "they exist only on this machine: OMITTED" in ops.notices[-1][1]


def test_the_tag_on_the_remote_from_local_refs_only(house):
    ops = FakeOps()
    ops.run_orig = ops.run

    def run(argv, timeout=300, stdin=None):
        if argv[0] == "git" and "--contains" in argv and argv[-1] == "0123abc":
            return B.Result(0, b"  origin/refactor\n", "")
        if argv[0] == "git":
            assert not {"fetch", "ls-remote", "pull", "push"} & set(argv)
        return ops.run_orig(argv, timeout, stdin)
    ops.run = run
    assert B.tag_on_remote(house, ops, "sprint-M16").startswith(
        "unknown: the tag itself cannot be told without contacting the remote; its commit 0123abc is on origin/refactor")


def test_unknown_ha_files_are_reported_and_known_ones_copied_by_name(house):
    """Note 23: a new top-level name in HA's /config is not a silent gap."""
    ops = FakeOps()
    ops.ha_names = [".storage", "configuration.yaml", "custom_components", "home-assistant_v2.db", "tts",
                    "home-assistant.log.1", "automations.yaml.bak-20260904_162955", "zigbee.db"]
    B.cmd_config(house, ops, dry_run=False)
    assert ops.ha_tar_names == [".storage", "configuration.yaml", "custom_components"]
    p = house.dest / "config" / "kronk-config-2026-12-10.tar.gz"
    assert "UNKNOWN\thomeassistant:/config/zigbee.db" in read_member(p, "MANIFEST").decode()
    assert "HA files not known to the backup, not copied: zigbee.db." in ops.notices[-1][1]


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


@pytest.mark.parametrize("dest", ["/mnt/nas-backup", "/mnt/nas-backup/", "//mnt/nas-backup", "/mnt//nas-backup",
                                  "/mnt/./nas-backup", "/mnt/x/../nas-backup", "/mnt/nas-backup/kronk",
                                  "//mnt/nas-backup/kronk/./sub"])
def test_any_path_at_or_under_the_share_needs_the_mount(monkeypatch, dest):
    """Note 14."""
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    a = type("A", (), {"dest": dest, "require_mount": False})()
    s = B.Settings.load(a)
    assert s.require_mount is True and s.mount_root == Path("/mnt/nas-backup")


def test_a_symlink_to_the_share_needs_the_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    (tmp_path / "usb").symlink_to("/mnt/nas-backup/kronk")
    a = type("A", (), {"dest": str(tmp_path / "usb"), "require_mount": False})()
    assert B.Settings.load(a).mount_root == Path("/mnt/nas-backup")
    for other in ("/mnt/nas-backupx", "/mnt/nas-music", str(tmp_path)):
        a.dest = other
        assert B.Settings.load(a).require_mount is False


def test_the_require_mount_flag_on_the_command_line(house, monkeypatch, tmp_path):
    """Mutant m06: --require-mount, not only the environment form."""
    monkeypatch.delenv("KRONK_BACKUP_REQUIRE_MOUNT", raising=False)
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_MOUNT_WAIT_S", "0")
    ops = FakeOps()
    assert B.main(["check", "--dest", str(house.dest), "--require-mount", "--no-notify"], ops=ops) == 1
    st = json.loads((house.state / "status.json").read_text())
    assert any("is not a mounted filesystem" in w for w in st["check"]["warnings"])
    assert list(house.dest.iterdir()) == []


def test_a_destination_under_the_share_must_be_on_the_mounted_share(house):
    sub = house.dest / "kronk"
    sub.mkdir()
    house.dest, house.require_mount, house.mount_root = sub, True, sub.parent
    ops = FakeOps()
    assert "is not a mounted filesystem" in B.dest_problem(house, ops)
    ops.mounted.add(str(sub.parent))
    assert B.dest_problem(house, ops) is None


def test_the_job_waits_a_bounded_time_for_the_mount(house):
    """Note 16: a catch-up run at boot waits for the nofail mount."""
    house.require_mount, house.mount_wait_s = True, 180
    ops = FakeOps()

    def sleep(sec):
        ops.t += dt.timedelta(seconds=sec)
        if ops.t - NOW >= dt.timedelta(seconds=60):
            ops.mounted.add(str(house.dest))            # the share comes up after a minute
    ops.sleep = sleep
    assert B.dest_problem(house, ops, wait=True) is None and ops.t - NOW == dt.timedelta(seconds=60)
    ops = FakeOps()
    assert "(waited 180 s)" in B.dest_problem(house, ops, wait=True) and ops.t - NOW == dt.timedelta(seconds=180)
    ops = FakeOps()
    assert "waited" not in B.dest_problem(house, ops) and ops.t == NOW        # no wait unless asked


@pytest.mark.parametrize("cmd", ["config", "archive", "check"])
def test_a_bad_numeric_setting_is_a_clear_failure_with_a_notice(house, monkeypatch, cmd):
    """Note 20: a notice and a status line, no traceback, nothing done."""
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_KEEP", "abc")
    monkeypatch.setenv("KRONK_ARCHIVE_AGE_DAYS", "-1")
    ops = FakeOps()
    assert B.main([cmd, "--dest", str(house.dest)], ops=ops) == 2
    title, body = ops.notices[-1]
    assert "FAILED" in title or title == "Kronk backup: needs a look"
    assert "KRONK_BACKUP_KEEP must be a whole number, not 'abc'" in body
    assert "KRONK_ARCHIVE_AGE_DAYS must be at least 1, not -1" in body
    st = json.loads((house.state / "status.json").read_text())
    entry = st["check"] if cmd == "check" else st[cmd]["last"]
    assert "bad setting" in json.dumps(entry)
    assert list(house.dest.iterdir()) == []


@pytest.mark.parametrize("cmd", ["config", "archive", "check"])
def test_no_notify_sends_nothing_even_without_a_stub(house, monkeypatch, cmd):
    """Finding 4: --no-notify on all three; notify.sh is never run."""
    monkeypatch.delenv("KRONK_BACKUP_NOTIFY_CMD", raising=False)
    monkeypatch.setenv("KRONK_BACKUP_STATE", str(house.state))
    monkeypatch.setenv("KRONK_BACKUP_DIR", str(house.dest))
    ran = []

    class Real(FakeOps):           # the real notify, the fake everything else
        notify = B.Ops.notify

        def run(self, argv, timeout=300, stdin=None):
            ran.append(argv)
            if "notify.sh" in " ".join(argv):
                raise AssertionError("notify.sh ran")
            return super().run(argv, timeout, stdin)
    monkeypatch.setattr(B.Settings, "load", classmethod(lambda cls, a: house))     # never the real repo's files
    ops = Real()
    ops.no_notify = False
    B.main([cmd, "--no-notify"], ops=ops)
    assert ops.no_notify is True
    assert any("notice not sent (--no-notify)" in m for m in ops.logs)
    assert not any("notify.sh" in " ".join(a) for a in ran)


def test_upgrade_lock_probe_never_creates_it_and_never_keeps_it(house):
    """Mutants l03 (creates the lock), l04 (keeps the shared lock)."""
    house.upgrade_state.mkdir(parents=True)
    assert B.upgrade_lock_held(house.upgrade_state) is False
    assert not (house.upgrade_state / "lock").exists()
    (house.upgrade_state / "lock").write_text("")
    assert B.upgrade_lock_held(house.upgrade_state) is False
    fd = os.open(house.upgrade_state / "lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)       # nobody holds it after the probe
    finally:
        os.close(fd)


def test_the_etc_kronk_helper_mount_is_read_only(tmp_path):
    """Mutant d10."""
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"", "")
    Capture().helper_tar(tmp_path)
    assert seen[0][seen[0].index("-v") + 1] == f"{tmp_path}:/src:ro"


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
    for d in (target.parent, target.parent.parent, target.parent.parent.parent, house.dest / "recordings" / "dev1",
              house.dest / "recordings"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d       # mutant t11: every level the archive makes
    assert stat.S_IMODE(target.stat().st_mode) == 0o600          # mutant t10
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
    assert a.exists() and not b.exists()                  # note 18: the smaller file after it still went
    assert not any(B.is_archive_tmp(f.name) for f in house.dest.rglob("*"))
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == [b.name]
    assert "destination full: No space left on device" in ops.notices[-1][1]


def test_a_file_that_does_not_fit_is_skipped_and_smaller_later_files_still_go(house, monkeypatch):
    """Note 18: one oversized file early in the order no longer blocks the rest."""
    big = rec(house, "dev1", "kronk", "1", age_days=10, data=b"B" * 50_000)
    big2 = rec(house, "dev1", "kronk", "2", age_days=10, data=b"C" * 60_000)
    small = rec(house, "dev1", "kronk", "3", age_days=10, data=b"s" * 100)
    real = B._write_all

    def tiny_disk(fd, data):
        if len(data) > 10_000:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd, data)
    monkeypatch.setattr(B, "_write_all", tiny_disk)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert big.exists() and big2.exists() and not small.exists()
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == [small.name]
    body = ops.notices[-1][1]
    assert "Moved 1 recording(s)" in body and "skipped, does not fit" in str(json.loads(
        (house.state / "status.json").read_text())["archive"]["last"]["problems"])


def test_fault_the_copy_reads_back_different(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    real = B.sha256_fd
    monkeypatch.setattr(B, "sha256_fd", lambda fd, drop_cache: "0" * 64 if drop_cache else real(fd, drop_cache))
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


def test_fault_killed_mid_copy_the_leftover_is_kept_and_reported_never_removed(house):
    """Finding 1: the job removes only the temporary file it made in this run."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    leftover = t.parent / ".kronk-archive.4242.0123456789abcdef.tmp"
    leftover.write_bytes(b"half a copy")
    old_name = t.parent / ".kronk-archive.tmp"
    old_name.write_bytes(b"a finished recording that once got this name")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert sorted(f.name for f in t.parent.iterdir()) == sorted([t.name, leftover.name, old_name.name])
    assert leftover.read_bytes() == b"half a copy" and old_name.read_bytes().startswith(b"a finished")
    assert not p.exists() and t.read_bytes()
    assert "2 leftover temporary file(s) from interrupted archive runs" in ops.notices[-1][1]


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


def vrec(rel, size=10, mtime_ns=5_000_000_000, dev=1, ino=2, month="2026-11", sha="0" * 64, ddev=3, dino=4):
    return B.Verified(B.Rec(tuple(rel), size, mtime_ns, dev, ino), month, sha, ddev, dino)


def test_the_delete_runs_by_literal_path_in_a_container_that_sees_only_the_two_trees(tmp_path, monkeypatch):
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    monkeypatch.delenv("KRONK_BACKUP_HELPER_IMAGE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            return B.Result(0, b"ok 1\n", "")
    src, dst = tmp_path / "rec", tmp_path / "dest" / "recordings"
    res, err = Capture().remove_recordings(src, dst, [vrec(["d", "p", "r 1", "a\nb.wav"])])
    argv = seen[0]
    assert argv[:11] == ["docker", "run", "--rm", "--pull=never", "--network", "none", "-v", f"{src}:/rec",
                         "-v", f"{dst}:/dst:ro", "alpine:3.20"]
    assert argv[11:14] == ["sh", "-c", B.DELETE_SCRIPT] and argv[14:17] == ["sh", "/rec", "/dst"]
    assert argv[17:] == ["1", "d", "p", "r 1", "a\nb.wav", "2026-11", "10", "5", "1:2", "0" * 64, "3:4"]
    assert res == {0: "ok"} and err == ""
    assert Capture().remove_recordings(tmp_path / "a,b", dst, [vrec(["d", "p", "r", "x"])]) == ({}, \
        f"refused: a path docker -v cannot carry: '{tmp_path}/a,b:/rec'")
    assert len(seen) == 1


def test_the_delete_script_text_is_pinned():
    """Mutants d02, d03: rm and rmdir only, no -r, no -f, after every check."""
    assert B.DELETE_SCRIPT == DELETE_SCRIPT_PINNED
    lines = B.DELETE_SCRIPT.splitlines()
    assert [ln for ln in lines if re.search(r"\brm(dir)? --", ln)] == [
        '  if [ -z "$why" ] && rm -- "$f"; then echo "ok $i"; rmdir -- "$R/$dv/$pl/$rn" 2>/dev/null']


DELETE_SCRIPT_PINNED = r"""R=$1; D=$2; n=$3; shift 3; i=0
while [ "$i" -lt "$n" ]; do
  dv=$1; pl=$2; rn=$3; nm=$4; mo=$5; sz=$6; mt=$7; si=$8; hs=$9; shift 9; di=$1; shift
  i=$((i+1)); f="$R/$dv/$pl/$rn/$nm"; t="$D/$dv/$pl/$mo/$rn/$nm"; why=""
  for p in "$R/$dv" "$R/$dv/$pl" "$R/$dv/$pl/$rn" "$D/$dv" "$D/$dv/$pl" "$D/$dv/$pl/$mo" "$D/$dv/$pl/$mo/$rn"; do
    if [ -L "$p" ] || [ ! -d "$p" ]; then why="a directory on the way is a symlink or missing"; break; fi
  done
  if [ -z "$why" ]; then
    if [ -L "$f" ] || [ ! -f "$f" ]; then why="not a regular file"
    elif [ -L "$t" ] || [ ! -f "$t" ]; then why="the destination copy is gone"
    else
      h=$(sha256sum < "$f"); h=${h%% *}
      s=$(stat -c '%s %Y %d:%i' -- "$f")
      d=$(stat -c '%s %d:%i' -- "$t")
      if [ "$s" != "$sz $mt $si" ]; then why="size, mtime or inode differ from the copied file"
      elif [ "$h" != "$hs" ]; then why="sha256 differs from the copied file"
      elif [ "$d" != "$sz $di" ]; then why="the destination copy is not the verified one"
      fi
    fi
  fi
  if [ -z "$why" ] && rm -- "$f"; then echo "ok $i"; rmdir -- "$R/$dv/$pl/$rn" 2>/dev/null
  else echo "refused $i ${why:-rm failed}"; fi
done
exit 0
"""


def pair(tmp_path, data=b"audio"):
    """A local recording and its verified copy, for the delete script."""
    src, dst = tmp_path / "rec", tmp_path / "dst"
    f = src / "d" / "p" / "r" / "a.wav"
    t = dst / "d" / "p" / "2026-11" / "r" / "a.wav"
    for x in (f, t):
        x.parent.mkdir(parents=True)
        x.write_bytes(data)
    a, b = f.stat(), t.stat()
    v = B.Verified(B.Rec(("d", "p", "r", "a.wav"), a.st_size, a.st_mtime_ns, a.st_dev, a.st_ino), "2026-11",
                   __import__("hashlib").sha256(data).hexdigest(), b.st_dev, b.st_ino)
    return src, dst, f, t, v


def test_the_delete_script_removes_only_the_verified_file(tmp_path):
    src, dst, f, t, v = pair(tmp_path)
    res, err = B.Ops().remove_recordings(src, dst, [v])
    assert res == {0: "ok"} and not f.exists() and not f.parent.exists() and t.read_bytes() == b"audio"


@pytest.mark.parametrize("change,why", [
    ("content", "sha256 differs"), ("swap", "size, mtime or inode differ"), ("mtime", "size, mtime or inode differ"),
    ("dir", "not a regular file"), ("symlink", "not a regular file"), ("dest-gone", "the destination copy is gone"),
    ("dest-swapped", "the destination copy is not the verified one"), ("dest-symlink", "the destination copy is gone"),
    ("run-symlink", "a directory on the way is a symlink"), ("dest-run-symlink", "a directory on the way is a symlink")])
def test_the_delete_script_refuses_anything_but_the_verified_file(tmp_path, change, why):
    """Finding 2: what is about to be removed is compared with what was copied."""
    src, dst, f, t, v = pair(tmp_path)
    if change == "content":
        f.write_bytes(b"AUDIO")                               # same size, same inode, new bytes
        os.utime(f, ns=(v.rec.mtime_ns, v.rec.mtime_ns))
    elif change == "swap":
        n = f.with_name("new")
        n.write_bytes(b"audio")
        os.utime(n, ns=(v.rec.mtime_ns, v.rec.mtime_ns))
        os.replace(n, f)                                      # same bytes, same mtime, another inode
    elif change == "mtime":
        os.utime(f, (1, 1))
    elif change == "dir":
        f.unlink()
        f.mkdir()                                             # a directory named like the file
        (f / "inside").write_bytes(b"keep")
    elif change == "symlink":
        keep = tmp_path / "keep.wav"
        keep.write_bytes(b"audio")
        f.unlink()
        f.symlink_to(keep)
    elif change == "dest-gone":
        t.unlink()
    elif change == "dest-swapped":
        n = t.with_name("new")
        n.write_bytes(b"audio")
        os.replace(n, t)                                      # same bytes, another inode
    elif change == "dest-symlink":
        t.unlink()
        t.symlink_to(f)
    elif change == "run-symlink":
        moved = tmp_path / "elsewhere"
        f.parent.rename(moved)
        f.parent.symlink_to(moved)
    elif change == "dest-run-symlink":
        t.parent.rename(tmp_path / "d2")
        t.parent.symlink_to(f.parent)                         # finding 3's shape
    before = every_file(tmp_path)
    res, err = B.Ops().remove_recordings(src, dst, [v])
    assert res[0].startswith(why), res
    assert every_file(tmp_path) == before


def test_thirty_thousand_deletes_go_in_batches(tmp_path, monkeypatch):
    """Finding 11: no argument list too long, every name exactly once."""
    monkeypatch.delenv("KRONK_ARCHIVE_DELETE", raising=False)
    seen = []

    class Capture(B.Ops):
        def run(self, argv, timeout=300, stdin=None):
            seen.append(argv)
            n = int(argv[17])
            return B.Result(0, "".join(f"ok {i}\n" for i in range(1, n + 1)).encode(), "")
    items = [vrec(["e0949788663255952ce7778975527882", "kronk", f"{1203688800703734 + i}",
                   "01_stt-stt.faster_whisper.wav"], ino=i) for i in range(30_000)]
    res, err = Capture().remove_recordings(tmp_path / "rec", tmp_path / "dst", items)
    assert len(seen) == 30_000 // B.DELETE_BATCH and res == {i: "ok" for i in range(30_000)}
    assert max(sum(len(a) + 1 for a in argv) for argv in seen) < 128 * 1024
    names = [argv[17 + 1 + 10 * k + 2] for argv in seen for k in range(int(argv[17]))]
    assert len(set(names)) == 30_000


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


# ═══ Blockers of the end-to-end test (docs/sprint/M16-e2e.md) ═══════════

def test_finding1_a_source_named_like_the_temporary_file_is_reported_and_never_copied(house):
    tmpname = rec(house, "devA", "kronk", "run1", name=".kronk-archive.tmp", data=b"t" * 5000)
    other = rec(house, "devA", "kronk", "run1", name="01_b.wav", data=b"b" * 6000)
    lookalike = rec(house, "devA", "kronk", "run1", name=".kronk-archive.1.0123456789abcdef.tmp", data=b"l" * 10)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1           # reported: a person looks
    assert tmpname.read_bytes() == b"t" * 5000 and lookalike.read_bytes() == b"l" * 10
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == ["01_b.wav"]
    assert not other.exists()
    assert "named like the archive's temporary files; never archived" in ops.notices[-1][1]
    # A second run: still there, still not copied, the archived file untouched.
    before = every_file(house.dest)
    B.cmd_archive(house, FakeOps(), dry_run=False)
    assert tmpname.exists() and every_file(house.dest) == before


def test_finding1_the_copy_never_unlinks_a_destination_file_it_did_not_create(house, monkeypatch):
    """Even a file that takes this run's own temporary name first is left alone."""
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    names = iter(["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"])
    monkeypatch.setattr(B.secrets, "token_hex", lambda n: next(names))
    squatter = t.parent / f".kronk-archive.{os.getpid()}.aaaaaaaaaaaaaaaa.tmp"
    squatter.write_bytes(b"someone else's")
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1][:300]
    assert squatter.read_bytes() == b"someone else's" and t.exists() and not p.exists()
    assert not (t.parent / f".kronk-archive.{os.getpid()}.bbbbbbbbbbbbbbbb.tmp").exists()


def test_finding1_the_temporary_file_is_removed_only_while_it_is_still_ours(tmp_path):
    d = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (tmp_path / "t").write_bytes(b"ours")
        st = os.stat(tmp_path / "t")
        own = (st.st_dev, st.st_ino)
        n = tmp_path / "n"
        n.write_bytes(b"someone else's")
        os.replace(n, tmp_path / "t")                      # the name now holds another file
        B._unlink_own("t", d, own)
        assert (tmp_path / "t").read_bytes() == b"someone else's"
        st = os.stat(tmp_path / "t")
        B._unlink_own("t", d, (st.st_dev, st.st_ino))
        assert not (tmp_path / "t").exists()
    finally:
        os.close(d)


def test_a_same_size_rewrite_during_the_copy_is_left_for_the_next_run(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 3 * (1 << 20))
    real = B._write_all
    once = []

    def rewrite(fd, data):
        real(fd, data)
        if not once:
            once.append(1)
            with open(p, "r+b") as f:
                f.write(b"b" * 10)                      # same size, new bytes, new mtime
            os.utime(p, ns=(p.stat().st_mtime_ns + 1_000_000_000,) * 2)
    monkeypatch.setattr(B, "_write_all", rewrite)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert p.exists() and [f for f in house.dest.rglob("*") if f.is_file()] == [] and ops.removed == []
    assert "Left for the next run: 1" in ops.notices[-1][1]


def test_local_exists_counts_what_it_cannot_see_as_still_there(tmp_path):
    (tmp_path / "d" / "p").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "d" / "p" / "r").symlink_to(tmp_path / "elsewhere")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert B.local_exists(fd, B.Rec(("d", "p", "r", "f.wav"), 1, 1)) is True
        assert B.local_exists(fd, B.Rec(("d", "p", "gone", "f.wav"), 1, 1)) is False
    finally:
        os.close(fd)


def test_a_destination_under_the_share_on_another_filesystem_is_refused(house, monkeypatch):
    sub = house.dest / "kronk"
    sub.mkdir()
    house.dest, house.require_mount, house.mount_root = sub, True, sub.parent
    ops = FakeOps()
    ops.mounted.add(str(sub.parent))
    real = os.stat

    def other(st):
        t = list(st)
        t[2] += 1                                         # st_dev: another filesystem
        return os.stat_result(t)
    monkeypatch.setattr(B.os, "stat", lambda p, *a, **k: other(real(p)) if str(p) == str(sub) else real(p, *a, **k))
    assert "is not on the share mounted at" in B.dest_problem(house, ops)


def test_a_file_that_appears_under_the_final_name_during_the_copy_is_never_replaced(house, monkeypatch):
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    real = B._write_all

    def and_someone_writes(fd, data):
        real(fd, data)
        t.write_bytes(b"appeared")
    monkeypatch.setattr(B, "_write_all", and_someone_writes)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert t.read_bytes() == b"appeared" and p.read_bytes() == b"a" * 100
    assert not any(B.is_archive_tmp(f.name) for f in t.parent.iterdir())
    assert "appeared on the destination during the copy (both kept)" in ops.notices[-1][1]


def test_finding2_a_file_swapped_after_its_verify_is_kept_and_the_run_fails(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "run1", name="01_a.wav", data=b"a" * 4000)
    real = B.copy_verified

    def then_swap(src_fd, dst_fd, r, month):
        out = real(src_fd, dst_fd, r, month)
        if r.rel[3] == "01_a.wav":
            n = a.with_name("new")
            n.write_bytes(b"A" * 4000)                       # the replacement, by rename
            os.utime(n, ns=(r.mtime_ns, r.mtime_ns))
            os.replace(n, a)
        return out
    monkeypatch.setattr(B, "copy_verified", then_swap)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"A" * 4000                       # the replacement exists, locally
    assert archived(house, a).read_bytes() == b"a" * 4000      # the first content, on the destination
    assert "local delete refused (size, mtime or inode differ from the copied file)" in ops.notices[-1][1]
    assert "Moved 0 recording(s)" in ops.notices[-1][1]


@pytest.mark.parametrize("how", ["vanished", "replaced", "symlinked"])
def test_finding2_a_destination_copy_that_changes_after_its_verify_stops_the_delete(house, monkeypatch, how):
    a = rec(house, "dev1", "kronk", "run1", data=b"a" * 4000)
    real = B.copy_verified

    def then_break(src_fd, dst_fd, r, month):
        out = real(src_fd, dst_fd, r, month)
        t = house.dest / "recordings" / "dev1" / "kronk" / month / "run1" / r.rel[3]
        if how == "vanished":
            t.unlink()
        elif how == "replaced":
            n = t.with_name("x")
            n.write_bytes(b"a" * 4000)
            os.replace(n, t)
        else:
            t.unlink()
            t.symlink_to(a)
        return out
    monkeypatch.setattr(B, "copy_verified", then_break)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"a" * 4000 and ops.removed == []
    assert "not deleted locally: the destination copy" in ops.notices[-1][1]


def test_finding3_a_symlinked_destination_run_directory_never_verifies_a_file_against_itself(house):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    month = dt.datetime.fromtimestamp(a.stat().st_mtime).strftime("%Y-%m")
    d = house.dest / "recordings" / "devA" / "kronk" / month
    d.mkdir(parents=True)
    (d / "run1").symlink_to(a.parent)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"x" * 5000 and ops.removed == []
    assert "is a symlink or not a directory" in ops.notices[-1][1]


def test_finding3_a_symlinked_device_directory_gets_nothing_written_through_it(house, tmp_path):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    outside = tmp_path / "outside"
    outside.mkdir()
    (house.dest / "recordings").mkdir()
    (house.dest / "recordings" / "devA").symlink_to(outside)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 1
    assert a.exists() and list(outside.iterdir()) == []


def test_finding3_samefile_is_a_conflict_never_already_archived(house):
    a = rec(house, "devA", "kronk", "run1", data=b"x" * 5000)
    t = archived(house, a)
    t.parent.mkdir(parents=True)
    os.link(a, t)                                             # the same file in both trees
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert a.read_bytes() == b"x" * 5000 and ops.removed == []
    assert "is the source file itself" in ops.notices[-1][1]


def test_finding3_a_symlinked_recordings_root_is_refused(house, tmp_path):
    real = tmp_path / "real-recordings"
    (real / "dev1" / "kronk" / "1").mkdir(parents=True)
    f = real / "dev1" / "kronk" / "1" / "01.wav"
    f.write_bytes(b"r")
    os.utime(f, (1, 1))
    house.recordings.symlink_to(real)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert f.exists() and not (house.dest / "recordings").exists()
    assert "is a symlink; refused, not followed" in ops.notices[-1][1]


@pytest.mark.parametrize("where", ["dest-in-src", "src-in-dest"])
def test_overlapping_trees_are_refused(house, where):
    p = rec(house, "dev1", "kronk", "1", age_days=10)
    if where == "dest-in-src":
        house.dest = house.recordings / "dev1"
    else:
        house.recordings = house.dest / "recordings" / "inner"
        house.recordings.mkdir(parents=True)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert p.exists() and "overlap; refused" in ops.notices[-1][1]


def test_a_directory_swapped_for_a_symlink_mid_run_is_not_followed(house, tmp_path, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    b = rec(house, "dev1", "kronk", "2", data=b"b" * 100)
    outside = tmp_path / "outside" / "2"
    outside.mkdir(parents=True)
    (outside / b.name).write_bytes(b"not a recording")
    os.utime(outside / b.name, ns=(b.stat().st_mtime_ns,) * 2)
    real = B.scan_recordings
    swapped = []

    def scan_then_swap(src):
        out = real(src)
        if not swapped:
            swapped.append(1)
            b.parent.rename(tmp_path / "moved-away")
            b.parent.symlink_to(outside)                   # between the scan and the copy
        return out
    monkeypatch.setattr(B, "scan_recordings", scan_then_swap)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert not a.exists() and (outside / b.name).read_bytes() == b"not a recording"
    assert (tmp_path / "moved-away" / b.name).read_bytes() == b"b" * 100
    assert "source: " in ops.notices[-1][1] and "is a symlink or not a directory" in ops.notices[-1][1]


def test_a_fifo_swapped_in_mid_run_never_hangs_the_run(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    real = B.scan_recordings

    def scan_then_fifo(src):
        out = real(src)
        a.unlink()
        os.mkfifo(a)
        return out
    monkeypatch.setattr(B, "scan_recordings", scan_then_fifo)
    assert B.cmd_archive(house, FakeOps(), dry_run=False) == 0      # left for the next run
    assert stat.S_ISFIFO(os.lstat(a).st_mode)


def test_a_kill_between_verify_and_delete_is_finished_by_the_next_run(house, monkeypatch):
    a = rec(house, "dev1", "kronk", "1", data=b"a" * 100)

    def killed(*args, **kw):
        raise KeyboardInterrupt
    ops = FakeOps()
    monkeypatch.setattr(ops, "remove_recordings", killed)
    with pytest.raises(KeyboardInterrupt):
        B.cmd_archive(house, ops, dry_run=False)
    assert a.exists() and archived(house, a).read_bytes() == b"a" * 100
    monkeypatch.undo()
    monkeypatch.setenv("KRONK_ARCHIVE_DELETE", "direct")
    monkeypatch.setenv("KRONK_BACKUP_NOTIFY_CMD", "/bin/false")
    monkeypatch.setattr(B, "HOME", house.repo.parent.parent.parent)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert not a.exists() and "Written this run: 0 B" in ops.notices[-1][1]


def test_same_size_different_content_on_the_destination_is_a_conflict(house):
    """Mutant v05: the hash decides, not the size."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    t.parent.mkdir(parents=True)
    t.write_bytes(b"b" * 100)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.read_bytes() == b"a" * 100 and t.read_bytes() == b"b" * 100 and ops.removed == []


def test_a_non_file_of_the_same_name_on_the_destination_is_a_conflict(house):
    """Mutant v15."""
    p = rec(house, "dev1", "kronk", "1", data=b"a" * 100)
    t = archived(house, p)
    t.mkdir(parents=True)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert p.exists() and t.is_dir() and "something else of the same name" in ops.notices[-1][1]


def test_files_at_the_wrong_depth_are_never_taken_as_recordings(house):
    """Mutant s06."""
    rec(house, "dev1", "kronk", "1")
    shallow = house.recordings / "dev1" / "kronk" / "x.wav"
    deep = house.recordings / "dev1" / "kronk" / "1" / "sub" / "y.wav"
    deep.parent.mkdir()
    for f in (shallow, deep):
        f.write_bytes(b"z")
        os.utime(f, (1, 1))
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    assert shallow.exists() and deep.exists()
    assert [f.name for f in house.dest.rglob("*") if f.is_file()] == ["01_stt-stt.faster_whisper.wav"]


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


def test_api_without_a_status_file_warns(tmp_path):
    """Note 17: no status file at all is not a green card."""
    r = api(tmp_path)
    assert r["present"] is False and r["warnings"] == [
        "no backup job has written a status file yet (the timers are not installed, or no job has ever run)"]


def test_api_a_job_that_never_succeeded_is_a_warning_and_a_string_is_one_warning(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    raw = {"config": {"last": {"at": now, "result": "incomplete", "detail": "HA missing"}},
           "check": {"at": now, "warnings": "destination /mnt/nas-backup is not a mounted filesystem"}}
    w = api(tmp_path, raw)["warnings"]
    assert "the config backup has never finished a good run" in w
    assert "the recordings archive has never finished a good run" in w
    assert any(x.startswith("the last config backup incomplete") for x in w)
    assert "destination /mnt/nas-backup is not a mounted filesystem" in w and "d" not in w
    raw["check"]["warnings"] = 5
    assert any("warnings are unreadable (int)" in x for x in api(tmp_path, raw)["warnings"])


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
