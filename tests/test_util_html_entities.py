import pytest

import util


def _slice_u16(text, offset, length):
    """Достаёт подстроку по UTF-16 offset/length — так же, как это делает Telegram."""
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


@pytest.mark.unit
def test_html_to_entities_bold_and_italic():
    plain, entities = util.html_to_entities("<b>Заголовок</b> обычный <i>курсив</i>")

    assert plain == "Заголовок обычный курсив"
    bold = next(e for e in entities if e.type == "bold")
    italic = next(e for e in entities if e.type == "italic")
    assert plain[bold.offset:bold.offset + bold.length] == "Заголовок"
    assert plain[italic.offset:italic.offset + italic.length] == "курсив"


@pytest.mark.unit
def test_html_to_entities_handles_emoji_offsets():
    plain, entities = util.html_to_entities("🥣 <b>Рецепт дня</b>")

    bold = entities[0]
    assert _slice_u16(plain, bold.offset, bold.length) == "Рецепт дня"


@pytest.mark.unit
def test_html_to_entities_unescapes_entities():
    plain, entities = util.html_to_entities("<b>Омлет &lt;сыр&gt;</b> и &amp; молоко")

    assert plain == "Омлет <сыр> и & молоко"
    bold = entities[0]
    assert plain[bold.offset:bold.offset + bold.length] == "Омлет <сыр>"


@pytest.mark.unit
def test_html_to_entities_link():
    plain, entities = util.html_to_entities('Ссылка: <a href="https://example.com">тут</a>')

    link = entities[0]
    assert link.type == "text_link"
    assert link.url == "https://example.com"
    assert plain[link.offset:link.offset + link.length] == "тут"


@pytest.mark.unit
def test_html_to_entities_empty_and_plain():
    assert util.html_to_entities("") == ("", [])
    assert util.html_to_entities(None) == ("", [])
    assert util.html_to_entities("просто текст") == ("просто текст", [])


@pytest.mark.unit
def test_entities_to_json_and_back_round_trip():
    _, entities = util.html_to_entities('<b>жирный</b> и <a href="https://x.com">ссылка</a>')

    data = util.entities_to_json(entities)
    assert data == [
        {"type": "bold", "offset": 0, "length": 6},
        {"type": "text_link", "offset": 9, "length": 6, "url": "https://x.com"},
    ]

    restored = util.entities_from_json(data)
    assert len(restored) == 2
    assert restored[0].type == "bold" and restored[0].offset == 0 and restored[0].length == 6
    assert restored[1].type == "text_link" and restored[1].url == "https://x.com"


@pytest.mark.unit
def test_entities_to_json_empty():
    assert util.entities_to_json([]) == []
    assert util.entities_to_json(None) == []
    assert util.entities_from_json([]) == []
    assert util.entities_from_json(None) == []


@pytest.mark.unit
def test_chunk_text_with_entities_returns_single_chunk_when_under_limit():
    plain, entities = util.html_to_entities("<b>Заголовок</b>\nтекст")

    chunks = util.chunk_text_with_entities(plain, entities, limit=4000)

    assert len(chunks) == 1
    assert chunks[0] == (plain, entities)


@pytest.mark.unit
def test_chunk_text_with_entities_splits_long_text_without_breaking_short_entities():
    head = "<b>Заголовок</b>\n"
    filler = "x" * 10
    tail = "<i>конец</i>"
    plain, entities = util.html_to_entities(head + filler + tail)

    chunks = util.chunk_text_with_entities(plain, entities, limit=10)

    assert len(chunks) > 1
    reassembled = "".join(c[0] for c in chunks)
    assert reassembled == plain

    for chunk_text, chunk_entities in chunks:
        u16_len = len(chunk_text.encode("utf-16-le")) // 2
        for e in chunk_entities:
            assert 0 <= e.offset
            assert e.offset + e.length <= u16_len


@pytest.mark.unit
def test_chunk_text_with_entities_clips_entity_crossing_chunk_boundary():
    # "abc" (3) + "жирный" (6, bold) + "def" (3) = 12 символов; лимит 6 -> ровно 2 чанка по 6.
    plain = "abc" + "жирный" + "def"
    bold_start = len("abc")
    bold_length = len("жирный")
    from telegram import MessageEntity
    entities = [MessageEntity(MessageEntity.BOLD, bold_start, bold_length)]

    chunks = util.chunk_text_with_entities(plain, entities, limit=6)

    assert len(chunks) == 2
    first_text, first_entities = chunks[0]
    second_text, second_entities = chunks[1]

    assert first_text + second_text == plain
    assert first_text == "abcжир"
    assert second_text == "ныйdef"
    assert len(first_entities) == 1
    assert first_entities[0].offset == 3
    assert first_entities[0].length == 3
    assert len(second_entities) == 1
    assert second_entities[0].offset == 0
    assert second_entities[0].length == 3


@pytest.mark.unit
def test_loading_phrase_returns_one_of_known_variants():
    assert util.loading_phrase() in util.LOADING_PHRASES


@pytest.mark.unit
def test_loading_phrase_is_randomized():
    seen = {util.loading_phrase() for _ in range(50)}
    assert len(seen) > 1  # с высокой вероятностью выпадет больше одного варианта из 5
