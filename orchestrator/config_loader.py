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
  - No file can take a load down (fix round 1): reading is bounded and
    YAML is read in the restricted dialect of config_files.py, and any
    exception at all while loading a file becomes a config error for that
    file. start() never raises and reload() always answers.
  - Warnings, not errors: a YAML file with no keys (every value at its
    default) and an entry under config/ that no service reads. They are on
    /api/config and in the reload answer; the load still succeeds.
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

import config_files
import prompt_defaults
from events import emit

logger = logging.getLogger(__name__)

PROMPTS_DIR = "prompts"
TUNING_FILE = "tuning.yaml"
# Read by tool_service (tool_service/config_loader.py); listed so the
# stray-file scan, done here once for the whole directory, knows it.
TOOL_SERVICE_FILES = frozenset({"music.yaml"})
EMPTY_YAML = "all defaults (file has no keys)"
# A prompt may name these, written {{name}}; agents.apply_config fills them.
PLACEHOLDERS = frozenset({"max_rounds"})
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HEADER_OPEN, _HEADER_CLOSE = "<!--", "-->"
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)


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

    @property
    def path(self) -> str:
        # "(env NAME)" for an env override; else the file under config/.
        return self.file if self.file.startswith("(") else f"config/{self.file}"

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"

    def as_dict(self) -> dict:
        return {"file": self.path, "line": self.line, "message": self.message}


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


class WeatherContext(_Strict):
    # The cached forecast stops being injected into prompts past this age.
    max_age_s: int = Field(7200, ge=60, le=86400)


_SECONDS = dict(gt=0, le=600)


class PerToolTimeouts(_Strict):
    """HTTP timeout (seconds) of one tool's call, by tool name. One field
    per tool in tools.TOOL_DEFINITIONS (tests/test_config_loader.py checks
    the names); None uses ToolTimeouts.default_s."""
    get_weather: float | None = Field(None, **_SECONDS)
    web_search: float | None = Field(None, **_SECONDS)
    fetch_url: float | None = Field(None, **_SECONDS)
    shopping_list_view: float | None = Field(None, **_SECONDS)
    shopping_list_add: float | None = Field(None, **_SECONDS)
    shopping_list_remove: float | None = Field(None, **_SECONDS)
    shopping_list_clear: float | None = Field(None, **_SECONDS)
    get_kronk_context: float | None = Field(None, **_SECONDS)
    generate_diagram: float | None = Field(30, **_SECONDS)
    query_hottub: float | None = Field(5, **_SECONDS)
    solar_status: float | None = Field(None, **_SECONDS)
    solar_detail: float | None = Field(None, **_SECONDS)
    solar_energy: float | None = Field(None, **_SECONDS)
    play_music: float | None = Field(20, **_SECONDS)
    control_music: float | None = Field(20, **_SECONDS)
    rate_music: float | None = Field(25, **_SECONDS)
    remember_music_preference: float | None = Field(None, **_SECONDS)
    now_playing: float | None = Field(15, **_SECONDS)
    update_magicmirror: float | None = Field(30, **_SECONDS)
    remote_exec: float | None = Field(35, **_SECONDS)
    http_check: float | None = Field(10, **_SECONDS)
    news_brief: float | None = Field(90, **_SECONDS)


class ToolTimeouts(_Strict):
    default_s: float = Field(15, **_SECONDS)
    per_tool: PerToolTimeouts = Field(default_factory=PerToolTimeouts)

    def for_tool(self, name: str) -> float:
        value = getattr(self.per_tool, name) if name in type(self.per_tool).model_fields else None
        return self.default_s if value is None else value


class Tuning(_Strict):
    agent_loop: AgentLoop = Field(default_factory=AgentLoop)
    weather_context: WeatherContext = Field(default_factory=WeatherContext)
    tool_timeouts: ToolTimeouts = Field(default_factory=ToolTimeouts)


# Env vars that set a value before the move keep working, and win over the
# file: env > file > built-in default. Only these; no new env vars.
ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "WEATHER_CTX_MAX_AGE_S": ("weather_context", "max_age_s"),
}


def _apply_env(tuning: "Tuning") -> tuple["Tuning", dict[str, str], list["Problem"]]:
    """tuning with each set env override applied and validated on its own,
    so an error names the variable, not a file line."""
    env: dict[str, str] = {}
    problems: list[Problem] = []
    for var, path in ENV_OVERRIDES.items():
        raw = os.getenv(var)
        if raw is None or raw.strip() == "":
            continue
        where = f"(env {var})"
        try:
            value = int(raw.strip())
        except ValueError:
            problems.append(Problem(where, None, f"not an integer: {raw!r}"))
            continue
        data = tuning.model_dump()
        node = data
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        try:
            tuning = validate(Tuning, data, {}, where)
            env[var] = raw
        except ConfigInvalid as e:
            problems += [Problem(where, None, p.message) for p in e.problems]
    return tuning, env, problems


@dataclass
class Config:
    prompts: dict[str, str]                       # name -> prompt text (placeholders unfilled)
    tuning: "Tuning"
    source: str                                   # "files" | "defaults"
    loaded_at: float = field(default_factory=time.time)
    digests: dict[str, str] = field(default_factory=dict)   # file -> sha256[:12]
    env: dict[str, str] = field(default_factory=dict)       # env overrides in effect
    warnings: list["Problem"] = field(default_factory=list)  # loaded, but worth a look


def defaults() -> Config:
    """The built-in config: today's values, no files read."""
    tuning, env, _ = _apply_env(Tuning())    # a bad env var is reported by load()
    return Config(prompts=dict(prompt_defaults.PROMPTS), tuning=tuning, source="defaults", env=env)


# ── parsing ──────────────────────────────────────────────────────────────────

def _read(base: Path, rel: str, max_bytes: int) -> str:
    """Bounded read (config_files.read_text): regular file, no symlink,
    at most max_bytes."""
    try:
        return config_files.read_text(base, rel, max_bytes)
    except config_files.FileError as e:
        raise ConfigInvalid([Problem(rel, e.line, e.message)])


def parse_prompt(text: str, rel: str) -> str:
    """The prompt in a prompt file: the text after its header comment,
    without leading and trailing blank lines."""
    bad = config_files.control_character(text)
    if bad:
        raise ConfigInvalid([Problem(rel, bad[0], f"control character {bad[1]} is not allowed "
                                                  "in a prompt (only newline and tab)")])
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
    if not _COMMENT.sub("", body).strip():
        raise ConfigInvalid([Problem(rel, header_lines, "prompt is empty (only comments after the header)")])
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
    """(data, key path -> 1-based line), in the dialect of config_files.build:
    no anchors, aliases, merge keys or tags, bounded nesting, duplicate keys
    are errors, and scalars resolved the same way as in tool_service."""
    try:
        return config_files.build(yaml.parse(text, Loader=yaml.SafeLoader))
    except config_files.FileError as e:
        raise ConfigInvalid([Problem(rel, e.line, e.message)])
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        why = getattr(e, "problem", None) or str(e)
        raise ConfigInvalid([Problem(rel, mark.line + 1 if mark else None,
                                     f"not valid YAML: {config_files.shorten(str(why))}")])


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
                msg = f"{name}: {err['msg']}" + (f" (got {config_files.quote(got)})" if not isinstance(got, dict) else "")
            problems.append(Problem(rel, line, msg))
        raise ConfigInvalid(problems)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _load_prompt(base: Path, rel: str) -> tuple[str, str]:
    text = _read(base, rel, config_files.MAX_PROMPT_BYTES)
    return parse_prompt(text, rel), _digest(text)


def _load_tuning(base: Path) -> tuple["Tuning", str, bool]:
    text = _read(base, TUNING_FILE, config_files.MAX_YAML_BYTES)
    data, lines = parse_yaml(text, TUNING_FILE)
    return validate(Tuning, data, lines, TUNING_FILE), _digest(text), bool(data)


def _strays(base: Path) -> tuple[list[Problem], list[Problem]]:
    """(errors, warnings) for entries under config/ that no service reads.
    An unknown prompts/*.md is an error (it looks like a prompt the
    operator expects to take effect); anything else is a warning."""
    errors, warnings = [], []
    prompt_files = {f"{n}.md" for n in prompt_defaults.PROMPTS}
    known = {"": {TUNING_FILE, PROMPTS_DIR, *TOOL_SERVICE_FILES}, PROMPTS_DIR: prompt_files}
    for rel, message in config_files.stray_entries(base, known):
        if rel.startswith(f"{PROMPTS_DIR}/") and rel.endswith(".md"):
            errors.append(Problem(rel, None, "unknown prompt file: no agent of that name "
                                             f"(known: {', '.join(prompt_defaults.PROMPTS)})"))
        else:
            warnings.append(Problem(rel, None, message))
    return errors, warnings


def load(base: Path | None = None) -> Config:
    """Read and validate every file. Raises ConfigInvalid with every
    problem found, and nothing else: any other exception while loading a
    file becomes a problem for that file."""
    base = base or config_dir()
    problems: list[Problem] = []
    warnings: list[Problem] = []
    prompts: dict[str, str] = {}
    digests: dict[str, str] = {}

    for name in prompt_defaults.PROMPTS:
        rel = f"{PROMPTS_DIR}/{name}.md"
        try:
            prompts[name], digests[rel] = _load_prompt(base, rel)
        except ConfigInvalid as e:
            problems += e.problems
        except Exception as e:          # RecursionError, MemoryError, a library bug
            problems.append(Problem(rel, None, config_files.unexpected(e)))

    tuning = None
    try:
        tuning, digests[TUNING_FILE], has_keys = _load_tuning(base)
        if not has_keys:
            warnings.append(Problem(TUNING_FILE, None, EMPTY_YAML))
    except ConfigInvalid as e:
        problems += e.problems
    except Exception as e:
        problems.append(Problem(TUNING_FILE, None, config_files.unexpected(e)))

    try:
        stray_errors, stray_warnings = _strays(base)
        problems += stray_errors
        warnings += stray_warnings
    except Exception as e:
        warnings.append(Problem(".", None, f"stray-file scan failed: {config_files.unexpected(e)}"))

    env: dict[str, str] = {}
    if tuning is not None:
        tuning, env, env_problems = _apply_env(tuning)
        problems += env_problems

    if problems:
        raise ConfigInvalid(problems)
    return Config(prompts=prompts, tuning=tuning, source="files", digests=digests, env=env,
                  warnings=warnings)


def _try_load() -> tuple[Config | None, list[Problem]]:
    """load(), never raising: (config, []) or (None, problems)."""
    try:
        return load(), []
    except ConfigInvalid as e:
        return None, config_files.cap_problems(
            e.problems, lambda first, n: Problem(first.file, None, f"and {n} more problems, not shown"))
    except Exception as e:
        return None, [Problem("(loader)", None, config_files.unexpected(e))]


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


def _report(problems: list[Problem], when: str, effect: str) -> None:
    for p in problems:
        logger.error("config %s rejected: %s (%s)", when, p, effect)
    emit("config_error", when=when, effect=effect, problems=[str(p) for p in problems][:10])


def _warn(warnings: list[Problem], when: str) -> None:
    for w in warnings:
        logger.warning("config %s: %s", when, w)


def _set(cfg: Config) -> None:
    global _current
    _current = cfg
    for fn in _listeners:
        fn(cfg)


def start() -> Config:
    """Load at startup: the files if valid, else the built-in defaults."""
    global _status
    cfg, problems = _try_load()
    if cfg is not None:
        _status = {"ok": True, "errors": [], "event": "startup",
                   "warnings": [w.as_dict() for w in cfg.warnings]}
        logger.info("config loaded from %s (%d prompts)", config_dir(), len(cfg.prompts))
        _warn(cfg.warnings, "at startup")
    else:
        cfg = defaults()
        _report(problems, "at startup", "running on built-in defaults")
        _status = {"ok": False, "errors": [p.as_dict() for p in problems], "event": "startup",
                   "kept": "built-in defaults", "warnings": []}
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
    new, problems = _try_load()
    if new is None:
        kept = f"last good values ({old.source}, loaded {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(old.loaded_at))})"
        _report(problems, "on reload", f"keeping {kept}")
        _status = {"ok": False, "errors": [p.as_dict() for p in problems], "event": "reload",
                   "kept": kept, "at": time.time(), "warnings": []}
        return {"ok": False, "errors": _status["errors"], "kept": kept}
    changed = _changes(old, new)
    _set(new)
    warnings = [w.as_dict() for w in new.warnings]
    _status = {"ok": True, "errors": [], "event": "reload", "at": time.time(), "changed": changed,
               "warnings": warnings}
    emit("config_reloaded", changed=changed[:20])
    logger.info("config reloaded: %s", "; ".join(changed) or "no changes")
    _warn(new.warnings, "on reload")
    result = {"ok": True, "changed": changed}
    if warnings:
        result["warnings"] = warnings
    return result


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
