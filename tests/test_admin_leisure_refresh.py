"""«Обновить карточки → Досуг» по частям: каждая часть собирается заново, итог — честный."""
import asyncio
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import admin
import config
import leisure_concerts
import leisure_hub
import leisure_reads
import local_events


def _profile(monkeypatch):
    profile = {}

    def mutate(_cid, change):
        updated = change(dict(profile))[0]
        profile.clear()
        profile.update(updated)

    for module in (leisure_reads.store, leisure_hub.store):
        monkeypatch.setattr(module, "get_profile", lambda _cid: profile)
        monkeypatch.setattr(module, "mutate_profile", mutate)
    return profile


def test_reads_part_rebuilds_a_stuck_set_into_ready_articles(monkeypatch):
    profile = _profile(monkeypatch)
    today = datetime.now(config.TZ).date().isoformat()
    # Набор дня уже есть, но без переводов: экран писал «ещё собираются» до завтра.
    profile["hub_reads"] = {"v": leisure_reads._VERSION, "date": today, "themes": list(leisure_reads.themes("42")),
                            "ru_tries": 3, "items": [{"title": "Black holes", "link": "https://q/bh",
                                                      "image": "https://img/bh.jpg", "about": ""}]}
    monkeypatch.setattr(leisure_reads, "_feed", lambda source, _url: [
        {"title": "Black holes", "link": "https://q/bh", "summary": "space", "date": "", "source": source}]
        if source == "Quanta" else [])
    monkeypatch.setattr(leisure_reads, "_excerpt", lambda _link: "text")
    monkeypatch.setattr(leisure_reads, "_minutes", lambda _link: 5)
    monkeypatch.setattr(leisure_reads, "_image", lambda *_a: "https://img/bh.jpg")
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: {"items": [
        {"id": 0, "title": "Как растут чёрные дыры", "about": "Чёрные дыры набирают массу быстрее, чем думали."}]})

    assert asyncio.run(admin._refresh_card_cache("42", "lz_reads")) is True
    assert [item["title"] for item in leisure_reads.ranked("42")] == ["Как растут чёрные дыры"]


def test_part_with_nothing_to_show_is_not_reported_as_updated(monkeypatch):
    _profile(monkeypatch)
    monkeypatch.setattr(leisure_reads, "_feed", lambda *_a: [])  # источники не ответили

    assert asyncio.run(admin._refresh_card_cache("42", "lz_reads")) is False
    assert leisure_hub.failed_steps["42"] == ["ничего не собралось, источники не ответили"]


def test_events_and_premieres_expire_their_day_caches_before_warming(monkeypatch):
    profile = _profile(monkeypatch)
    profile.update({"hub_series": {"date": "x"}, "hub_music_fav": {"date": "x"}, "keep": 1})
    expired = []
    monkeypatch.setattr(local_events, "expire", lambda cid: expired.append(cid))

    leisure_hub.expire_part("42", "premieres")
    leisure_hub.expire_part("42", "events")

    assert profile == {"keep": 1} and expired == ["42"]


def test_local_events_expire_keeps_events_as_fallback(monkeypatch):
    saved = {"alkmaar|NL": {"ts": 999, "events": [{"name": "Kermis"}]},
             "agenda|alkmaar|NL": {"ts": 999, "events": [{"name": "Kerstmarkt"}]}}
    monkeypatch.setattr(local_events, "_place", lambda _cid: ("Alkmaar", "NL", "alkmaar|NL"))
    monkeypatch.setattr(local_events.store, "mutate_kv", lambda _key, change: saved.update(change(saved)[0]))

    local_events.expire("42")

    assert saved["alkmaar|NL"] == {"ts": 0, "events": [{"name": "Kermis"}]}  # следующая сборка ищет заново
    assert saved["agenda|alkmaar|NL"]["ts"] == 0


def test_concerts_part_checks_every_artist_now(monkeypatch):
    seen = {}

    async def fetch(artists, cc, cname, *, cid=None, force_artists=(), **_k):
        seen["force"] = list(force_artists)
        return []

    monkeypatch.setattr(leisure_concerts, "get_popular_music_events", lambda *_a: asyncio.sleep(0, []))
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Muse", "Kehlani"])
    monkeypatch.setattr(leisure_concerts, "_fetch_concerts", fetch)
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_set", lambda *_a: None)
    monkeypatch.setattr(leisure_concerts, "invalidate_user_concerts_cache", lambda _cid: True)
    monkeypatch.setattr(leisure_concerts.config, "TICKETMASTER_API_KEY", "k")
    monkeypatch.setattr(leisure_concerts.store, "get_settings", lambda _cid: {"cc": "NL"})

    assert asyncio.run(admin._refresh_card_cache("42", "lz_concerts")) is True
    assert seen["force"] == ["Muse", "Kehlani"]  # не раз в месяц, а все сейчас
