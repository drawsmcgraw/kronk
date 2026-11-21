"""config/ loader for tool_service (sprint M21 step 1).

The same rules as orchestrator/config_loader.py, for the one file
tool_service reads. The two services build separate images from their own
directories, and each already pins a different YAML library (PyYAML there,
ruamel.yaml here), so each carries its own small loader rather than a
shared package. What both must agree on (bounded reading, the YAML
dialect) is config_files.py, byte-identical in both directories: ruamel
only tokenises here, and config_files.build() decides what a value is, so
music.yaml and tuning.yaml are read as the same language.

Files (docs/features/config-directory.md has the full description):
  music.yaml   the playback verify window, the resolver's thresholds, and
               (sprint M11) the sentence parser's words and the decision
               order's policy, (sprint M23) the stripped-request guard's
               switch; schema: the Music model below.

Rules:
  - Load at startup (the first current() call). A file that fails
    validation never takes effect: at startup the built-in defaults (the
    Field defaults below, equal to the values before the move) are used;
    after a failed reload the last good values stay in effect. Either way
    the error is kept, with file and line, for GET /config/status (shown
    on the orchestrator's /services page) and is logged.
  - Reload only on an explicit POST /config/reload. No file watching.
  - No file can take a load down (fix round 1): any exception at all
    while loading becomes a config error; start() never raises and
    reload() always answers. An empty or comments-only music.yaml loads,
    with the warning "all defaults (file has no keys)".
  - Stray files under config/ are reported once, by the orchestrator,
    which scans the whole directory.
  - Every Problem's file and message and every "changed" line passes
    through config_files.safe() when it is built (fix round 2), so the
    status and the reload answer can always be encoded.
  - Reload isolation (step 2, criterion 9): PinConfig, an ASGI middleware
    in main.py, pins current() for each request, so a request in flight
    keeps the values it started with across a reload; latest() ignores the
    pin.
  - The models are not frozen so tests can patch a value with
    patch.object(config_loader.current().music.verify, "timeout_s", 0);
    code never assigns to them.
"""
import hashlib
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError

# Dual-compat, as in main.py: package import in tests, flat in the image.
try:
    from . import config_files
except ImportError:
    import config_files

logger = logging.getLogger("tool_service.config")

MUSIC_FILE = "music.yaml"
EMPTY_YAML = "all defaults (file has no keys)"


def config_dir() -> Path:
    return Path(os.getenv("KRONK_CONFIG_DIR", "/config"))


# ── errors ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Problem:
    file: str            # relative to the config directory
    line: int | None     # 1-based; None when the problem is the whole file
    message: str

    def __post_init__(self):
        # Made safe at construction (config_files.safe): nothing that cannot
        # be encoded as JSON or written to a log can exist in a Problem.
        object.__setattr__(self, "file", config_files.safe(self.file, config_files.MAX_MESSAGE))
        object.__setattr__(self, "message", config_files.safe(self.message, config_files.MAX_MESSAGE))

    @property
    def path(self) -> str:
        # "(loader)" for a failure outside any one file; else the file under config/.
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


MediaType = Literal["artist", "album", "track", "playlist", "radio"]
_WORDS = dict(min_length=1, max_length=64)


def _lower_words(v: list[str]) -> list[str]:
    for w in v:
        if not w.strip() or w != w.strip().lower() or "  " in w:
            raise ValueError(f"{w!r}: words are lowercase, single-spaced, not blank")
    if len(set(v)) != len(v):
        raise ValueError("a word is listed twice")
    return v


class Parser(_Strict):
    """music_resolve.parse_sentence() (sprint M11): the words that read a
    sentence. Lowercase, compared after music_resolve.fold()."""
    # Words that mark a style ("play some jazz", "play italian music"):
    # "some"/"something" as the request's first word, the others as its last.
    style_markers: list[str] = Field(default_factory=lambda: ["some", "something", "music", "songs", "vibes"],
                                     **_WORDS)
    # A type word names a media type and decides outright: "play the album
    # Jazz" is the album. Word -> media type.
    type_words: dict[str, MediaType] = Field(default_factory=lambda: {
        "album": "album", "albums": "album", "track": "track", "song": "track", "artist": "artist",
        "band": "artist", "playlist": "playlist", "radio": "radio", "station": "radio"})
    # Words in front of a style or before "songs by" that are not part of
    # it ("some good deep house", "top songs by").
    fillers: list[str] = Field(default_factory=lambda: [
        "a bit of", "a little bit of", "a little", "any", "more", "good", "nice", "great", "top", "best",
        "greatest", "me", "us"], **_WORDS)
    # A room or speaker clause at the end ("on the sonos move dirt") that
    # matches no real name exactly still matches one at this similarity.
    room_min_score: float = Field(0.85, **_SCORE)

    @field_validator("style_markers", "fillers")
    @classmethod
    def _words(cls, v):
        return _lower_words(v)

    @field_validator("type_words")
    @classmethod
    def _type_words(cls, v):
        _lower_words(list(v))
        return v


# The decision order's steps (music_decide.py), in their fixed order. The
# model step is placed among them by Decision.order.
DECISION_STEPS = ("markers", "genre_list", "named", "cross_type", "other_reading")
ProviderDomain = Literal["filesystem_local", "ytmusic", "pandora", "spotify", "library"]


def _providers() -> list[str]:
    return ["filesystem_local", "ytmusic"]


class ProviderOrder(_Strict):
    """Which provider wins among strong candidates, per media type (Drew,
    2026-11-11 and 11-14). A provider the sentence names goes first."""
    artist: list[ProviderDomain] = Field(default_factory=_providers, max_length=5)
    album: list[ProviderDomain] = Field(default_factory=_providers, max_length=5)
    track: list[ProviderDomain] = Field(default_factory=_providers, max_length=5)
    playlist: list[ProviderDomain] = Field(default_factory=_providers, max_length=5)
    radio: list[ProviderDomain] = Field(default_factory=lambda: ["pandora"], max_length=5)


class ModelStep(_Strict):
    """The one model question: style or named item."""
    enabled: bool = True
    model: str = Field("gemma-4-e4b", min_length=1, max_length=64)
    thinking: bool = False
    timeout_s: int | float = Field(4, gt=0, le=30)


class Decision(_Strict):
    """music_decide.decide() (sprint M11): style or named item, and what plays."""
    order: list[str] = Field(default_factory=lambda: [*DECISION_STEPS[:5], "model"])
    strong_min_score: float = Field(0.75, **_SCORE)
    title_min_score: float = Field(0.9, **_SCORE)
    provider_min_score: float = Field(0.95, ge=0.0, le=1.2)
    provider_order: ProviderOrder = Field(default_factory=ProviderOrder)
    model: ModelStep = Field(default_factory=ModelStep)

    @field_validator("order")
    @classmethod
    def _order(cls, v):
        if v.count("model") != 1 or [s for s in v if s != "model"] != list(DECISION_STEPS):
            raise ValueError(f"must be {', '.join(DECISION_STEPS)} in that order, with model "
                             "placed once anywhere among them")
        return v


STRIPPED_REFUSE_DESCRIPTION = (
    "Off: the guard refused ordinary requests for music in both end-to-end tests "
    "(docs/sprint/M23-e2e.md, M23-e2e2.md); true refuses a bare 'music' query whose sentence "
    "names something else.")


class StrippedRequest(_Strict):
    # The stripped-request guard (sprint M23, fix 2; taste.said_request).
    # Off by default and when the key is absent: the description is shown
    # on /settings when the live file carries no comment for the key
    # (production's live file predates it).
    refuse: bool = Field(False, description=STRIPPED_REFUSE_DESCRIPTION)


class Music(_Strict):
    verify: Verify = Field(default_factory=Verify)
    resolver: Resolver = Field(default_factory=Resolver)
    parser: Parser = Field(default_factory=Parser)
    decision: Decision = Field(default_factory=Decision)
    stripped_request: StrippedRequest = Field(default_factory=StrippedRequest)


@dataclass
class Config:
    music: Music
    source: str                                   # "files" | "defaults"
    loaded_at: float = field(default_factory=time.time)
    digests: dict[str, str] = field(default_factory=dict)
    warnings: list[Problem] = field(default_factory=list)
    # The parsed YAML as loaded (step 2): which keys the file sets.
    data: dict[str, dict] = field(default_factory=dict)


def defaults() -> Config:
    return Config(music=Music(), source="defaults")


# ── parsing ──────────────────────────────────────────────────────────────────

def _yaml() -> YAML:
    """ruamel, tokenising as YAML 1.1 like PyYAML in the orchestrator (fix
    round 2). Only the scanner and parser are used, never ruamel's resolver,
    so the version changes no value; it makes ruamel refuse a `?` inside a
    plain scalar in [ ] or { } as PyYAML does ("{a?b: 1}")."""
    y = YAML(typ="safe", pure=True)
    y.version = (1, 1)
    return y


def parse_yaml(text: str, rel: str) -> tuple[dict, dict[tuple, int]]:
    """(data, key path -> 1-based line), in the dialect of
    config_files.build (the orchestrator reads tuning.yaml the same way)."""
    try:
        config_files.check_yaml_text(text)
        return config_files.build(_yaml().parse(text))
    except config_files.FileError as e:
        raise ConfigInvalid([Problem(rel, e.line, e.message)])
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        why = config_files.shorten(str(e.problem or e))
        if mark:
            raise ConfigInvalid([Problem(rel, mark.line + 1, f"not valid YAML: {why}")])
        raise ConfigInvalid([Problem(rel, None, f"not valid YAML: {why}; line unknown")])
    except YAMLError as e:
        raise ConfigInvalid([Problem(rel, None, f"not valid YAML: {config_files.shorten(str(e))}; "
                                                "line unknown")])
    except Exception as e:              # e.g. ValueError from a "\UFFFFFFFF" escape
        raise ConfigInvalid([Problem(rel, None, config_files.unparsable(e))])


def validate(model: type[BaseModel], data: dict, lines: dict[tuple, int], rel: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems, seen = [], set()
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
            name = config_files.safe(".".join(loc)) or "(top level)"    # a key is shortened like a value
            if err["type"] == "extra_forbidden":
                msg = f"{name}: unknown key"
            else:
                got = err.get("input")
                msg = f"{name}: {err['msg']}" + (f" (got {config_files.quote(got)})" if not isinstance(got, dict) else "")
            problem = Problem(rel, line, msg)
            if problem not in seen:         # a set: a file of thousands of keys stays linear
                seen.add(problem)
                problems.append(problem)
        raise ConfigInvalid(problems)


def load(base: Path | None = None) -> Config:
    """Read and validate music.yaml. Raises ConfigInvalid and nothing else:
    any other exception while loading becomes a problem for the file."""
    base = base or config_dir()
    try:
        text = config_files.read_text(base, MUSIC_FILE, config_files.MAX_YAML_BYTES)
        data, lines = parse_yaml(text, MUSIC_FILE)
        music = validate(Music, data, lines, MUSIC_FILE)
    except config_files.FileError as e:
        raise ConfigInvalid([Problem(MUSIC_FILE, e.line, e.message)])
    except ConfigInvalid:
        raise
    except Exception as e:              # RecursionError, MemoryError, a library bug
        raise ConfigInvalid([Problem(MUSIC_FILE, None, config_files.unexpected(e))])
    warnings = [] if data else [Problem(MUSIC_FILE, None, EMPTY_YAML)]
    return Config(music=music, source="files", warnings=warnings, data={MUSIC_FILE: data},
                  digests={MUSIC_FILE: hashlib.sha256(text.encode()).hexdigest()[:12]})


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
# The config a request started with (PinConfig); None outside a request.
_pinned: ContextVar["Config | None"] = ContextVar("kronk_ts_config_pinned", default=None)


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
    return [config_files.safe(f"{k}: {config_files.quote(a.get(k))} -> {config_files.quote(b.get(k))}",
                              config_files.MAX_MESSAGE)
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def _report(problems: list[Problem], when: str, effect: str) -> None:
    for p in problems:
        logger.error("config %s rejected: %s (%s)", when, p, effect)


def _warn(warnings: list[Problem], when: str) -> None:
    for w in warnings:
        logger.warning("config %s: %s", when, w)


def start() -> Config:
    """Load at startup: the file if valid, else the built-in defaults.
    Never raises."""
    global _current, _status
    cfg, problems = _try_load()
    if cfg is not None:
        _current = cfg
        _status = {"ok": True, "errors": [], "event": "startup",
                   "warnings": [w.as_dict() for w in cfg.warnings]}
        logger.info("config loaded from %s", config_dir())
        _warn(cfg.warnings, "at startup")
    else:
        _current = defaults()
        _report(problems, "at startup", "running on built-in defaults")
        _status = {"ok": False, "errors": [p.as_dict() for p in problems], "event": "startup",
                   "kept": "built-in defaults", "warnings": []}
    _status["at"] = time.time()
    return _current


def latest() -> Config:
    """The config in effect for new requests, ignoring any pin."""
    return _current if _current is not None else start()


def current() -> Config:
    """The config this request started with (see PinConfig), else latest()."""
    pinned = _pinned.get()
    return pinned if pinned is not None else latest()


@contextmanager
def pinned(cfg: "Config | None" = None):
    """Pin current() to cfg (default: the config in effect now) inside the block."""
    token = _pinned.set(cfg if cfg is not None else latest())
    try:
        yield
    finally:
        _pinned.reset(token)


class PinConfig:
    """ASGI middleware: each HTTP request runs with current() pinned to the
    config in effect when it arrived, until its response is complete."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        with pinned():
            await self.app(scope, receive, send)


def reload() -> dict:
    global _current, _status
    old = latest()
    new, problems = _try_load()
    if new is None:
        kept = (f"last good values ({old.source}, loaded "
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(old.loaded_at))})")
        _report(problems, "on reload", f"keeping {kept}")
        _status = {"ok": False, "errors": [p.as_dict() for p in problems], "event": "reload",
                   "kept": kept, "at": time.time(), "warnings": []}
        return {"ok": False, "errors": _status["errors"], "kept": kept}
    changed = _changes(old, new)
    _current = new
    warnings = [w.as_dict() for w in new.warnings]
    _status = {"ok": True, "errors": [], "event": "reload", "at": time.time(), "changed": changed,
               "warnings": warnings}
    logger.info("config reloaded: %s", "; ".join(changed) or "no changes")
    _warn(new.warnings, "on reload")
    result = {"ok": True, "changed": changed}
    if warnings:
        result["warnings"] = warnings
    return result


def status() -> dict:
    cfg = latest()
    return {"service": "tool_service", "dir": config_files.safe(config_dir(), config_files.MAX_MESSAGE),
            "source": cfg.source,
            "loaded_at": cfg.loaded_at, "files": cfg.digests, **_status}
