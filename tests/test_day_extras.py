import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import news_digest
import public_holidays
import sun
from ui import myday as myday_ui
from ui import news_digest as news_ui

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 10, 8, 19, 0, tzinfo=TZ)


def _item(source, title, hours_ago=1, summary=""):
    return {"source": source, "title": title, "link": f"https://x/{abs(hash(title))}",
            "published": NOW - timedelta(hours=hours_ago), "summary": summary}


def test_news_selection_local_first_no_duplicates_only_last_day():
    items = [
        _item("NOS", "Kabinet valt na ruzie over begroting"),
        _item("NU.nl", "Kabinet valt na ruzie over de begroting"),  # дубль NOS
        _item("NH Nieuws", "Brug in Alkmaar dicht dit weekend"),
        _item("NU.nl", "Storm op komst", summary="Noord-Holland code oranje"),
        _item("NOS", "Oud nieuws", hours_ago=30),
        _item("NOS", "Record astronauten"),
    ]

    titles = [item["title"] for item in news_digest.select(items, "Alkmaar", now=NOW)]

    assert titles[0] == "Brug in Alkmaar dicht dit weekend"
    assert "Oud nieuws" not in titles
    assert sum("begroting" in title for title in titles) == 1
    assert len(titles) <= news_digest.MAX_ITEMS


def test_news_card_links_titles_to_originals():
    msg = news_ui.digest("Alkmaar", [{"title": "Мост в Алкмаре закроют", "link": "https://nh/1",
                                      "source": "NH Nieuws"}])

    assert msg.text == "📰 Главное за день · Alkmaar\n\n• Мост в Алкмаре закроют — NH Nieuws"
    assert [e.url for e in msg.entities if e.url] == ["https://nh/1"]


def test_holidays_today_and_tomorrow_with_russian_names(monkeypatch):
    monkeypatch.setattr(public_holidays, "_year_holidays", lambda year, cc: [
        {"date": "2026-04-27", "local": "Koningsdag", "name": "King's Day"},
    ])

    assert public_holidays.holiday_lines("NL", date(2026, 4, 26)) == ["Завтра: Koningsdag — День короля"]
    assert public_holidays.holiday_lines("NL", date(2026, 4, 27)) == ["Сегодня: Koningsdag — День короля"]
    assert public_holidays.holiday_lines("NL", date(2026, 4, 20)) == []
    assert public_holidays.holiday_lines("", date(2026, 4, 27)) == []


def test_day_summary_shows_holiday_line():
    msg = myday_ui.day_summary("Пн, 27 апр", "Alkmaar", weather_line="до +15°C",
                               holidays=["Сегодня: Koningsdag — День короля"])

    assert "🎉 Сегодня: Koningsdag — День короля" in msg.text


def test_golden_hour_matches_known_sun_times():
    # Амстердам, солнцестояния: восход/закат по таблицам ±2 мин.
    rise = sun.crossing(52.37, 4.89, date(2026, 6, 21), sun.SUNRISE, TZ, rising=True)
    sets = sun.crossing(52.37, 4.89, date(2026, 12, 21), sun.SUNRISE, TZ, rising=False)
    assert abs((rise.hour * 60 + rise.minute) - (5 * 60 + 18)) <= 2
    assert abs((sets.hour * 60 + sets.minute) - (16 * 60 + 29)) <= 2

    line = sun.golden_hour_line(52.63, 4.75, date(2026, 10, 8), TZ)
    assert line.startswith("🌅 Золотой час: ") and " и " in line and "синий час" in line
    assert sun.evening_golden_start(52.63, 4.75, date(2026, 10, 8), TZ) == line.split(" и ")[1][:5]
    assert sun.golden_hour_line(None, None, date(2026, 10, 8), TZ) == ""
