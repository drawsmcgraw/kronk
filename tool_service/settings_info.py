"""What tool_service tells the /settings page (sprint M21 step 2), read
only: the values in effect for music.yaml, the environment settings that
shape its behaviour, and the model-facing text it keeps in code.

ENV is an allow-list. Only the names below are ever read for the page; a
secret (HA_TOKEN, MA_TOKEN, SOLAR_SERIAL, the MagicMirror target and key)
is not on it and so cannot be shown. tests/test_m21s2_view.py feeds secret
values into the environment and finds none in any answer.
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from . import config_files, config_loader
except ImportError:
    import config_files
    import config_loader

WHERE_PROD = "docker-compose.yml (tool_service environment); commit, tag, scripts/deploy_tag.sh deploy"
WHERE_DEV = ".env.dev or docker-compose.yml (tool_service-dev); then the dev up line"

# (name, default in code, what it does)
ENV = (
    ("MUSIC_DRY_RUN", "off", "Music requests resolve and answer, but nothing is sent to a player or Music Assistant"),
    ("MUSIC_SHADOW", "off", "Every /music request also runs the M11 decision and logs a comparison (nothing it decides plays)"),
    ("MUSIC_SHADOW_TIMEOUT_S", "30", "How long the shadow decision may run after the answer"),
    ("MUSIC_DEFAULT_PLAYER", "(none)", "Where music goes when the request names no speaker and has no origin"),
    ("MUSIC_MODEL_URL", "http://host.docker.internal:8002/v1", "Where the music model question is sent (LiteLLM)"),
    ("MA_LIKE_TIMEOUT_S", "180", "How long a Pandora thumbs up/down may take through Music Assistant"),
    ("MUSIC_TASTE_ENABLED", "1", "Build the household taste profile at startup and nightly"),
    ("MUSIC_TASTE_BUILD_AT", "03:30", "Local time of the nightly taste build"),
    ("MUSIC_TASTE_TZ", "America/New_York", "Time zone of the taste build"),
    ("MUSIC_TASTE_TAG_MODEL", "gemma-4-e4b", "Model that tags artists with genres"),
    ("MUSIC_TASTE_TAG_TOP_N", "300", "Most untagged artists tagged per build"),
    ("MUSIC_GENRE_ARTIST_RADIO", "0", "Genre picks may start an artist radio"),
    ("HOME_LOCATION", "Laurel, MD", "Location of the cached home forecast"),
    ("WEATHER_REFRESH_SEC", "3600", "How often the home forecast cache refreshes"),
    ("ANNOUNCE_SATELLITE", "(compose)", "The satellite that announces a finished mirror update"),
    ("HA_NOTIFY_SERVICE", "notify/mobile_app_drew_pixel_11_pro", "Operator push alerts"),
    ("SOLAR_POLL_ENABLED", "1", "Run the solar poller (off on dev: it reads production's DB)"),
    ("SOLAR_PRODUCING_FLOOR", "0.03", "Array-median kW below which the array is not producing"),
    ("SOLAR_FAIL_RATIO", "0.40", "Inverter output / median below this is under-producing"),
    ("SOLAR_MARGINAL_BAND", "0.15", "Width above the fail ratio that counts as marginal"),
    ("SOLAR_MIN_SAMPLES", "8", "Producing samples needed to judge a day"),
    ("SOLAR_BAD_FRACTION", "0.70", "Share of a day's samples under that makes a bad day"),
    ("SOLAR_CONFIRM_DAYS", "3", "Consecutive bad days before an alert"),
    ("SOLAR_POLL_MIN", "15", "Minutes between solar polls"),
    ("SOLAR_TZ", "America/New_York", "Time zone for today and period boundaries"),
    ("NEWS_ENABLED", "1", "Build news editions"),
    ("NEWS_MODEL", "gemma-4-e4b", "Model that writes the news brief"),
    ("NEWS_TZ", "America/New_York", "Time zone of the editions"),
    ("NEWS_WINDOW_H", "24", "Hours of items an edition considers"),
    ("NEWS_PER_FEED", "6", "Items taken per feed"),
    ("NEWS_MAX_ITEMS", "36", "Items per edition at most"),
    ("NEWS_CHECK_S", "60", "Seconds between edition checks"),
    ("TELEMETRY_ENABLED", "false", "OpenTelemetry tracing"),
    ("TELEMETRY_RECORD_TEXT", "false", "What was said and answered goes into spans and logs"),
    ("OTEL_SERVICE_NAME", "kronk-tool-service", "The service name traces carry (the dev tag)"),
)


def env_table() -> list[dict]:
    return [{"name": name, "value": config_files.safe(os.environ[name], config_files.MAX_MESSAGE)
             if name in os.environ else None, "default": default, "what": what,
             "change": {"production": WHERE_PROD, "dev": WHERE_DEV}}
            for name, default, what in ENV]


def code_text(model_system: str) -> list[dict]:
    try:
        from . import news
    except ImportError:
        import news
    sample = news.build_prompt([], "morning", datetime(2026, 1, 5))
    return [
        {"name": "Music model question (system message)", "where": "tool_service/main.py _MODEL_SYSTEM",
         "text": model_system,
         "note": "Sent with the sentence and the words to classify; the model answers style or named only"},
        {"name": "News brief prompt", "where": "tool_service/news.py build_prompt",
         "text": sample,
         "note": "Shown for a sample edition with no items; each edition adds its date, label and item list"},
        {"name": "Genre tagging prompt", "where": "tool_service/taste.py tag_artists",
         "text": None,
         "note": "Assembled per batch around the artist names; it asks for ONLY a JSON object, which the same "
                 "function parses (one contract with the parser)"},
    ]


def effective() -> dict:
    """music.yaml's keys with the values in effect, for the page."""
    cfg = config_loader.current()
    return {
        "service": "tool_service",
        "source": cfg.source,
        "digests": cfg.digests,
        "files": {config_loader.MUSIC_FILE: config_files.describe_keys(
            cfg.music, cfg.data.get(config_loader.MUSIC_FILE, {}))},
    }


def validate(name: str, text: str) -> dict:
    """Validate a candidate music.yaml with the schema the loader uses.
    {"ok": True} or {"ok": False, "errors": [{file, line, message}]}."""
    if name != config_loader.MUSIC_FILE:
        return {"ok": False, "errors": [{"file": None, "line": None,
                                         "message": f"tool_service reads only {config_loader.MUSIC_FILE}"}]}
    try:
        check_candidate(text)
    except config_loader.ConfigInvalid as e:
        return {"ok": False, "errors": [p.as_dict() for p in config_files.cap_problems(
            e.problems, lambda first, n: config_loader.Problem(first.file, None, f"and {n} more problems"))]}
    return {"ok": True, "errors": []}


def check_candidate(text: str) -> "config_loader.Music":
    """The Music a candidate text loads to; raises ConfigInvalid."""
    rel = config_loader.MUSIC_FILE
    if len(text.encode("utf-8", "surrogatepass")) > config_files.MAX_YAML_BYTES:
        raise config_loader.ConfigInvalid([config_loader.Problem(
            rel, None, f"too large; the limit is {config_files.MAX_YAML_BYTES} bytes")])
    try:
        data, lines = config_loader.parse_yaml(text, rel)
        return config_loader.validate(config_loader.Music, data, lines, rel)
    except config_loader.ConfigInvalid:
        raise
    except Exception as e:
        raise config_loader.ConfigInvalid([config_loader.Problem(rel, None, config_files.unexpected(e))])


# ── the voice corpus check (criterion 12) ────────────────────────────────────
# The corpus and the MA/HA snapshot are copied into the image from the
# repo's tests/ at build time (Dockerfile); tests point these at the repo.
CORPUS_FILE = Path(os.getenv("VOICE_CORPUS_FILE", "/app/corpus/voice_corpus.jsonl"))
SNAPSHOT_FILE = Path(os.getenv("MUSIC_SNAPSHOT_FILE", "/app/corpus/music_snapshot.json"))
CORPUS_TIMEOUT_S = 120


def corpus_digest() -> str | None:
    try:
        return hashlib.sha256(CORPUS_FILE.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


async def corpus_run(candidate_yaml: str | None, baseline_said: dict, candidate_said: dict) -> dict:
    """Run the parser-and-decision layer of the corpus twice in a child
    process (corpus_check.py): under the music policy in effect, and under
    the candidate (music.yaml text, or the policy in effect with other
    sentences). {"baseline": {...}, "candidate": {...}, "corpus_digest"}
    or {"error": why}."""
    if not CORPUS_FILE.is_file() or not SNAPSHOT_FILE.is_file():
        return {"error": f"the corpus is not in this image ({CORPUS_FILE}, {SNAPSHOT_FILE})"}
    in_effect = config_loader.latest().music.model_dump(mode="json")
    req = {"corpus": str(CORPUS_FILE), "snapshot": str(SNAPSHOT_FILE), "runs": {
        "baseline": {"music": in_effect, "said": baseline_said or {}},
        "candidate": {"music": in_effect, "music_yaml": candidate_yaml, "said": candidate_said or {}}}}
    script = Path(__file__).resolve().parent / "corpus_check.py"
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
           "LC_ALL": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="corpus-run-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(script), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=cwd)
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(req).encode()), CORPUS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"the corpus check took longer than {CORPUS_TIMEOUT_S} s"}
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        return {"error": f"the corpus check failed (exit {proc.returncode}): {config_files.safe(tail[0], 200)}"}
    try:
        answer = json.loads(out)
    except ValueError:
        return {"error": "the corpus check answered something that is not JSON"}
    return {**answer, "corpus_digest": corpus_digest()}
