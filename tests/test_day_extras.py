import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import public_holidays
import sun
from ui import myday as myday_ui

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 10, 8, 19, 0, tzinfo=TZ)


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
    assert line.startswith("Золотой час будет с ") and " по " in line and "синий" not in line
    assert line == f"Золотой час будет {sun.evening_golden_range(52.63, 4.75, date(2026, 10, 8), TZ)}"
    assert sun.evening_golden_range(52.63, 4.75, date(2026, 10, 8), TZ).startswith("с 18:")
    assert sun.golden_hour_line(None, None, date(2026, 10, 8), TZ) == ""


def test_quote_author_follows_copyright_sign():
    msg = myday_ui.day_summary("Пт", "Alkmaar", quote_text="Даже самый маленький человек может изменить ход будущего.",
                               quote_author="Джон Толкин")

    assert msg.text.endswith("💭 «Даже самый маленький человек может изменить ход будущего.» © Джон Толкин")
