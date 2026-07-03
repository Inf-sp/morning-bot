import pytest
from telegram import MessageEntity

from ui import dictionary


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_dictionary_added_message_spec_for_phrase():
    msg = dictionary.dict_add_confirmation([
        {"lang": "nl", "kind": "phrase", "word": "Je hand opsteken", "ru": "Поднять руку"},
    ])

    assert msg.text.startswith("Словарь")
    assert "✅ Фраза добавлена в нидерландские фразы" in msg.text
    assert "Je hand opsteken - Поднять руку" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    quote_offset = msg.text.index("Je hand opsteken - Поднять руку")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in msg.entities)


@pytest.mark.unit
def test_dictionary_added_section_title_is_bold_via_component():
    msg = dictionary.dict_add_confirmation([
        {"lang": "nl", "kind": "word", "word": "Toevoegen", "ru": "добавлять"},
    ])

    assert _entities_of_type(msg, "bold") == ["Словарь"]
    assert "\n\n\n" not in msg.text
    # section() + spacer() reproduce exactly one blank line after the title.
    assert msg.text.startswith("Словарь\n\n✅")


@pytest.mark.unit
def test_dictionary_added_message_spec_for_multiple_items():
    msg = dictionary.dict_add_confirmation([
        {"lang": "nl", "kind": "word", "word": "Toevoegen", "ru": "добавлять"},
        {"lang": "en", "kind": "phrase", "word": "catch up", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "a", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "b", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "c", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "d", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "e", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "f", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "g", "ru": ""},
    ])

    assert msg.text.startswith("Словарь")
    assert "✅ Добавлено: 8 в словарь нидерландских слов; 1 в словарь английских фраз" in msg.text
    assert "...и ещё 1" in msg.text
    assert "Новые записи будут храниться в словаре" in msg.text
    bold = _entities_of_type(msg, "bold")
    assert "Словарь" in bold
    assert "✅ Добавлено: 8 в словарь нидерландских слов; 1 в словарь английских фраз" in bold
    quotes = _entities_of_type(msg, "blockquote")
    assert "Toevoegen - добавлять" in quotes
    assert "catch up" in quotes
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_dictionary_duplicate_message_spec_for_word():
    msg = dictionary.dict_duplicate_confirmation([
        {"lang": "nl", "kind": "word", "word": "Toevoegen", "ru": "добавлять"},
    ])

    assert msg.text.startswith("Словарь")
    assert "✅ Слово уже есть в нидерландских словах" in msg.text
    assert "Toevoegen - добавлять" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    quote_offset = msg.text.index("Toevoegen - добавлять")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in msg.entities)


@pytest.mark.unit
def test_dictionary_duplicate_message_spec_for_multiple_items():
    msg = dictionary.dict_duplicate_confirmation([
        {"lang": "nl", "kind": "word", "word": "a", "ru": ""},
        {"lang": "nl", "kind": "word", "word": "b", "ru": ""},
    ])

    assert msg.text == (
        "Словарь\n\n✅ Эти записи уже есть в словаре\n\na\nb\n\n"
        "Повторно не добавляю их, чтобы словарь оставался чистым."
    )
    assert _entities_of_type(msg, "bold") == ["Словарь"]
    assert _entities_of_type(msg, "blockquote") == ["a", "b"]


@pytest.mark.unit
def test_dictionary_overview_message_spec():
    msg = dictionary.dict_overview(3, 2)

    assert msg.text == "🗂️ Мой словарь\n\nВсего: 5 (🇳🇱 3 · 🇬🇧 2)\n\nВыбери язык 👇"
    assert _entities_of_type(msg, "bold") == ["🗂️ Мой словарь"]


@pytest.mark.unit
def test_dictionary_language_message_spec():
    msg = dictionary.dict_language("nl", {"word": 4, "phrase": 1})

    assert msg.text == "🇳🇱 Словарь · Нидерландский\n\nСлов: 4 · Фраз: 1"
    assert _entities_of_type(msg, "bold") == ["🇳🇱 Словарь · Нидерландский"]


@pytest.mark.unit
def test_dictionary_deleted_message_spec_with_word():
    msg = dictionary.dict_deleted("Toevoegen")

    assert msg.text == (
        "✅ Слово Toevoegen удалено из текущего списка.\n\n"
        "Если хочешь, можно сразу открыть словарь или добавить новое."
    )
    assert _entities_of_type(msg, "bold") == ["Toevoegen"]


@pytest.mark.unit
def test_dictionary_deleted_message_spec_without_word():
    msg = dictionary.dict_deleted("")

    assert msg.text == (
        "✅ Слово удалено из текущего списка.\n\n"
        "Если хочешь, можно сразу открыть словарь или добавить новое."
    )
    assert _entities_of_type(msg, "bold") == []
