import pytest
from telegram import MessageEntity

from ui import dictionary


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
