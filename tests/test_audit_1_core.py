import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cleanup
import util


def test_chunks_never_split_surrogate_pair():
    text = "a" * 3999 + "😀" + "b" * 10

    chunks = util.chunk_text_with_entities(text, [], 4000)

    assert "".join(chunk for chunk, _ in chunks) == text
    assert all(util.u16_len(chunk) <= 4000 for chunk, _ in chunks)


def _view_with_items(monkeypatch, items, page=0):
    monkeypatch.setattr(cleanup, "_view_items", lambda ctx, cid: ("T", list(items), "m_menu"))
    monkeypatch.setattr(cleanup, "_view_store_key", lambda ctx: None)
    rendered = []

    async def render(bot, cid, view_id, q=None):
        rendered.append(view_id)

    monkeypatch.setattr(cleanup, "_render_view", render)
    view_id = "audit1"
    cleanup._views[view_id] = {
        "ctx": "cinema_favorites", "revision": 0, "selected_ids": set(), "page": page,
        "back": "m_menu", "created_at": cleanup.time.time(), "editing": True,
    }
    return view_id


def test_toggle_uses_short_id_of_visible_page(monkeypatch):
    # Оба id начинаются с "abcd"; на второй странице короткий id = "abcd",
    # а первая страница содержит другой элемент с тем же префиксом.
    first_page = [(f"abcd{i:028x}", f"a{i}") for i in range(cleanup.CLEAN_PAGE)]
    target = ("abcdffff" + "0" * 24, "zz")
    view_id = _view_with_items(monkeypatch, first_page + [target], page=1)

    asyncio.run(cleanup.handle_view_callback(None, "1", f"clt:{view_id}:abcd"))

    assert cleanup._views.pop(view_id)["selected_ids"] == {target[0]}


def test_select_page_uses_display_order_for_artists(monkeypatch):
    items = [(f"id{i}", f"name{i}") for i in range(cleanup.CLEAN_PAGE + 2)]
    grouped = list(reversed(items))
    import leisure_music
    monkeypatch.setattr(leisure_music, "group_favorite_artist_items", lambda cid, rows: grouped)
    view_id = _view_with_items(monkeypatch, items)
    cleanup._views[view_id]["ctx"] = "music_favorite_artists"

    asyncio.run(cleanup.handle_view_callback(None, "1", f"cla:{view_id}:0"))

    expected = {i for i, _ in grouped[:cleanup.CLEAN_PAGE]}
    assert cleanup._views.pop(view_id)["selected_ids"] == expected
