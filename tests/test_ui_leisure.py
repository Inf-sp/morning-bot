import pytest

from ui import leisure


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


def _italic_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "italic"]


def _assert_no_leaked_html(msg):
    assert "<b>" not in msg.text
    assert "<i>" not in msg.text
    assert "</b>" not in msg.text
    assert "</i>" not in msg.text


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

    assert msg.text.startswith("📚 Олдос Хаксли • «Дивный новый мир» (1932)")
    assert "Олдос Хаксли • «Дивный новый мир» (1932)" in _bold_texts(msg)
    assert "(1932)" in _italic_texts(msg)
    assert "Почему стоит читать" in _bold_texts(msg)
    assert "• Анти-Оруэлл" in msg.text
    assert "Цитата" in _bold_texts(msg)
    assert "\n\n\n" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_book_text_empty_why_and_missing_optional_fields():
    """why=[] и отсутствующие plot/quote/desc не должны рендерить пустые секции."""
    msg = leisure.book_text({
        "author": "A",
        "title": "T",
        "why": [],
    })

    assert msg.text == "📚 A • «T»"
    assert "Почему стоит читать" not in msg.text
    assert "Коротко о сюжете" not in msg.text
    assert "Цитата" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_book_text_keeps_html_like_chars_verbatim():
    """esc() внутри — временный шаг к from_html; итоговый plain text не должен
    экранировать спецсимволы (entities не требуют HTML-эскейпинга)."""
    msg = leisure.book_text({
        "author": "A & B",
        "title": "T <vol. 1>",
        "desc": "плохой <tag> и амперсанд & кусок",
        "why": ["риск <спойлер> есть"],
    })

    assert "A & B" in msg.text
    assert "T <vol. 1>" in msg.text
    assert "плохой <tag> и амперсанд & кусок" in msg.text
    assert "риск <спойлер> есть" in msg.text
    assert "&amp;" not in msg.text
    assert "&lt;" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_movie_card_message_spec():
    title, msg = leisure.movie_card(
        {"title": "Патерсон", "title_en": "Paterson", "hook": "тихое кино про поэта-водителя"},
        {"name": "Патерсон", "name_en": "Paterson", "year": 2016, "kind": "movie",
         "genres": "драма", "rating": 7.4, "overview": "Неделя из жизни водителя автобуса.",
         "url": "https://example.com/paterson"},
    )

    assert title == "Патерсон"
    assert "Патерсон (2016)" in _bold_texts(msg)
    # Новый дизайн: рейтинг без источника, тип и жанры в одной строке-якоре.
    assert "⭐ 7.4 · Фильм · драма" in msg.text
    assert "/10 TMDb" not in msg.text
    # Ссылка TMDb и дубль оригинального названия убраны.
    assert "🔗" not in msg.text
    assert "https://example.com/paterson" not in msg.text
    assert "\n\n\n" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_movie_card_keeps_html_like_chars_verbatim_and_handles_missing_tmdb():
    """Контент от LLM с тегоподобными подстроками (<b>, <Weird>) не эскейпится
    и не парсится как реальная разметка — только настоящий <b>…</b> из esc()-обхода
    считался бы протёкшим, а он не в счёт: тут <b> — часть пользовательского текста."""
    title, msg = leisure.movie_card(
        {"title": "Film & <Weird>", "hook": "hook with <b> and & chars"},
        None,
    )

    assert title == "Film & <Weird>"
    assert "Film & <Weird>" in msg.text
    assert "hook with <b> and & chars" in msg.text
    assert "&amp;" not in msg.text
    assert "&lt;" not in msg.text
    # единственная bold-entity — заголовок (из esc()+<b> в movie_card), а не
    # тегоподобная подстрока "<b>" из пользовательского hook-текста
    assert "Film & <Weird>" in _bold_texts(msg)
    assert "hook with <b> and & chars" not in " ".join(_bold_texts(msg))


@pytest.mark.unit
def test_leisure_movie_card_emoji_before_bold_uses_utf16_offsets():
    """Тайтл с эмодзи (surrogate pair) перед жирным текстом — офсеты обязаны быть в UTF-16."""
    title, msg = leisure.movie_card(
        {"title": "🎬🔥Movie", "hook": "x"},
        {"name": "🎬🔥Movie", "kind": "tv"},
    )

    assert "🎬🔥Movie" in _bold_texts(msg)
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_artist_card_message_spec():
    msg = leisure.artist_card({
        "artist": "The xx",
        "desc": "минималистичный инди-поп",
        "why": ["похожи по настроению"],
        "tracks": ["Intro"],
        "fact": "Лондонская группа.",
    })

    assert msg.text.startswith("🎸 The xx")
    assert "The xx" in _bold_texts(msg)
    assert "Почему тебе зайдёт:" in _bold_texts(msg)
    assert "С чего начать:" in _bold_texts(msg)
    assert "Факт:" in _bold_texts(msg)
    assert "\n\n\n" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_artist_card_empty_lists_and_missing_fact():
    """why=[] и tracks=[] не должны рендерить пустые секции; fact отсутствует."""
    msg = leisure.artist_card({
        "artist": "X",
        "desc": "d",
        "why": [],
        "tracks": [],
    })

    assert msg.text == "🎸 X\n\nd"
    assert "Почему тебе зайдёт:" not in msg.text
    assert "С чего начать:" not in msg.text
    assert "Факт:" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_artist_card_keeps_html_like_chars_verbatim():
    msg = leisure.artist_card({
        "artist": "AC & DC <live>",
        "desc": "rock & roll <legend>",
        "why": ["riff <hooky> & loud"],
        "tracks": ["T&T <edit>"],
    })

    assert "AC & DC <live>" in msg.text
    assert "rock & roll <legend>" in msg.text
    assert "riff <hooky> & loud" in msg.text
    assert "T&T <edit>" in msg.text
    assert "&amp;" not in msg.text
    assert "&lt;" not in msg.text
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_artist_card_emoji_before_bold_uses_utf16_offsets():
    msg = leisure.artist_card({"artist": "🎉🎉Party Band", "desc": "x"})

    assert "🎉🎉Party Band" in _bold_texts(msg)
    _assert_no_leaked_html(msg)


@pytest.mark.unit
def test_leisure_plain_from_html_strips_tags():
    assert leisure.plain_from_html("<b>bold</b> and <i>italic</i>") == "bold and italic"
    assert leisure.plain_from_html(None) == ""
    assert leisure.plain_from_html("") == ""


@pytest.mark.unit
def test_leisure_clip_short_and_long_text():
    assert leisure.clip("short") == "short"
    assert leisure.clip("  short  ") == "short"

    long_text = "Первое предложение. " + "слово " * 100 + "Последнее предложение."
    clipped = leisure.clip(long_text, limit=60)

    assert len(clipped) <= 61
    assert clipped != long_text


@pytest.mark.unit
def test_concerts_list_renders_artist_place_price_date_and_hidden_link():
    msg = leisure.concerts_list("Концерты в Нидерландах", [{
        "artist": "Romy", "flag": "🇳🇱", "place": "Netherlands, Biddinghuizen",
        "genre": "Электроника", "price": "от 35 EUR", "date": "21 августа 2026",
        "url": "https://ticketmaster.com/romy",
    }])

    assert "Romy" in msg.text
    assert "Netherlands, Biddinghuizen" in msg.text
    assert "Электроника" not in msg.text
    assert "от 35 EUR" in msg.text
    assert "21 августа 2026" in msg.text
    assert "https://ticketmaster.com/romy" not in msg.text
    link_entities = [e for e in msg.entities if e.type == "text_link"]
    assert any(e.url == "https://ticketmaster.com/romy" for e in link_entities)


@pytest.mark.unit
def test_concerts_list_skips_missing_genre_price_and_url():
    msg = leisure.concerts_list("Концерты в Нидерландах", [{
        "artist": "Romy", "flag": "🇳🇱", "place": "Netherlands, Biddinghuizen",
        "genre": "", "price": "", "date": "21 августа 2026", "url": "",
    }])

    assert "Подробнее" not in msg.text
    assert "🎵" not in msg.text
    assert "💶" not in msg.text


@pytest.mark.unit
def test_concerts_list_empty_shows_hint():
    msg = leisure.concerts_list("Концерты в Нидерландах", [])

    assert "ничего не нашёл" in msg.text


