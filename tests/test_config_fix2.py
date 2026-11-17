"""config/ text that reaches an HTTP answer or a log line (sprint M21 step 1,
fix round 2; docs/sprint/M21-step1-e2e2.md finding 1 and observations 2-5
and 9).

The class: text taken from a file or a file name reaches an HTTP answer or a
log line without being made safe to encode. A file name that is not UTF-8,
or a duplicate key written "\\uD800", put a lone surrogate into a Problem,
and the reload answer and /api/config could not be encoded (500).

Layer 1: config_files.safe(), applied when every Problem is built.
Layer 2: config_files.render_reload() / render_status() at the endpoints.

Also here: a file that changes while it is read (observation 2), one YAML
language for both loaders (observation 3), key names shortened (4), YAML
errors without a line (5), and the suite leaving no scratch files (9).
Every test runs against both services where the case applies to both. No
test opens a socket: the orchestrator's call to tool_service's
/config/status is served in process.
"""
import json
import logging
import os
import random
import subprocess
import sys
import types
import unicodedata

import httpx
import pytest
from fastapi.testclient import TestClient

import config_api
import config_loader as orch_loader
from tool_service import config_loader as ts_loader

from tests.test_config_hardening import CONFIG, REPO, _orch_app, _problems, _ts_app, orch, svc  # noqa: F401

NOT_UTF8_STRAY = b"caf\xe9.yaml"          # a Latin-1 name, as copied from another system
SHOWN_STRAY = "config/caf\\udce9.yaml"     # as it appears in answers and logs
SURROGATE_DUPLICATE = '"\\uD800": 1\n"\\uD800": 2\n'


def _encodes(obj) -> bool:
    """As Starlette's JSONResponse encodes."""
    try:
        json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return True
    except (UnicodeEncodeError, ValueError):
        return False


class _ToolServiceInProcess:
    """Stands in for httpx.AsyncClient in config_api: GET /config/status is
    answered by tool_service's own app in process, or by a raw body."""

    def __init__(self, raw: bytes | None = None):
        self.raw = raw

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self.raw is not None:
            return httpx.Response(200, content=self.raw, request=httpx.Request("GET", url))
        r = TestClient(_ts_app()).get("/config/status")
        return httpx.Response(r.status_code, content=r.content, request=httpx.Request("GET", url))


@pytest.fixture
def api_config(monkeypatch):
    """GET /api/config on the orchestrator, tool_service served in process."""
    # config_api's own name for httpx only: patching httpx.AsyncClient itself
    # would reach every module that imports httpx afterwards.
    monkeypatch.setattr(config_api, "httpx", types.SimpleNamespace(AsyncClient=_ToolServiceInProcess()))

    def get():
        r = TestClient(_orch_app()).get("/api/config")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        return {s["service"]: s for s in r.json()["services"]}
    return get


def _make_not_utf8(directory, name: bytes, content: bytes = b"x: 1\n"):
    fd = os.open(os.fsencode(str(directory)) + b"/" + name, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o644)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def _clean(text: str) -> bool:
    """No character an answer or a log line must not carry raw."""
    return all(unicodedata.category(ch) not in ("Cc", "Cs", "Cf", "Zl", "Zp") for ch in text)


# ── finding 1: the four inputs, on reload and after a restart ────────────────

def test_a_stray_file_whose_name_is_not_utf8_still_answers(orch, api_config):
    _make_not_utf8(orch, NOT_UTF8_STRAY)
    r = TestClient(_orch_app()).post("/config/reload")
    assert r.status_code == 200, (r.status_code, r.text[:200])
    want = {"file": SHOWN_STRAY, "line": None,
            "message": "not a file any service reads (a typo, or a leftover copy?); ignored"}
    assert want in r.json()["warnings"]
    assert want in api_config()["orchestrator"]["warnings"]
    orch_loader.start()                               # after a restart, the same
    assert want in api_config()["orchestrator"]["warnings"]


def test_an_unknown_prompt_whose_name_is_not_utf8_is_an_error_that_answers(orch, api_config):
    _make_not_utf8(orch / "prompts", b"caf\xe9.md", b"<!--\nx\n-->\nhello\n")
    r = TestClient(_orch_app()).post("/config/reload")
    assert r.status_code == 422, (r.status_code, r.text[:200])
    (err,) = r.json()["errors"]
    assert err["file"] == "config/prompts/caf\\udce9.md" and err["message"].startswith("unknown prompt file")
    st = api_config()["orchestrator"]
    assert st["ok"] is False and st["errors"] == [err]
    orch_loader.start()
    st = api_config()["orchestrator"]
    assert st["source"] == "defaults" and st["errors"] == [err]


def test_a_duplicate_surrogate_key_is_an_error_that_answers(svc, api_config):
    svc.yaml_path.write_text(svc.yaml_path.read_text() + SURROGATE_DUPLICATE)
    line = svc.yaml_path.read_text().count("\n")
    r = svc.client().post("/config/reload")
    assert r.status_code == 422, (r.status_code, r.text[:200])
    assert r.json()["errors"] == [{"file": f"config/{svc.yaml_file}", "line": line,
                                   "message": "duplicate key \\ud800"}]
    st = api_config()[svc.name]
    assert st["ok"] is False and st["errors"][0]["message"] == "duplicate key \\ud800"
    svc.loader.start()
    st = api_config()[svc.name]
    assert st["source"] == "defaults" and st["errors"][0]["line"] == line


def test_the_service_answers_a_normal_request_meanwhile(orch, api_config):
    _make_not_utf8(orch, NOT_UTF8_STRAY)
    (orch / "tuning.yaml").write_text((orch / "tuning.yaml").read_text() + SURROGATE_DUPLICATE)
    orch_loader.start()
    client = TestClient(_orch_app())
    assert client.post("/config/reload").status_code == 422
    assert client.get("/api/agents").status_code == 200


# ── layer 1: safe() ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,shown", [
    ("caf\udce9.yaml", "caf\\udce9.yaml"),
    ("\ud800", "\\ud800"),
    ("a\x1b[31mb", "a\\x1b[31mb"),
    ("two\nlines\r", "two\\x0alines\\x0d"),
    ("bidi \u202eevil", "bidi \\u202eevil"),
    ("zero\u200bwidth", "zero\\u200bwidth"),
    ("sep\u2028x\u2029", "sep\\u2028x\\u2029"),
    ("é ü 😀 tab\there", "é ü 😀 tab\\x09here"),
])
def test_safe_escapes_visibly(raw, shown):
    for cf in (orch_loader.config_files, ts_loader.config_files):
        assert cf.safe(raw) == shown


def test_safe_caps_length_after_escaping():
    for cf in (orch_loader.config_files, ts_loader.config_files):
        for raw in ("x" * 1000, "\ud800" * 1000, "\x1b" * 1000):
            out = cf.safe(raw)
            assert len(out) <= cf.MAX_QUOTE and out.endswith("…") and _encodes(out)
        assert cf.safe("k" * 80) == "k" * 80
        assert len(cf.safe("k" * 1000, cf.MAX_MESSAGE)) == cf.MAX_MESSAGE


@pytest.mark.parametrize("loader", [orch_loader, ts_loader], ids=["orchestrator", "tool_service"])
def test_a_problem_is_made_safe_when_it_is_built(loader):
    p = loader.Problem("caf\udce9.yaml", 3, "duplicate key \ud800 and \x1b[2J" + "y" * 1000)
    assert _encodes(p.as_dict()) and _clean(p.file + p.message) and _clean(str(p))
    assert p.file == "caf\\udce9.yaml"
    assert len(p.message) <= loader.config_files.MAX_MESSAGE


def test_key_names_are_shortened_like_values(svc):
    """Observation 4: 63 unknown 1,000-character keys made a 53 kB answer."""
    lines = [f"  {'k' * 1000}{i:02d}: 1" for i in range(5)]
    svc.yaml_path.write_text(f"{svc.section}:\n" + "\n".join(lines) + "\n")
    errs = _problems(svc.loader, svc.dir)
    assert len(errs) == 5
    for f, line, msg in errs:
        assert line is not None and msg.endswith(": unknown key") and len(msg) < 120, msg
        assert msg.startswith(f"{svc.section}.kkkk") and "…" in msg


def test_control_characters_in_a_stray_name_do_not_reach_the_log_raw(orch, caplog):
    _make_not_utf8(orch, b"a\x1b[31m\nb.yaml")
    with caplog.at_level(logging.WARNING, logger=orch_loader.logger.name):
        r = orch_loader.reload()
    (w,) = r["warnings"]
    assert w["file"] == "config/a\\x1b[31m\\x0ab.yaml"
    logged = [rec.getMessage() for rec in caplog.records if "not a file any service reads" in rec.getMessage()]
    assert logged and all(_clean(m) for m in logged)


# ── layer 2: the endpoints never answer a bare 500 ───────────────────────────

UNENCODABLE = "\ud800"


@pytest.mark.parametrize("ok,code,effect", [
    (True, 200, "the reload took effect"),
    (False, 422, "the reload was rejected and the last good values stay in effect"),
])
def test_a_reload_answer_that_cannot_be_encoded_still_answers_json(svc, monkeypatch, ok, code, effect):
    bad = {"ok": ok, "changed": [UNENCODABLE]} if ok else {"ok": ok, "errors": [{"message": UNENCODABLE}]}
    with monkeypatch.context() as m:
        m.setattr(svc.loader, "reload", lambda: bad)
        r = svc.client().post("/config/reload")
    assert r.status_code == code
    body = r.json()
    assert body["ok"] is ok
    (err,) = body["errors"]
    assert err["message"] == ("the config status could not be rendered as JSON (UnicodeEncodeError); "
                              f"{effect}; see the service log")


def test_a_status_that_cannot_be_encoded_still_answers_json(svc, api_config, monkeypatch):
    other = "tool_service" if svc.name == "orchestrator" else "orchestrator"
    sentence = ("the config status could not be rendered as JSON (UnicodeEncodeError); "
                "see the service log")
    with monkeypatch.context() as m:
        m.setattr(svc.loader, "status", lambda: {"service": svc.name, "ok": True, "x": UNENCODABLE})
        services = api_config()
        if svc.name == "tool_service":
            r = TestClient(_ts_app()).get("/config/status")
            assert r.status_code == 200 and r.json()["errors"][0]["message"] == sentence
    st = services[svc.name]
    # What the /services page reads: ok, errors[].message.
    assert st["ok"] is False and st["errors"] == [{"file": None, "line": None, "message": sentence}]
    assert services[other]["ok"] is True            # the other service still shows


def test_tool_service_status_with_a_surrogate_escape_does_not_break_api_config(monkeypatch):
    raw = b'{"service":"tool_service","ok":false,"errors":[{"file":"config/music.yaml","line":1,"message":"\\ud800"}]}'
    monkeypatch.setattr(config_api, "httpx", types.SimpleNamespace(AsyncClient=_ToolServiceInProcess(raw)))
    r = TestClient(_orch_app()).get("/api/config")
    assert r.status_code == 200
    ts = r.json()["services"][1]
    assert ts["service"] == "tool_service" and ts["ok"] is False
    assert "could not be rendered as JSON" in ts["errors"][0]["message"]


# ── observation 2: a file that changes while it is read ──────────────────────

def _editor_saving_in_place(monkeypatch, path, texts, times):
    """os.read, but the first read of each attempt on path finds the editor
    mid-save: the file truncated (what open(path, "w") does), then written
    with the next of texts. Same inode throughout."""
    target = os.stat(path).st_ino
    real = os.read
    state = {"saves": 0}

    def read(fd, n):
        if (state["saves"] < times and os.fstat(fd).st_ino == target
                and os.lseek(fd, 0, os.SEEK_CUR) == 0):
            text = texts[state["saves"] % len(texts)]
            state["saves"] += 1
            with open(path, "r+b") as fh:
                fh.truncate(0)
                chunk = real(fd, n)             # the reader sees the empty file
                fh.write(text.encode())
            return chunk
        return real(fd, n)
    monkeypatch.setattr(os, "read", read)
    return state


def test_a_file_saved_in_place_during_the_read_is_read_again(svc, monkeypatch):
    new = 600 if svc.name == "orchestrator" else 5
    with monkeypatch.context() as m:
        state = _editor_saving_in_place(m, svc.yaml_path, [svc.set_value(str(new))], times=1)
        r = svc.loader.reload()
    assert state["saves"] == 1
    assert r["ok"] is True and "warnings" not in r, r      # not "all defaults (file has no keys)"
    assert svc.value() == new


def test_a_file_that_keeps_changing_is_an_error_and_the_last_good_values_stay(svc, monkeypatch):
    before = svc.value()
    texts = ([svc.set_value("600"), svc.set_value("3600")] if svc.name == "orchestrator"
             else [svc.set_value("5"), svc.set_value("10")])
    with monkeypatch.context() as m:
        _editor_saving_in_place(m, svc.yaml_path, texts, times=99)
        r = svc.client().post("/config/reload")
    assert r.status_code == 422, r.text[:300]
    assert r.json()["errors"] == [{"file": f"config/{svc.yaml_file}", "line": None,
                                   "message": "file changed during read (read twice); "
                                              "reload again once it is saved"}]
    assert svc.value() == before
    assert svc.loader.status()["ok"] is False


def test_a_prompt_saved_in_place_during_the_read_is_read_again(orch, monkeypatch):
    path = orch / "prompts" / "home.md"
    edited = path.read_text().rstrip("\n") + " Heron.\n"
    with monkeypatch.context() as m:
        _editor_saving_in_place(m, path, [edited], times=1)
        r = orch_loader.reload()
    assert r["ok"] is True, r
    assert orch_loader.current().prompts["home"].endswith(" Heron.")


# ── observation 3: one YAML language ─────────────────────────────────────────
# Each row: the text, and what both loaders must make of it: ("ok", data) or
# ("error", line, start of the message). Library syntax errors are compared
# by line and the "not valid YAML" prefix (their wording differs).

ONE_LANGUAGE = [
    ("a: x\u0085b: 1\n", ("error", 1, "control character U+0085 at byte 4 is not allowed")),
    ("a: 1\u2028b: 2\n", ("error", 1, "line separator U+2028 at byte 4 is not allowed")),
    ("a: 1\u2029b: 2\n", ("error", 1, "paragraph separator U+2029 at byte 4 is not allowed")),
    ("# a comment \u2028 with a separator\na: 1\n", ("error", 1, "line separator U+2028")),
    ("verify: y:\u2028\n", ("error", 1, "line separator U+2028")),
    (": 1\n", ("error", 1, "not valid YAML")),
    ("{: 1}\n", ("error", 1, "not valid YAML")),
    ("a: {: 1}\n", ("error", 1, "not valid YAML")),
    ("? \n: 1\n", ("error", 1, "empty key; a key must be a name")),
    ("{? : 1}\n", ("error", 1, "empty key; a key must be a name")),
    ("a:\n  ? \n  : 1\n", ("error", 2, "empty key; a key must be a name")),
    ("{a?b: 1}\n", ("error", 1, "not valid YAML")),
    ("a: {b?c: 1}\n", ("error", 1, "not valid YAML")),
    ("a: [b?c]\n", ("error", 1, "not valid YAML")),
    ("{a?: 1}\n", ("error", 1, "not valid YAML")),
    ("? a\n: 1\n", ("ok", {"a": 1})),
    ("? a\n", ("ok", {"a": None})),
    ("{? a: 1}\n", ("ok", {"a": 1})),
    ("a?b: 1\n", ("ok", {"a?b": 1})),
    ("a: x?y\n", ("ok", {"a": "x?y"})),
    ("\ufeffa: 1\n", ("ok", {"a": 1})),
    ("a: \ufffe\n", ("error", 1, "noncharacter U+FFFE at byte 3 is not allowed")),
    ("a: 1\nb: x\x1by\n", ("error", 2, "control character U+001B at byte 9 is not allowed")),
    ("a: 1\nb: \x7f\n", ("error", 2, "control character U+007F at byte 8 is not allowed")),
    ('a: "\\UFFFFFFFF"\n', ("error", None, "not valid YAML (ValueError")),
    ("a: " + "1" * 5000 + "\n", ("error", 1, "number too long (5000 digits)")),
    ("a: 1\n" + SURROGATE_DUPLICATE, ("error", 3, "duplicate key \\ud800")),
    ('"\\uD800": 1\n', ("ok", {"\ud800": 1})),
]


def _outcome(loader, text):
    try:
        data, lines = loader.parse_yaml(text, "x.yaml")
        return ("ok", data, lines)
    except loader.ConfigInvalid as e:
        (p,) = e.problems
        return ("error", p.line, p.message)


@pytest.mark.parametrize("text,want", ONE_LANGUAGE)
def test_both_loaders_read_the_same_language(text, want):
    a, b = _outcome(orch_loader, text), _outcome(ts_loader, text)
    assert a[0] == b[0] == want[0], (a, b)
    if want[0] == "ok":
        assert a == b and a[1] == want[1], (a, b)
    else:
        assert a[1] == b[1] == want[1], (a, b)
        assert a[2].startswith(want[2]) and b[2].startswith(want[2]), (a, b)
        if not want[2].startswith("not valid YAML"):
            assert a[2] == b[2]


_ALPHABET = list("ab:- \n[]{},?#'\"|>&*!%@`~0123456789.\t") + [
    "\u0085", "\u2028", "\u2029", "\ufeff", "\x00", "\x1b", "\u00a0", ": ", "- ", "? ", "\n  ",
    "a: ", "\n- ", "'x'", '"\\u00e9"', "---", "...", "%YAML 1.2\n", "é", "\\"]


def _generated(n, seed):
    """Small texts: the shipped YAML files with a few edits, and token soups."""
    rng = random.Random(seed)
    shipped = [(CONFIG / f).read_text() for f in ("tuning.yaml", "music.yaml", "lead_in.yaml")]
    for _ in range(n):
        if rng.random() < 0.4:
            s = list(rng.choice(shipped))
            for _ in range(rng.randint(1, 4)):
                i = rng.randrange(len(s) + 1)
                op = rng.random()
                if op < 0.4:
                    s.insert(i, rng.choice(_ALPHABET))
                elif i < len(s):
                    if op < 0.7:
                        del s[i]
                    else:
                        s[i] = rng.choice(_ALPHABET)
            yield "".join(s)
        else:
            yield "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 30)))


def test_both_loaders_agree_on_generated_texts():
    """Accepted by both or refused by both; when accepted, the same data and
    key lines. (Where both refuse a broken text, each library may name a
    different line; that is allowed.)"""
    disagree = []
    for text in _generated(2000, seed=21):
        a, b = _outcome(orch_loader, text), _outcome(ts_loader, text)
        if a[0] != b[0] or (a[0] == "ok" and a != b):
            disagree.append((text, a, b))
    assert not disagree, disagree[:5]


# ── observation 5: YAML errors without a line ────────────────────────────────

def test_a_control_character_in_yaml_names_the_character_line_and_byte(svc):
    text = svc.set_value(f"{svc.good}  # set\x1bhere")
    svc.yaml_path.write_text(text)
    i = text.index("\x1b")
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert (f, line) == (svc.yaml_file, text.count("\n", 0, i) + 1)
    assert msg == (f"control character U+001B at byte {len(text[:i].encode())} is not allowed "
                   "in a YAML file (only newline and tab)")


def test_an_escape_no_library_can_read_says_line_unknown(svc):
    svc.yaml_path.write_text(svc.set_value('"\\UFFFFFFFF"'))
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert line is None and msg.startswith("not valid YAML (ValueError") and msg.endswith("; line unknown")


def test_a_yaml_error_without_a_mark_says_line_unknown(svc, monkeypatch):
    if svc.name == "orchestrator":
        def parse(*a, **kw):
            raise orch_loader.yaml.YAMLError("the library gave no position")
        monkeypatch.setattr(orch_loader.yaml, "parse", parse)
    else:
        from ruamel.yaml.error import YAMLError

        class NoPosition:
            def __init__(self, *a, **kw):
                pass

            def parse(self, text):
                raise YAMLError("the library gave no position")
        monkeypatch.setattr(ts_loader, "YAML", NoPosition)
    assert _problems(svc.loader, svc.dir) == [
        (f, None, "not valid YAML: the library gave no position; line unknown") for f in svc.yaml_files]


def test_a_very_long_integer_names_its_line(svc):
    text = svc.set_value("1" * 5000)
    svc.yaml_path.write_text(text)
    ((f, line, msg),) = _problems(svc.loader, svc.dir)
    assert line == text[:text.index("1" * 5000)].count("\n") + 1
    assert msg == "number too long (5000 digits)"


# ── observation 9: the suite leaves no scratch file behind ───────────────────

def test_a_run_of_the_fifo_tests_leaves_nothing_behind(tmp_path):
    """The FIFO tests, in a pytest run of their own with TMPDIR pointed at
    an empty directory: afterwards there is no FIFO, no file and no
    kronk-taste-* directory in it (only pytest's empty per-user base)."""
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env = {**os.environ, "TMPDIR": str(scratch)}
    env.pop("MUSIC_TASTE_DB", None)
    env.pop("MUSIC_TASTE_FILE", None)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_config_hardening.py", "-k", "pipe_in_place"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "2 passed" in r.stdout
    left = []
    for root, dirs, files in os.walk(scratch):
        left += [os.path.join(root, f) for f in files]
        left += [os.path.join(root, d) for d in dirs if d.startswith("kronk-taste-")]
        left += [os.path.join(root, d) for d in dirs if root != str(scratch)]
    assert left == [], left
