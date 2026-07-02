import pytest

from ui import myday


@pytest.mark.unit
def test_myday_summary_message_spec():
    msg = myday.day_summary(
        "Четверг, 2 июля",
        "Amsterdam",
        flag="🇳🇱",
        priorities=["работа", "спорт"],
        weather_title="☀️ Погода сегодня",
        weather_line="До +22°C • 💨 Ветер 4 м/с",
        humidity="У каналов свежо",
        word_line="Toevoegen → добавлять",
        fact="Город стоит на каналах.",
        lifehack="Подготовь воду заранее.",
        quote_line="«Тише едешь — дальше будешь» — Автор",
    )

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("<b>Мой день • Четверг, 2 июля • Amsterdam 🇳🇱</b>")
    assert "🎯 <b>Фокус:</b> работа, спорт" in msg.text
    assert "<b>☀️ Погода сегодня</b>" in msg.text
    assert "<b>📚 Слово дня</b>" in msg.text
    assert "<b>💭 Цитата</b>" in msg.text
