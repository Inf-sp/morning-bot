"""UI-контракты админ-панели (ui/admin.py): единый стиль, метрики, статус-точки."""
import pytest

from ui import admin
from ui.builder import MessageBuilder


def _bold_texts(msg):
    u16 = msg.text.encode("utf-16-le")
    out = []
    for e in msg.entities or []:
        if e.type == "bold":
            out.append(u16[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le"))
    return out


@pytest.mark.unit
def test_metric_component_aligns_and_bolds_value():
    b = MessageBuilder()
    b.metric("Всего", 142)
    msg = b.build_stripped()
    assert "Всего" in msg.text
    assert "·" in msg.text          # точки-заполнитель
    assert "142" in _bold_texts(msg)  # значение жирным


@pytest.mark.unit
def test_home_shows_status_and_day_line():
    msg = admin.home(admin.OK, "всё работает", 38, 214, 0)
    assert "🛠 Администратор" in _bold_texts(msg)
    assert "🟢 всё работает" in msg.text
    assert "38 активны" in msg.text


@pytest.mark.unit
def test_users_screen_has_all_metrics_and_last_activity():
    stats = {"total": 142, "new_today": 6, "active_1d": 38, "active_7d": 71,
             "onboarded": 118, "not_onboarded": 24, "all_off": 9, "avg_msgs": 12.4}
    last = ("🟢", "Аня", "Тбилиси", "Досуг · План", "Последний вход: 2 мин назад")
    msg = admin.users(stats, last)
    assert "142" in _bold_texts(msg)
    assert "Последняя активность" in _bold_texts(msg)
    assert "Аня" in msg.text and "Тбилиси" in msg.text


@pytest.mark.unit
def test_cost_empty_and_populated():
    empty = admin.cost("7 дней", 0, 0, 0, [], [])
    assert "Данных пока нет" in empty.text

    populated = admin.cost("7 дней", 1214, 486000, 400,
                            [("Gemini", True, 68), ("OpenAI", False, 0)],
                            [("🍿 Досуг", 38)])
    assert "486k" in populated.text          # человекочитаемые токены
    assert "🟢 ключ" in populated.text
    assert "⚪ нет ключа" in populated.text


@pytest.mark.unit
def test_services_uses_only_three_status_dots():
    rows = [(admin.OK, "Telegram", "120 мс"), (admin.BAD, "TMDB", "502"),
            (admin.OFF, "Spotify", "не настроен")]
    msg = admin.services(rows, "3 мин назад")
    assert "🟢 Telegram" in msg.text
    assert "🔴 TMDB" in msg.text
    assert "⚪ Spotify" in msg.text
    assert "Проверено: 3 мин назад" in msg.text


@pytest.mark.unit
def test_logs_empty_and_filter_label():
    empty = admin.logs([], "Все")
    assert "Ошибок нет" in empty.text
    assert "Фильтр: Все" in empty.text

    with_errs = admin.logs([("🔴", "22:41", "service", "TMDB 502")], "Сервисы")
    assert "22:41" in with_errs.text
    assert "TMDB 502" in with_errs.text


@pytest.mark.unit
def test_llm_screen_metrics():
    msg = admin.llm(admin.OK, "работает", "40 сек назад", 340, 2, "Gemini",
                    ["Gemini 2.5", "Claude"])
    assert "🟢 работает" in msg.text
    assert "340 мс" in msg.text
    assert "Gemini 2.5 · Claude" in msg.text
