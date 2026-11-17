"""config/ loader for the orchestrator (sprint M21 step 1).

Prompts and tuning values live as plain files in the repo's config/
directory, bind-mounted read-only at /config (KRONK_CONFIG_DIR). This
module reads them, validates them against the schema below, and holds the
values in effect.

Files (docs/features/config-directory.md has the full description):
  prompts/<name>.md  one per agent in agents.AGENTS and the coordinator.
                     Starts with a <!-- header --> comment, which is
                     stripped; the rest, minus leading and trailing blank
                     lines, is the system prompt, byte for byte.
  tuning.yaml        agent-loop budgets and the other orchestrator
                     numbers; schema: the Tuning model below.

Rules:
  - Load at startup (the first current() call). A file that fails
    validation never takes effect: at startup the whole config falls back
    to the built-in defaults (prompt_defaults.py and the Field defaults
    below, equal to the values before the move); after a failed reload
    the last good config stays in effect. Either way the error is kept,
    with file and line, for /api/config (the /services page) and is
    logged.
  - All or nothing: one bad file rejects the whole load, so prompts and
    tuning never come from two different loads.
  - Reload only on an explicit POST /config/reload (main.py). No file
    watching.
  - Precedence where a value has more than one source: env var (only the
    ones that existed before the move) > file > built-in default.
"""
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import prompt_defaults
from events import emit

logger = logging.getLogger(__name__)

PROMPTS_DIR = "prompts"
TUNING_FILE = "tuning.yaml"
# A prompt may name these, written {{name}}; agents.apply_config fills them.
PLACEHOLDERS = frozenset({"max_rounds"})
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HEADER_OPEN, _HEADER_CLOSE = "<!--", "-->"


def fill(text: str, **values) -> str:
    """Replace each {{name}} in a prompt with values[name]."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)


def config_dir() -> Path:
    return Path(os.getenv("KRONK_CONFIG_DIR", "/config"))


# ── errors ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Problem:
    file: str            # relative to the config directory, e.g. "prompts/home.md"
    line: int | None     # 1-based; None when the problem is the whole file
    message: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"config/{where}: {self.message}"

    def as_dict(self) -> dict:
        return {"file": f"config/{self.file}", "line": self.line, "message": self.message}


class ConfigInvalid(Exception):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


# ── schema ───────────────────────────────────────────────────────────────────
# Every model forbids unknown keys and coerces nothing ("3" is not 3).
# Field defaults are the built-in defaults: the values before the move.

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_ROUNDS = dict(ge=1, le=20)


class MaxRounds(_Strict):
    """Tool-use rounds per request. One per agent in agents.AGENTS and the
    coordinator (tests/test_config_loader.py checks the names); an agent
    left at None uses `default`."""
    default: int = Field(3, **_ROUNDS)
    coordinator: int | None = Field(None, **_ROUNDS)
    research: int | None = Field(8, **_ROUNDS)
    home: int | None = Field(None, **_ROUNDS)
    assistant: int | None = Field(None, **_ROUNDS)
    coding: int | None = Field(None, **_ROUNDS)
    talkie: int | None = Field(None, **_ROUNDS)
    devops: int | None = Field(None, **_ROUNDS)

    def for_agent(self, name: str) -> int:
        value = getattr(self, name, None) if name in type(self).model_fields else None
        return self.default if value is None else value


class AgentLoop(_Strict):
    max_rounds: MaxRounds = Field(default_factory=MaxRounds)
    repeat_tool_nudge_at: int = Field(3, ge=2, le=20)


class Tuning(_Strict):
    agent_loop: AgentLoop = Field(default_factory=AgentLoop)


@dataclass
class Config:
    prompts: dict[str, str]                       # name -> prompt text (placeholders unfilled)
    tuning: "Tuning"
    source: str                                   # "files" | "defaults"
    loaded_at: float = field(default_factory=time.time)
    digests: dict[str, str] = field(default_factory=dict)   # file -> sha256[:12]
    env: dict[str, str] = field(default_factory=dict)       # env overrides in effect


def defaults() -> Config:
    """The built-in config: today's values, no files read."""
    return Config(prompts=dict(prompt_defaults.PROMPTS), tuning=Tuning(), source="defaults")


# ── parsing ──────────────────────────────────────────────────────────────────

def _read(base: Path, rel: str) -> str:
    path = base / rel
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigInvalid([Problem(rel, None, "file is missing")])
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigInvalid([Problem(rel, None, f"cannot be read: {e}")])


def parse_prompt(text: str, rel: str) -> str:
    """The prompt in a prompt file: the text after its header comment,
    without leading and trailing blank lines."""
    if not text.startswith(_HEADER_OPEN):
        raise ConfigInvalid([Problem(rel, 1, "must start with a <!-- header comment -->")])
    end = text.find(_HEADER_CLOSE)
    if end < 0:
        raise ConfigInvalid([Problem(rel, 1, "header comment is never closed with -->")])
    body_start = end + len(_HEADER_CLOSE)
    body = text[body_start:].strip("\n")
    header_lines = text[:body_start].count("\n") + 1
    if not body.strip():
        raise ConfigInvalid([Problem(rel, header_lines, "prompt is empty")])
    problems = []
    for m in _PLACEHOLDER.finditer(text, body_start):
        if m.group(1) not in PLACEHOLDERS:
            problems.append(Problem(rel, text.count("\n", 0, m.start()) + 1,
                                    f"unknown placeholder {{{{{m.group(1)}}}}}; "
                                    f"known: {', '.join(sorted(PLACEHOLDERS))}"))
    if problems:
        raise ConfigInvalid(problems)
    return body


def parse_yaml(text: str, rel: str) -> tuple[dict, dict[tuple, int]]:
    """(data, key path -> 1-based line). Duplicate keys are an error:
    PyYAML would silently keep the last one."""
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        why = getattr(e, "problem", None) or str(e)
        raise ConfigInvalid([Problem(rel, mark.line + 1 if mark else None, f"not valid YAML: {why}")])
    if data is None:
        data, node = {}, None
    if not isinstance(data, dict):
        raise ConfigInvalid([Problem(rel, 1, "must be a mapping of keys to values")])
    lines: dict[tuple, int] = {}
    problems: list[Problem] = []

    def walk(n, path):
        if not isinstance(n, yaml.MappingNode):
            return
        seen = set()
        for k, v in n.value:
            key = k.value
            p = path + (key,)
            if key in seen:
                problems.append(Problem(rel, k.start_mark.line + 1,
                                        f"duplicate key {'.'.join(p)}"))
            seen.add(key)
            lines[p] = k.start_mark.line + 1
            walk(v, p)

    walk(node, ())
    if problems:
        raise ConfigInvalid(problems)
    return data, lines


def validate(model: type[BaseModel], data: dict, lines: dict[tuple, int], rel: str):
    """model.model_validate(data), with each error pinned to a file line."""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = tuple(str(x) for x in err["loc"])
            line = None
            for i in range(len(loc), 0, -1):
                if loc[:i] in lines:
                    line = lines[loc[:i]]
                    break
            name = ".".join(loc) or "(top level)"
            if err["type"] == "extra_forbidden":
                msg = f"{name}: unknown key"
            else:
                got = err.get("input")
                msg = f"{name}: {err['msg']}" + (f" (got {got!r})" if not isinstance(got, dict) else "")
            problems.append(Problem(rel, line, msg))
        raise ConfigInvalid(problems)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load(base: Path | None = None) -> Config:
    """Read and validate every file. Raises ConfigInvalid with every
    problem found."""
    base = base or config_dir()
    problems: list[Problem] = []
    prompts: dict[str, str] = {}
    digests: dict[str, str] = {}

    pdir = base / PROMPTS_DIR
    for name in prompt_defaults.PROMPTS:
        rel = f"{PROMPTS_DIR}/{name}.md"
        try:
            text = _read(base, rel)
            prompts[name] = parse_prompt(text, rel)
            digests[rel] = _digest(text)
        except ConfigInvalid as e:
            problems += e.problems
    if pdir.is_dir():
        for p in sorted(pdir.glob("*.md")):
            if p.stem not in prompt_defaults.PROMPTS:
                problems.append(Problem(f"{PROMPTS_DIR}/{p.name}", None,
                                        "unknown prompt file: no agent of that name "
                                        f"(known: {', '.join(prompt_defaults.PROMPTS)})"))

    tuning = None
    try:
        text = _read(base, TUNING_FILE)
        data, lines = parse_yaml(text, TUNING_FILE)
        tuning = validate(Tuning, data, lines, TUNING_FILE)
        digests[TUNING_FILE] = _digest(text)
    except ConfigInvalid as e:
        problems += e.problems

    if problems:
        raise ConfigInvalid(problems)
    return Config(prompts=prompts, tuning=tuning, source="files", digests=digests)


# ── the values in effect ─────────────────────────────────────────────────────

_current: Config | None = None
_status: dict = {}
_listeners: list = []


def _flatten(cfg: Config) -> dict[str, object]:
    flat: dict[str, object] = {f"prompts/{n}.md": t for n, t in cfg.prompts.items()}

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}", v)
        else:
            flat[prefix] = value

    for k, v in cfg.tuning.model_dump().items():
        walk(f"{TUNING_FILE}:{k}", v)
    return flat


def _changes(old: Config, new: Config) -> list[str]:
    a, b = _flatten(old), _flatten(new)
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            if k.startswith("prompts/"):
                out.append(f"{k}: changed ({len(a.get(k) or '')} -> {len(b.get(k) or '')} chars)")
            else:
                out.append(f"{k}: {a.get(k)!r} -> {b.get(k)!r}")
    return out


def _report(problems: list[Problem], when: str, kept: str) -> None:
    for p in problems:
        logger.error("config %s rejected: %s (%s)", when, p, kept)
    emit("config_error", when=when, kept=kept, problems=[str(p) for p in problems][:10])


def _set(cfg: Config) -> None:
    global _current
    _current = cfg
    for fn in _listeners:
        fn(cfg)


def start() -> Config:
    """Load at startup: the files if valid, else the built-in defaults."""
    global _status
    try:
        cfg = load()
        _status = {"ok": True, "errors": [], "event": "startup"}
        logger.info("config loaded from %s (%d prompts)", config_dir(), len(cfg.prompts))
    except ConfigInvalid as e:
        cfg = defaults()
        _report(e.problems, "at startup", "running on built-in defaults")
        _status = {"ok": False, "errors": [p.as_dict() for p in e.problems], "event": "startup",
                   "kept": "built-in defaults"}
    _status["at"] = time.time()
    _set(cfg)
    return cfg


def current() -> Config:
    return _current if _current is not None else start()


def on_change(fn) -> None:
    """Call fn(config) now and after every successful reload."""
    cfg = current()
    _listeners.append(fn)
    fn(cfg)


def reload() -> dict:
    """Re-read every file. Valid: it takes effect, and the answer lists
    what changed. Invalid: nothing changes, and the answer has the errors."""
    global _status
    old = current()
    try:
        new = load()
    except ConfigInvalid as e:
        kept = f"last good values ({old.source}, loaded {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(old.loaded_at))})"
        _report(e.problems, "on reload", f"kept {kept}")
        _status = {"ok": False, "errors": [p.as_dict() for p in e.problems], "event": "reload",
                   "kept": kept, "at": time.time()}
        return {"ok": False, "errors": _status["errors"], "kept": kept}
    changed = _changes(old, new)
    _set(new)
    _status = {"ok": True, "errors": [], "event": "reload", "at": time.time(), "changed": changed}
    emit("config_reloaded", changed=changed[:20])
    logger.info("config reloaded: %s", "; ".join(changed) or "no changes")
    return {"ok": True, "changed": changed}


def status() -> dict:
    """For /api/config: what is in effect, from where, and the last error."""
    cfg = current()
    return {
        "service": "orchestrator",
        "dir": str(config_dir()),
        "source": cfg.source,
        "loaded_at": cfg.loaded_at,
        "files": cfg.digests,
        "env_overrides": cfg.env,
        **_status,
    }
