"""Bounded reading of config/ files and the one YAML dialect both services
accept (sprint M21 step 1, fix round 1).

This file is byte-identical in orchestrator/ and tool_service/. Each service
builds its image from its own directory, so the file is copied rather than
shared; tests/test_config_hardening.py fails if the two copies differ. It
imports no YAML library. Each loader hands build() the parse events of its
own library (PyYAML in the orchestrator, ruamel.yaml in tool_service); both
libraries emit the same event stream, and this module, not the library,
decides what a scalar means. So both services read exactly one language.

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

Errors are raised as FileError(line, message); each loader turns them into
its own Problem with the file name.
"""
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


class FileError(Exception):
    def __init__(self, line: int | None, message: str):
        self.line = line
        self.message = message
        super().__init__(message)


def shorten(text: str) -> str:
    """text, cut to MAX_QUOTE characters: an error message must stay small
    whatever the file held."""
    return text if len(text) <= MAX_QUOTE else text[:MAX_QUOTE - 1] + "…"


def quote(value) -> str:
    """repr(value), shortened."""
    return shorten(repr(value))


def unexpected(e: BaseException) -> str:
    """The message for an exception no check anticipated."""
    detail = str(e)
    return f"could not be loaded ({type(e).__name__}{': ' + shorten(detail) if detail else ''})"


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


def read_text(base: Path, rel: str, max_bytes: int) -> str:
    """The text of base/rel, bounded. Raises FileError."""
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
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FileError(None, f"is {_NOT_REGULAR}")
        chunks, size = [], 0
        while size <= max_bytes:
            chunk = os.read(fd, max_bytes + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as e:
        raise FileError(None, f"cannot be read: {e.strerror}")
    finally:
        os.close(fd)
    if size > max_bytes:
        raise FileError(None, f"too large: more than {max_bytes} bytes")
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as e:
        raise FileError(None, f"cannot be read: {e}")
    # Universal newlines, as Path.read_text did before this module.
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
                if key in top.seen:
                    raise FileError(_line(ev), f"duplicate key {'.'.join(top.path + (key,))}")
                top.seen.add(key)
                top.key, top.key_line = key, _line(ev)
            else:
                add(resolve(ev.value, ev.style), ev)
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
