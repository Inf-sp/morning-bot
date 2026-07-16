import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import cleanup
import movie_engine
import recommendation_stoplist
import saved_items


def _memory_store(monkeypatch, initial=None):
    state = {key: list(value) for key, value in (initial or {}).items()}
    monkeypatch.setattr(
        recommendation_stoplist.store,
        "get_list",
        lambda key, _cid: list(state.get(key, [])),
    )
    monkeypatch.setattr(
        recommendation_stoplist.store,
        "set_list",
        lambda key, _cid, value: state.__setitem__(key, list(value)),
    )
    return state


def test_stoplist_has_one_database_category_for_all_media(monkeypatch):
    state = _memory_store(monkeypatch)

    assert recommendation_stoplist.add("stoplist", "movie", "Патерсон", "seen")
    assert recommendation_stoplist.add("stoplist", "artist", "Massive Attack", "removed")
    assert not recommendation_stoplist.add("stoplist", "movie", "патерсон", "hidden")

    assert state[config.RECOMMENDATION_STOPLIST_KEY] == [
        {
            "category": "Не рекомендовать",
            "type": "movie",
            "value": "Патерсон",
            "reason": "seen",
        },
        {
            "category": "Не рекомендовать",
            "type": "artist",
            "value": "Massive Attack",
            "reason": "removed",
        },
    ]


def test_saved_cards_store_the_recommendation_name_not_the_whole_card(monkeypatch):
    state = _memory_store(monkeypatch)

    recommendation_stoplist.add(
        "stoplist", "book", {"text": "📚 Автор • «Название книги»\n\nОписание"})
    recommendation_stoplist.add(
        "stoplist", "artist", {"text": "🎸 Massive Attack\n\nПочему тебе зайдёт:"})

    assert [item["value"] for item in state[config.RECOMMENDATION_STOPLIST_KEY]] == [
        "Название книги",
        "Massive Attack",
    ]


def test_refresh_migrates_hidden_and_seen_lists_and_clears_old_categories(monkeypatch):
    state = _memory_store(monkeypatch, {
        config.MOVIE_BLACKLIST_KEY: ["Пылающий"],
        config.MOVIE_SEEN_KEY: ["Патерсон"],
        config.MUSIC_DISLIKE_KEY: ["Artist"],
    })

    changed = recommendation_stoplist.migrate_legacy("stoplist")

    assert changed == 3
    assert state[config.MOVIE_BLACKLIST_KEY] == []
    assert state[config.MOVIE_SEEN_KEY] == []
    assert state[config.MUSIC_DISLIKE_KEY] == []
    assert {item["category"] for item in state[config.RECOMMENDATION_STOPLIST_KEY]} == {
        "Не рекомендовать"
    }


def test_removed_saved_movie_moves_to_stoplist_and_is_filtered(monkeypatch):
    state = _memory_store(monkeypatch, {
        config.NOTES_KEY: [{
            "text": "Патерсон",
            "source": "Досуг · Кино",
            "bucket": "fav",
        }],
    })

    async def no_bucket(*_args, **_kwargs):
        return None

    monkeypatch.setattr(saved_items, "send_bucket", no_bucket)

    asyncio.run(saved_items.fav_del(None, "stoplist", 0))

    assert state[config.NOTES_KEY] == []
    assert recommendation_stoplist.values("stoplist", "movie") == ["Патерсон"]
    assert movie_engine._norm("Патерсон") in movie_engine._excluded_norms("stoplist", include_shown=False)


def test_removed_favorite_and_seen_item_use_same_stoplist(monkeypatch):
    state = _memory_store(monkeypatch)
    monkeypatch.setattr(cleanup, "_selected_values", lambda *_args: ["Патерсон"])
    monkeypatch.setattr(cleanup, "_view_delete", lambda *_args: 1)

    cleanup._apply_collection_action(
        "cinema_favorites", "stoplist", "remove", {"movie-id"})
    cleanup._apply_collection_action(
        "cinema_watched", "stoplist", "remove", {"movie-id"})

    entries = state[config.RECOMMENDATION_STOPLIST_KEY]
    assert len(entries) == 1
    assert entries[0]["category"] == "Не рекомендовать"
    assert entries[0]["value"] == "Патерсон"
