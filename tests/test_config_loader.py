"""config/ loading, validation and reload (sprint M21 step 1).

The pins (tests/test_m21_pins.py) prove the shipped files render today's
prompts; these prove the loader's rules: what is an error, that an invalid
file never takes effect, and that reload reports what changed.
"""
import os
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agents
import config_loader
import prompt_defaults

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    """A scratch copy of config/ that the orchestrator loader reads; the
    repo's config is loaded again afterwards."""
    d = tmp_path / "config"
    shutil.copytree(CONFIG, d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    config_loader.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(CONFIG))
    config_loader.start()


def _errors(result_or_status):
    return [(e["file"], e["line"], e["message"]) for e in result_or_status["errors"]]


# ── the shipped directory ────────────────────────────────────────────────────

def test_shipped_config_loads_cleanly():
    cfg = config_loader.load(CONFIG)
    assert cfg.source == "files"
    assert set(cfg.prompts) == set(prompt_defaults.PROMPTS)


def test_every_prompt_file_has_a_header_that_is_not_sent():
    for name in prompt_defaults.PROMPTS:
        text = (CONFIG / "prompts" / f"{name}.md").read_text()
        assert text.startswith("<!--\n"), name
        header = text[:text.index("-->")]
        assert len(header.strip().splitlines()) >= 3, f"{name}: header too short"
        assert "<!--" not in config_loader.current().prompts[name]


def test_moved_prompts_are_gone_from_agents_py():
    """One source of truth (criterion 12): a moved prompt's text is in its
    file and in prompt_defaults.py, not in agents.py."""
    src = (REPO / "orchestrator" / "agents.py").read_text()
    for name, text in prompt_defaults.PROMPTS.items():
        first = text.split("\n", 1)[0][:60]
        assert first not in src, f"{name} prompt still in agents.py"


# ── criterion 5: no secrets ──────────────────────────────────────────────────

def _secret_names() -> set[str]:
    """Secret names as the repo declares them: compose's ${NAME}
    interpolations with no default (they come from .env), every key of the
    *.example env templates, and the .env keys the docs name."""
    names = set(re.findall(r"\$\{([A-Z_]+)\}", (REPO / "docker-compose.yml").read_text()))
    for ex in REPO.glob(".env*.example"):
        names |= set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", ex.read_text(), re.M))
    names |= {"HA_TOKEN", "MA_TOKEN", "SOLAR_SERIAL", "LANGFUSE_PUBLIC_KEY",
              "LANGFUSE_SECRET_KEY", "UNIFI_USER", "UNIFI_PASSWORD",
              "PANDORA_USERNAME", "PANDORA_PASSWORD", "GF_ADMIN_USER",
              "GF_ADMIN_PASSWORD", "SETTINGS_PASSPHRASE_HASH"}
    # .env.dev.example holds knobs, not secrets: keep only secret-shaped names.
    return {n for n in names if re.search(r"TOKEN|KEY|SECRET|PASS|SERIAL|USER", n)}


_TOKENISH = [
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),        # JWT (HA, MA tokens)
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),                             # long hex
    re.compile(r"\b(sk|pk)-[A-Za-z0-9_-]{16,}"),                     # API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
]


def test_config_directory_holds_no_secrets():
    names = _secret_names()
    assert "HA_TOKEN" in names and "MA_TOKEN" in names
    files = [p for p in CONFIG.rglob("*") if p.is_file()]
    assert files
    for p in files:
        text = p.read_text()
        for n in names:
            assert n not in text, f"{p.relative_to(REPO)} names the secret {n}"
        for rx in _TOKENISH:
            m = rx.search(text)
            assert not m, f"{p.relative_to(REPO)} has a token-shaped string: {m.group(0)[:12]}…"


# ── criterion 7: errors name the file and line ───────────────────────────────

def test_missing_prompt_file_is_an_error(cfgdir):
    (cfgdir / "prompts" / "home.md").unlink()
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert [str(p) for p in e.value.problems] == ["config/prompts/home.md: file is missing"]


def test_empty_prompt_is_an_error_with_its_line(cfgdir):
    (cfgdir / "prompts" / "home.md").write_text("<!--\nhome\n-->\n\n   \n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    (p,) = e.value.problems
    assert (p.file, p.line, p.message) == ("prompts/home.md", 3, "prompt is empty")


def test_prompt_without_header_is_an_error(cfgdir):
    (cfgdir / "prompts" / "home.md").write_text("You are Kronk.\n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert str(e.value.problems[0]) == \
        "config/prompts/home.md:1: must start with a <!-- header comment -->"


def test_unknown_placeholder_is_an_error_with_its_line(cfgdir):
    (cfgdir / "prompts" / "home.md").write_text("<!--\nhome\n-->\nline one\nbudget {{max_round}}\n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    (p,) = e.value.problems
    assert (p.file, p.line) == ("prompts/home.md", 5)
    assert "unknown placeholder {{max_round}}" in p.message


def test_unknown_prompt_file_is_an_error(cfgdir):
    (cfgdir / "prompts" / "hom.md").write_text("<!--\nx\n-->\nhello\n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert e.value.problems[0].file == "prompts/hom.md"
    assert "unknown prompt file" in e.value.problems[0].message


def test_prompt_body_is_exact_minus_surrounding_blank_lines():
    text = "<!--\nheader\n-->\n\nline one\n  indented\n\nline three  \n\n"
    assert config_loader.parse_prompt(text, "prompts/x.md") == "line one\n  indented\n\nline three  "


def test_yaml_syntax_error_names_the_line():
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.parse_yaml("a: 1\nb: [1, 2\nc: 3\n", "tuning.yaml")
    (p,) = e.value.problems
    assert p.file == "tuning.yaml" and p.line is not None and "not valid YAML" in p.message


def test_yaml_duplicate_key_is_an_error():
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.parse_yaml("a:\n  b: 1\n  b: 2\n", "tuning.yaml")
    assert str(e.value.problems[0]) == "config/tuning.yaml:3: duplicate key a.b"


# ── criterion 8: an invalid file never takes effect ──────────────────────────

def test_bad_file_at_startup_runs_on_builtin_defaults(cfgdir, caplog):
    (cfgdir / "prompts" / "home.md").write_text("no header\n")
    config_loader.start()
    st = config_loader.status()
    assert st["ok"] is False and st["source"] == "defaults"
    assert ("config/prompts/home.md", 1, "must start with a <!-- header comment -->") in _errors(st)
    assert agents.AGENTS["home"].system_prompt == prompt_defaults.PROMPTS["home"]
    assert "config/prompts/home.md:1" in caplog.text


def test_bad_reload_keeps_the_last_good_values(cfgdir):
    good = agents.AGENTS["home"].system_prompt
    (cfgdir / "prompts" / "home.md").write_text("<!--\nh\n-->\n")
    result = config_loader.reload()
    assert result["ok"] is False
    assert _errors(result) == [("config/prompts/home.md", 3, "prompt is empty")]
    assert "last good values" in result["kept"]
    assert agents.AGENTS["home"].system_prompt == good
    assert config_loader.status()["ok"] is False


def test_good_reload_takes_effect_and_says_what_changed(cfgdir):
    p = cfgdir / "prompts" / "home.md"
    p.write_text(p.read_text().rstrip("\n") + " Scratch.\n")
    result = config_loader.reload()
    assert result["ok"] is True
    assert len(result["changed"]) == 1 and result["changed"][0].startswith("prompts/home.md: changed")
    assert agents.AGENTS["home"].system_prompt.endswith("reply in plain sentences only. Scratch.")
    assert config_loader.reload() == {"ok": True, "changed": []}


# ── criteria 9 and 10: the endpoints ─────────────────────────────────────────

@pytest.fixture
def client():
    import main
    return TestClient(main.app)


def test_reload_endpoint_answers_with_the_change(cfgdir, client):
    p = cfgdir / "prompts" / "home.md"
    p.write_text(p.read_text().rstrip("\n") + " Scratch.\n")
    r = client.post("/config/reload")
    assert r.status_code == 200 and r.json()["changed"][0].startswith("prompts/home.md")


def test_reload_endpoint_answers_422_with_file_and_line(cfgdir, client):
    (cfgdir / "prompts" / "home.md").write_text("<!--\nh\n-->\n")
    r = client.post("/config/reload")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"file": "config/prompts/home.md", "line": 3,
                                   "message": "prompt is empty"}]


@pytest.mark.parametrize("header", ["X-Real-IP", "X-Forwarded-For"])
def test_reload_refuses_requests_nginx_proxied(cfgdir, client, header):
    r = client.post("/config/reload", headers={header: "192.168.1.50"})
    assert r.status_code == 403


def test_nginx_never_proxies_config_paths():
    conf = (REPO / "nginx" / "nginx.conf").read_text()
    assert "location ^~ /config/     { return 404; }" in conf
    assert "location ^~ /dev/config/ { return 404; }" in conf


def test_status_endpoint_shows_the_error_with_file_and_line(cfgdir, client, monkeypatch):
    import config_api

    class DeadTools:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): raise RuntimeError("tool_service down")

    monkeypatch.setattr(config_api.httpx, "AsyncClient", DeadTools)
    (cfgdir / "prompts" / "home.md").write_text("<!--\nh\n-->\n")
    config_loader.reload()
    orch, tool = client.get("/api/config").json()["services"]
    assert orch["service"] == "orchestrator" and orch["ok"] is False
    assert orch["errors"][0] == {"file": "config/prompts/home.md", "line": 3, "message": "prompt is empty"}
    assert tool["ok"] is False and "tool_service down" in tool["errors"][0]["message"]


def test_services_page_shows_config_status():
    html = (REPO / "orchestrator" / "static" / "services.html").read_text()
    assert "fetch('api/config')" in html and 'id="config-grid"' in html


# ── compose wiring (criterion 6) ─────────────────────────────────────────────

def test_config_is_mounted_read_only_into_all_four_services():
    import yaml
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    for svc in ("orchestrator", "orchestrator-dev", "tool_service", "tool_service-dev"):
        assert "./config:/config:ro" in compose[svc]["volumes"], svc


# ── tuning.yaml (criterion 7: unknown keys, wrong types, ranges) ─────────────

def _tuning_error(cfgdir, text):
    (cfgdir / "tuning.yaml").write_text(text)
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    return [(p.file, p.line, p.message) for p in e.value.problems]


def test_tuning_unknown_key_names_file_and_line(cfgdir):
    assert _tuning_error(cfgdir, "agent_loop:\n  max_rounds:\n    default: 3\n    reserch: 8\n") == \
        [("tuning.yaml", 4, "agent_loop.max_rounds.reserch: unknown key")]


def test_tuning_wrong_type_names_file_and_line(cfgdir):
    (err,) = _tuning_error(cfgdir, "agent_loop:\n  repeat_tool_nudge_at: '3'\n")
    assert err[:2] == ("tuning.yaml", 2)
    assert err[2].startswith("agent_loop.repeat_tool_nudge_at: Input should be a valid integer")


def test_tuning_bool_is_not_an_int(cfgdir):
    (err,) = _tuning_error(cfgdir, "agent_loop:\n  max_rounds:\n    default: true\n")
    assert err[:2] == ("tuning.yaml", 3)


def test_tuning_out_of_range_names_file_and_line(cfgdir):
    (err,) = _tuning_error(cfgdir, "agent_loop:\n  max_rounds:\n    research: 0\n")
    assert err == ("tuning.yaml", 3,
                   "agent_loop.max_rounds.research: Input should be greater than or equal to 1 (got 0)")


def test_tuning_must_be_a_mapping(cfgdir):
    assert _tuning_error(cfgdir, "- 1\n- 2\n") == \
        [("tuning.yaml", 1, "must be a mapping of keys to values")]


def test_missing_tuning_file_is_an_error(cfgdir):
    (cfgdir / "tuning.yaml").unlink()
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert str(e.value.problems[0]) == "config/tuning.yaml: file is missing"


def test_every_problem_is_reported_not_just_the_first(cfgdir):
    (cfgdir / "prompts" / "talkie.md").write_text("no header\n")
    (cfgdir / "tuning.yaml").write_text("agent_loop:\n  repeat_tool_nudge_at: 1\n")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert {p.file for p in e.value.problems} == {"prompts/talkie.md", "tuning.yaml"}


def test_max_rounds_names_are_the_agents():
    names = set(config_loader.MaxRounds.model_fields) - {"default"}
    assert names == set(agents.AGENTS) | {"coordinator"}
    assert set(prompt_defaults.PROMPTS) == set(agents.AGENTS) | {"coordinator"}


def test_key_left_out_takes_the_builtin_default(cfgdir):
    (cfgdir / "tuning.yaml").write_text("# empty\n")
    cfg = config_loader.load(cfgdir)
    assert cfg.tuning == config_loader.Tuning()
    assert cfg.tuning.agent_loop.max_rounds.for_agent("research") == 8
    assert cfg.tuning.agent_loop.max_rounds.for_agent("home") == 3


def test_tuning_reload_changes_the_budget_and_the_prompt_that_states_it(cfgdir):
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("research: 8", "research: 6"))
    result = config_loader.reload()
    assert result["changed"] == ["tuning.yaml:agent_loop.max_rounds.research: 8 -> 6"]
    assert agents.AGENTS["research"].max_rounds == 6
    assert "budget of 6 tool-use rounds" in agents.AGENTS["research"].system_prompt


def test_bad_tuning_reload_keeps_budget(cfgdir):
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("research: 8", "research: 99"))
    result = config_loader.reload()
    assert result["ok"] is False
    assert result["errors"][0]["file"] == "config/tuning.yaml"
    assert result["errors"][0]["line"] == t.read_text().splitlines().index("    research: 99") + 1
    assert agents.AGENTS["research"].max_rounds == 8


def test_every_yaml_key_has_a_comment():
    """Criterion 4: each key in config/*.yaml has a comment on its line or
    on the lines just above it."""
    for path in CONFIG.glob("*.yaml"):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*:", line):
                continue
            above = lines[i - 1].strip() if i else ""
            assert "#" in line or above.startswith("#"), f"{path.name}:{i + 1} has no comment: {line!r}"


# ── criterion 11: precedence env > file > built-in default ───────────────────

def test_env_var_wins_over_the_file(cfgdir, monkeypatch):
    monkeypatch.setenv("WEATHER_CTX_MAX_AGE_S", "600")
    cfg = config_loader.load(cfgdir)
    assert cfg.tuning.weather_context.max_age_s == 600
    assert cfg.env == {"WEATHER_CTX_MAX_AGE_S": "600"}


def test_file_wins_over_the_default_when_env_is_unset(cfgdir, monkeypatch):
    monkeypatch.delenv("WEATHER_CTX_MAX_AGE_S", raising=False)
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("max_age_s: 7200", "max_age_s: 3600"))
    assert config_loader.load(cfgdir).tuning.weather_context.max_age_s == 3600
    t.write_text("# nothing\n")
    assert config_loader.load(cfgdir).tuning.weather_context.max_age_s == 7200


def test_bad_env_var_is_an_error_naming_the_variable(cfgdir, monkeypatch):
    monkeypatch.setenv("WEATHER_CTX_MAX_AGE_S", "two hours")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert str(e.value.problems[0]) == "(env WEATHER_CTX_MAX_AGE_S): not an integer: 'two hours'"
    monkeypatch.setenv("WEATHER_CTX_MAX_AGE_S", "5")
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    assert e.value.problems[0].file == "(env WEATHER_CTX_MAX_AGE_S)"
    assert "greater than or equal to 60" in e.value.problems[0].message


def test_builtin_defaults_keep_a_valid_env_var(monkeypatch):
    monkeypatch.setenv("WEATHER_CTX_MAX_AGE_S", "900")
    assert config_loader.defaults().tuning.weather_context.max_age_s == 900


@pytest.mark.asyncio
async def test_weather_context_uses_the_age_in_effect(cfgdir, monkeypatch):
    class Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            class R:
                status_code = 200
                def json(self): return {"age_s": 5000, "location": "Laurel, MD", "summary": "Sunny."}
            return R()

    monkeypatch.setattr(agents.httpx, "AsyncClient", Client)
    assert await agents.weather_context() is not None          # 5000 < 7200
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("max_age_s: 7200", "max_age_s: 3600"))
    assert config_loader.reload()["ok"]
    assert await agents.weather_context() is None              # 5000 > 3600


# ── tool timeouts ─────────────────────────────────────────────────────────────

def test_tool_timeout_names_are_the_tools():
    import tools
    assert set(config_loader.PerToolTimeouts.model_fields) == \
        {d["function"]["name"] for d in tools.TOOL_DEFINITIONS}


def test_unknown_tool_timeout_is_rejected_with_its_line(cfgdir):
    t = cfgdir / "tuning.yaml"
    text = t.read_text().replace("    query_hottub: 5\n", "    query_hottub: 5\n    play_musik: 20\n")
    t.write_text(text)
    with pytest.raises(config_loader.ConfigInvalid) as e:
        config_loader.load(cfgdir)
    (p,) = e.value.problems
    assert (p.line, p.message) == (text.splitlines().index("    play_musik: 20") + 1,
                                   "tool_timeouts.per_tool.play_musik: unknown key")


@pytest.mark.asyncio
async def test_execute_uses_the_timeout_in_effect(cfgdir, monkeypatch):
    import tools
    seen = []

    class Client:
        def __init__(self, timeout=None, **kw): seen.append(timeout)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    async def handler(client, args):
        return "ok"

    monkeypatch.setattr(tools.httpx, "AsyncClient", Client)
    monkeypatch.setitem(tools._HANDLERS, "query_hottub", handler)
    monkeypatch.setitem(tools._HANDLERS, "web_search", handler)
    await tools.execute("query_hottub", {})
    await tools.execute("web_search", {})
    t = cfgdir / "tuning.yaml"
    t.write_text(t.read_text().replace("query_hottub: 5", "query_hottub: 7")
                 .replace("default_s: 15", "default_s: 12"))
    assert config_loader.reload()["ok"]
    await tools.execute("query_hottub", {})
    await tools.execute("web_search", {})
    assert seen == [5, 15, 7, 12]
