#!/usr/bin/env python3
"""Build the Whisper vocabulary prompt (sprint M13).

Writes one short line of household names (rooms, Music Assistant players,
Pandora stations, top artists) as an EnvironmentFile for the dev Whisper
instance (`systemd/wyoming-whisper-dev.service`), which passes it to
`--initial-prompt`. Production's Whisper does not read it.

    scripts/build_stt_prompt.py [--out FILE] [--print]
        [--max-tokens 100] [--artists 200] [--restart-unit UNIT]

Sources, all read-only and all on loopback:
  - HA area names and the friendly names of HA's music_assistant
    media_player entities (HA's template API; HA_TOKEN from the
    environment, else the HA_TOKEN line of the repo's .env);
  - Pandora stations in the MA library (tool_service GET /music/stations);
  - top artists (tool_service GET /music/taste/profile).
The tool_service default is the dev one (127.0.0.1:8103), which serves
production's taste DB read-only.

Names only: no command verbs are added. A name that itself contains a word
of the spoken command forms (COMMAND_WORDS) is kept and listed in the
summary line, so it can be reviewed.

The budget rule (when not every name fits in --max-tokens): Kronk and the
rooms first, in full. Then stations (most played first), artists (highest
score first) and players take turns, one name each, in that order, until
all three lists are spent. Each name is kept when the whole line, with it,
still fits; a name that does not fit is dropped and the next one is tried.
Names already kept (case and punctuation folded) are not repeated, so an
artist whose station is already in ("X Radio" is kept as "X") is not added
twice. A station's trailing " Radio" is dropped: every Pandora station name
ends in it, and the distinctive words are the ones before it.
The line lists the kept names by category (Kronk, rooms, stations, artists,
players) and alphabetically inside each category, so it changes only when
the kept names change, not when play counts reorder them.

Tokens are counted as faster-whisper encodes the prompt: the model's own
tokenizer (tokenizer.json in its snapshot) on " " + prompt, no special
tokens.

The file is written only when its content changes (temporary file in the
same directory, then rename). With --restart-unit, a changed file restarts
that unit with `systemctl --user try-restart` (a stopped unit stays
stopped). Production's unit is refused, and so is any unit whose name does
not end in "-dev.service".

Exit status: 0 written or unchanged; 1 a source failed or returned nothing
(the old file is not touched); 2 usage or a refused unit; 3 the tokenizer
could not be loaded; 4 the restart failed (the new file is in place).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

HOME = Path.home()
REPO_DIR = Path(os.environ.get("KRONK_REPO_DIR", Path(__file__).resolve().parent.parent))
DEFAULT_OUT = HOME / "services/wyoming-whisper/dev-prompt.env"
DEFAULT_TOKENIZER = next(
    iter(sorted((HOME / "services/wyoming-whisper/models/"
                 "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots").glob("*/tokenizer.json"))),
    None,
)
DEFAULT_HA_URL = "http://127.0.0.1:8123"
DEFAULT_TOOL_SERVICE = "http://127.0.0.1:8103"
PRODUCTION_UNIT = "wyoming-whisper.service"
ENV_KEY = "WHISPER_INITIAL_PROMPT"
FIXED_NAMES = ("Kronk",)           # plan M19 step 4: the lead-in's name
HTTP_TIMEOUT_S = 15

# Words of the spoken command forms (orchestrator/command_forms.py and the
# music verbs in routing._MUSIC_RE). tests/test_build_stt_prompt.py checks
# this set against those patterns, so a new command word cannot be missed.
COMMAND_WORDS = frozenset({
    "play", "pause", "stop", "skip", "next", "resume", "unpause", "shuffle",
    "volume", "thumb", "thumbs", "louder", "quieter", "turn", "shut", "queue",
    "listen", "put", "throw", "start", "like", "love", "hate", "dislike",
    "never", "again", "vote", "upvote", "downvote", "mute", "unmute",
    "remember", "forget", "rate", "ask", "tell", "try", "different", "else",
    "something", "more", "less", "up", "down", "playing", "playback",
})

AREAS_TEMPLATE = "{{ areas() | map('area_name') | list | to_json }}"
PLAYERS_TEMPLATE = (
    "{{ integration_entities('music_assistant') | select('match', 'media_player[.]')"
    " | map('state_attr', 'friendly_name') | reject('none') | list | to_json }}"
)

# Characters that systemd's EnvironmentFile parser treats specially inside
# double quotes are dropped from names; control characters become spaces.
_UNSAFE = re.compile(r'["\\$`]')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


class SourceError(Exception):
    """A source was unreachable, answered badly, or returned nothing."""


class RefusedUnit(Exception):
    """--restart-unit named a unit this script may not restart."""


# ── HTTP (injectable for tests) ─────────────────────────────────────────────

Fetch = Callable[[str, str, dict, bytes | None], object]


def http_json(method: str, url: str, headers: dict, body: bytes | None) -> object:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(fetch: Fetch, what: str, method: str, url: str, headers: dict | None = None,
         body: bytes | None = None) -> object:
    try:
        return fetch(method, url, headers or {}, body)
    except urllib.error.HTTPError as e:
        raise SourceError(f"{what}: HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise SourceError(f"{what}: unreachable ({type(e).__name__}: {e}) at {url}") from e
    except ValueError as e:
        raise SourceError(f"{what}: not JSON from {url} ({e})") from e


def _names(what: str, value: object) -> list[str]:
    if not isinstance(value, list):
        raise SourceError(f"{what}: expected a list, got {type(value).__name__}")
    out = [v for v in value if isinstance(v, str) and v.strip()]
    if not out:
        raise SourceError(f"{what}: returned nothing")
    return out


def ha_token() -> str:
    tok = os.environ.get("HA_TOKEN", "").strip()
    if tok:
        return tok
    env = REPO_DIR / ".env"
    try:
        for line in env.read_text().splitlines():
            if line.startswith("HA_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise SourceError(f"HA: HA_TOKEN not set (no env var, not in {env})")


def ha_template(fetch: Fetch, ha_url: str, token: str, what: str, template: str) -> list[str]:
    raw = _get(fetch, what, "POST", ha_url.rstrip("/") + "/api/template",
               {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
               json.dumps({"template": template}).encode())
    # HA renders a template to text; a JSON list comes back parsed by
    # http_json only when HA set a JSON content type, so accept both.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            raise SourceError(f"{what}: template output is not JSON ({e})") from e
    return _names(what, raw)


def stations(fetch: Fetch, tool_service: str) -> list[str]:
    raw = _get(fetch, "Pandora stations", "GET",
               tool_service.rstrip("/") + "/music/stations?provider=pandora")
    rows = raw.get("stations") if isinstance(raw, dict) else None
    return _names("Pandora stations", [r.get("name") for r in rows or [] if isinstance(r, dict)])


def artists(fetch: Fetch, tool_service: str, limit: int) -> list[str]:
    raw = _get(fetch, "top artists", "GET",
               tool_service.rstrip("/") + "/music/taste/profile?" + urllib.parse.urlencode({"limit": limit}))
    rows = raw.get("artists") if isinstance(raw, dict) else None
    return _names("top artists", [r.get("name") for r in rows or [] if isinstance(r, dict)])


# ── the line ────────────────────────────────────────────────────────────────

def clean(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = _CONTROL.sub(" ", name)
    name = _UNSAFE.sub("", name)
    name = name.replace(",", " ")          # the separator; a comma inside a name would split it
    return " ".join(name.split())


def fold(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def command_words_in(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", name.casefold()) if w in COMMAND_WORDS]


def render(groups: list[list[str]]) -> str:
    parts = [n for g in groups for n in sorted(g, key=lambda s: (s.casefold(), s))]
    return ", ".join(parts) + "." if parts else ""


TAKE_TURNS_FROM = 2     # categories from this index on share the budget in turns


def candidates(categories: list[tuple[str, list[str]]]) -> list[tuple[int, str]]:
    """(category index, raw name) in the order names are offered to the budget:
    the first TAKE_TURNS_FROM categories in full, then the rest one name
    each in turn."""
    out = [(i, n) for i, (_, names) in enumerate(categories[:TAKE_TURNS_FROM]) for n in names]
    rest = [(i, list(names)) for i, (_, names) in enumerate(categories) if i >= TAKE_TURNS_FROM]
    depth = max((len(n) for _, n in rest), default=0)
    for k in range(depth):
        out += [(i, names[k]) for i, names in rest if k < len(names)]
    return out


def build(categories: list[tuple[str, list[str]]], count: Callable[[str], int],
          max_tokens: int) -> tuple[str, dict]:
    """The prompt line and a summary. `categories` are (label, names) in
    output order, each list in rank order (see the budget rule above)."""
    kept: list[list[str]] = [[] for _ in categories]
    offered = [0] * len(categories)
    seen: set[str] = set()
    dropped: list[str] = []
    for i, raw in candidates(categories):
        name = clean(raw)
        if categories[i][0] == "stations":
            name = re.sub(r"(?i)\s+radio$", "", name) or name
        key = fold(name)
        if not key or key in seen:
            continue
        words = re.findall(r"[a-z]+", name.casefold())
        if words and all(w in COMMAND_WORDS for w in words):
            # A name that is nothing but command words ("Stop", "Next") would
            # put the bare command into the prompt (M13-e2e D5): left out.
            dropped.append(name)
            continue
        offered[i] += 1
        trial = [g + [name] if j == i else g for j, g in enumerate(kept)]
        if count(render(trial)) <= max_tokens:
            kept = trial
            seen.add(key)
    stats = {label: {"kept": len(kept[i]), "offered": offered[i]}
             for i, (label, _) in enumerate(categories)}
    line = render(kept)
    flagged = sorted({n for g in kept for n in g if command_words_in(n)}, key=str.casefold)
    return line, {"tokens": count(line) if line else 0, "names": sum(map(len, kept)),
                  "categories": stats, "command_word_names": flagged,
                  "command_only_names_dropped": sorted(set(dropped), key=str.casefold)}


def whisper_counter(tokenizer_path: Path | None) -> Callable[[str], int]:
    """Token count as faster-whisper encodes an initial prompt."""
    if tokenizer_path is None or not Path(tokenizer_path).is_file():
        raise RuntimeError(f"tokenizer.json not found: {tokenizer_path}")
    try:
        from tokenizers import Tokenizer
    except ImportError as e:
        raise RuntimeError("the `tokenizers` package is not installed in this Python") from e
    tok = Tokenizer.from_file(str(tokenizer_path))
    return lambda text: len(tok.encode(" " + text.strip(), add_special_tokens=False).ids)


def file_content(line: str, summary: dict) -> str:
    return (
        "# Whisper vocabulary prompt for the dev instance (wyoming-whisper-dev.service).\n"
        "# Generated by scripts/build_stt_prompt.py (sprint M13); edits are overwritten.\n"
        f"# tokens={summary['tokens']} names={summary['names']}\n"
        f'{ENV_KEY}="{line}"\n'
    )


def write_if_changed(out: Path, content: str) -> bool:
    try:
        if out.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, out)
    return True


DEV_UNIT = "wyoming-whisper-dev.service"


def check_unit(unit: str) -> str:
    """The one unit this script may restart is the dev Whisper instance, by
    its exact name (M13-e2e observation 2: a name pattern would also accept
    a future *-dev unit, an alias of production, or a string systemctl
    reads as an option)."""
    if unit != DEV_UNIT:
        raise RefusedUnit(f"refusing to restart {unit!r}: only {DEV_UNIT}")
    return unit


def restart(unit: str, run: Callable[[list[str]], int]) -> int:
    return run(["systemctl", "--user", "try-restart", "--", check_unit(unit)])


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def gather(fetch: Fetch, ha_url: str, tool_service: str, artist_limit: int,
           token: Callable[[], str] = ha_token) -> list[tuple[str, list[str]]]:
    tok = token()
    return [
        ("kronk", list(FIXED_NAMES)),
        ("rooms", ha_template(fetch, ha_url, tok, "HA areas", AREAS_TEMPLATE)),
        ("stations", stations(fetch, tool_service)),
        ("artists", artists(fetch, tool_service, artist_limit)),
        ("players", ha_template(fetch, ha_url, tok, "MA players", PLAYERS_TEMPLATE)),
    ]


def main(argv: Iterable[str] | None = None, fetch: Fetch = http_json,
         counter: Callable[[Path | None], Callable[[str], int]] = whisper_counter,
         run: Callable[[list[str]], int] = _run, token: Callable[[], str] = ha_token) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="print the line; write nothing")
    ap.add_argument("--max-tokens", type=int, default=100)
    ap.add_argument("--artists", type=int, default=200, help="artists to ask the profile for")
    ap.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER)
    ap.add_argument("--ha-url", default=DEFAULT_HA_URL)
    ap.add_argument("--tool-service", default=DEFAULT_TOOL_SERVICE)
    ap.add_argument("--restart-unit", default=None)
    a = ap.parse_args(list(argv) if argv is not None else None)

    if a.max_tokens < 1 or not 1 <= a.artists <= 500:
        print("build_stt_prompt: --max-tokens must be >= 1 and --artists 1..500", file=sys.stderr)
        return 2
    if a.restart_unit:
        try:
            check_unit(a.restart_unit)
        except RefusedUnit as e:
            print(f"build_stt_prompt: {e}", file=sys.stderr)
            return 2
    try:
        count = counter(a.tokenizer)
    except RuntimeError as e:
        print(f"build_stt_prompt: {e}", file=sys.stderr)
        return 3
    try:
        cats = gather(fetch, a.ha_url, a.tool_service, a.artists, token)
    except SourceError as e:
        print(f"build_stt_prompt: {e}; {a.out} left as it was", file=sys.stderr)
        return 1
    line, summary = build(cats, count, a.max_tokens)
    if a.print:
        print(line)
        print(json.dumps(summary), file=sys.stderr)
        return 0
    changed = write_if_changed(a.out, file_content(line, summary))
    print(f"build_stt_prompt: {'written' if changed else 'unchanged'} {a.out} "
          + json.dumps(summary, ensure_ascii=False))
    if changed and a.restart_unit:
        rc = restart(a.restart_unit, run)
        if rc != 0:
            print(f"build_stt_prompt: systemctl --user try-restart {a.restart_unit} exited {rc}",
                  file=sys.stderr)
            return 4
        print(f"build_stt_prompt: try-restart {a.restart_unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
