"""config/ loader for tool_service (sprint M21 step 1).

The same rules as orchestrator/config_loader.py, for the one file
tool_service reads. The two services build separate images from their own
directories, and each already pins a different YAML library (PyYAML there,
ruamel.yaml here), so each carries its own small loader rather than a
shared package.

Files (docs/features/config-directory.md has the full description):
  music.yaml   the playback verify window and the resolver's thresholds;
               schema: the Music model below.

Rules:
  - Load at startup (the first current() call). A file that fails
    validation never takes effect: at startup the built-in defaults (the
    Field defaults below, equal to the values before the move) are used;
    after a failed reload the last good values stay in effect. Either way
    the error is kept, with file and line, for GET /config/status (shown
    on the orchestrator's /services page) and is logged.
  - Reload only on an explicit POST /config/reload. No file watching.
  - The models are not frozen so tests can patch a value with
    patch.object(config_loader.current().music.verify, "timeout_s", 0);
    code never assigns to them.
"""
import hashlib
import io
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError

logger = logging.getLogger("tool_service.config")

MUSIC_FILE = "music.yaml"


def config_dir() -> Path:
    return Path(os.getenv("KRONK_CONFIG_DIR", "/config"))


# ── errors ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Problem:
    file: str            # relative to the config directory
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
# Unknown keys are errors and no type is coerced ("8" is not 8). The Field
# defaults are the built-in defaults: the values before the move.

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Verify(_Strict):
    # How long tool_service polls a player for the state a command should
    # produce (`playing` after play, paused after pause, a new track after
    # skip) before it reports failure. int | float so 8 stays 8 in the
    # sentence that names it ("within 8s").
    timeout_s: int | float = Field(8, gt=0, le=60)


_SCORE = dict(ge=0.0, le=1.0)
_BONUS = dict(ge=0.0, le=0.2)


class TypeBonus(_Strict):
    """Added to a candidate's score by its media type, to break near-ties."""
    artist: float = Field(0.02, **_BONUS)
    album: float = Field(0.015, **_BONUS)
    playlist: float = Field(0.01, **_BONUS)
    track: float = Field(0.0, **_BONUS)
    radio: float = Field(0.0, **_BONUS)


class Resolver(_Strict):
    """music_resolve.score() and main._resolve(): how a named request is
    matched against Music Assistant's search results."""
    play_min_score: float = Field(0.75, **_SCORE)
    coverage_min: float = Field(0.80, **_SCORE)
    provider_bonus: float = Field(0.05, **_BONUS)
    library_bonus: float = Field(0.03, **_BONUS)
    type_bonus: TypeBonus = Field(default_factory=TypeBonus)
    hint_min_score: float = Field(0.5, **_SCORE)


class Music(_Strict):
    verify: Verify = Field(default_factory=Verify)
    resolver: Resolver = Field(default_factory=Resolver)


@dataclass
class Config:
    music: Music
    source: str                                   # "files" | "defaults"
    loaded_at: float = field(default_factory=time.time)
    digests: dict[str, str] = field(default_factory=dict)


def defaults() -> Config:
    return Config(music=Music(), source="defaults")


# ── parsing ──────────────────────────────────────────────────────────────────

def _plain(v):
    """ruamel round-trip types → plain Python, so strict validation sees
    int, float, str, bool, dict and list."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    if isinstance(v, str):
        return str(v)
    return v


def parse_yaml(text: str, rel: str) -> tuple[dict, dict[tuple, int]]:
    """(data, key path -> 1-based line). ruamel rejects duplicate keys."""
    try:
        doc = YAML(typ="rt").load(io.StringIO(text))
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        why = e.problem or str(e)
        raise ConfigInvalid([Problem(rel, mark.line + 1 if mark else None, f"not valid YAML: {why}")])
    except YAMLError as e:
        raise ConfigInvalid([Problem(rel, None, f"not valid YAML: {e}")])
    if doc is None:
        return {}, {}
    if not isinstance(doc, CommentedMap):
        raise ConfigInvalid([Problem(rel, 1, "must be a mapping of keys to values")])
    lines: dict[tuple, int] = {}

    def walk(m, path):
        for k in m:
            p = path + (str(k),)
            lines[p] = m.lc.key(k)[0] + 1
            v = m[k]
            if isinstance(v, CommentedMap):
                walk(v, p)
            elif isinstance(v, CommentedSeq):
                pass

    walk(doc, ())
    return _plain(doc), lines


def validate(model: type[BaseModel], data: dict, lines: dict[tuple, int], rel: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = tuple(str(x) for x in err["loc"])
            if loc and loc[-1] in ("int", "float"):
                # int | float reports once per member; say it once.
                loc = loc[:-1]
                err = {**err, "msg": "Input should be a number"}
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
            if Problem(rel, line, msg) not in problems:
                problems.append(Problem(rel, line, msg))
        raise ConfigInvalid(problems)


def load(base: Path | None = None) -> Config:
    base = base or config_dir()
    path = base / MUSIC_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigInvalid([Problem(MUSIC_FILE, None, "file is missing")])
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigInvalid([Problem(MUSIC_FILE, None, f"cannot be read: {e}")])
    data, lines = parse_yaml(text, MUSIC_FILE)
    music = validate(Music, data, lines, MUSIC_FILE)
    return Config(music=music, source="files",
                  digests={MUSIC_FILE: hashlib.sha256(text.encode()).hexdigest()[:12]})


# ── the values in effect ─────────────────────────────────────────────────────

_current: Config | None = None
_status: dict = {}


def _flatten(cfg: Config) -> dict[str, object]:
    flat: dict[str, object] = {}

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            flat[f"{MUSIC_FILE}:{prefix}"] = value

    walk("", cfg.music.model_dump())
    return flat


def _changes(old: Config, new: Config) -> list[str]:
    a, b = _flatten(old), _flatten(new)
    return [f"{k}: {a.get(k)!r} -> {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def _report(problems: list[Problem], when: str, kept: str) -> None:
    for p in problems:
        logger.error("config %s rejected: %s (%s)", when, p, kept)


def start() -> Config:
    global _current, _status
    try:
        _current = load()
        _status = {"ok": True, "errors": [], "event": "startup"}
        logger.info("config loaded from %s", config_dir())
    except ConfigInvalid as e:
        _current = defaults()
        _report(e.problems, "at startup", "running on built-in defaults")
        _status = {"ok": False, "errors": [p.as_dict() for p in e.problems], "event": "startup",
                   "kept": "built-in defaults"}
    _status["at"] = time.time()
    return _current


def current() -> Config:
    return _current if _current is not None else start()


def reload() -> dict:
    global _current, _status
    old = current()
    try:
        new = load()
    except ConfigInvalid as e:
        kept = (f"last good values ({old.source}, loaded "
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(old.loaded_at))})")
        _report(e.problems, "on reload", f"kept {kept}")
        _status = {"ok": False, "errors": [p.as_dict() for p in e.problems], "event": "reload",
                   "kept": kept, "at": time.time()}
        return {"ok": False, "errors": _status["errors"], "kept": kept}
    changed = _changes(old, new)
    _current = new
    _status = {"ok": True, "errors": [], "event": "reload", "at": time.time(), "changed": changed}
    logger.info("config reloaded: %s", "; ".join(changed) or "no changes")
    return {"ok": True, "changed": changed}


def status() -> dict:
    cfg = current()
    return {"service": "tool_service", "dir": str(config_dir()), "source": cfg.source,
            "loaded_at": cfg.loaded_at, "files": cfg.digests, **_status}
