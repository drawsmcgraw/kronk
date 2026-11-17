"""What the M11 decision order may read (main.ReadOnlyMusic, ma_read) and
its one model question (main.ask_style_or_named). Criteria 10 and 14."""
import asyncio
import json
import sqlite3
from unittest.mock import patch

import httpx
import pytest

import tool_service.main as ts
from tool_service import config_loader, taste

pytestmark = pytest.mark.usefixtures("no_network")


# ── reads only ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", ["music/favorites/set_item", "player_queues/play_media",
                                     "players/cmd/play", "player_queues/shuffle", "config/providers/save"])
def test_ma_read_refuses_anything_but_a_read(command):
    sent = []

    async def fake(cmd, _timeout=20, **a):
        sent.append(cmd)
    with patch.object(ts, "ma_command", new=fake):
        with pytest.raises(ts.MAError, match="not a read command"):
            asyncio.run(ts.ma_read(command, item="x"))
    assert sent == []


def test_the_read_set_is_the_one_the_snapshot_tests_use():
    from tests import music_snapshot
    assert ts.MA_READ_COMMANDS == music_snapshot.READ_COMMANDS


def test_the_resolvers_reads_go_through_ma_read():
    """Search, library listing and station lookup: a write slipped into any
    of them would be refused, not sent."""
    seen = []

    async def fake_read(cmd, _timeout=20, **a):
        seen.append(cmd)
        return {} if cmd == "music/search" else []
    with patch.object(ts, "ma_read", new=fake_read):
        asyncio.run(ts._search_each("x", ["album", "track"], 5))
        asyncio.run(ts._library_items("radio"))
        asyncio.run(ts._pandora_station("x"))
    assert seen == ["music/search", "music/search", "music/radios/library_items", "music/item_by_name"]


def test_library_exact_matches_the_whole_name_and_the_artist():
    items = [{"uri": "library://album/1", "name": "Blue Lines (Remastered)", "media_type": "album",
              "artists": [{"name": "Massive Attack"}]},
             {"uri": "library://album/2", "name": "Blue Lines", "media_type": "album",
              "artists": [{"name": "Massive Attack"}]}]
    calls = []

    async def fake(cmd, _timeout=20, **a):
        calls.append((cmd, a))
        return items
    r = ts.ReadOnlyMusic()
    with patch.object(ts, "ma_command", new=fake):
        c = asyncio.run(r.library_exact("album", "blue lines", "Massive Attack"))
        assert c.uri == "library://album/2"
        assert asyncio.run(r.library_exact("album", "Blue Lines", "Portishead")) is None
    assert calls[0] == ("music/albums/library_items", {"search": "blue lines", "limit": ts.EXACT_LOOKUP_LIMIT})


def test_db_readonly_never_creates_a_file(tmp_path):
    missing = tmp_path / "none.db"
    with pytest.raises(sqlite3.Error):
        taste.db_readonly(missing)
    assert not missing.exists()


def test_genre_known_reads_without_writing(tmp_path):
    dbp = tmp_path / "t.db"
    con = taste.db(dbp)
    with con:
        con.execute("INSERT INTO artists (key, name, uri, genres, genre_source, history, station, library, score) "
                    "VALUES ('artist:x','x',NULL,'[\"city pop\"]','ma',0,0,0,0)")
    con.close()
    before = dbp.stat().st_mtime_ns
    tf = taste.TasteFile(tmp_path / "taste.yaml")         # never created: create=False
    with patch.object(taste, "TASTE_DB", dbp), patch.object(taste, "TASTE", tf):
        r = ts.ReadOnlyMusic()
        assert r.genre_known("some city pop music") is True
        assert r.genre_known("Portishead") is False
    assert dbp.stat().st_mtime_ns == before and not (tmp_path / "taste.yaml").exists()


def test_genre_known_without_a_taste_db_uses_the_builtin_list(tmp_path):
    with patch.object(taste, "TASTE_DB", tmp_path / "none.db"):
        assert ts.ReadOnlyMusic().genre_known("jazz") is True
    assert not (tmp_path / "none.db").exists()


# ── the model question ───────────────────────────────────────────────────────

class FakeLLM:
    answer = '{"reading": "style"}'
    status = 200
    delay = 0.0
    sent: list = []

    def __init__(self, *a, timeout=None, **kw):
        FakeLLM.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        FakeLLM.sent.append((url, json))
        if FakeLLM.delay:
            await asyncio.sleep(FakeLLM.delay)
        if isinstance(FakeLLM.answer, Exception):
            raise FakeLLM.answer
        return httpx.Response(FakeLLM.status, json={"choices": [{"message": {"content": FakeLLM.answer}}]},
                              request=httpx.Request("POST", url))


@pytest.fixture
def llm():
    FakeLLM.answer, FakeLLM.status, FakeLLM.delay, FakeLLM.sent = '{"reading": "style"}', 200, 0.0, []
    with patch.object(ts.httpx, "AsyncClient", FakeLLM):
        yield FakeLLM


def test_the_question_is_constrained_to_two_values(llm):
    ans, info = asyncio.run(ts.ask_style_or_named("play italian music", "italian"))
    assert (ans, info) == ("style", {})
    url, body = llm.sent[0]
    assert url == f"{ts.MUSIC_MODEL_URL}/chat/completions"
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["reading"]["enum"] == ["style", "named"]
    assert body["model"] == "gemma-4-e4b" and body["temperature"] == 0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}       # thinking: false
    assert "italian" in body["messages"][1]["content"]


def test_thinking_on_drops_the_template_switch(llm):
    with patch.object(config_loader.current().music.decision.model, "thinking", True):
        asyncio.run(ts.ask_style_or_named("play x", "x"))
    assert "chat_template_kwargs" not in llm.sent[0][1]


@pytest.mark.parametrize("answer,status,err", [
    ('{"reading": "maybe"}', 200, "outside the two values"),
    ("not json", 200, "JSONDecodeError"),
    ('{"reading": "style"}', 500, "HTTPStatusError"),
    (httpx.ConnectError("refused"), 200, "ConnectError"),
])
def test_a_bad_answer_or_a_down_model_gives_no_reading(llm, answer, status, err):
    llm.answer, llm.status = answer, status
    ans, info = asyncio.run(ts.ask_style_or_named("play x", "x"))
    assert ans is None and err in info["error"]


def test_the_question_has_a_timeout(llm):
    llm.delay = 1.0
    with patch.object(config_loader.current().music.decision.model, "timeout_s", 0.05):
        ans, info = asyncio.run(ts.ask_style_or_named("play x", "x"))
    assert ans is None and info["error"] == "no answer within 0.05s"
