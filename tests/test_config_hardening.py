"""config/ files that make a parser choke, not just fail validation
(sprint M21 step 1, fix round 1; docs/sprint/M21-step1-e2e.md findings
1-3 and observations 2-6).

The class: any way a file can make a loader raise, hang or exhaust memory
outside its own error path. Every test runs against both loaders (the
orchestrator's tuning.yaml/prompts and tool_service's music.yaml) where
the case applies to both. Hostile inputs stay small: the alias bomb is
326 bytes and is refused at its first anchor, the nesting file 40 kB and
refused at depth 11, and the over-cap files are sparse or just past the
cap. No test opens a socket.
"""
import filecmp
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agents
import config_loader as orch_loader
from tool_service import config_loader as ts_loader

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"

NESTED = "[" * 20000 + "]" * 20000
# The caps, written out so a test fails on what a file does, not on a
# missing attribute (test_the_caps_are_what_the_report_says checks them).
CAP = 64 * 1024
MAX_PROBLEMS = 50
ALIAS_BOMB_LINES = [
    "a: &a [x,x,x,x,x,x,x,x,x]",
    "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]",
    "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]",
    "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]",
    "e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]",
    "f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]",
    "g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]",
    "h: &h [*g,*g,*g,*g,*g,*g,*g,*g,*g]",
    "i: &i [*h,*h,*h,*h,*h,*h,*h,*h,*h]",
]


class Svc:
    """One service's loader, pointed at a scratch copy of config/."""

    def __init__(self, name, loader, yaml_file, section, field, good, app):
        self.name, self.loader, self.yaml_file = name, loader, yaml_file
        self.section, self.field, self.good = section, field, good
        self._app = app

    @property
    def yaml_path(self) -> Path:
        return self.dir / self.yaml_file

    def value(self):
        cfg = self.loader.current()
        root = cfg.tuning if self.name == "orchestrator" else cfg.music
        return getattr(getattr(root, self.section), self.field)

    def set_value(self, raw: str) -> str:
        """The shipped YAML with this service's numeric field set to raw."""
        text = (CONFIG / self.yaml_file).read_text()
        old = f"  {self.field}: {self.good}\n"
        assert old in text
        return text.replace(old, f"  {self.field}: {raw}\n")

    def client(self) -> TestClient:
        return TestClient(self._app())


def _orch_app():
    import main
    return main.app


def _ts_app():
    from tool_service import main as ts
    return ts.app


SERVICES = {
    "orchestrator": lambda: Svc("orchestrator", orch_loader, "tuning.yaml",
                                "weather_context", "max_age_s", 7200, _orch_app),
    "tool_service": lambda: Svc("tool_service", ts_loader, "music.yaml",
                                "verify", "timeout_s", 8, _ts_app),
}


@pytest.fixture(params=list(SERVICES))
def svc(request, tmp_path, monkeypatch):
    s = SERVICES[request.param]()
    s.dir = tmp_path / "config"
    shutil.copytree(CONFIG, s.dir)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(s.dir))
    s.loader.start()
    yield s
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    s.loader.start()


@pytest.fixture
def orch(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    orch_loader.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    orch_loader.start()


def _problems(loader, base):
    with pytest.raises(loader.ConfigInvalid) as e:
        loader.load(base)
    return [(p.file, p.line, p.message) for p in e.value.problems]


def _bounded(fn, seconds, unblock=None):
    """fn() in a thread; fail if it is still running after `seconds`
    (unblock() is then called so the thread can finish)."""
    out = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as e:          # noqa: BLE001 - reported below
            out["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        if unblock:
            unblock()
        t.join(5)
        pytest.fail(f"still running after {seconds}s (blocked)")
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _assert_rejected_everywhere(svc, rel, check):
    """A bad file at rel: load names it, startup runs on defaults with the
    error on the status, and a reload through the endpoint answers 422,
    keeps the last good values and shows the error."""
    errs = _problems(svc.loader, svc.dir)
    assert any(f == rel and check(line, msg) for f, line, msg in errs), errs

    svc.loader.start()
    st = svc.loader.status()
    assert st["ok"] is False and st["source"] == "defaults" and st["event"] == "startup"
    assert any(e["file"] == f"config/{rel}" for e in st["errors"])


def _assert_reload_rejects(svc, rel, write_bad, check):
    """Start on the good files, make the file bad, reload through HTTP."""
    svc.loader.start()
    assert svc.loader.status()["ok"] is True
    before = svc.value()
    write_bad()
    r = svc.client().post("/config/reload")
    assert r.status_code == 422, (r.status_code, r.text[:300])
    body = r.json()
    assert any(e["file"] == f"config/{rel}" and check(e["line"], e["message"])
               for e in body["errors"]), body
    assert "last good values" in body["kept"]
    st = svc.loader.status()
    assert st["ok"] is False and st["event"] == "reload" and st["source"] == "files"
    assert svc.value() == before


# ── finding 1: deep nesting ──────────────────────────────────────────────────

def test_deeply_nested_yaml_is_a_config_error(svc):
    bad = f"{svc.section}: {NESTED}\n"
    svc.yaml_path.write_text(bad)
    t0 = time.monotonic()
    _assert_rejected_everywhere(svc, svc.yaml_file,
                                lambda line, msg: line == 1 and "nested more than 10 levels" in msg)
    assert time.monotonic() - t0 < 2


def test_deeply_nested_yaml_on_reload_answers_422_not_500(svc):
    _assert_reload_rejects(svc, svc.yaml_file,
                           lambda: svc.yaml_path.write_text(f"{svc.section}: {NESTED}\n"),
                           lambda line, msg: "nested more than" in msg)


def test_nesting_just_under_the_limit_is_an_ordinary_schema_error(svc):
    svc.yaml_path.write_text(f"{svc.section}: " + "[" * 9 + "]" * 9 + "\n")
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert f == svc.yaml_file and "nested" not in msg


# ── finding 2: anchors, aliases, merge keys, tags ────────────────────────────

def test_alias_bomb_is_refused_at_its_first_anchor(svc):
    svc.yaml_path.write_text("\n".join(ALIAS_BOMB_LINES + [f"{svc.section}: *i"]) + "\n")
    t0 = time.monotonic()
    _assert_rejected_everywhere(svc, svc.yaml_file,
                                lambda line, msg: (line, msg) == (1, "anchors (&name) are not allowed in config files"))
    assert time.monotonic() - t0 < 2


def test_alias_bomb_on_reload_answers_422(svc):
    _assert_reload_rejects(
        svc, svc.yaml_file,
        lambda: svc.yaml_path.write_text("\n".join(ALIAS_BOMB_LINES + [f"{svc.section}: *i"]) + "\n"),
        lambda line, msg: "anchors" in msg)


def test_a_single_harmless_anchor_and_alias_are_refused(svc):
    text = svc.set_value(f"&v {svc.good}") + f"# again\nzz_copy: *v\n"
    svc.yaml_path.write_text(text)
    errs = _problems(svc.loader, svc.dir)
    assert errs[0][2] == "anchors (&name) are not allowed in config files"


def test_merge_key_is_refused(svc):
    # PyYAML and ruamel both expand `<<` into the mapping; the shipped
    # loaders accepted this as {field: 9}.
    svc.yaml_path.write_text(f"{svc.section}:\n  <<: {{{svc.field}: 9}}\n")
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert (f, line, msg) == (svc.yaml_file, 2, "merge keys (<<) are not allowed in config files")


@pytest.mark.parametrize("tagged", ['!!int "9"', "!!float 9", "!!str 9", "!!python/object/apply:os.system ['true']"])
def test_explicit_tags_are_refused(svc, tagged):
    svc.yaml_path.write_text(svc.set_value(tagged))
    errs = _problems(svc.loader, svc.dir)
    assert len(errs) == 1 and errs[0][2].startswith("explicit tags (")
    assert svc.loader.status()["ok"] is True


# ── finding 3: size, and files that are not regular files ────────────────────

def test_the_caps_are_what_the_report_says():
    for loader in (orch_loader, ts_loader):
        cf = loader.config_files
        assert (cf.MAX_YAML_BYTES, cf.MAX_PROMPT_BYTES, cf.MAX_DEPTH, cf.MAX_PROBLEMS) == \
            (CAP, CAP, 10, MAX_PROBLEMS)


def test_yaml_over_the_cap_is_refused_from_stat(svc):
    cap = CAP
    text = svc.set_value(str(svc.good))
    pad = "# pad\n" * ((cap - len(text)) // 6 + 1)
    svc.yaml_path.write_text(pad + text)
    assert svc.yaml_path.stat().st_size > cap
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert f == svc.yaml_file and msg.startswith("too large: ") and f"the limit is {cap}" in msg


def test_yaml_at_the_cap_still_loads(svc):
    cap = CAP
    text = svc.set_value(str(svc.good))
    pad = "#" * (cap - len(text.encode()) - 1) + "\n"
    svc.yaml_path.write_text(pad + text)
    assert svc.yaml_path.stat().st_size == cap
    assert svc.loader.load(svc.dir).source == "files"


def test_huge_sparse_file_is_refused_without_reading_it(svc):
    # 16 MB of holes: no disk or memory is used unless something reads it.
    with open(svc.yaml_path, "wb") as fh:
        fh.truncate(16 * 1024 * 1024)
    t0 = time.monotonic()
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert time.monotonic() - t0 < 0.5
    assert f == svc.yaml_file and msg == f"too large: {16 * 1024 * 1024} bytes; the limit is 65536"


def test_huge_file_on_reload_answers_at_once(svc):
    def bad():
        with open(svc.yaml_path, "wb") as fh:
            fh.truncate(16 * 1024 * 1024)
    t0 = time.monotonic()
    _assert_reload_rejects(svc, svc.yaml_file, bad, lambda line, msg: msg.startswith("too large"))
    assert time.monotonic() - t0 < 1


def test_prompt_over_the_cap_is_refused(orch):
    p = orch / "prompts" / "home.md"
    with open(p, "wb") as fh:
        fh.write(b"<!--\nhome\n-->\n")
        fh.truncate(CAP + 1)
    ((f, line, msg),) = _problems(orch_loader, orch)
    assert f == "prompts/home.md" and msg.startswith("too large: ")


def test_a_pipe_in_place_of_the_file_does_not_block(svc):
    svc.yaml_path.unlink()
    os.mkfifo(svc.yaml_path)

    def unblock():
        # Give a blocked reader its EOF.
        fd = os.open(svc.yaml_path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)

    try:
        errs = _bounded(lambda: _problems(svc.loader, svc.dir), 2, unblock)
        assert errs == [(svc.yaml_file, None, "is not a regular file; config files must be "
                                              "regular files inside the config directory")]
    finally:
        svc.yaml_path.unlink()      # no FIFO left on disk (e2e2 observation 9)


def test_a_directory_in_place_of_the_file_is_an_error(svc):
    svc.yaml_path.unlink()
    svc.yaml_path.mkdir()
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert f == svc.yaml_file and msg.startswith("is not a regular file")


# ── observation 3: symlinks ──────────────────────────────────────────────────

def test_symlink_to_a_valid_file_outside_is_refused(svc, tmp_path):
    outside = tmp_path / "elsewhere.yaml"
    outside.write_text(svc.set_value("9"))
    svc.yaml_path.unlink()
    svc.yaml_path.symlink_to(outside)
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert (f, msg) == (svc.yaml_file, "is a symlink; config files must be regular "
                                       "files inside the config directory")


def test_symlink_inside_the_directory_is_refused_too(svc):
    copy = svc.dir / "copy-of-the-file"
    copy.write_text(svc.yaml_path.read_text())
    svc.yaml_path.unlink()
    svc.yaml_path.symlink_to(copy.name)
    errs = _problems(svc.loader, svc.dir)
    assert (svc.yaml_file, None, "is a symlink; config files must be regular files "
                                 "inside the config directory") in errs


def test_symlinked_prompt_and_prompts_directory_are_refused(orch, tmp_path):
    outside = tmp_path / "home.md"
    shutil.copy(orch / "prompts" / "home.md", outside)
    (orch / "prompts" / "home.md").unlink()
    (orch / "prompts" / "home.md").symlink_to(outside)
    errs = _problems(orch_loader, orch)
    assert ("prompts/home.md", None, "is a symlink; config files must be regular files "
                                     "inside the config directory") in errs

    real = tmp_path / "prompts-elsewhere"
    shutil.copytree(orch / "prompts", real, symlinks=False)
    shutil.rmtree(orch / "prompts")
    (orch / "prompts").symlink_to(real)
    errs = _problems(orch_loader, orch)
    assert len(errs) == len(orch_loader.prompt_defaults.PROMPTS)
    assert all(m == "prompts/ is a symlink; config files must be regular files inside "
                    "the config directory" for _, _, m in errs)


# ── any exception at all becomes a config error ──────────────────────────────

@pytest.mark.parametrize("exc", [RecursionError("maximum recursion depth exceeded"), MemoryError(),
                                 ValueError("a library bug")])
def test_any_exception_while_loading_a_file_is_a_config_error(svc, monkeypatch, exc):
    def boom(*a, **kw):
        raise exc
    with monkeypatch.context() as m:        # undone before the fixture's teardown
        m.setattr(svc.loader, "validate", boom)
        errs = _problems(svc.loader, svc.dir)
        assert errs == [(svc.yaml_file, None, f"could not be loaded ({type(exc).__name__}"
                                              + (f": {exc})" if str(exc) else ")"))]
        # Startup survives it ...
        svc.loader.start()
        st = svc.loader.status()
        assert st["ok"] is False and st["source"] == "defaults"
        assert st["errors"][0]["file"] == f"config/{svc.yaml_file}"
        # ... and a reload answers 422 with the file, never a bare 500.
        r = svc.client().post("/config/reload")
        assert r.status_code == 422 and r.json()["errors"][0]["file"] == f"config/{svc.yaml_file}"


def test_any_exception_while_loading_a_prompt_names_the_prompt(orch, monkeypatch):
    real = orch_loader.parse_prompt

    def boom(text, rel):
        if rel == "prompts/talkie.md":
            raise RecursionError("maximum recursion depth exceeded")
        return real(text, rel)
    with monkeypatch.context() as m:
        m.setattr(orch_loader, "parse_prompt", boom)
        assert _problems(orch_loader, orch) == [
            ("prompts/talkie.md", None, "could not be loaded (RecursionError: maximum recursion depth exceeded)")]
        orch_loader.start()
        assert orch_loader.status()["source"] == "defaults"
        assert agents.AGENTS["talkie"].system_prompt == orch_loader.prompt_defaults.PROMPTS["talkie"]


def test_a_failure_outside_any_file_still_leaves_the_service_up(svc, monkeypatch):
    def boom(base=None):
        raise RuntimeError("unforeseen")
    with monkeypatch.context() as m:
        m.setattr(svc.loader, "load", boom)
        cfg = svc.loader.start()
        assert cfg.source == "defaults"
        st = svc.loader.status()
        assert st["ok"] is False
        assert st["errors"] == [{"file": "(loader)", "line": None,
                                 "message": "could not be loaded (RuntimeError: unforeseen)"}]
        r = svc.client().post("/config/reload")
        assert r.status_code == 422


# ── the last good values, from a state that is not the defaults ──────────────
# (e2e finding 7: mutants o6 and s4 survived because every earlier test
# started from the shipped files, which equal the defaults.)

def test_bad_reload_keeps_a_non_default_last_good_value(svc):
    new = 600 if svc.name == "orchestrator" else 5
    svc.yaml_path.write_text(svc.set_value(str(new)))
    assert svc.loader.reload()["ok"] is True
    assert svc.value() == new
    svc.yaml_path.write_text(f"{svc.section}: {NESTED}\n")
    assert svc.loader.reload()["ok"] is False
    assert svc.value() == new


# ── error messages stay small ────────────────────────────────────────────────

def test_a_long_rejected_value_is_quoted_short(svc):
    svc.yaml_path.write_text(svc.set_value("x" * 20000))
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert f == svc.yaml_file and line is not None
    assert len(msg) < 250 and msg.endswith("…)")


# ── observation 4: one YAML dialect ──────────────────────────────────────────

DIALECT = [
    "a: 1", "a: -3", "a: +4", "a: 0", "a: 1.5", "a: .5", "a: 5.", "a: 1e3", "a: 2.5E-2",
    "a: 2:00:00", "a: 0o17", "a: 017", "a: 0x1f", "a: 7_200", "a: 1,000",
    "a: yes", "a: no", "a: on", "a: off", "a: y", "a: true", "a: False", "a: TRUE",
    "a: ~", "a: null", "a:", "a: Null", "a: .inf", "a: -.inf", "a: .nan", "a: NaN",
    "a: '5'", 'a: "5"', "a: |\n  5\n", "a: >\n  five\n", "a: 2026-11-17", "a: =",
    "a:\n  b:\n    c: 1\n  d: [1, 2]\n", "a: {b: 1, c: [x, y]}", "1: x", "~: x", "'a': 1",
    "a: [1, [2, [3]]]", "- 1\n- 2\n", "x", "", "# only a comment\n", "---\na: 1\n", "a: 1\n---\nb: 2\n",
    "a: 1\na: 2\n", "? [a]\n: 1\n", "a: &x 1\n", "a: *x\n", "a: !!int '1'\n", "<<: {a: 1}\n",
    "a: 'it''s'", "a: \"tab\\there\"", "a: [1, 2", "a: b: c", "\ta: 1",
]


def _parse(loader, text):
    try:
        return ("ok",) + loader.parse_yaml(text, "x.yaml")
    except loader.ConfigInvalid as e:
        (p,) = e.problems
        return ("error", p.line, p.message.split(":")[0] if p.message.startswith("not valid YAML") else p.message)


@pytest.mark.parametrize("text", DIALECT)
def test_both_services_read_yaml_the_same_way(text):
    assert _parse(orch_loader, text) == _parse(ts_loader, text)


@pytest.mark.parametrize("raw,value", [
    ("2:00:00", "2:00:00"), ("0o17", "0o17"), ("017", "017"), ("0x1f", "0x1f"), ("7_200", "7_200"),
    ("yes", "yes"), ("on", "on"), ("no", "no"), (".inf", ".inf"),
    ("7200", 7200), ("-3", -3), ("1.5", 1.5), (".5", 0.5), ("1e3", 1000.0), ("true", True), ("~", None),
])
def test_yaml_1_1_surprises_stay_strings(raw, value):
    for loader in (orch_loader, ts_loader):
        data, _ = loader.parse_yaml(f"a: {raw}\n", "x.yaml")
        assert data == {"a": value} and type(data["a"]) is type(value), (loader.__name__, raw)


@pytest.mark.parametrize("raw", ["2:00:00", "0o17", "017", "0x1f", "1_0", "yes", "on", ".inf"])
def test_schema_rejects_yaml_1_1_numbers_and_booleans(svc, raw):
    svc.yaml_path.write_text(svc.set_value(raw))
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert f == svc.yaml_file and line is not None
    assert msg.startswith(f"{svc.section}.{svc.field}: Input should be a")
    assert f"(got '{raw}')" in msg


def test_the_shared_module_is_byte_identical_in_both_services():
    assert filecmp.cmp(REPO / "orchestrator" / "config_files.py",
                       REPO / "tool_service" / "config_files.py", shallow=False)


def test_tool_service_image_copies_the_shared_module():
    assert "config_files.py" in (REPO / "tool_service" / "Dockerfile").read_text()


def _fill(prefix, item, sep, suffix, cap):
    """prefix + item(0) sep item(1) ... + suffix, as long as fits in cap
    bytes; item(i) must have the same length for every i."""
    n = (cap - len(prefix) - len(suffix)) // (len(item(0)) + len(sep))
    return prefix + sep.join(item(i) for i in range(n)) + suffix


def test_hostile_yaml_within_the_cap_parses_fast(svc):
    cap = CAP
    shapes = {
        "many keys": _fill("", lambda i: f"k{i:05d}: 1", "\n", "\n", cap),
        "wide flow": _fill("a: {", lambda i: f"k{i:05d}: 1", ", ", "}\n", cap),
        "long list": _fill("a: [", lambda i: "1", ",", "]\n", cap),
        "long string": "a: '" + "x" * (cap - 8) + "'\n",
        "deep and wide": _fill("a: [", lambda i: "[" * 8 + "1" + "]" * 8, ", ", "]\n", cap),
        "unclosed brackets": "a: " + "[" * (cap - 8) + "\n",
        "many comments": _fill("", lambda i: "#", "\n", "\na: 1\n", cap),
    }
    for name, text in shapes.items():
        assert len(text.encode()) <= cap, name
        svc.yaml_path.write_text(text)
        t0 = time.monotonic()
        svc.loader.reload()
        took = time.monotonic() - t0
        assert took < 1, f"{name}: {took:.2f}s"
        st = svc.loader.status()
        assert st["ok"] is False, name
        assert len(st["errors"]) <= MAX_PROBLEMS + 1, name


# ── observation 2: a YAML file with no keys ──────────────────────────────────

@pytest.mark.parametrize("text", ["", "   \n\n", "# every line\n# a comment\n", "~\n", "---\n"])
def test_yaml_file_with_no_keys_loads_with_a_warning(svc, text):
    svc.yaml_path.write_text(text)
    r = svc.loader.reload()
    assert r["ok"] is True
    want = {"file": f"config/{svc.yaml_file}", "line": None, "message": "all defaults (file has no keys)"}
    assert want in r["warnings"]
    st = svc.loader.status()
    assert st["ok"] is True and st["source"] == "files" and want in st["warnings"]
    svc.loader.start()
    assert want in svc.loader.status()["warnings"]


def test_shipped_files_load_with_no_warnings(svc):
    assert svc.loader.reload() == {"ok": True, "changed": []}
    assert svc.loader.status()["warnings"] == []


# ── observation 5: stray files ───────────────────────────────────────────────

def test_stray_files_are_warnings_on_status_and_in_the_reload_answer(orch):
    (orch / "tunning.yaml").write_text("agent_loop: {}\n")
    (orch / "music.yml").write_text("verify: {}\n")
    shutil.copy(orch / "prompts" / "home.md", orch / "prompts" / "home.md.bak")
    (orch / "notes.md").write_text("x\n")
    r = TestClient(_orch_app()).post("/config/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    files = sorted(w["file"] for w in body["warnings"])
    assert files == ["config/music.yml", "config/notes.md", "config/prompts/home.md.bak",
                     "config/tunning.yaml"]
    assert all("not a file any service reads" in w["message"] for w in body["warnings"])
    assert sorted(w["file"] for w in orch_loader.status()["warnings"]) == files


def test_an_unknown_prompt_md_stays_an_error(orch):
    (orch / "prompts" / "hom.md").write_text("<!--\nx\n-->\nhello\n")
    errs = _problems(orch_loader, orch)
    assert errs[0][0] == "prompts/hom.md" and "unknown prompt file" in errs[0][2]


def test_tool_service_does_not_report_strays_twice(tmp_path):
    """The orchestrator scans the whole directory; tool_service reading the
    same directory stays quiet, so /services shows each stray once."""
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    (d / "tunning.yaml").write_text("x: 1\n")
    assert ts_loader.load(d).warnings == []
    assert [w.file for w in orch_loader.load(d).warnings] == ["tunning.yaml"]


# ── observation 6: prompt bodies ─────────────────────────────────────────────

def test_prompt_with_only_comments_after_the_header_is_empty(orch):
    (orch / "prompts" / "home.md").write_text("<!--\nhome\n-->\n<!-- second comment -->\n\n<!-- third\n-->\n")
    assert _problems(orch_loader, orch) == [
        ("prompts/home.md", 3, "prompt is empty (only comments after the header)")]


@pytest.mark.parametrize("ch,code", [("\x00", "U+0000"), ("\x07", "U+0007"), ("\x1b", "U+001B"),
                                     ("\x7f", "U+007F"), ("\x85", "U+0085"), ("\x0c", "U+000C")])
def test_control_characters_in_a_prompt_are_errors(orch, ch, code):
    (orch / "prompts" / "home.md").write_text(f"<!--\nhome\n-->\nline one\nline{ch}two\n")
    assert _problems(orch_loader, orch) == [
        ("prompts/home.md", 5, f"control character {code} is not allowed in a prompt (only newline and tab)")]


def test_tabs_and_crlf_in_a_prompt_are_fine(orch):
    (orch / "prompts" / "home.md").write_bytes(b"<!--\r\nhome\r\n-->\r\nline\tone\r\nline two\r\n")
    assert orch_loader.load(orch).prompts["home"] == "line\tone\nline two"


# ── observation 10: scripts/config_reload.sh prints both HTTP codes ──────────

_FAKE_URLOPEN = r'''
import io, os, sys, urllib.error, urllib.request
code, body = int(os.environ["FAKE_CODE"]), os.environ["FAKE_BODY"].encode()

class Resp(io.BytesIO):
    status = code

def urlopen(req, timeout=None):
    if code != 200:
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
    return Resp(body)

urllib.request.urlopen = urlopen
exec(sys.argv[1])
'''


@pytest.mark.parametrize("code,body,exit_status", [
    (200, '{"ok":true,"changed":[]}', 0),
    (422, '{"ok":false,"errors":[]}', 1),
])
def test_config_reload_script_prints_the_http_code_of_both_services(tmp_path, code, body, exit_status):
    """docker and curl are stubs on PATH: nothing is contacted. The stub
    docker runs the script's own Python snippet with urlopen faked."""
    import subprocess
    import sys
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "fake_urlopen.py").write_text(_FAKE_URLOPEN)
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\n# docker exec <name> python -c <code>\nexec "{sys.executable}" "{bin_dir}/fake_urlopen.py" "$5"\n')
    (bin_dir / "curl").write_text('#!/bin/sh\nprintf "%s\\n%s" "$FAKE_BODY" "$FAKE_CODE"\n')
    for f in ("docker", "curl"):
        (bin_dir / f).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "FAKE_CODE": str(code), "FAKE_BODY": body}
    r = subprocess.run(["bash", str(REPO / "scripts" / "config_reload.sh"), "dev"],
                       capture_output=True, text=True, env=env, timeout=20)
    lines = r.stdout.splitlines()
    assert lines.count(f"{code} {body}") == 2, r.stdout + r.stderr
    assert r.returncode == exit_status


# ── the orchestrator's startup log line ──────────────────────────────────────

def test_startup_log_names_file_and_line_only_when_there_is_a_line(orch, caplog):
    """Seen live on dev in fix round 1: "config/tuning.yaml:None: too large"."""
    import logging
    import main
    with open(orch / "tuning.yaml", "wb") as fh:
        fh.truncate(1024 * 1024)
    (orch / "tunning.yaml").write_text("x: 1\n")
    orch_loader.start()
    with caplog.at_level(logging.INFO, logger=main.logger.name):
        main._log_config_status()
    assert "config at startup rejected: config/tuning.yaml: too large: 1048576 bytes" in caplog.text
    assert ":None" not in caplog.text
    (orch / "tuning.yaml").write_bytes((CONFIG / "tuning.yaml").read_bytes())
    orch_loader.start()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=main.logger.name):
        main._log_config_status()
    assert "config at startup: config/tunning.yaml: not a file any service reads" in caplog.text


def test_stray_scan_is_sorted_and_bounded(orch):
    for i in range(30):
        (orch / f"zz-stray-{i:02d}.yaml").write_text("x: 1\n")
    warnings = orch_loader.load(orch).warnings
    files = [w.file for w in warnings]
    assert len(warnings) == orch_loader.config_files.MAX_WARNINGS + 1
    assert files[:-1] == sorted(files[:-1]) and files[0] == "zz-stray-00.yaml"
    assert warnings[-1].message.startswith("more entries not checked")
