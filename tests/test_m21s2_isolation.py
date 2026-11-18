"""Sprint M21 step 2, criterion 9: reload isolation.

A request in flight keeps the values it started with. The test runs a real
agent loop (agents.run_stream, llm.stream and tools.execute faked) across a
reload that changes the agent's prompt, its round budget, the tool's
timeout and the repeat-call threshold, and compares every config-derived
value the run used with the values it started with. Every config the run
read is recorded: they must all be the one object it started with.
"""
import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import agents
import config_loader
import tools
from tool_service import config_loader as ts_config

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def live(tmp_path, monkeypatch):
    d = tmp_path / "live"
    shutil.copytree(REPO / "config", d)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    config_loader.start()
    ts_config.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(REPO / "config"))
    config_loader.start()
    ts_config.start()


def _edit(path: Path, old: str, new: str):
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_run_keeps_its_config_across_a_reload(live):
    research = agents.AGENTS["research"]
    start_cfg = config_loader.latest()
    start_prompt, start_rounds = agents.prompt_and_rounds(research)
    assert start_rounds == 8
    seen_configs = []
    real_current = config_loader.current

    def recording_current():
        cfg = real_current()
        seen_configs.append(cfg)
        return cfg

    system_prompts, timeouts, reloaded = [], [], {}

    async def fake_stream(messages, model, tools=None):
        tool_defs = tools
        system_prompts.append(messages[0]["content"])
        n = len(system_prompts)
        if n == 1:
            # Mid-request: the files change and both services reload.
            _edit(live / "prompts" / "research.md", "You are", "You were once")
            _edit(live / "tuning.yaml", "research: 8", "research: 2")
            _edit(live / "tuning.yaml", "repeat_tool_nudge_at: 3", "repeat_tool_nudge_at: 2")
            reloaded["orch"] = config_loader.reload()
            reloaded["ts"] = ts_config.reload()
        if tool_defs:
            yield {"tool_calls": [{"id": f"c{n}", "function": {
                "name": "web_search", "arguments": {"query": f"q{n}"}}}]}
        else:
            yield {"token": "final"}
        yield {"usage": {}}

    async def fake_execute(name, args):
        timeouts.append(config_loader.current().tuning.tool_timeouts.for_tool(name))
        return agents.tools.ToolResult(f"result for {args}", failed=False)

    async def run():
        out = []
        with config_loader.pinned():
            async for ev in agents.run_stream(research, "look it up", []):
                out.append(ev)
        return out

    with patch("agents.llm.stream", new=fake_stream), \
            patch("agents.tools.execute", new=fake_execute), \
            patch("agents.config_loader.current", new=recording_current), \
            patch("agents.weather_context", new=lambda: asyncio.sleep(0, result=None)):
        events = asyncio.run(run())

    assert reloaded["orch"]["ok"] is True, reloaded
    assert any("max_rounds.research: 8 -> 2" in c for c in reloaded["orch"]["changed"])
    # The run: 8 tool rounds (the old budget, not 2) and forced synthesis.
    assert len(system_prompts) == start_rounds + 1
    # The same system text on every call, the old prompt's.
    assert all(p == system_prompts[0] for p in system_prompts)
    assert system_prompts[0].startswith(start_prompt)
    assert "You were once" not in system_prompts[0]
    # The same tool timeout on every call.
    assert len(set(timeouts)) == 1
    # Every config the run read is the one it started with.
    assert seen_configs and all(c is start_cfg for c in seen_configs)
    # The old threshold (3), not the new one (2): the nudge first rides on call 3.
    assert events[-1]["type"] == "done"
    # After the request, a new one sees the reload.
    new_prompt, new_rounds = agents.prompt_and_rounds(research)
    assert new_rounds == 2 and "You were once" in new_prompt
    assert config_loader.current() is not start_cfg


def test_repeat_nudge_uses_the_starting_threshold(live):
    """The repeat-call threshold changes 3 -> 2 after the first call; the
    nudge first appears on call 3, as the starting value says."""
    research = agents.AGENTS["research"]
    tool_messages = []

    async def fake_stream(messages, model, tools=None):
        tool_defs = tools
        tool_messages[:] = [m["content"] for m in messages if m["role"] == "tool"]
        n = sum(1 for m in messages if m["role"] == "tool")
        if n == 0:
            _edit(live / "tuning.yaml", "repeat_tool_nudge_at: 3", "repeat_tool_nudge_at: 2")
            assert config_loader.reload()["ok"]
        if tool_defs and n < 4:
            yield {"tool_calls": [{"id": f"c{n}", "function": {
                "name": "web_search", "arguments": {"query": f"q{n}"}}}]}
        else:
            yield {"token": "final"}
        yield {"usage": {}}

    async def fake_execute(name, args):
        return agents.tools.ToolResult("ok", failed=False)

    async def run():
        with config_loader.pinned():
            async for _ in agents.run_stream(research, "look it up", []):
                pass

    with patch("agents.llm.stream", new=fake_stream), patch("agents.tools.execute", new=fake_execute):
        asyncio.run(run())
    nudged = ["[NOTE: that was call #" in t for t in tool_messages]
    assert nudged == [False, False, True, True]


def _app_with_probe(loader):
    """A tiny ASGI app behind loader.PinConfig whose one request reads the
    config, waits for the test to reload, and reads it again."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    gate = {"reached": asyncio.Event(), "go": asyncio.Event()}

    async def probe(request):
        before = loader.current()
        gate["reached"].set()
        await gate["go"].wait()
        after = loader.current()
        return JSONResponse({"same": before is after, "loaded_at": [before.loaded_at, after.loaded_at]})

    app = Starlette(routes=[Route("/probe", probe)])
    return loader.PinConfig(app), gate


@pytest.mark.parametrize("loader", [config_loader, ts_config], ids=["orchestrator", "tool_service"])
def test_middleware_pins_a_request_across_a_reload(live, loader):
    app, gate = _app_with_probe(loader)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            task = asyncio.create_task(client.get("/probe"))
            await gate["reached"].wait()
            old = loader.latest()
            target = live / ("music.yaml" if loader is ts_config else "tuning.yaml")
            if loader is ts_config:
                _edit(target, "timeout_s: 8", "timeout_s: 9")
            else:
                _edit(target, "research: 8", "research: 7")
            assert loader.reload()["ok"]
            assert loader.latest() is not old
            gate["go"].set()
            return (await task).json()

    body = asyncio.run(run())
    assert body["same"] is True


def test_both_apps_install_the_pin_outermost():
    import main
    import tool_service.main as ts_main
    assert main.app.user_middleware[0].cls is config_loader.PinConfig
    assert ts_main.app.user_middleware[0].cls is ts_config.PinConfig


def test_reload_reports_changes_against_the_latest_not_the_pin(live):
    """A reload made inside a pinned request (the /settings save) diffs
    against the config in effect, not against the request's pin."""
    with config_loader.pinned():
        _edit(live / "tuning.yaml", "research: 8", "research: 7")
        assert config_loader.reload()["changed"] == ["tuning.yaml:agent_loop.max_rounds.research: 8 -> 7"]
        _edit(live / "tuning.yaml", "research: 7", "research: 6")
        assert config_loader.reload()["changed"] == ["tuning.yaml:agent_loop.max_rounds.research: 7 -> 6"]
        assert config_loader.current().tuning.agent_loop.max_rounds.research == 8
    assert config_loader.current().tuning.agent_loop.max_rounds.research == 6


def test_loop_texts_are_the_old_bytes():
    """The five loop texts moved from inline f-strings to named templates
    (so /settings can show them); the filled text is byte for byte the old."""
    t, a, n = "web_search", "devops", 4
    assert agents.TOOL_REJECTED_TEXT.format(tool=t, agent=a) == (
        f"[TOOL: {t} — REJECTED: not available to the {a} agent. It was not run. Use only "
        "the tools you were given.]")
    assert agents.DUPLICATE_CALL_TEXT.format(tool=t) == (
        f"[{t} was already called with these exact arguments this turn; use the earlier result]")
    assert agents.NO_SUCH_SPECIALIST_TEXT.format(name="x") == "[no such specialist: x]"
    assert agents.REPEAT_CALL_TEXT.format(n=n, tool=t) == (
        f"\n\n[NOTE: that was call #{n} to {t} this turn. Do not call {t} again — answer from the "
        "results above, or use a different tool if one fits better.]")
    assert agents.BUDGET_EXHAUSTED_TEXT == (
        "[Your tool budget for this question is exhausted — no more tool calls are possible. Using "
        "ONLY the information gathered above, give your best final answer now. If parts are missing, "
        "say which parts you could not look up. Do not write tool-call syntax.]")
