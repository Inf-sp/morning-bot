from datetime import datetime, timedelta
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import personal_news as news


NOW = datetime(2026, 7, 8, 10, 0, tzinfo=config.TZ)


def _source(**overrides):
    item = {
        "title": "NS wijziging dienstregeling vandaag",
        "content": "Nieuwe werkzaamheden zorgen vandaag voor vertraging bij Alkmaar.",
        "url": "https://ns.nl/reisinfo/werkzaamheden-alkmaar",
        "published_at": NOW.isoformat(),
        "_category_hint": "transport",
    }
    item.update(overrides)
    return item


def test_relative_day_unknown_date():
    assert news._relative_day(None, NOW) == "дата неизвестна"


def test_strict_filter_rejects_missing_date_unofficial():
    item = _source(published_at="", url="https://example.com/reisinfo/werkzaamheden-alkmaar")
    assert news.strict_filter([item], now=NOW) == []


def test_strict_filter_accepts_missing_date_official():
    item = _source(published_at="")
    assert news.strict_filter([item], now=NOW)


def test_strict_filter_rejects_old_evergreen():
    item = _source(
        title="Huurregels vanaf 2024",
        content="Overzicht van huur en belasting voor woningen.",
    )
    assert news.strict_filter([item], now=NOW) == []


def test_strict_filter_accepts_today():
    item = _source()
    assert news.strict_filter([item], now=NOW)


def test_build_card_adhd_format():
    items = [
        {
            "category": "city",
            "relevance_score": 90,
            "title": "Werkzaamheden bij station Alkmaar",
            "summary": "tijdelijk sluit een weg bij het station.",
            "why_important": "dit kan je route met fiets of OV vertragen.",
            "action_hint": "check je route voor vertrek.",
            "source": "Gemeente Alkmaar",
            "url": "https://gemeentealkmaar.nl/nieuws/werkzaamheden",
            "published_at": NOW.isoformat(),
        },
        {
            "category": "tech",
            "relevance_score": 76,
            "title": "OpenAI API update",
            "summary": "limieten en modelgedrag zijn aangepast.",
            "why_important": "dit kan invloed hebben op de bot.",
            "source": "OpenAI",
            "url": "https://openai.com/news/api-update",
            "published_at": (NOW - timedelta(days=1)).isoformat(),
        },
        {
            "category": "leisure",
            "relevance_score": 70,
            "title": "Nieuw concert in Alkmaar",
            "summary": "er is een nieuwe show aangekondigd.",
            "why_important": "dit past bij je muziekinteresses.",
            "source": "Ticketmaster",
            "url": "https://ticketmaster.nl/event/example",
            "published_at": NOW.isoformat(),
        },
    ]

    text, buttons = news._build_card(items)

    assert "💡 Почему важно" not in text
    assert "Почему тебе" not in text
    assert "[Алкмар]" in text
    assert "[Технологии]" in text
    assert "[📍" not in text
    assert "[🇳🇱" not in text
    assert "🔥 Главное" in text
    assert "⚠️ Может повлиять" in text
    assert "👀 Интересное" in text
    for label in ("Что:", "Почему:", "Сделать:", "Источник:"):
        assert label in text
    assert buttons[0][0].text == "1 источник"
    assert buttons[1][0].text == "2 источник"


def test_build_card_empty_state():
    text, buttons = news._build_card([])
    assert "Сегодня ничего срочного." in text
    assert "Сегодня нет достаточно важных новостей" not in text
    assert buttons == []


def main():
    test_relative_day_unknown_date()
    test_strict_filter_rejects_missing_date_unofficial()
    test_strict_filter_accepts_missing_date_official()
    test_strict_filter_rejects_old_evergreen()
    test_strict_filter_accepts_today()
    test_build_card_adhd_format()
    test_build_card_empty_state()
    print("ok")


if __name__ == "__main__":
    main()
