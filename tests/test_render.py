"""Render-profile tests (docs/plans/RENDER_PROFILES_PLAN.md).

to_speech() is the global speech scrub: every markdown construct the house
emits must flatten to clean spoken prose, plain text must pass through,
and the /voice mount must be the only place it applies.
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import render


# ── to_speech unit behavior ──────────────────────────────────────────────────

def test_headers_get_colon_pause():
    # Colon, not period: measured 420ms vs 200ms pause on live piper.
    assert render.to_speech("### World\nStories here.") == "World:\nStories here."
    assert render.to_speech("## Already ended?\nx") == "Already ended?\nx."


def test_bold_italic_links_and_code():
    src = ("**CVE-2026-73570** is an *RCE* in [Zimbra](https://x.y/z) — "
           "patch via `zmcontrol`.")
    assert render.to_speech(src) == (
        "CVE-2026-73570 is an RCE in Zimbra — patch via zmcontrol.")


def test_snake_case_survives_underscore_emphasis():
    assert render.to_speech("run tool_service and _really_ check max_rounds") == \
        "run tool_service and really check max_rounds."


def test_bullets_flatten():
    src = "- first item\n* second item\n  - nested item"
    assert render.to_speech(src) == "first item.\nsecond item.\nnested item."


def test_code_fence_markers_stripped_content_kept():
    src = "```bash\nsystemctl status\n```"
    assert render.to_speech(src) == "systemctl status."


def test_table_flattens_to_comma_rows():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert render.to_speech(src) == "a, b.\n1, 2."


def test_plain_text_untouched():
    src = "The panels produced 29.9 kWh yesterday, worth about $4.83."
    assert render.to_speech(src) == src


def test_idempotent():
    src = "### Head\n**bold** and [link](http://x) and\n\n\n- item"
    once = render.to_speech(src)
    assert render.to_speech(once) == once


def test_news_brief_shape_end_to_end():
    src = ("### World\n**Iran sanctions** dominate.\n\n### Tech & AI\n"
           "- Model releases\n\n### Cybersecurity\nCISA orders patching.")
    out = render.to_speech(src)
    assert "#" not in out and "*" not in out and "- " not in out
    assert "World:" in out and "Cybersecurity:" in out
    assert "Iran sanctions dominate." in out


# ── shim profiles: /api/chat display vs /voice/api/chat speech ───────────────

MARKDOWN_REPLY = "### Brief\n**Bold lead** and [a link](https://x.y)."
SCRUBBED_REPLY = "Brief:\nBold lead and a link."


def _fake_pipeline():
    async def fake_classify(text, history):
        return "direct"

    def fake_run_stream(agent, task, context, **kwargs):
        async def gen():
            yield {"type": "token", "text": MARKDOWN_REPLY}
            yield {"type": "done", "model": "gemma-4-e4b", "ok": True}
        return gen()
    return fake_classify, fake_run_stream


@pytest.fixture
def client():
    import orchestrator.main as orch
    return TestClient(orch.app)


def _post_chat(client, path, stream):
    import orchestrator.main as orch
    fake_classify, fake_run_stream = _fake_pipeline()
    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=fake_run_stream):
        return client.post(path, json={
            "model": "kronk", "stream": stream,
            "messages": [{"role": "user", "content": "brief me"}]})


def test_display_mount_preserves_markdown(client):
    resp = _post_chat(client, "/api/chat", stream=False)
    assert resp.json()["message"]["content"] == MARKDOWN_REPLY


def test_voice_mount_scrubs_markdown(client):
    resp = _post_chat(client, "/voice/api/chat", stream=False)
    assert resp.json()["message"]["content"] == SCRUBBED_REPLY


def test_voice_mount_streaming_is_buffered_single_chunk(client):
    resp = _post_chat(client, "/voice/api/chat", stream=True)
    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    assert len(lines) == 2                      # one content chunk + terminal
    assert lines[0]["message"]["content"] == SCRUBBED_REPLY
    assert lines[1]["done"] is True


def test_display_mount_streaming_unchanged(client):
    resp = _post_chat(client, "/api/chat", stream=True)
    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    contents = "".join(l["message"]["content"] for l in lines if not l["done"])
    assert contents == MARKDOWN_REPLY           # raw tokens, no scrub


def test_voice_mount_mirrors_protocol_surface(client):
    assert client.get("/voice/api/tags").json() == client.get("/api/tags").json()
    assert client.get("/voice/api/version").json() == client.get("/api/version").json()
    assert client.post("/voice/api/show").json() == client.post("/api/show").json()
