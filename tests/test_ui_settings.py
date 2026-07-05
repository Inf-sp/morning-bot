import pytest

from ui import settings


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


@pytest.mark.unit
def test_settings_core_messages():
    notif = settings.notifications()
    assert "Рассылки" in _bold_texts(notif)
    assert notif.text == "Рассылки\nНажми для включения/выключения. 🟢 — включено."

    prio = settings.priorities("здоровье")
    assert "Сейчас: здоровье" in prio.text
    assert "🎯 Приоритеты" in _bold_texts(prio)
    assert "Сейчас:" in _bold_texts(prio)

    cuis = settings.cuisines("Азиатская")
    assert "Сейчас: Азиатская" in cuis.text
    assert "🍽️ Кухни" in _bold_texts(cuis)
    assert "Сейчас:" in _bold_texts(cuis)

    home = settings.settings_home()
    assert "🎚️ Настройки" in _bold_texts(home)

    leisure = settings.leisure_settings()
    assert "🍿 Настройки досуга" in _bold_texts(leisure)


@pytest.mark.unit
def test_settings_body_profile_message():
    msg = settings.body_profile("рост 178")

    assert "Сейчас сохранено:\nрост 178" in msg.text
    assert "Напиши одним сообщением" in msg.text
    bold = _bold_texts(msg)
    assert "🎚️ Мои параметры" in bold
    assert "Сейчас сохранено:" in bold
    assert "Напиши одним сообщением:" in bold
    assert not msg.text.startswith("\n")
    assert not msg.text.endswith("\n")
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_settings_list_messages():
    empty = settings.artists_home([])
    assert "Пока пусто" in empty.text
    assert "🎤 Мои музыканты" in _bold_texts(empty)

    full = settings.artists_home(["Eefje"])
    assert "🎤 Мои музыканты" in _bold_texts(full)
    assert full.text == "🎤 Мои музыканты"

    lagom_empty = settings.lagom_home([])
    assert "Пока пусто — добавь первый принцип" in lagom_empty.text
    assert "☕️ Лагом" in _bold_texts(lagom_empty)
    assert "Примеры:" in _bold_texts(lagom_empty)

    lagom_full = settings.lagom_home(["меньше, но лучше"])
    assert "Лагом" in lagom_full.text
    assert "Пока пусто" not in lagom_full.text


@pytest.mark.unit
def test_settings_input_prompts_and_list_added():
    assert "Напиши город" in settings.city_input().text
    assert settings.wardrobe_item_input().entities
    assert "Напиши установку" in settings.lagom_input().text
    assert "Напиши страну" in settings.list_add_prompt("country").text
    assert "Напиши имя артиста" in settings.list_add_prompt("artist").text
    assert "Напиши название книги" in settings.list_add_prompt("book").text
    assert "A < B" in settings.list_added("book", "A < B").text
    assert "Опиши свой стиль" in settings.style_custom_input().text
    assert "Параметры тела" in settings.body_input().text


@pytest.mark.unit
def test_settings_saved_and_later_messages():
    assert settings.saved_to_later().text == "⏳ Сохранено во временные закладки."
    assert "Нечего сохранять" in settings.nothing_to_save().text
    assert "Что сделать" in settings.note_action_prompt("длинный текст").text
    assert "чёрный список" in settings.note_blacklisted("Фильм", "Кино").text
    assert settings.note_removed_from_later().text == "Удалил из закладок."
    assert "в любимые" in settings.note_moved_to_favorites("Фильм", "Кино").text
    assert settings.note_deleted().text == "❌ Удалил."

    trips_empty = settings.trips_empty()
    assert "Пока пусто" in trips_empty.text
    assert "🧳 Поездки" in _bold_texts(trips_empty)

    trips_home = settings.trips_home()
    assert "Мои поездки" in trips_home.text
    assert "🧳 Мои поездки" in _bold_texts(trips_home)
    assert "\n\n\n" not in trips_home.text

    later_empty = settings.later_home_empty()
    assert "Сохранить" in later_empty.text
    assert "⭐️ Сохранить" in _bold_texts(later_empty)

    later_home = settings.later_home()
    assert "Открой категорию" in later_home.text

    later_group = settings.later_group("🎬 Кино", "фильмы")
    assert "Сохранить · 🎬 Кино" in later_group.text
    assert "⭐️ Сохранить · 🎬 Кино" in _bold_texts(later_group)


@pytest.mark.unit
def test_settings_favorite_messages_keep_user_content_verbatim():
    msg = settings.favorite_section("🎬 Мое кино", ["A < B", "C"])

    assert "🎬 Мое кино" in _bold_texts(msg)
    assert "A < B" in msg.text
    assert msg.text == "🎬 Мое кино\n\n• A < B\n• C"

    empty = settings.favorite_section("Книги", [])
    assert any(e.type == "italic" for e in empty.entities)
    assert empty.text == "Книги\n\nпусто"

    card = settings.favorite_card("Src <x>", "01.01", "body")
    assert card.text == "⭐ Src <x> · 01.01\n\nbody"
    assert "Src <x>" in _bold_texts(card)
    assert "Напиши книгу" in settings.favorite_add_prompt("книгу").text
    assert settings.favorite_added().text == "Добавлено."


@pytest.mark.unit
def test_favorite_card_embeds_body_entities_with_shifted_offsets():
    from telegram import MessageEntity

    body_entities = [MessageEntity(MessageEntity.BOLD, 0, 6), MessageEntity(MessageEntity.ITALIC, 7, 4)]
    card = settings.favorite_card("Кино", "", "жирный курс", body_entities)

    assert card.text == "⭐ Кино\n\nжирный курс"
    assert "Кино" in _bold_texts(card)
    bold_texts = _bold_texts(card)
    italic_texts = [_slice_u16(card.text, e.offset, e.length) for e in card.entities if e.type == "italic"]
    assert "жирный" in bold_texts
    assert italic_texts == ["курс"]


@pytest.mark.unit
def test_favorite_card_without_source_date_has_no_dot_separator():
    card = settings.favorite_card("Кино", "", "текст заметки")

    assert card.text == "⭐ Кино\n\nтекст заметки"
    assert not card.entities or all(e.type != "italic" for e in card.entities)


@pytest.mark.unit
def test_settings_admin_only_kept():
    # admin_only и превью-рассылка остались в старом UI, остальное переехало в ui/admin
    assert settings.admin_only().text == "⛔ Только для администратора."
    run_notif = settings.admin_run_notifications()
    assert settings.ADMIN_RUN_NOTIF_TITLE in run_notif.text
