"""«Прочитать за 2 минуты» в хабе Досуга: одна статья, темы по очереди, время чтения, «Другая статья»."""
import os
from datetime import date, datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import leisure_reads
from ui import leisure as leisure_ui


def _profile(monkeypatch):
    profile = {}

    def mutate(_cid, change):
        updated = change(dict(profile))[0]
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(leisure_reads.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_reads.store, "mutate_profile", mutate)
    return profile


def test_day_set_puts_todays_theme_first_by_interests_and_fresh_only(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 3, tzinfo=config.TZ)
    theme = leisure_reads.theme_of(now.date())
    feeds = {
        "Quanta": [{"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "summary": "space",
                    "date": "Fri, 16 Oct 2026 10:00:00 +0000", "source": "Quanta"},
                   {"title": "Old news", "link": "https://q.example/old", "summary": "",
                    "date": "Fri, 02 Oct 2026 10:00:00 +0000", "source": "Quanta"}],
        "Science News": [{"title": "Soil microbes", "link": "https://s.example/soil", "summary": "",
                          "date": "2026-10-17T09:00:00Z", "source": "Science News"}],
        "Colossal": [{"title": "Paper sculptures", "link": "https://c.example/paper", "summary": "sculpture",
                      "date": "2026-10-17T09:00:00Z", "source": "Colossal"}],
    }
    monkeypatch.setattr(leisure_reads, "_feed", lambda source, _url: feeds.get(source, []))
    monkeypatch.setattr(leisure_reads, "_minutes", lambda link: 6 if "bh" in link else None)
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: {"items": [
        {"id": 0, "about": "Как сверхмассивные чёрные дыры набирают массу так быстро"}]})

    entry = leisure_reads.build("42", now)

    links = [item["link"] for item in entry["items"]]
    assert "https://q.example/old" not in links  # старше недели — нет
    first_theme = "science" if links[0].startswith(("https://q.", "https://s.")) else "design"
    assert first_theme == theme  # тема дня — первой
    if theme == "science":
        assert links[0] == "https://q.example/bh"  # по интересам (космос) — выше
    current = leisure_reads.current("42", now.date())
    assert current == entry["items"][0] and profile["hub_reads"]["seen"]
    assert leisure_reads.advance("42", now.date()) == entry["items"][1 % len(entry["items"])]
    assert leisure_reads.current("42", date(2026, 10, 19)) is None  # набор — только на свой день


def test_reading_time_counts_paragraph_words():
    page = "<html><nav>menu</nav>" + "<p>" + " ".join(["word"] * 1380) + "</p></html>"
    assert leisure_reads.minutes_of(page) == 6
    assert leisure_reads.minutes_of("<p>short</p>") is None


def test_hub_shows_one_read_with_source_language_minutes_and_a_change_button():
    read = {"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "source": "Quanta", "lang": "EN",
            "minutes": 6, "about": "Как сверхмассивные чёрные дыры набирают массу так быстро"}
    msg = leisure_ui.leisure_today_screen([], read=read, library={"movies": 2, "artists": 1})
    assert ("Прочитать за 2 минуты:\nHow Black Holes Grow Up · Quanta · EN · 6 мин\n"
            "Как сверхмассивные чёрные дыры набирают массу так быстро.\n\nТвоя библиотека") in msg.text
    links = [entity.url for entity in msg.entities if entity.type == "text_link"]
    assert links == ["https://q.example/bh"]
    callbacks = [b.callback_data for row in leisure_ui.leisure_hub_kb(read=True).inline_keyboard for b in row]
    assert callbacks[5] == "lz_read_next" and "lz_read_next" not in [
        b.callback_data for row in leisure_ui.leisure_hub_kb().inline_keyboard for b in row]
