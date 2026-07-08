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


def _news(title="New API limit", url="https://openai.com/news/x", days=0):
    dt = datetime.now(config.TZ) - timedelta(days=days)
    return {
        "title": title,
        "url": url,
        "content": "New pricing and API limit change confirmed.",
        "published_at": dt.isoformat(),
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
        return [_news("New outage", "https://cloudflare.com/news/outage")]

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


def test_irrelevant_news_falls_back_to_source_card(monkeypatch):
    monkeypatch.setattr(pn, "_score_items", lambda cid, items: [])
    entry, _ = pn.build_from_sources("1", "today", [_news("Random article", "https://example.com/a")])
    assert "Random article" in entry["text"]
    assert "ничего действительно важного" not in entry["text"]


def test_old_news_filtered_out(monkeypatch):
    monkeypatch.setattr(pn, "_score_items", lambda cid, items: pytest.fail("old item reached Gemini"))
    entry, _ = pn.build_from_sources("1", "today", [_news(days=8)])
    assert "Новости пока не загрузились" in entry["text"]


def test_official_undated_sources_reach_gemini(monkeypatch):
    seen = {}

    def fake_score(cid, items):
        seen["items"] = items
        return []

    monkeypatch.setattr(pn, "_score_items", fake_score)
    source = {
        "title": "API pricing update",
        "url": "https://openai.com/api/pricing",
        "content": "New API pricing update and limit changes.",
    }
    pn.build_from_sources("1", "today", [source])
    assert len(seen["items"]) == 1
    assert seen["items"][0]["_date_missing"] is True


def test_duplicates_are_merged():
    items = pn.strict_filter([
        _news("New API limit change", "https://openai.com/news/x"),
        _news("New API limit change!", "https://openai.com/news/x?utm=1"),
    ])
    assert len(pn._dedupe_semantic(items)) == 1


def test_empty_result_card():
    text, buttons = pn._build_card([])
    assert "Новости пока не загрузились" in text
    assert buttons == []


def test_stale_cache_can_be_rendered(monkeypatch, isolated_store):
    _score_one(monkeypatch)
    pn.build_from_sources("1", "today", [_news()])
    key = pn.cache_key("1", "today")
    isolated_store[pn.NEWS_CACHE_KEY][key]["ts"] = 1

    stale = pn._cache_get("1", "today", allow_stale=True)
    text, _ = pn._build_card(stale["items"], stale["ts"], stale=True)

    assert "Обновлено вчера" in text


def test_primary_structured_sources_documented():
    assert "themoviedb.org" in pn._OFFICIAL_DOMAINS["screen"]
    assert "ticketmaster.com" in pn._OFFICIAL_DOMAINS["music"]


def test_admin_stats_shows_total_tavily_monthly_limit():
    pn._reserve_credits(1)
    text = pn.admin_stats_text()
    assert "Месяц: 1 / 1000 credits" in text
