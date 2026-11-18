"""scripts/build_stt_prompt.py (sprint M13, criteria 2 to 4 and 8).

Sources are stubbed (no socket is opened: `no_network`), the token counter
is a stand-in, files go to tmp_path, and the restart is a recorded call.
The names below are made up; none is household data.
"""
import importlib.util
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("build_stt_prompt_m13", SCRIPTS / "build_stt_prompt.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bsp = _load()


def words(text: str) -> int:
    """Stand-in token counter: one token per word and per punctuation mark."""
    return len(re.findall(r"\w+|[^\w\s]", text))


AREAS = ["Sun Room", "Pantry", "Garage"]
PLAYERS = ["Pantry Speaker", "garage", "Den Box"]          # "garage" repeats a room
STATIONS = [{"name": "Moss Radio", "plays": 3.0}, {"name": "Fjord Radio", "plays": 1.0},
            {"name": "Quartz Radio", "plays": 0.0}]
ARTISTS = [{"name": "Velvet Orchard"}, {"name": "Tin Heron"}, {"name": "Ola Brix"}]


class Sources:
    """A fake fetch: answers by URL, records every call."""

    def __init__(self, **override):
        self.data = {"areas": AREAS, "players": PLAYERS,
                     "stations": {"provider": "pandora", "stations": STATIONS},
                     "artists": {"artists": ARTISTS}}
        self.data.update(override)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if url.endswith("/api/template"):
            tpl = json.loads(body)["template"]
            key = "areas" if "areas()" in tpl else "players"
        elif "/music/stations" in url:
            key = "stations"
        elif "/music/taste/profile" in url:
            key = "artists"
        else:
            raise AssertionError(url)
        v = self.data[key]
        if isinstance(v, Exception):
            raise v
        return v


def run(tmp_path, *args, fetch=None, restarts=None, out=None):
    out = out or tmp_path / "prompt.env"
    fetch = fetch or Sources()
    rec = restarts if restarts is not None else []

    def fake_run(cmd):
        rec.append(cmd)
        return 0
    rc = bsp.main(["--out", str(out), *args], fetch=fetch, counter=lambda p: words,
                  run=fake_run, token=lambda: "t0ken")
    return rc, out, rec


def prompt_of(out: Path) -> str:
    line = [ln for ln in out.read_text().splitlines() if ln.startswith(bsp.ENV_KEY + "=")]
    assert len(line) == 1
    return line[0].split("=", 1)[1].strip('"')


# ── criterion 2: one line, from the four sources, names only ────────────────

def test_writes_one_line_from_every_source(tmp_path):
    rc, out, _ = run(tmp_path)
    assert rc == 0
    p = prompt_of(out)
    assert "\n" not in p
    for n in ("Kronk", "Sun Room", "Pantry", "Garage", "Pantry Speaker", "Den Box",
              "Moss", "Fjord", "Quartz", "Velvet Orchard", "Tin Heron", "Ola Brix"):
        assert n in p, n
    # categories in order, alphabetical inside each; "garage" only once;
    # stations without their trailing "Radio"
    assert p == ("Kronk, Garage, Pantry, Sun Room, Fjord, Moss, Quartz, "
                 "Ola Brix, Tin Heron, Velvet Orchard, Den Box, Pantry Speaker.")
    assert "Radio" not in p


def test_reads_with_the_right_requests(tmp_path):
    src = Sources()
    run(tmp_path, "--ha-url", "http://127.0.0.1:8123", "--tool-service", "http://127.0.0.1:8103",
        "--artists", "50", fetch=src)
    assert src.calls == [
        ("POST", "http://127.0.0.1:8123/api/template"),
        ("GET", "http://127.0.0.1:8103/music/stations?provider=pandora"),
        ("GET", "http://127.0.0.1:8103/music/taste/profile?limit=50"),
        ("POST", "http://127.0.0.1:8123/api/template"),
    ]


def test_defaults_are_loopback_and_dev_tool_service():
    assert bsp.DEFAULT_HA_URL == "http://127.0.0.1:8123"
    assert bsp.DEFAULT_TOOL_SERVICE == "http://127.0.0.1:8103"
    assert bsp.DEFAULT_OUT == Path.home() / "services/wyoming-whisper/dev-prompt.env"


def test_command_words_cover_the_routing_command_forms():
    """Every word in the command-form patterns is either a command word or
    in this list of nouns and function words. A new word in a pattern
    fails here until it is sorted into one or the other."""
    import command_forms
    import routing
    not_commands = {
        "album", "albums", "artist", "atlas", "can", "could", "don", "for", "game", "games",
        "genre", "hey", "in", "it", "kronk", "mix", "movie", "movies", "music", "my", "nas",
        "news", "no", "not", "on", "one", "pandora", "playlist", "playlists", "please",
        "podcast", "radio", "song", "songs", "spotify", "station", "that", "the", "this",
        "to", "track", "tracks", "tune", "video", "videos", "you", "youtube",
    }
    found = set()
    for p in (command_forms.PLAYBACK_RE, command_forms.RATE_RE, command_forms.ANOTHER_RE, routing._MUSIC_RE):
        pat = re.sub(r"\(\?P<\w+>", "(", p.pattern)         # group names are not words
        pat = re.sub(r"\\[a-zA-Z]", "", pat)                 # nor \s, \b, \w
        found |= set(re.findall(r"[a-z]{2,}", pat))
    assert found - not_commands <= bsp.COMMAND_WORDS, sorted(found - not_commands - bsp.COMMAND_WORDS)
    for w in ("play", "pause", "stop", "skip", "next", "resume", "shuffle", "volume", "thumbs"):
        assert w in bsp.COMMAND_WORDS


def test_the_line_adds_no_command_word(tmp_path):
    rc, out, _ = run(tmp_path)
    assert rc == 0
    assert not [w for w in re.findall(r"[a-z]+", prompt_of(out).casefold()) if w in bsp.COMMAND_WORDS]


def test_a_name_with_a_command_word_is_kept_and_listed(tmp_path, capsys):
    src = Sources(stations={"stations": [{"name": "Stop Motion Radio", "plays": 1}]},
                  artists={"artists": [{"name": "The Skip Turners"}, {"name": "Tin Heron"}]})
    rc, out, _ = run(tmp_path, fetch=src)
    assert rc == 0
    p = prompt_of(out)
    assert "Stop Motion" in p and "The Skip Turners" in p
    summary = json.loads(capsys.readouterr().out.split(" ", 3)[3])
    assert summary["command_word_names"] == ["Stop Motion", "The Skip Turners"]


def test_names_are_made_safe_for_the_environment_file(tmp_path):
    src = Sources(artists={"artists": [{"name": 'Ke$ha "Live"'}, {"name": "Back\\slash"},
                                       {"name": "Earth, Wind & Fire"}, {"name": "Guns N' Roses"},
                                       {"name": "Line\nBreak"}]})
    rc, out, _ = run(tmp_path, fetch=src)
    p = prompt_of(out)
    for bad in ('"', "\\", "$", "`", "\n"):
        assert bad not in p
    assert "Keha Live" in p and "Backslash" in p and "Earth Wind & Fire" in p
    assert "Guns N' Roses" in p and "Line Break" in p


# ── criterion 3: read-only, deterministic, stable, fails without touching ────

def test_same_inputs_same_file_and_no_rewrite(tmp_path):
    rc, out, _ = run(tmp_path)
    first = out.read_bytes()
    mtime = out.stat().st_mtime_ns
    rc2, _, _ = run(tmp_path, out=out)
    assert rc2 == 0 and out.read_bytes() == first and out.stat().st_mtime_ns == mtime


def test_order_does_not_follow_play_counts(tmp_path):
    """Plays reorder the source; the line is the same, because the kept names are."""
    rc, out, _ = run(tmp_path)
    a = prompt_of(out)
    swapped = Sources(stations={"stations": list(reversed(STATIONS))},
                      artists={"artists": list(reversed(ARTISTS))})
    run(tmp_path, fetch=swapped, out=tmp_path / "b.env")
    assert prompt_of(tmp_path / "b.env") == a


@pytest.mark.parametrize("which", ["areas", "players", "stations", "artists"])
@pytest.mark.parametrize("failure", [
    urllib.error.URLError("refused"), urllib.error.HTTPError("u", 502, "bad", {}, None),
    TimeoutError("slow"), ValueError("not json"), "empty",
])
def test_a_failed_or_empty_source_leaves_the_old_file(tmp_path, which, failure, capsys):
    out = tmp_path / "prompt.env"
    out.write_text("OLD\n")
    empty = {"areas": [], "players": [], "stations": {"stations": []}, "artists": {"artists": []}}
    src = Sources(**{which: empty[which] if failure == "empty" else failure})
    rec = []
    rc, _, _ = run(tmp_path, "--restart-unit", "wyoming-whisper-dev.service", fetch=src, restarts=rec, out=out)
    assert rc == 1
    assert out.read_text() == "OLD\n"
    assert rec == []
    assert "left as it was" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.tmp"))


def test_a_source_that_answers_the_wrong_shape_fails(tmp_path):
    rc, out, _ = run(tmp_path, fetch=Sources(stations={"detail": "nope"}))
    assert rc == 1 and not out.exists()


def test_missing_token_fails_before_any_request(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.setattr(bsp, "REPO_DIR", tmp_path)            # no .env there
    src = Sources()
    rc = bsp.main(["--out", str(tmp_path / "p.env")], fetch=src, counter=lambda p: words,
                  run=lambda c: 0)
    assert rc == 1 and src.calls == [] and not (tmp_path / "p.env").exists()


def test_token_from_repo_env_line(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    (tmp_path / ".env").write_text("OTHER=1\nHA_TOKEN=abc123\n")
    monkeypatch.setattr(bsp, "REPO_DIR", tmp_path)
    assert bsp.ha_token() == "abc123"


def test_print_writes_nothing(tmp_path, capsys):
    rc, out, _ = run(tmp_path, "--print")
    assert rc == 0 and not out.exists()
    assert capsys.readouterr().out.startswith("Kronk, ")


def test_missing_tokenizer_is_exit_3(tmp_path):
    rc = bsp.main(["--out", str(tmp_path / "p.env"), "--tokenizer", str(tmp_path / "none.json")],
                  fetch=Sources(), run=lambda c: 0, token=lambda: "t")
    assert rc == 3 and not (tmp_path / "p.env").exists()


# ── criterion 4: the budget rule ────────────────────────────────────────────

def cats(rooms=AREAS, stations=None, artists=None, players=PLAYERS):
    return [("kronk", ["Kronk"]), ("rooms", rooms),
            ("stations", stations if stations is not None else [x["name"] for x in STATIONS]),
            ("artists", artists if artists is not None else [x["name"] for x in ARTISTS]),
            ("players", players)]


def test_candidates_rooms_in_full_then_turns():
    assert bsp.candidates(cats()) == [
        (0, "Kronk"), (1, "Sun Room"), (1, "Pantry"), (1, "Garage"),
        (2, "Moss Radio"), (3, "Velvet Orchard"), (4, "Pantry Speaker"),
        (2, "Fjord Radio"), (3, "Tin Heron"), (4, "garage"),
        (2, "Quartz Radio"), (3, "Ola Brix"), (4, "Den Box"),
    ]


def test_budget_keeps_kronk_and_rooms_then_takes_turns():
    full = bsp.build(cats(), words, 1000)[0]
    assert words(full) == 30
    # 20 tokens: Kronk and the three rooms (9), then Moss, Velvet Orchard,
    # Pantry Speaker, Fjord (19); every later name would pass 20
    line, s = bsp.build(cats(), words, 20)
    assert line == "Kronk, Garage, Pantry, Sun Room, Fjord, Moss, Velvet Orchard, Pantry Speaker."
    assert s["tokens"] == words(line) == 19
    assert s["categories"]["rooms"] == {"kept": 3, "offered": 3}
    assert s["categories"]["stations"] == {"kept": 2, "offered": 3}
    assert s["categories"]["artists"] == {"kept": 1, "offered": 3}
    assert s["categories"]["players"] == {"kept": 1, "offered": 2}      # "garage" folded away


def test_budget_keeps_the_most_played_station_and_top_artist_first():
    c = cats(rooms=["Den"], stations=["Moss Radio", "Fjord Radio"], artists=["Tin Heron", "Ola Brix"],
             players=["Box"])
    assert bsp.build(c, words, 9)[0] == "Kronk, Den, Moss, Tin Heron."
    assert bsp.build(c, words, 13)[0] == "Kronk, Den, Fjord, Moss, Tin Heron, Box."
    assert bsp.build(c, words, 16)[0] == "Kronk, Den, Fjord, Moss, Ola Brix, Tin Heron, Box."


def test_rooms_come_before_everything_else():
    c = cats(rooms=["Den", "Loft"], stations=["Moss Radio"], artists=["Ola"], players=["Box"])
    assert bsp.build(c, words, 6)[0] == "Kronk, Den, Loft."


def test_a_name_too_long_is_skipped_and_the_next_tried():
    c = cats(rooms=["Den"], stations=["S"], artists=["A Very Long Artist Name Indeed", "Ola"], players=["Box"])
    line, s = bsp.build(c, words, 12)
    assert line == "Kronk, Den, S, Ola, Box."
    assert s["categories"]["artists"] == {"kept": 1, "offered": 2}


def test_an_artist_already_in_as_a_station_is_not_repeated():
    c = cats(rooms=["Den"], stations=["Groove Hall Radio"], artists=["Groove Hall", "Ola"], players=["Box"])
    line, s = bsp.build(c, words, 100)
    assert line == "Kronk, Den, Groove Hall, Ola, Box."
    assert s["categories"]["artists"] == {"kept": 1, "offered": 1}


def test_default_budget_is_100_tokens():
    import argparse  # noqa: F401  (the default lives in main's parser)
    src = (SCRIPTS / "build_stt_prompt.py").read_text()
    assert 'ap.add_argument("--max-tokens", type=int, default=100)' in src


# ── criterion 8: restart only when the text changed, never production ───────

def test_restart_only_when_changed(tmp_path):
    rec = []
    rc, out, _ = run(tmp_path, "--restart-unit", "wyoming-whisper-dev.service", restarts=rec)
    assert rc == 0 and rec == [["systemctl", "--user", "try-restart", "--", "wyoming-whisper-dev.service"]]
    rc, _, _ = run(tmp_path, "--restart-unit", "wyoming-whisper-dev.service", restarts=rec, out=out)
    assert rc == 0 and len(rec) == 1                                    # unchanged: no restart
    changed = Sources(artists={"artists": [{"name": "New Artist"}]})
    rc, _, _ = run(tmp_path, "--restart-unit", "wyoming-whisper-dev.service", restarts=rec,
                   out=out, fetch=changed)
    assert rc == 0 and len(rec) == 2


@pytest.mark.parametrize("unit", ["wyoming-whisper.service", "wyoming-whisper", "llama-gemma4-e4b.service",
                                  "homeassistant", "wyoming-whisper-dev.service; reboot",
                                  "wyoming-whisper-dev.timer"])
def test_restart_refuses_production_and_non_dev_units(tmp_path, unit):
    rec = []
    rc, out, _ = run(tmp_path, "--restart-unit", unit, restarts=rec)
    assert rc == 2 and rec == [] and not out.exists()


def test_failed_restart_is_exit_4_with_the_file_in_place(tmp_path):
    out = tmp_path / "p.env"
    rc = bsp.main(["--out", str(out), "--restart-unit", "wyoming-whisper-dev.service"],
                  fetch=Sources(), counter=lambda p: words, run=lambda c: 5, token=lambda: "t")
    assert rc == 4 and out.exists()


def test_environment_file_shape(tmp_path):
    rc, out, _ = run(tmp_path)
    lines = out.read_text().splitlines()
    assert [ln for ln in lines if not ln.startswith("#")] == [f'{bsp.ENV_KEY}="{prompt_of(out)}"']
    assert any(ln.startswith("# tokens=") for ln in lines)


# ── the units (criteria 5 and 8) ────────────────────────────────────────────

SYSTEMD = SCRIPTS.parent / "systemd"


def _exec_args(unit: Path) -> list[str]:
    text = unit.read_text().replace("\\\n", " ")
    line = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")][0]
    return line.split("=", 1)[1].split()


def _flag(args, name):
    return args[args.index(name) + 1]


def test_dev_unit_is_production_plus_prompt_on_loopback():
    prod = _exec_args(SYSTEMD / "wyoming-whisper.service")
    dev = _exec_args(SYSTEMD / "wyoming-whisper-dev.service")
    for f in ("--model", "--language", "--compute-type", "--device", "--data-dir"):
        assert _flag(dev, f) == _flag(prod, f), f
    assert _flag(prod, "--uri") == "tcp://0.0.0.0:10300" and "--initial-prompt" not in prod
    assert _flag(dev, "--uri") == "tcp://127.0.0.1:10301"
    assert _flag(dev, "--initial-prompt") == "${WHISPER_INITIAL_PROMPT}"   # braces: one argument
    text = (SYSTEMD / "wyoming-whisper-dev.service").read_text()
    assert "EnvironmentFile=%h/services/wyoming-whisper/dev-prompt.env" in text
    assert "Environment=HF_HUB_OFFLINE=1" in text
    assert bsp.ENV_KEY == "WHISPER_INITIAL_PROMPT"


def test_regeneration_unit_restarts_only_the_dev_instance():
    args = _exec_args(SYSTEMD / "kronk-stt-prompt.service")
    assert args[-2:] == ["--restart-unit", "wyoming-whisper-dev.service"]
    assert bsp.check_unit("wyoming-whisper-dev.service")
    timer = (SYSTEMD / "kronk-stt-prompt.timer").read_text()
    assert "OnCalendar=*-*-* 04:00" in timer


# ── after the end-to-end test (M13-e2e observation 2, D5) ──

@pytest.mark.parametrize("unit", [
    "wyoming-whisper.service", "wyoming-whisper", "wyoming-whisper.service-dev.service",
    "wyoming-whisper@x-dev.service", "llama-gemma4-e4b-dev.service", "homeassistant-dev.service",
    "x-dev.service", "..-dev.service", "-Hsomehost-dev.service", "--system-dev.service",
    "wyoming-whisper-dev.service ", "Wyoming-Whisper-Dev.service", "wyoming-whisper-dev", ""])
def test_only_the_dev_unit_by_its_exact_name(unit):
    with pytest.raises(bsp.RefusedUnit):
        bsp.check_unit(unit)
    assert bsp.check_unit("wyoming-whisper-dev.service") == "wyoming-whisper-dev.service"


def test_a_name_that_is_only_command_words_is_left_out():
    cats = [("kronk", ["Kronk"]), ("rooms", ["Kitchen"]),
            ("stations", ["Stop Radio", "Next", "Mushroom Jazz Radio"]),
            ("artists", ["Play", "Shuffle", "Stop Making Sense", "Orbital"]), ("players", ["Pause"])]
    line, summary = bsp.build(cats, lambda s: len(s.split()), 100)
    names = [n.strip() for n in line.rstrip(".").split(",")]
    assert names == ["Kronk", "Kitchen", "Mushroom Jazz", "Orbital", "Stop Making Sense"]
    assert summary["command_only_names_dropped"] == ["Next", "Pause", "Play", "Shuffle", "Stop"]
    assert summary["command_word_names"] == ["Stop Making Sense"]
