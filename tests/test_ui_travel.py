import pytest

from ui import travel


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


def _assert_no_leaked_html(msg):
    assert "<b>" not in msg.text


@pytest.mark.unit
def test_travel_country_and_plan_cards():
    country = travel.country_card({
        "flag": "🇳🇱",
        "country": "Нидерланды",
        "about": "каналы и музеи",
        "for_what": "за городами",
        "langs": "нидерландский",
        "note": "ветер",
        "fact": "много велосипедов",
    })
    plan = travel.travel_plan({
        "flag": "🇳🇱",
        "title": "Нидерланды",
        "why": ["музеи"],
        "budget": ["эконом"],
    }, "Нидерланды")

    assert "🇳🇱 Нидерланды" in country.text
    assert "Нидерланды" in _bold_texts(country)
    assert "Ради чего ехать:" in _bold_texts(country)
    assert "Язык:" in _bold_texts(country)
    assert "Главный нюанс:" in _bold_texts(country)
    assert "Факт:" in _bold_texts(country)
    assert "за городами" in country.text
    assert "\n\n\n" not in country.text
    _assert_no_leaked_html(country)

    assert "🇳🇱 Нидерланды" in plan.text
    assert "Почему тебе подойдёт" in _bold_texts(plan)
    assert "Бюджет" in _bold_texts(plan)
    assert "• музеи" in plan.text
    assert "• эконом" in plan.text
    assert "\n\n\n" not in plan.text
    _assert_no_leaked_html(plan)


@pytest.mark.unit
def test_travel_country_card_missing_optional_fields_and_no_leak():
    """Только flag+country — все остальные секции опциональны и не должны рендериться."""
    country = travel.country_card({"flag": "🇯🇵", "country": "Япония"})

    assert country.text == "🇯🇵 Япония"
    assert "Ради чего ехать" not in country.text
    assert "Язык" not in country.text
    assert "Главный нюанс" not in country.text
    assert "Факт" not in country.text
    _assert_no_leaked_html(country)


@pytest.mark.unit
def test_travel_country_card_keeps_html_like_chars_verbatim():
    country = travel.country_card({
        "flag": "🇳🇱",
        "country": "A & B <land>",
        "about": "text with <tag> and & sign",
        "for_what": "reason <x> & y",
    })

    assert "A & B <land>" in country.text
    assert "text with <tag> and & sign" in country.text
    assert "reason <x> & y" in country.text
    assert "&amp;" not in country.text
    assert "&lt;" not in country.text
    _assert_no_leaked_html(country)


# ---------- приветственный экран раздела «Путешествия» ----------

@pytest.mark.unit
def test_travel_home_screen_shows_counts():
    msg = travel.home_screen(visited_count=3, fav_count=2, plan_count=1)
    assert "Путешествия" in _bold_texts(msg)
    assert "Посещено 3 страны" in msg.text
    assert "В любимых 2 страны" in msg.text
    assert "Планов поездок 1" in msg.text


@pytest.mark.unit
def test_travel_home_screen_empty_state():
    msg = travel.home_screen(visited_count=0, fav_count=0, plan_count=0)
    assert "Пока пусто" in msg.text
    assert "Посещено" not in msg.text
    assert "В любимых" not in msg.text
