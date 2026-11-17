"""Bounded reading of config/ files, the one YAML dialect both services
accept, and safe text for answers and logs (sprint M21 step 1, fix rounds 1
and 2).

This file is byte-identical in orchestrator/ and tool_service/. Each service
builds its image from its own directory, so the file is copied rather than
shared; tests/test_config_hardening.py fails if the two copies differ. It
imports no YAML library. Each loader hands build() the parse events of its
own library (PyYAML in the orchestrator, ruamel.yaml in tool_service), and
this module, not the library, decides what a scalar means. The two libraries
do not tokenise every text alike (PyYAML reads U+0085, U+2028 and U+2029 as
line breaks, ruamel does not; ruamel in its default YAML 1.2 mode accepts
`{a?b: 1}`, PyYAML does not), so check_yaml_text() refuses those
characters before either library sees the text, and tool_service runs
ruamel's scanner in YAML 1.1 mode. tests/test_config_hardening.py and
tests/test_config_fix2.py run one table of documents, and 2,000 generated
ones, through both loaders and require the same accept/reject outcome and,
when accepted, the same data and key lines.

Why: a config file must never make a loader raise, hang or exhaust memory
outside its own error path (docs/sprint/M21-step1-e2e.md, findings 1-3).

  - read_text() checks the file with lstat before opening it: a regular
    file, no symlink anywhere under the config directory, and no larger
    than the cap. It then reads at most cap + 1 bytes from a descriptor
    opened with O_NOFOLLOW | O_NONBLOCK and re-checked with fstat, so a
    file swapped for a symlink or a pipe between the check and the read
    still cannot block or grow the read.
  - build() turns parse events into plain dicts, lists and scalars without
    recursion. It refuses anchors, aliases, merge keys and explicit tags
    (config files need none of them; aliases are how a 300-byte file
    expands into gigabytes), stops at MAX_DEPTH levels of nesting before
    the parser reads further, and refuses a second document and duplicate
    keys.
  - Plain scalars are resolved here, by a deliberately small subset of the
    YAML 1.2 core schema: null, true/false, decimal integers without
    leading zeros, decimal floats. Everything else is a string, so
    `2:00:00`, `0o17`, `017`, `0x1f`, `7_200`, `yes`, `on` and `.inf` reach
    the schema as strings and a numeric field rejects them, in both
    services alike. Quoted and block scalars are always strings.
  - safe() makes any text that came from a file, a file name, a key, a
    value or a parser message fit for a JSON answer and a log line: lone
    surrogates (a file name that is not UTF-8, a "\\uD800" escape) and
    control and format characters are escaped visibly, and the length is
    capped. Each loader's Problem passes its file and message through it
    when it is built, so nothing unencodable can reach the status object.
    renderable() is the second layer, at the HTTP boundary.
  - read_text() reads each file, then checks that its size and mtime did
    not change during the read (an editor saving in place truncates, then
    writes); if they did, it reads once more, then gives up with "file
    changed during read".

Errors are raised as FileError(line, message); each loader turns them into
its own Problem with the file name.
"""
import json
import logging
import os
import re
import stat
import unicodedata
from pathlib import Path

# Caps, checked from stat before a byte is read. The largest file today is
# 4.2 kB (prompts/home.md); 64 KiB is fifteen times that. A 64 KiB prompt is
# about 16k tokens, half of gemma-4-e4b's 32k context, so a larger one could
# not work as a system prompt anyway. Parsing 64 KiB of YAML takes well under
# a second, which bounds how long a reload can hold the event loop.
MAX_YAML_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 64 * 1024
# Collections nested inside each other. The deepest key today is
# tool_timeouts.per_tool.<tool>: three mappings.
MAX_DEPTH = 10
# Stray-file scan: entries looked at per directory, and warnings reported.
MAX_SCAN_ENTRIES = 500
MAX_WARNINGS = 20
# A rejected value is quoted in the error up to this many characters.
MAX_QUOTE = 80
# Problems reported per load; the rest are counted. A file of thousands of
# unknown keys must not become thousands of log lines and a huge status.
MAX_PROBLEMS = 50
# A whole problem message, file name or "changed" line, after safe(). Each
# part from outside the code (a key, a value, a parser message) is already
# cut to MAX_QUOTE, so this only catches what composition adds.
MAX_MESSAGE = 300

logger = logging.getLogger("config_files")


class FileError(Exception):
    def __init__(self, line: int | None, message: str):
        self.line = line
        self.message = message
        super().__init__(message)


# Escaped by safe(): control characters (Cc), lone surrogates (Cs: what a
# file name that is not UTF-8 decodes to, and what "\\uD800" in a quoted
# scalar becomes; they cannot be encoded as UTF-8), format characters (Cf:
# bidi overrides, zero-width marks) and line and paragraph separators.
_ESCAPED = frozenset({"Cc", "Cs", "Cf", "Zl", "Zp"})


def _escape(ch: str) -> str:
    o = ord(ch)
    return f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}" if o < 0x10000 else f"\\U{o:08x}"


def safe(text, limit: int = MAX_QUOTE) -> str:
    """text fit for a JSON answer and a log line: valid UTF-8, every
    character of _ESCAPED written as a visible escape (\\x1b, \\udce9,
    \\u202e), at most limit characters. Accepts any object (str() of it)."""
    if not isinstance(text, str):
        text = str(text)
    head = text[:limit]          # escaping only lengthens; nothing past limit survives
    out = "".join(_escape(ch) if unicodedata.category(ch) in _ESCAPED else ch for ch in head)
    if len(out) > limit or len(text) > limit:
        out = out[:limit - 1] + "…"
    return out


def shorten(text: str) -> str:
    """text, safe and cut to MAX_QUOTE characters: an error message must
    stay small and encodable whatever the file held."""
    return safe(text, MAX_QUOTE)


def quote(value) -> str:
    """repr(value), shortened."""
    return shorten(repr(value))


def unexpected(e: BaseException) -> str:
    """The message for an exception no check anticipated."""
    detail = str(e)
    return f"could not be loaded ({type(e).__name__}{': ' + shorten(detail) if detail else ''})"


def unparsable(e: BaseException) -> str:
    """The message for an exception a YAML library raised that is not one
    of its own errors (a "\\UFFFFFFFF" escape is a ValueError from chr()).
    The library does not say where; the message says so."""
    detail = str(e)
    return f"not valid YAML ({type(e).__name__}{': ' + shorten(detail) if detail else ''}); line unknown"


# ── the HTTP boundary ────────────────────────────────────────────────────────

UNRENDERABLE = "the config status could not be rendered as JSON ({})"


def renderable(obj, fallback):
    """obj if it encodes the way the HTTP answer will (Starlette's
    JSONResponse: json.dumps with ensure_ascii=False and allow_nan=False,
    then UTF-8); otherwise fallback(sentence), where sentence says the
    status could not be rendered and why. safe() should make this
    unreachable; it is here so an answer is never a bare 500."""
    try:
        json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        return obj
    except Exception as e:
        logger.error("config status could not be rendered as JSON: %s: %s",
                     type(e).__name__, safe(e, MAX_MESSAGE))
        return fallback(UNRENDERABLE.format(type(e).__name__))


def render_status(status, service: str) -> dict:
    """A service's config status, or, if it cannot be encoded, a status
    entry whose one error is the sentence (the /services page shows it)."""
    return renderable(status, lambda sentence: {
        "service": service, "ok": False, "event": "status", "source": "unknown",
        "errors": [{"file": None, "line": None, "message": f"{sentence}; see the service log"}]})


def render_reload(result) -> dict:
    """A reload answer, or, if it cannot be encoded, one that says so and
    whether the reload took effect."""
    ok = isinstance(result, dict) and result.get("ok") is True
    effect = ("the reload took effect" if ok
              else "the reload was rejected and the last good values stay in effect")
    return renderable(result, lambda sentence: {
        "ok": ok, "errors": [{"file": None, "line": None,
                              "message": f"{sentence}; {effect}; see the service log"}]})


def cap_problems(problems: list, make_summary) -> list:
    """problems, at most MAX_PROBLEMS of them plus one summary made by
    make_summary(first problem left out, number left out)."""
    if len(problems) <= MAX_PROBLEMS:
        return problems
    rest = problems[MAX_PROBLEMS:]
    return problems[:MAX_PROBLEMS] + [make_summary(rest[0], len(rest))]


# ── reading ──────────────────────────────────────────────────────────────────

_NOT_REGULAR = "not a regular file; config files must be regular files inside the config directory"
_SYMLINK = "a symlink; config files must be regular files inside the config directory"


CHANGED_DURING_READ = "file changed during read (read twice); reload again once it is saved"


def read_text(base: Path, rel: str, max_bytes: int) -> str:
    """The text of base/rel, bounded. Raises FileError. A file whose size
    or mtime changed while it was read (an editor saving it in place) is
    read once more; if it changed again, that is an error."""
    for _ in range(2):
        data = _read_once(base, rel, max_bytes)
        if data is not None:
            break
    else:
        raise FileError(None, CHANGED_DURING_READ)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FileError(None, f"cannot be read: {e}")
    # Universal newlines, as Path.read_text did before this module.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_once(base: Path, rel: str, max_bytes: int) -> bytes | None:
    """The bytes of base/rel, or None if the file changed while it was
    read. Raises FileError."""
    parts = rel.split("/")
    path = Path(base)
    for i, part in enumerate(parts):
        path = path / part
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise FileError(None, "file is missing")
        except OSError as e:
            raise FileError(None, f"cannot be read: {e.strerror}")
        last = i == len(parts) - 1
        if stat.S_ISLNK(st.st_mode):
            where = "" if last else f"{'/'.join(parts[:i + 1])}/ "
            raise FileError(None, f"{where}is {_SYMLINK}")
        if not last and not stat.S_ISDIR(st.st_mode):
            raise FileError(None, "file is missing")
    if not stat.S_ISREG(st.st_mode):
        raise FileError(None, f"is {_NOT_REGULAR}")
    if st.st_size > max_bytes:
        raise FileError(None, f"too large: {st.st_size} bytes; the limit is {max_bytes}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        raise FileError(None, f"cannot be read: {e.strerror}")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise FileError(None, f"is {_NOT_REGULAR}")
        chunks, size = [], 0
        while size <= max_bytes:
            chunk = os.read(fd, max_bytes + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
    except OSError as e:
        raise FileError(None, f"cannot be read: {e.strerror}")
    finally:
        os.close(fd)
    if (size, after.st_size, after.st_mtime_ns) != (before.st_size, before.st_size, before.st_mtime_ns):
        return None
    if size > max_bytes:
        raise FileError(None, f"too large: more than {max_bytes} bytes")
    return b"".join(chunks)


# What check_yaml_text() refuses: control characters other than newline and
# tab (C0, DEL, C1 including U+0085), the line and paragraph separators, lone
# surrogates and the noncharacters U+FFFE and U+FFFF. PyYAML and ruamel
# disagree on U+0085, U+2028 and U+2029 (line breaks to one, text to the
# other) and both refuse the rest without a line number.
_YAML_REFUSED = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff\ufffe\uffff]")


def _kind(ch: str) -> str:
    o = ord(ch)
    if o in (0x2028, 0x2029):
        return "line separator" if o == 0x2028 else "paragraph separator"
    if 0xD800 <= o <= 0xDFFF:
        return "surrogate"
    if o in (0xFFFE, 0xFFFF):
        return "noncharacter"
    return "control character"


def check_yaml_text(text: str) -> None:
    """Refuse a YAML text holding a character of _YAML_REFUSED, naming it,
    its line and its byte offset (counted in UTF-8 from the start of the
    text as the loader reads it: a CRLF line end counts as one byte).
    Raises FileError."""
    m = _YAML_REFUSED.search(text)
    if m:
        i, ch = m.start(), m.group()
        offset = len(text[:i].encode("utf-8", "surrogatepass"))
        raise FileError(text.count("\n", 0, i) + 1,
                        f"{_kind(ch)} U+{ord(ch):04X} at byte {offset} is not allowed in a YAML "
                        "file (only newline and tab)")


def control_character(text: str) -> tuple[int, str] | None:
    """(1-based line, U+XXXX) of the first control character other than
    newline and tab, or None."""
    for i, ch in enumerate(text):
        if ch not in "\n\t" and unicodedata.category(ch) == "Cc":
            return text.count("\n", 0, i) + 1, f"U+{ord(ch):04X}"
    return None


def stray_entries(base: Path, known: dict[str, set[str] | None]) -> list[tuple[str, str]]:
    """(relative name, message) for each entry of the config directory that
    no service reads. known maps a directory ("" for the top level) to the
    names expected in it; a name that is itself a key of known is an
    expected subdirectory. A directory mapped to None is not scanned (its
    own loader judges it)."""
    out: list[tuple[str, str]] = []
    for rel_dir, names in known.items():
        if names is None:
            continue
        d = Path(base) / rel_dir if rel_dir else Path(base)
        try:
            if not stat.S_ISDIR(os.lstat(d).st_mode):
                continue
            entries = []
            with os.scandir(d) as it:
                for entry in it:
                    entries.append(entry.name)
                    if len(entries) > MAX_SCAN_ENTRIES:
                        break
            more = len(entries) > MAX_SCAN_ENTRIES
            for name in sorted(entries[:MAX_SCAN_ENTRIES]):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                if name in names or rel in known:
                    continue
                if len(out) >= MAX_WARNINGS:
                    more = True
                    break
                out.append((rel, "not a file any service reads (a typo, or a leftover copy?); ignored"))
            if more:
                out.append((rel_dir or ".", f"more entries not checked (the scan stops at "
                                            f"{MAX_SCAN_ENTRIES} entries or {MAX_WARNINGS} warnings)"))
                return out
        except OSError:
            continue
    return out


# ── the YAML dialect ─────────────────────────────────────────────────────────

_INT = re.compile(r"[-+]?(?:0|[1-9][0-9]*)\Z")
_FLOAT = re.compile(r"[-+]?(?:\.[0-9]+|(?:0|[1-9][0-9]*)(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?\Z")
_NULL = frozenset({"", "~", "null", "Null", "NULL"})
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})


def resolve(value: str, style) -> object:
    """A scalar's value. style is None for a plain scalar; quoted ('"' or
    "'") and block ('|' or '>') scalars are strings."""
    if style is not None:
        return value
    if value in _NULL:
        return None
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    if _INT.match(value):
        return int(value)
    if _FLOAT.match(value):
        return float(value)
    return value


def _line(ev) -> int:
    return ev.start_mark.line + 1


class _Frame:
    __slots__ = ("node", "path", "key", "key_line", "seen")

    def __init__(self, node, path):
        self.node = node          # dict or list being filled
        self.path = path          # key path of this node
        self.key = None           # pending mapping key, or None
        self.key_line = None
        self.seen = set()


def build(events) -> tuple[dict, dict[tuple, int]]:
    """(data, key path -> 1-based line) from a YAML library's parse events.
    An empty document, or one holding only comments or a null, is {}.
    Raises FileError."""
    stack: list[_Frame] = []
    root = None
    have_root = False
    lines: dict[tuple, int] = {}
    documents = 0

    def add(value, ev):
        """Put value (or a new container) where the next node goes; return
        the path it got."""
        nonlocal root, have_root
        if not stack:
            if have_root:
                raise FileError(_line(ev), "more than one YAML document; a config file is one mapping")
            root, have_root = value, True
            return ()
        top = stack[-1]
        if isinstance(top.node, list):
            path = top.path + (str(len(top.node)),)
            top.node.append(value)
            return path
        if top.key is None:
            raise AssertionError("value without a key")     # build() only calls add() for values
        path = top.path + (top.key,)
        top.node[top.key] = value
        lines[path] = top.key_line
        top.key = None
        return path

    def expecting_key() -> bool:
        return bool(stack) and isinstance(stack[-1].node, dict) and stack[-1].key is None

    for ev in events:
        kind = type(ev).__name__
        if kind in ("StreamStartEvent", "StreamEndEvent", "DocumentEndEvent"):
            continue
        if kind == "DocumentStartEvent":
            documents += 1
            if documents > 1:
                raise FileError(_line(ev), "more than one YAML document; a config file is one mapping")
            continue
        if kind == "AliasEvent":
            raise FileError(_line(ev), "aliases (*name) are not allowed in config files")
        if getattr(ev, "anchor", None) is not None:
            raise FileError(_line(ev), "anchors (&name) are not allowed in config files")
        if getattr(ev, "tag", None) is not None:
            raise FileError(_line(ev), f"explicit tags ({shorten(ev.tag)}) are not allowed in config files")

        if kind == "ScalarEvent":
            if expecting_key():
                top = stack[-1]
                key = ev.value
                if ev.style is None and key == "<<":
                    raise FileError(_line(ev), "merge keys (<<) are not allowed in config files")
                if ev.style is None and key == "":
                    # `? ` with nothing after it. A key is a name.
                    raise FileError(_line(ev), "empty key; a key must be a name")
                if key in top.seen:
                    raise FileError(_line(ev), f"duplicate key {safe('.'.join(top.path + (key,)))}")
                top.seen.add(key)
                top.key, top.key_line = key, _line(ev)
            else:
                try:
                    value = resolve(ev.value, ev.style)
                except ValueError:      # int() refuses more than 4300 digits
                    raise FileError(_line(ev), f"number too long ({len(ev.value)} digits)")
                add(value, ev)
            continue

        if kind in ("MappingStartEvent", "SequenceStartEvent"):
            if expecting_key():
                raise FileError(_line(ev), "a key must be a plain name, not a mapping or a list")
            if len(stack) >= MAX_DEPTH:
                raise FileError(_line(ev), f"nested more than {MAX_DEPTH} levels deep")
            node = {} if kind == "MappingStartEvent" else []
            path = add(node, ev)
            stack.append(_Frame(node, path))
            continue

        if kind in ("MappingEndEvent", "SequenceEndEvent"):
            stack.pop()
            continue

        raise FileError(_line(ev), f"unexpected YAML event {kind}")

    if root is None:
        return {}, {}
    if not isinstance(root, dict):
        raise FileError(1, "must be a mapping of keys to values")
    return root, lines
