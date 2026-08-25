import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import config
import personal_collections
from ui import data_export


def _button_labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_export_choice_has_clear_categories_and_navigation():
    msg = data_export.export_choice()
    labels = _button_labels(data_export.export_choice_keyboard())

    assert msg.text == "📤 Экспорт данных\n\nЧто сохранить в файл?"
    assert labels == [
        ["📤 Всё"],
        ["📤 Мой шкаф"],
        ["📤 Мой холодильник"],
        ["📤 Мой словарь"],
        ["📤 Любимое"],
        ["📤 Поездки"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_text_export_is_readable_and_hides_internal_fields(monkeypatch):
    monkeypatch.setattr(personal_collections.store, "get_settings", lambda _cid: {"city": "Алкмар", "lat": 1})
    monkeypatch.setattr(personal_collections.store, "get_profile", lambda _cid: {"learning_language": "nl"})
    monkeypatch.setattr(personal_collections.store, "load_wardrobe", lambda _cid: {
        "_v": 7, "zones": {"Верх": {"Футболки": [{"id": "secret-id", "name": "Белая футболка"}]}}
    })

    def get_list(key, _cid):
        return {
            config.FRIDGE_KEY: [{"id": "food-id", "name": "Помидоры", "category": "Овощи"}],
            config.DICT_KEY: [{"id": "word-id", "term": "Immers", "translation": "ведь", "srs_level": 4}],
            config.FAVORITE_MOVIES_KEY: [{"id": "movie-id", "title": "Arrival"}],
            config.FAVORITE_BOOKS_KEY: [], config.FAVORITE_ARTISTS_KEY: [],
            config.FAVORITE_GAMES_KEY: [], config.SAVED_COUNTRIES_KEY: ["Исландия"],
            config.THOUGHTS_KEY: [{"text": "Старая заметка", "internal": "hidden"}],
            personal_collections._ARCHIVED_CONTENT_RECORDS_KEY: [],
        }.get(key, [])

    monkeypatch.setattr(personal_collections.store, "get_list", get_list)

    text = personal_collections._export_text("42", "all")

    assert "Настройки\n=========\n• Город: Алкмар" in text
    assert "• Верх: Белая футболка" in text
    assert "• Помидоры — Овощи" in text
    assert "• Immers → ведь" in text
    assert "• Кино: Arrival" in text
    assert "• Исландия" in text
    assert "• Старая заметка" in text
    assert "secret-id" not in text
    assert "srs_level" not in text
    assert "{" not in text


def test_export_sends_txt_file(monkeypatch):
    sent = []

    class Bot:
        async def send_document(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(personal_collections, "_export_text", lambda *_args: "Мой словарь\n• Immers → ведь\n")

    asyncio.run(personal_collections.export_data(Bot(), "42", "dictionary"))

    assert sent[0]["filename"] == "moi-dannye-dictionary.txt"
    assert sent[0]["document"].getvalue().decode("utf-8") == "Мой словарь\n• Immers → ведь\n"
    assert sent[0]["caption"] == "📤 Готово · Мой словарь"
