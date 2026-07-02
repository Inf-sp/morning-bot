import pytest

from ui import myday


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


@pytest.mark.unit
def test_myday_summary_message_spec():
    msg = myday.day_summary(
        "Четверг, 2 июля",
        "Amsterdam",
        flag="🇳🇱",
        priorities=["работа", "спорт"],
        weather_title="☀️ Погода сегодня",
        weather_line="До +22°C",
        humidity_title="💧 Высокая влажность",
        humidity_line="У каналов свежо",
        word_line="Toevoegen → добавлять",
        fact="Город стоит на каналах.",
        lifehack="Подготовь воду заранее.",
        quote_line="«Тише едешь — дальше будешь» — Автор",
    )

    assert msg.text.startswith("Мой день • Четверг, 2 июля • Amsterdam 🇳🇱")
    bold = _bold_texts(msg)
    assert "Мой день • Четверг, 2 июля • Amsterdam 🇳🇱" in bold
    assert "Фокус:" in bold
    assert "🎯 Фокус: работа, спорт" in msg.text
    assert "☀️ Погода сегодня" in bold
    assert "📚 Слово дня" in bold
    assert "💭 Цитата" in bold
    assert "У каналов свежо" in msg.text
