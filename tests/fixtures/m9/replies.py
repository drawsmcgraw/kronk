#!/usr/bin/env python3
"""What every route sends for a request run alone, with a stubbed model and
tool_service (sprint M9, criterion 12).

`run_all(main)` drives the orchestrator app (the module `main`) through
every chat route, streaming and not, voice and display, for a fixed set of
scenarios, and returns {scenario: response bytes} with the parts that change
from run to run normalised (timestamps, request ids, completion ids, timing
values). The fixture replies.json was written ONCE from the tree before M9
(commit b762fe9) and tests/test_m9_bytes.py holds HEAD to it:

    git archive b762fe9 orchestrator config | tar -x -C <dir>
    KRONK_CONFIG_DIR=<dir>/config tests/.venv/bin/python \
        tests/fixtures/m9/replies.py <dir>/orchestrator tests/fixtures/m9/replies.json

Regenerating it from the current tree proves nothing about M9.
"""
import asyncio
import io
import json
import os
import re
import sys
import tempfile
import unittest.mock as mock_module
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

STAMP = "[kronk-origin] device=office_dev area=Office"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class ToolService:
    def __init__(self, tools_url):
        self.url = tools_url

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    def _answer(self, path):
        return {
            "/music/control": (200, {"detail": "Paused on the Office speaker."}),
            "/music": (200, {"detail": "Playing Bonobo on the Office speaker."}),
            "/music/now_playing": (502, {"detail": "Home Assistant is unreachable (ConnectError)."}),
        }.get(path, (200, {"location": "Laurel", "summary": "sunny, high 64F"}))

    async def post(self, url, *a, json=None, **kw):
        await asyncio.sleep(0)
        return _Resp(*self._answer(url.replace(self.url, "")))

    async def get(self, url, *a, **kw):
        await asyncio.sleep(0)
        return _Resp(*self._answer(url.split("?")[0].replace(self.url, "")))


def model(messages, model_name, tools=None):
    """Deterministic model: by the tools it is offered and the last message."""
    async def gen():
        names = {t["function"]["name"] for t in (tools or [])}
        last = messages[-1]
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        await asyncio.sleep(0)
        if "explode" in user:
            raise RuntimeError("LiteLLM returned 503: upstream down")
        if last["role"] == "user" and "play_music" in names and "jazz" in user:
            yield {"tool_calls": [{"id": "c1", "function": {"name": "play_music",
                                                             "arguments": {"query": "jazz"}}}]}
        elif last["role"] == "user" and "get_weather" in names and "Paris" in user:
            yield {"token": "Let me check. "}
            yield {"tool_calls": [{"id": "w1", "function": {"name": "get_weather",
                                                             "arguments": {"location": "Paris"}}}]}
        elif last["role"] == "user" and "ask_home" in names and "kitchen" in user:
            yield {"tool_calls": [{"id": "h1", "function": {"name": "ask_home",
                                                             "arguments": {"query": user}}}]}
        elif last["role"] == "user" and "now_playing" in names and "kitchen" in user:
            yield {"tool_calls": [{"id": "n1", "function": {"name": "now_playing",
                                                             "arguments": {"player": "kitchen"}}}]}
        else:
            for word in "**Paris** is the capital.\n\n- one\n- two\n\nThat is all.".split(" "):
                yield {"token": word + " "}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return gen()


TEXTS = {
    "direct": "pause",
    "coord": "what is the capital of France",
    "weather_tool": "what's the weather like in Paris",
    "terminal": "play some jazz",
    "delegate": "what's playing in the kitchen",
    "model_error": "please explode",
}

ROUTES = [  # (name, path, stream, voice_stream env, voice)
    ("message", "/message", None, "off", False),
    ("api_chat", "/api/chat", False, "off", False),
    ("api_chat_stream", "/api/chat", True, "off", False),
    ("v1", "/v1/chat/completions", False, "off", False),
    ("v1_stream", "/v1/chat/completions", True, "off", False),
    ("voice", "/voice/api/chat", False, "off", True),
    ("voice_stream_off", "/voice/api/chat", True, "off", True),
    ("voice_stream_on", "/voice/api/chat", True, "on", True),
]

_NORMALISE = [
    (re.compile(r'"created_at": ?"[^"]+"'), '"created_at": "T"'),
    (re.compile(r'"created": ?\d+'), '"created": 0'),
    (re.compile(r'chatcmpl-[0-9a-f]+'), 'chatcmpl-ID'),
    (re.compile(r'\[rid [0-9a-f]+\]'), '[rid R]'),
    (re.compile(r'"(ttft_s|generation_s|s)": ?[0-9.]+'), r'"\1": 0'),
    # COORDINATOR_MODEL is read at import; another test may set it first
    (re.compile(r'"model": ?"[^"]*"'), '"model": "M"'),
]


def normalise(text: str) -> str:
    for pat, rep in _NORMALISE:
        text = pat.sub(rep, text)
    return text


def run_all(main) -> dict:
    import llm
    import tools
    out = {}
    target = "_stream_body" if hasattr(llm, "_stream_body") else "stream"
    tmp = tempfile.mkdtemp(prefix="kronk-m9-bytes-")
    from fastapi.testclient import TestClient
    with patch.object(main.metrics, "METRICS_DB", Path(tmp) / "metrics.db"), \
         patch.object(main.sessions, "SESSIONS_DB", Path(tmp) / "sessions.db"), \
         patch.dict(main.sessions._cache, {}, clear=True), \
         patch.object(llm, target, new=model), \
         patch("tools.httpx.AsyncClient", new=ToolService(tools.TOOL_SERVICE_URL)), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)):
        main.sessions.init_db()
        with TestClient(main.app) as c:
            for tname, text in TEXTS.items():
                for rname, path, stream, vs, voice in ROUTES:
                    os.environ["VOICE_STREAM"] = vs
                    if path == "/message":
                        main.sessions.clear("m9-bytes")
                        r = c.post(path, json={"text": text, "session_id": "m9-bytes"})
                        body = r.text + "\nSTORED " + json.dumps(main.sessions.window("m9-bytes"))
                    else:
                        msgs = ([{"role": "system", "content": STAMP}] if voice else []) + [
                            {"role": "user", "content": text}]
                        r = c.post(path, json={"model": "kronk", "messages": msgs, "stream": stream})
                        body = r.text
                    out[f"{tname}|{rname}"] = f"{r.status_code}\n" + normalise(body)
    os.environ.pop("VOICE_STREAM", None)
    return out


def main_cli() -> int:
    orch_dir, out_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    sys.path.insert(0, str(orch_dir))
    sys.path.insert(0, str(orch_dir.parent))
    real_open = open

    def fake_open(path, *a, **kw):
        if "/app/static/" in str(path):
            return io.StringIO("<html></html>")
        return real_open(path, *a, **kw)
    with mock_module.patch("builtins.open", side_effect=fake_open):
        import main  # noqa: E402  (the tree under test)
        replies = run_all(main)
    out_path.write_text(json.dumps(replies, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(replies)} replies to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
