"""tool_service's config/ loader (sprint M21 step 1): config/music.yaml.

Same rules as the orchestrator's (tests/test_config_loader.py): errors name
the file and line, an invalid file never takes effect, reload says what
changed."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tool_service import config_loader
from tool_service import main as ts

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    config_loader.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    config_loader.start()


def _problems(cfgdir, text):
    (cfgdir / "music.yaml").write_text(text)
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    return [(p.file, p.line, p.message) for p in e.value.problems]


def test_shipped_music_yaml_loads_and_equals_the_builtin_defaults():
    cfg = config_loader.load(CONFIG)
    assert cfg.source == "files"
    assert cfg.music == config_loader.Music()


def test_unknown_key_names_file_and_line(cfgdir):
    assert _problems(cfgdir, "verify:\n  timeout_s: 8\n  timout_s: 9\n") == \
        [("music.yaml", 3, "verify.timout_s: unknown key")]


def test_wrong_type_names_file_and_line(cfgdir):
    ((f, line, msg),) = _problems(cfgdir, "verify:\n  timeout_s: '8'\n")
    assert (f, line) == ("music.yaml", 2) and "verify.timeout_s" in msg


def test_out_of_range_names_file_and_line(cfgdir):
    ((f, line, msg),) = _problems(cfgdir, "# c\nverify:\n  timeout_s: 0\n")
    assert (f, line) == ("music.yaml", 3)
    assert msg.startswith("verify.timeout_s: Input should be greater than 0")


def test_yaml_syntax_error_and_duplicate_key_name_the_line(cfgdir):
    ((f, line, msg),) = _problems(cfgdir, "verify:\n  timeout_s: [8\n")
    assert f == "music.yaml" and line is not None and "not valid YAML" in msg
    # One dialect (fix round 1): the shared builder reports duplicates, in
    # the orchestrator's words.
    assert _problems(cfgdir, "verify:\n  timeout_s: 8\n  timeout_s: 9\n") == \
        [("music.yaml", 3, "duplicate key verify.timeout_s")]


def test_missing_file_is_an_error(cfgdir):
    (cfgdir / "music.yaml").unlink()
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert str(e.value.problems[0]) == "config/music.yaml: file is missing"


def test_bad_file_at_startup_runs_on_builtin_defaults(cfgdir):
    (cfgdir / "music.yaml").write_text("verify:\n  timeout_s: -1\n")
    config_loader.start()
    st = config_loader.status()
    assert st["ok"] is False and st["source"] == "defaults"
    assert st["errors"][0]["file"] == "config/music.yaml" and st["errors"][0]["line"] == 2
    assert config_loader.current().music.verify.timeout_s == 8


def test_bad_reload_keeps_the_last_good_value_and_good_reload_changes_it(cfgdir):
    m = cfgdir / "music.yaml"
    m.write_text(m.read_text().replace("timeout_s: 8", "timeout_s: 99"))
    r = config_loader.reload()
    assert r["ok"] is False and r["errors"][0]["line"] is not None
    assert config_loader.current().music.verify.timeout_s == 8
    assert "within 8s" in ts._no_start_sentence("the kitchen speaker")
    m.write_text(m.read_text().replace("timeout_s: 99", "timeout_s: 6"))
    assert config_loader.reload() == {"ok": True, "changed": ["music.yaml:verify.timeout_s: 8 -> 6"]}
    assert "within 6s" in ts._no_start_sentence("the kitchen speaker")


def test_reload_and_status_endpoints(cfgdir):
    c = TestClient(ts.app)
    r = c.post("/config/reload")
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": []}
    (cfgdir / "music.yaml").write_text("verify:\n  speed: 1\n")
    r = c.post("/config/reload")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"file": "config/music.yaml", "line": 2,
                                   "message": "verify.speed: unknown key"}]
    st = c.get("/config/status").json()
    assert st["service"] == "tool_service" and st["ok"] is False and st["event"] == "reload"
    assert "last good values" in st["kept"]


def test_verify_constant_is_gone():
    src = (REPO / "tool_service" / "main.py").read_text()
    assert "MUSIC_VERIFY_TIMEOUT_S" not in src


def test_dockerfile_copies_the_loader():
    assert "config_loader.py" in (REPO / "tool_service" / "Dockerfile").read_text()


# ── resolver thresholds ──────────────────────────────────────────────────────

def test_resolver_values_take_effect_on_reload(cfgdir):
    from tool_service import music_resolve as mr
    q = mr.normalize("Hotel Costes 3")
    c = mr.Candidate(uri="library://album/1", media_type="album", name="Hotel Costes 3",
                     artists=["Stéphane Pompougnac"], providers={"filesystem_smb"}, in_library=True)
    before = mr.score(c, q)
    m = cfgdir / "music.yaml"
    m.write_text(m.read_text().replace("library_bonus: 0.03", "library_bonus: 0.1"))
    r = config_loader.reload()
    assert r == {"ok": True, "changed": ["music.yaml:resolver.library_bonus: 0.03 -> 0.1"]}
    assert mr.score(c, q) == pytest.approx(before + 0.07)


def test_resolver_out_of_range_is_rejected_with_its_line(cfgdir):
    m = cfgdir / "music.yaml"
    text = m.read_text().replace("play_min_score: 0.75", "play_min_score: 1.5")
    ((f, line, msg),) = _problems(cfgdir, text)
    assert line == text.splitlines().index("  play_min_score: 1.5") + 1
    assert msg.startswith("resolver.play_min_score: Input should be less than or equal to 1")


def test_type_bonus_names_are_the_media_types():
    assert set(config_loader.TypeBonus.model_fields) == {"artist", "album", "playlist", "track", "radio"}
