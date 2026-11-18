"""Sprint M21 step 2, criteria 7 and 10: the editor's write path
(orchestrator/settings_store.py). Names, paths, symlinks, size, atomicity."""
import os
import shutil
import threading
from pathlib import Path

import pytest

import config_files
import config_loader
import prompt_defaults
import settings_store as store

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    shutil.copytree(REPO / "config", d)
    return d


def test_editable_is_exactly_the_files_the_loaders_read():
    expected = {f"prompts/{n}.md" for n in prompt_defaults.PROMPTS} | {
        config_loader.TUNING_FILE, config_loader.LEAD_IN_FILE, config_loader.VOICE_FILE,
        *config_loader.TOOL_SERVICE_FILES}
    assert set(store.EDITABLE) == expected
    shipped = {str(p.relative_to(REPO / "config")) for p in (REPO / "config").rglob("*") if p.is_file()}
    assert set(store.EDITABLE) == shipped
    assert {i.owner for n, i in store.EDITABLE.items() if n == "music.yaml"} == {"tool_service"}


@pytest.mark.parametrize("name", [
    "../tuning.yaml", "/etc/passwd", "prompts/../tuning.yaml", "tuning.yaml/", "./tuning.yaml",
    "tuning.yaml\x00", "prompts/home.md/", "prompts//home.md", "prompts/new.md", "new.yaml",
    "TUNING.yaml", "tuning.yml", ".git/config", "prompts", "", None, 3, ["tuning.yaml"],
    "prompts\\home.md", "tuning.yaml ", " tuning.yaml",
])
def test_unknown_names_are_refused(live, name):
    with pytest.raises(store.StoreError, match="not a config file"):
        store.write_file(live, name, b"x: 1\n")
    with pytest.raises(store.StoreError, match="not a config file"):
        store.read_bytes(live, name)


def test_write_replaces_the_file_atomically_with_mode_0644(live):
    old_inode = os.stat(live / "tuning.yaml").st_ino
    store.write_file(live, "tuning.yaml", b"agent_loop:\n  repeat_tool_nudge_at: 4\n")
    st = os.stat(live / "tuning.yaml")
    assert (live / "tuning.yaml").read_bytes() == b"agent_loop:\n  repeat_tool_nudge_at: 4\n"
    assert st.st_ino != old_inode                  # a new file was renamed into place
    assert oct(st.st_mode & 0o777) == "0o644"
    assert st.st_uid == os.getuid()
    assert not [p for p in live.iterdir() if ".tmp-" in p.name]


def test_write_ignores_the_umask(live):
    old = os.umask(0o077)
    try:
        store.write_file(live, "voice.yaml", b"stream:\n  hold_sentences: 1\n")
    finally:
        os.umask(old)
    assert oct(os.stat(live / "voice.yaml").st_mode & 0o777) == "0o644"


def test_symlinked_prompts_dir_is_refused(live, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(live / "prompts")
    (live / "prompts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(store.StoreError, match="symlink"):
        store.write_file(live, "prompts/home.md", b"<!-- x -->\nhi\n")
    assert list(outside.iterdir()) == []


def test_symlinked_target_file_is_refused_and_left(live, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("keep me")
    (live / "voice.yaml").unlink()
    (live / "voice.yaml").symlink_to(outside)
    with pytest.raises(store.StoreError, match="symlink"):
        store.write_file(live, "voice.yaml", b"deadline_s: 25\n")
    assert outside.read_text() == "keep me"
    assert (live / "voice.yaml").is_symlink()
    with pytest.raises(store.StoreError, match="symlink"):
        store.read_bytes(live, "voice.yaml")


def test_size_limit(live):
    limit = config_files.MAX_YAML_BYTES
    with pytest.raises(store.StoreError, match=f"too large: {limit + 1} bytes; the limit is {limit}"):
        store.encode("tuning.yaml", "#" * (limit + 1))
    with pytest.raises(store.StoreError, match="too large"):
        store.write_file(live, "tuning.yaml", b"#" * (limit + 1))
    assert len(store.encode("tuning.yaml", "#" * limit)) == limit
    assert store.max_bytes("prompts/home.md") == config_files.MAX_PROMPT_BYTES


def test_encode_normalises_line_ends_and_refuses_bad_unicode():
    assert store.encode("tuning.yaml", "a: 1\r\nb: 2\rc: 3\n") == b"a: 1\nb: 2\nc: 3\n"
    with pytest.raises(store.StoreError, match="not valid Unicode"):
        store.encode("tuning.yaml", "a: \ud800\n")
    with pytest.raises(store.StoreError, match="must be a string"):
        store.encode("tuning.yaml", b"a: 1")


def test_failed_rename_leaves_the_old_file_and_no_temp(live, monkeypatch):
    before = (live / "lead_in.yaml").read_bytes()

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(store.StoreError, match="could not put the new file in place"):
        store.write_file(live, "lead_in.yaml", b"verbs: [ask]\n")
    assert (live / "lead_in.yaml").read_bytes() == before
    assert not [p for p in live.iterdir() if ".tmp-" in p.name]


def test_failed_write_leaves_the_old_file_and_no_temp(live, monkeypatch):
    before = (live / "music.yaml").read_bytes()
    real_write = os.write

    def short(fd, data):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(store.os, "write", short)
    with pytest.raises(store.StoreError, match="write failed"):
        store.write_file(live, "music.yaml", b"verify:\n  timeout_s: 9\n")
    monkeypatch.setattr(store.os, "write", real_write)
    assert (live / "music.yaml").read_bytes() == before
    assert not [p for p in live.iterdir() if ".tmp-" in p.name]


def test_a_reader_never_sees_a_partial_file(live):
    """A reader loops over the file while it is rewritten 200 times between
    two large versions: every read is one version or the other, whole."""
    a = ("# a\n" + "agent_loop:\n  repeat_tool_nudge_at: 3\n" + "#" * 30000 + "\n").encode()
    b = ("# b\n" + "agent_loop:\n  repeat_tool_nudge_at: 4\n" + "~" * 40000 + "\n").encode()
    store.write_file(live, "tuning.yaml", a)
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            data = (live / "tuning.yaml").read_bytes()
            if data not in (a, b):
                bad.append(len(data))

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(200):
            store.write_file(live, "tuning.yaml", b if i % 2 == 0 else a)
    finally:
        stop.set()
        t.join()
    assert bad == []


def test_read_bytes(live):
    assert store.read_bytes(live, "tuning.yaml") == (live / "tuning.yaml").read_bytes()
    (live / "voice.yaml").unlink()
    assert store.read_bytes(live, "voice.yaml") is None


def test_written_file_loads(live, monkeypatch):
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(live))
    text = (live / "tuning.yaml").read_text().replace("research: 8", "research: 6")
    store.write_file(live, "tuning.yaml", store.encode("tuning.yaml", text))
    assert config_loader.load(live).tuning.agent_loop.max_rounds.research == 6
