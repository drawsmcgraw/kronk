"""Sprint M21 step 1: byte-identical pins for everything the move touches.

The fixtures in tests/fixtures/m21/ were written ONCE, from the tree before
the move (commit recorded in tests/fixtures/m21/README.md), by running this
module as a script:

    tests/.venv/bin/python tests/test_m21_pins.py --write

The writer refuses to overwrite existing fixtures. Never regenerate them
from the new files: the point is that the rendering after the move equals
the rendering before it (criteria 13 and 14).

What is pinned:
  - prompts.json: the system message every agent and the coordinator
    receive, rendered by agents.run_stream in every variant the code can
    produce (error style debug/friendly/derived, voice and not, with and
    without an origin stamp, escalation offered or not, each system_extra
    block main.py builds, weather context present or absent, embedded
    conversation context or not), plus the coordinator and home prompts
    as main._run_pipeline assembles them end to end (direct, escalated,
    specialist failure in both styles, web UI local context). Stored as
    sha256 per variant, with the full text of the base variant per agent
    for readable diffs.
  - menus.json: the tool menu (the `tools` argument of the LLM call) each
    agent and the coordinator receive, with and without escalation.
  - values.json: every tuning value the move touches, as the code uses it.

The clock is frozen and LOCATION is fixed, so kronk_facts() is stable.
"""
import asyncio
import hashlib
import itertools
import json
import os
import sys
import time as _real_time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "m21"

if __name__ == "__main__":   # script mode: same import setup as conftest
    sys.path.insert(0, str(HERE.parent))
    sys.path.append(str(HERE.parent / "orchestrator"))
    import tests.conftest  # noqa: F401  (env defaults for the taste store)

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import pytest  # noqa: E402

import agents  # noqa: E402
import errors  # noqa: E402
import origin  # noqa: E402

# ── frozen inputs ────────────────────────────────────────────────────────────

_FROZEN = _real_time.struct_time((2026, 11, 16, 9, 5, 0, 0, 320, 0, "EST", -18000))


class _FrozenTime:
    """agents.time stand-in: localtime() is fixed, everything else is real."""

    def localtime(self, *a):
        return _FROZEN

    def __getattr__(self, name):
        return getattr(_real_time, name)


_WEATHER = {"age_s": 600, "location": "Laurel, MD",
            "summary": "Today: sunny, high 61F, low 40F. Tomorrow: rain, high 55F."}


class _FakeWeatherClient:
    """httpx.AsyncClient stand-in for tool_service's /weather/cached."""
    available = True

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, *a, **kw):
        class R:
            pass
        r = R()
        if _FakeWeatherClient.available and url.endswith("/weather/cached"):
            r.status_code = 200
            r.json = lambda: dict(_WEATHER)
        else:
            r.status_code = 503
            r.json = lambda: {}
        return r


class _Capture:
    """llm.stream stand-in: records each call's messages and tools, answers
    with one token (or, when told, an error or an escalate call)."""

    def __init__(self):
        self.calls = []
        self.script = []    # per-call behaviour, popped in order; default "token"

    async def stream(self, messages, model, tools=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)),
                           "tools": json.loads(json.dumps(tools)) if tools else None})
        how = self.script.pop(0) if self.script else "token"
        if how == "error":
            raise RuntimeError("LiteLLM returned 500: pinned failure")
        if how == "escalate":
            yield {"tool_calls": [{"id": "c1", "function": {
                "name": "escalate", "arguments": {"note": "I have the kWh, not the rate."}}}]}
            yield {"usage": {}}
            return
        yield {"token": "ok"}
        yield {"usage": {}}


def _sha(text: str) -> str:
    # 128 bits of sha256: plenty to prove identity, half the fixture size.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ── variants ─────────────────────────────────────────────────────────────────

def _extras() -> dict:
    """Every system_extra block main._run_pipeline builds, by name."""
    return {
        "none": None,
        "state_and_file": "[Kronk shared state]\nThe porch light is on.\n\n"
                          "[Attached file: notes.txt]\nBuy more coffee.",
        "failed_debug": errors.specialist_failed_block("home", "[home agent error: boom]", errors.DEBUG),
        "failed_friendly": errors.specialist_failed_block("home", "[home agent error: boom]", errors.FRIENDLY),
        "escalation_note": ("[The home specialist escalated this request back to you with the "
                            "note: \"I have the kWh, not the rate.\". Complete the FULL request: "
                            "delegate each part to the right ask_* specialist (ask_home still "
                            "answers its own domain), then combine the results into one answer.]"),
    }


_CONTEXT = [{"role": "user", "content": "what's on the list"},
            {"role": "assistant", "content": "Milk and eggs."}]
_ORIGIN = origin.Origin(device_id="abc123", area="Kitchen")


def _all_agents():
    return list(agents.AGENTS.values()) + [agents.COORDINATOR]


async def _render_one(cap, agent, *, style, voice, stamp, escalation, extra, weather, context):
    _FakeWeatherClient.available = weather
    tok_v = origin.voice.set(voice)
    try:
        with origin.scope(_ORIGIN if stamp else None):
            n = len(cap.calls)
            async for _ in agents.run_stream(
                agent, "pinned task", _CONTEXT if context else [],
                system_extra=extra, error_style=style, allow_escalation=escalation,
            ):
                pass
    finally:
        origin.voice.reset(tok_v)
    call = cap.calls[n]
    return call["messages"][0]["content"], call["tools"]


async def _render_direct() -> tuple[dict, dict, dict]:
    """(variant -> sha, agent -> base text, menus) via agents.run_stream."""
    cap = _Capture()
    shas, base, menus = {}, {}, {}
    extras = _extras()
    with patch("agents.llm.stream", new=cap.stream), \
         patch("agents.metrics.record", new=lambda **kw: None), \
         patch("agents.httpx.AsyncClient", new=_FakeWeatherClient), \
         patch.object(agents, "time", _FrozenTime()), \
         patch.dict(os.environ, {"LOCATION": "Laurel, MD"}):
        for agent in _all_agents():
            for style, voice, stamp, esc, (ename, extra), weather, ctx in itertools.product(
                    ("debug", "friendly", None), (False, True), (False, True), (False, True),
                    extras.items(), (False, True), (False, True)):
                text, tools = await _render_one(
                    cap, agent, style=style, voice=voice, stamp=stamp, escalation=esc,
                    extra=extra, weather=weather, context=ctx)
                key = (f"{agent.name}|style={style}|voice={int(voice)}|origin={int(stamp)}"
                       f"|esc={int(esc)}|extra={ename}|wx={int(weather)}|ctx={int(ctx)}")
                shas[key] = _sha(text)
                if (style, voice, stamp, esc, ename, weather, ctx) == \
                        ("debug", False, False, False, "none", False, False):
                    base[agent.name] = text
                if style == "debug" and not voice and not stamp and ename == "none" \
                        and not weather and not ctx:
                    menus[f"{agent.name}|esc={int(esc)}"] = tools
    return shas, base, menus


async def _render_pipeline(tmp_state: Path) -> dict:
    """System prompts as main._run_pipeline assembles them, in call order."""
    import main
    out = {}
    scenarios = [
        # name, route, voice, stamp, local_context, llm script
        ("direct", "direct", False, False, False, []),
        ("direct_voice_origin", "direct", True, True, False, []),
        ("direct_webui_local", "direct", False, False, True, []),
        ("home_ok", "home", False, False, False, []),
        ("home_ok_voice_origin", "home", True, True, False, []),
        ("home_escalated", "home", False, False, False, ["escalate"]),
        ("home_failed_debug", "home", False, False, False, ["error"]),
        ("home_failed_friendly_voice", "home", True, True, False, ["error"]),
    ]
    tmp_state.write_text("The porch light is on.\n")

    async def run(route, voice, stamp, local, script):
        cap = _Capture()
        cap.script = list(script)

        async def classify(text, history):
            return route

        with patch("agents.llm.stream", new=cap.stream), \
             patch("agents.metrics.record", new=lambda **kw: None), \
             patch("agents.httpx.AsyncClient", new=_FakeWeatherClient), \
             patch.object(agents, "time", _FrozenTime()), \
             patch.dict(os.environ, {"LOCATION": "Laurel, MD"}), \
             patch.object(main.routing, "classify", new=classify), \
             patch.object(main, "STATE_FILE", tmp_state), \
             patch.object(main, "file_contexts", [{"name": "notes.txt", "content": "Buy more coffee.", "tokens": 4}]), \
             patch.object(main, "new_request_id", new=lambda: "pinrid"):
            _FakeWeatherClient.available = True
            with origin.scope(_ORIGIN if stamp else None):
                async for _ in main._run_pipeline(
                    "pinned task", [{"role": "user", "content": "earlier"},
                                    {"role": "assistant", "content": "earlier reply"}],
                    said="pinned task", voice=voice, transport="pin",
                    pipeline_name="pipeline.pin", include_local_context=local):
                    pass
        return cap.calls

    for name, route, voice, stamp, local, script in scenarios:
        calls = await run(route, voice, stamp, local, script)
        out[f"pipeline|{name}|calls"] = len(calls)
        for i, c in enumerate(calls):
            out[f"pipeline|{name}|call={i}"] = _sha(c["messages"][0]["content"])
            out[f"pipeline|{name}|call={i}|tools"] = _sha(json.dumps(c["tools"], sort_keys=True))
    return out


def render_prompts(tmp_dir: Path) -> dict:
    shas, base, menus = asyncio.run(_render_direct())
    shas.update(asyncio.run(_render_pipeline(tmp_dir / "kronk_state.md")))
    return {"variants": shas, "base_text": base}, menus


# ── tuning values in effect ──────────────────────────────────────────────────
# How each value is READ changes as it moves (constant → config); the
# expected numbers in values.json do not. Each accessor reads the value the
# way the running code does.

def effective_values() -> dict:
    import tools
    from tool_service import main as ts
    from tool_service import music_resolve as mr

    v = {}
    for a in _all_agents():
        v[f"agent.{a.name}.max_rounds"] = a.max_rounds
        v[f"agent.{a.name}.terminal_tools"] = sorted(a.terminal_tools)
        v[f"agent.{a.name}.tool_names"] = list(a.tool_names)
    v["agents.repeat_tool_nudge_at"] = \
        agents.config_loader.current().tuning.agent_loop.repeat_tool_nudge_at
    v["agents.weather_ctx_max_age_s"] = \
        agents.config_loader.current().tuning.weather_context.max_age_s
    names = sorted(d["function"]["name"] for d in tools.TOOL_DEFINITIONS)
    timeouts = tools.config_loader.current().tuning.tool_timeouts
    v["tools.timeout_s"] = {n: timeouts.for_tool(n) for n in names}
    v["tools.timeout_default_s"] = timeouts.default_s
    v["tool_service.music_verify_timeout_s"] = ts.config_loader.current().music.verify.timeout_s
    r = mr.policy()
    v["tool_service.resolve_hint_min_score"] = r.hint_min_score
    v["music_resolve.play_min_score"] = r.play_min_score
    v["music_resolve.coverage_min"] = r.coverage_min
    v["music_resolve.provider_bonus"] = r.provider_bonus
    v["music_resolve.library_bonus"] = r.library_bonus
    v["music_resolve.type_bonus"] = r.type_bonus.model_dump()
    return v


# ── the tests ────────────────────────────────────────────────────────────────

def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    return render_prompts(tmp_path_factory.mktemp("m21pins"))


def test_fixtures_exist():
    for f in ("prompts.json", "menus.json", "values.json"):
        assert (FIXTURES / f).is_file(), f"missing pinned fixture {f}"


def test_base_prompt_text_is_byte_identical(rendered):
    want = _load("prompts.json")["base_text"]
    got = rendered[0]["base_text"]
    assert sorted(got) == sorted(want)
    for name in want:
        assert got[name] == want[name], f"{name}: rendered system prompt changed"


def test_every_prompt_variant_is_byte_identical(rendered):
    want = _load("prompts.json")["variants"]
    got = rendered[0]["variants"]
    assert sorted(got) == sorted(want), "the set of variants changed"
    changed = [k for k in want if got[k] != want[k]]
    assert not changed, f"{len(changed)} variants changed, e.g. {changed[:5]}"


def test_tool_menus_are_identical(rendered):
    assert rendered[1] == _load("menus.json")


def test_tuning_values_are_identical():
    assert effective_values() == _load("values.json")


def test_builtin_defaults_render_the_pinned_prompts_and_values(tmp_path, monkeypatch):
    """With config/ unreadable the services run on the built-in defaults
    (criterion 8), and those are today's values too."""
    import config_loader
    from tool_service import config_loader as ts_config
    good = os.environ["KRONK_CONFIG_DIR"]
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(tmp_path / "missing"))
    config_loader.start()
    ts_config.start()
    try:
        assert config_loader.current().source == "defaults"
        assert ts_config.current().source == "defaults"
        prompts, menus = render_prompts(tmp_path)
        assert prompts == _load("prompts.json")
        assert menus == _load("menus.json")
        assert effective_values() == _load("values.json")
    finally:
        monkeypatch.setenv("KRONK_CONFIG_DIR", good)
        config_loader.start()
        ts_config.start()


# ── writer (once, before the move) ───────────────────────────────────────────

def _write():
    import subprocess
    import tempfile
    if any((FIXTURES / f).exists() for f in ("prompts.json", "menus.json", "values.json")):
        sys.exit(f"refusing to overwrite pinned fixtures in {FIXTURES}")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    prompts, menus = render_prompts(Path(tempfile.mkdtemp(prefix="m21pins-")))
    dump = lambda o: json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"  # noqa: E731
    (FIXTURES / "prompts.json").write_text(dump(prompts))
    (FIXTURES / "menus.json").write_text(dump(menus))
    (FIXTURES / "values.json").write_text(dump(effective_values()))
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=HERE.parent).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "orchestrator", "tool_service"],
                           capture_output=True, text=True, cwd=HERE.parent).stdout.strip()
    (FIXTURES / "README.md").write_text(
        "# M21 step 1 pins\n\n"
        f"Written by `tests/.venv/bin/python tests/test_m21_pins.py --write` at commit\n"
        f"`{head}`{' (orchestrator/ and tool_service/ had uncommitted changes!)' if dirty else ''},\n"
        "before any code of the config move. Do not regenerate: tests/test_m21_pins.py\n"
        "compares the rendering after the move against these files.\n\n"
        f"- prompts.json: {len(prompts['variants'])} rendered system-prompt variants (sha256)\n"
        f"  and the base text for {len(prompts['base_text'])} agents\n"
        f"- menus.json: {len(menus)} tool menus\n"
        "- values.json: tuning values in effect\n")
    print(f"wrote {FIXTURES}: {len(prompts['variants'])} variants, {len(menus)} menus")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        _write()
    else:
        sys.exit("usage: tests/test_m21_pins.py --write")
