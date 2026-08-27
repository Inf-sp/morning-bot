import os
from datetime import datetime, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

import category_news
import config
from ui import leisure as leisure_ui
from ui import menu as menu_ui
from ui import travel as travel_ui
from ui import wardrobe as wardrobe_ui


def _news(now):
    return {
        "id": "story",
        "category": "food",
        "text_ru": "EFSA обновила рекомендации по безопасному хранению продуктов.",
        "source_name": "EFSA",
        "source_url": "https://efsa.europa.eu/news/story",
        "published_at": now.isoformat(),
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "importance": 90,
        "confidence": 95,
        "evidence_urls": ["https://efsa.europa.eu/news/story"],
    }


def test_all_home_renderers_append_one_linked_weekly_news_line():
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    news = _news(now)

    wardrobe = wardrobe_ui.render_wardrobe_message(
        {"main_accent": "Спокойная палитра связывает комплект."}, news=news,
    )
    food = menu_ui.restaurant_menu({}, news=news)
    movie = leisure_ui.movie_now_playing_screen(
        "Алкмар", [{"title": "Фильм", "genres": ["drama"]}],
        {"rebus": {"emoji": "🎬", "answer": "Ответ"}}, news=news,
    )
    travel = travel_ui.home_screen({
        "emoji": "🚆", "transport_title": "Поезд", "intro": "Маршрут на день.",
        "from": "Алкмар", "to": "Утрехт", "route": ["Центр", "Музей"],
        "tip": "Проверь время отправления.",
    }, news=news)

    for message in (wardrobe, food, movie, travel):
        assert (
            "📰 На неделе: EFSA обновила рекомендации по безопасному хранению продуктов."
        ) in message.text
        links = [
            entity for entity in message.entities
            if entity.type == MessageEntity.TEXT_LINK
        ]
        assert any(entity.url == news["source_url"] for entity in links)


def test_cached_line_reads_valid_item_without_search(monkeypatch):
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    monkeypatch.setattr(
        category_news.store, "_load",
        lambda _key: {"categories": {"food": {"items": [_news(now)]}}},
    )
    monkeypatch.setattr(
        category_news, "_discover",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cached read must not search")),
    )

    assert category_news.cached_line("food", now=now)["id"] == "story"
    assert category_news.cached_line("unknown", now=now) is None


def test_editor_accepts_two_independent_sources_and_saves_pool(monkeypatch):
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    memory = {}
    rows = [
        {
            "url": "https://reuters.com/world/food-change",
            "domain": "reuters.com",
            "title": "Food safety rules change",
            "content": "Authorities introduced consequential food safety rules.",
            "published_at": now.isoformat(),
        },
        {
            "url": "https://apnews.com/article/food-change",
            "domain": "apnews.com",
            "title": "New food safety rules",
            "content": "The same authorities confirmed new food safety rules.",
            "published_at": now.isoformat(),
        },
    ]

    monkeypatch.setattr(category_news.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(category_news.store, "_save", lambda key, value: memory.update({key: value}))
    monkeypatch.setattr(
        category_news, "_discover",
        lambda category, _now: rows if category == "food" else [],
    )
    monkeypatch.setattr(category_news.ai, "llm_json", lambda *_args, **_kwargs: {
        "categories": {
            "food": [{
                "text_ru": "Новые правила безопасности продуктов изменят требования к хранению в Европе.",
                "importance": 88,
                "confidence": 92,
                "evidence_ids": ["food:0", "food:1"],
            }],
        },
    })

    report = category_news.refresh_pool(categories=("food",), now=now, force=True)

    assert report["updated"] == ("food",)
    item = memory[config.CATEGORY_NEWS_CACHE_KEY]["categories"]["food"]["items"][0]
    assert item["source_name"] == "Reuters"
    assert len(item["evidence_urls"]) == 2
    assert category_news.cached_line("food", now=now)["id"] == item["id"]


def test_editor_rejects_one_non_primary_source():
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    rows = [{
        "url": "https://reuters.com/world/food-change",
        "domain": "reuters.com",
        "title": "Food change",
        "content": "A claimed change.",
        "published_at": now.isoformat(),
    }]
    decisions = [{
        "text_ru": "Новые правила заметно изменят требования к хранению продуктов в Европе.",
        "importance": 90,
        "confidence": 90,
        "evidence_ids": ["food:0"],
    }]

    assert category_news._selected_items("food", decisions, rows, now) == []


def test_editor_accepts_one_official_primary_source():
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    rows = [{
        "url": "https://efsa.europa.eu/news/food-change",
        "domain": "efsa.europa.eu",
        "title": "EFSA changes food safety guidance",
        "content": "EFSA published consequential food safety guidance.",
        "published_at": now.isoformat(),
    }]
    decisions = [{
        "text_ru": "EFSA обновила важные рекомендации по безопасному хранению продуктов в Европе.",
        "importance": 90,
        "confidence": 95,
        "evidence_ids": ["food:0"],
    }]

    items = category_news._selected_items("food", decisions, rows, now)

    assert len(items) == 1
    assert items[0]["source_name"] == "EFSA"


def test_failed_category_discovery_does_not_block_other_categories(monkeypatch):
    now = datetime(2026, 8, 27, 12, tzinfo=config.TZ)
    memory = {}
    official_food_row = {
        "url": "https://efsa.europa.eu/news/food-change",
        "domain": "efsa.europa.eu",
        "title": "EFSA changes food safety guidance",
        "content": "EFSA published consequential food safety guidance.",
        "published_at": now.isoformat(),
    }

    monkeypatch.setattr(category_news.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(category_news.store, "_save", lambda key, value: memory.update({key: value}))

    def discover(category, _now):
        if category == "wardrobe":
            raise RuntimeError("temporary search failure")
        return [official_food_row]

    monkeypatch.setattr(category_news, "_discover", discover)
    monkeypatch.setattr(category_news.ai, "llm_json", lambda *_args, **_kwargs: {
        "categories": {
            "food": [{
                "text_ru": "EFSA обновила важные рекомендации по безопасному хранению продуктов в Европе.",
                "importance": 90,
                "confidence": 95,
                "evidence_ids": ["food:0"],
            }],
        },
    })

    report = category_news.refresh_pool(
        categories=("wardrobe", "food"), now=now, force=True,
    )

    assert report["updated"] == ("food",)
    assert report["missing"] == ("wardrobe",)
