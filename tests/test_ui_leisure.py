import pytest

from ui import leisure


@pytest.mark.unit
def test_leisure_book_text_message_spec():
    msg = leisure.book_text({
        "author": "Олдос Хаксли",
        "title": "Дивный новый мир",
        "year": "1932",
        "desc": "Генетический рай без свободы.",
        "why": ["-Анти-Оруэлл"],
        "plot": "Бернард привозит Дикаря.",
        "quote": "Лучше быть несчастным в свободе.",
    })

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("📚 <b>Олдос Хаксли • «Дивный новый мир» <i>(1932)</i></b>")
    assert "🎯 <b>Почему стоит читать</b>" in msg.text
    assert "• Анти-Оруэлл" in msg.text
    assert "💬 <b>Цитата</b>" in msg.text


@pytest.mark.unit
def test_leisure_artist_card_message_spec():
    msg = leisure.artist_card({
        "artist": "The xx",
        "desc": "минималистичный инди-поп",
        "why": ["похожи по настроению"],
        "tracks": ["Intro"],
        "fact": "Лондонская группа.",
    })

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("🎸 <b>The xx</b>")
    assert "🎯 <b>Почему тебе зайдёт:</b>" in msg.text
    assert "🎧 <b>С чего начать:</b>" in msg.text


@pytest.mark.unit
def test_leisure_country_and_plan_cards():
    country = leisure.country_card({
        "flag": "🇳🇱",
        "country": "Нидерланды",
        "about": "каналы и музеи",
        "for_what": "за городами",
        "langs": "нидерландский",
        "note": "ветер",
        "fact": "много велосипедов",
    })
    plan = leisure.travel_plan({
        "flag": "🇳🇱",
        "title": "Нидерланды",
        "why": ["музеи"],
        "budget": ["эконом"],
    }, "Нидерланды")

    assert "🇳🇱 <b>Нидерланды</b>" in country.text
    assert "🎯 <b>Ради чего ехать:</b> за городами" in country.text
    assert "🎯 <b>Почему тебе подойдёт</b>" in plan.text
    assert "💰 <b>Бюджет</b>" in plan.text
