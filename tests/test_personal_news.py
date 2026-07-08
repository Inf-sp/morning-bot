from datetime import datetime, timedelta
import asyncio
import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import config
import personal_news as pn


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    mem = {}

    def load(key):
        return mem.get(key, {})

    def save(key, data):
        mem[key] = data

    def mutate(key, fn):
        cur = mem.get(key, {})
        new, result = fn(cur)
        mem[key] = new
        return result

    monkeypatch.setattr(pn.store, "_load", load)
    monkeypatch.setattr(pn.store, "_save", save)
    monkeypatch.setattr(pn.store, "mutate_kv", mutate)
    monkeypatch.setattr(pn.store, "get_settings", lambda cid: {
        "city": "Алкмар", "country": "Нидерланды", "cc": "NL"
    })
    monkeypatch.setattr(pn.store, "get_list", lambda key, cid: [])
    monkeypatch.setattr(pn.store, "get_profile", lambda cid: mem.get("profile", {}).get(str(cid), {}))

    def set_profile(cid, prof):
        mem.setdefault("profile", {})[str(cid)] = prof

    monkeypatch.setattr(pn.store, "set_profile", set_profile)
    return mem


def _news(
    title="OpenAI API pricing update",
    url="https://openai.com/news/x",
    days=0,
    content="OpenAI announced a new API pricing and limit update for developers.",
    category="tech",
    language="en",
):
    dt = datetime.now(config.TZ) - timedelta(days=days)
    return {
        "title": title,
        "url": url,
        "content": content,
        "published_at": dt.isoformat(),
        "_category_hint": category,
        "_query_language": language,
    }


def _score_one(monkeypatch):
    monkeypatch.setattr(pn, "_score_items", lambda cid, items: [{
        "is_relevant": True,
        "importance": 4,
        "category": "tech",
        "title_ru": "OpenAI изменил лимиты API",
        "summary_ru": "Появилось подтверждённое изменение лимитов.",
        "why_it_matters_ru": "это может повлиять на работу бота.",
        "source_name": "OpenAI",
        "source_url": "https://openai.com/news/x",
        "published_at": datetime.now(config.TZ).isoformat(),
        "action_type": "prepare",
    }])


def test_fresh_cache_skips_tavily(monkeypatch):
    called = {"search": 0}
    _score_one(monkeypatch)
    entry, _ = pn.build_from_sources("1", "today", [_news()])
    monkeypatch.setattr(pn, "_search_all", lambda cid: called.__setitem__("search", called["search"] + 1))

    cached = pn._cache_get("1", "today")

    assert cached["text"] == entry["text"]
    assert called["search"] == 0


def test_expired_cache_requires_one_search(monkeypatch, isolated_store):
    _score_one(monkeypatch)
    pn.build_from_sources("1", "today", [_news()])
    key = pn.cache_key("1", "today")
    isolated_store[pn.NEWS_CACHE_KEY][key]["ts"] = 1
    calls = {"n": 0}

    def fake_search(cid):
        calls["n"] += 1
        return [_news("Cloudflare API outage update", "https://cloudflare.com/news/outage")]

    monkeypatch.setattr(pn, "_search_all", fake_search)
    entry, _ = pn.build_from_sources("1", "today", fake_search("1"))

    assert calls["n"] == 1
    assert "Новости" in entry["text"]


def test_refresh_cooldown_blocks_tavily(monkeypatch, isolated_store):
    calls = {"n": 0}
    monkeypatch.setattr(pn, "_search_all", lambda cid: calls.__setitem__("n", calls["n"] + 1))
    pn._set_last_refresh("1")

    last = pn._last_refresh("1")
    assert last > 0
    assert calls["n"] == 0


def test_refresh_cooldown_message_for_regular_user(monkeypatch):
    sent = []
    calls = {"n": 0}

    class FakeBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def fake_send_period(bot, cid, period="today", force=False):
        calls["n"] += 1

    monkeypatch.setattr(pn, "send_period", fake_send_period)
    monkeypatch.setattr(pn, "_is_admin", lambda cid: False)
    pn._set_last_refresh("1")

    asyncio.run(pn.refresh(FakeBot(), "1"))

    assert calls["n"] == 0
    assert "Последняя проверка была" in sent[0]["text"]


def test_admin_refresh_bypasses_cooldown(monkeypatch):
    calls = []

    class FakeBot:
        async def send_message(self, **kwargs):
            raise AssertionError("admin refresh should not send cooldown message")

    async def fake_send_period(bot, cid, period="today", force=False):
        calls.append((cid, period, force))

    monkeypatch.setattr(pn.config, "ADMIN_CHAT_ID", "1")
    monkeypatch.setattr(pn, "send_period", fake_send_period)
    pn._set_last_refresh("1")

    asyncio.run(pn.refresh(FakeBot(), "1", "today"))

    assert calls == [("1", "today", True)]


def test_daily_and_monthly_budget_block_new_requests():
    assert all(pn._reserve_credits(1) for _ in range(pn.NEWS_DAILY_CREDIT_BUDGET))
    assert pn._reserve_credits(1) is False


def test_monthly_budget_is_1000(monkeypatch):
    day = {"n": 0}

    def fake_today(now=None):
        day["n"] += 1
        return f"2026-07-{day['n']:02d}"

    monkeypatch.setattr(pn, "_today_key", fake_today)
    assert all(pn._reserve_credits(1) for _ in range(1000))
    assert pn._reserve_credits(1) is False


def test_irrelevant_news_shows_honest_empty_state(monkeypatch):
    source = _news(
        "Random lifestyle article",
        "https://example.com/a",
        content="A generic story without a concrete useful change.",
        category="netherlands",
    )
    entry, _ = pn.build_from_sources("1", "today", [source])
    assert "Сегодня нет достаточно важных новостей" in entry["text"]
    assert "Random lifestyle article" not in entry["text"]


def test_news_older_than_7_days_filtered_out(monkeypatch):
    entry, _ = pn.build_from_sources("1", "today", [_news(days=8)])
    assert "Сегодня нет достаточно важных новостей" in entry["text"]


def test_news_without_date_filtered_out(monkeypatch):
    source = {
        "title": "API pricing update",
        "url": "https://openai.com/api/pricing",
        "content": "New API pricing update and limit changes.",
        "_category_hint": "tech",
        "_query_language": "en",
    }
    entry, _ = pn.build_from_sources("1", "today", [source])
    assert "Сегодня нет достаточно важных новостей" in entry["text"]


def test_duplicates_are_merged():
    items = pn.strict_filter([
        _news("New API limit change", "https://openai.com/news/x"),
        _news("New API limit change!", "https://openai.com/news/x?utm=1"),
    ])
    assert len(pn._dedupe_semantic(items)) == 1


def test_empty_result_card():
    text, buttons = pn._build_card([])
    assert "Сегодня нет достаточно важных новостей" in text
    assert "Проверил:" in text
    assert buttons == []


def test_repeated_url_is_not_shown(monkeypatch):
    first, _ = pn.build_from_sources("1", "today", [_news()])
    second, _ = pn.build_from_sources("1", "today", [_news()])

    assert "OpenAI API pricing update" in first["text"]
    assert "OpenAI API pricing update" not in second["text"]


def test_similar_title_from_history_is_not_shown(monkeypatch):
    pn.build_from_sources("1", "today", [_news(
        "OpenAI API pricing update",
        "https://openai.com/news/x",
    )])
    entry, _ = pn.build_from_sources("1", "today", [_news(
        "OpenAI API pricing update announced",
        "https://theverge.com/openai-api-pricing",
    )])
    assert "OpenAI API pricing update announced" not in entry["text"]


def test_one_item_per_category_in_release(monkeypatch):
    sources = [
        _news("OpenAI API pricing update", "https://openai.com/news/x"),
        _news("Telegram API limit update", "https://telegram.org/blog/api-limit"),
    ]
    entry, _ = pn.build_from_sources("1", "today", sources)
    assert entry["text"].count("🤖 AI / технологии") == 1
    assert len(entry["items"]) == 1


def test_alkmaar_local_news_has_high_priority(monkeypatch):
    sources = [
        _news(
            "Nederland algemene regels wijziging",
            "https://nos.nl/artikel/1",
            content="Nederland krijgt een algemene wijziging voor inwoners.",
            category="netherlands",
            language="nl",
        ),
        _news(
            "Gemeente Alkmaar meldt wegwerkzaamheden vandaag",
            "https://gemeentealkmaar.nl/nieuws/wegwerkzaamheden",
            content="Gemeente Alkmaar meldt vandaag nieuwe wegwerkzaamheden en afsluiting.",
            category="city",
            language="nl",
        ),
    ]
    entry, _ = pn.build_from_sources("1", "today", sources)
    assert entry["items"][0]["category"] == "city"
    assert entry["items"][0]["relevance_score"] >= 70


def test_dutch_query_used_for_alkmaar_and_netherlands():
    queries = pn._queries_for("1")
    assert any(q["language"] == "nl" and "Alkmaar" in q["query"] for q in queries)
    assert any(q["language"] == "nl" and "Nederland" in q["query"] for q in queries)


def test_english_query_used_for_openai_apple_telegram():
    queries = pn._queries_for("1")
    tech = [q for q in queries if q["category"] == "tech"]
    assert tech
    assert all(q["language"] == "en" for q in tech)
    assert any("OpenAI" in q["query"] and "Apple" in q["query"] and "Telegram" in q["query"] for q in tech)


def test_admin_stats_shows_total_tavily_monthly_limit():
    pn._reserve_credits(1)
    text = pn.admin_stats_text()
    assert "Месяц: 1 / 1000 credits" in text


def test_news_limit_can_cover_all_categories():
    assert pn.NEWS_MAX_ITEMS == 5
    assert len(pn._CATEGORY_LABELS) >= 12


def test_search_uses_category_queries_with_domains_and_freshness(monkeypatch):
    calls = []

    def fake_reserve(credits):
        return len(calls) < 8

    def fake_tavily(query, max_results=5, domains=None, time_range=None):
        calls.append((query, max_results, domains, time_range))
        return []

    monkeypatch.setattr(pn, "_reserve_credits", fake_reserve)
    monkeypatch.setattr(pn, "_tavily_search", fake_tavily)

    pn._search_all("1")

    assert any("Alkmaar" in query and domains for query, _, domains, _ in calls)
    assert any(time_range == "week" for _, _, _, time_range in calls)
    assert all(max_results == 5 for _, max_results, _, _ in calls)


def test_compact_format_has_no_debug_info():
    item = pn.NewsItem(
        title="OpenAI API pricing update",
        summary="OpenAI changed API pricing.",
        url="https://openai.com/news/x",
        source="OpenAI",
        published_at=datetime.now(config.TZ).isoformat(),
        category="tech",
        language="en",
        relevance_score=90,
        why_important="это полезно для работы бота.",
        action_hint="Подробнее",
        hash="x",
    )
    text, _ = pn._build_card([pn.asdict(item)])
    assert "Коротко:" in text
    assert "💡 Почему важно:" in text
    assert "relevance" not in text.lower()
    assert "raw query" not in text.lower()
    assert "search provider" not in text.lower()
    assert "Почему тебе" not in text


def test_news_keyboard_has_no_period_or_topic_buttons():
    kb = pn._default_keyboard("today", cid="1")
    labels = [
        button.text
        for row in kb.inline_keyboard
        for button in row
    ]

    assert "📰 Сегодня" not in labels
    assert "📅 За неделю" not in labels
    assert "⚙️ Темы" not in labels
    assert not any("Проверить" in label for label in labels)
    assert "⬅️ Назад" in labels
