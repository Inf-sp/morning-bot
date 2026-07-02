import pytest

from ui import settings


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


@pytest.mark.unit
def test_settings_core_messages():
    assert "🔔 Уведомления" in settings.notifications().text
    assert "Сейчас: здоровье" in settings.priorities("здоровье").text
    assert "🎚️ Настройки" in settings.settings_home().text
    assert "🍿 Настройки досуга" in settings.leisure_settings().text


@pytest.mark.unit
def test_settings_body_profile_message():
    msg = settings.body_profile("рост 178")

    assert "Сейчас сохранено:\nрост 178" in msg.text
    assert "Напиши одним сообщением" in msg.text
    assert "Сейчас сохранено:" in _bold_texts(msg)


@pytest.mark.unit
def test_settings_list_messages():
    assert "Пока пусто" in settings.artists_home([]).text
    assert "🎤 Мои музыканты" in settings.artists_home(["Eefje"]).text
    assert "Пока пусто — добавь первый принцип" in settings.lagom_home([]).text
    assert "Лагом" in settings.lagom_home(["меньше, но лучше"]).text


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
    assert "Пока пусто" in settings.trips_empty().text
    assert "Мои поездки" in settings.trips_home().text
    assert "Позже" in settings.later_home_empty().text
    assert "Открой категорию" in settings.later_home().text
    assert "Позже · 🎬 Кино" in settings.later_group("🎬 Кино", "фильмы").text


@pytest.mark.unit
def test_settings_favorite_messages_keep_user_content_verbatim():
    msg = settings.favorite_section("🎬 Мое кино", ["A < B"])

    assert "🎬 Мое кино" in _bold_texts(msg)
    assert "A < B" in msg.text
    assert any(e.type == "italic" for e in settings.favorite_section("Книги", []).entities)
    assert settings.favorite_card("Src <x>", "01.01", "body").parse_mode == "HTML"
    assert "Src &lt;x&gt;" in settings.favorite_card("Src <x>", "01.01", "body").text
    assert "Напиши книгу" in settings.favorite_add_prompt("книгу").text
    assert settings.favorite_added().text == "Добавлено."


@pytest.mark.unit
def test_settings_admin_messages():
    assert settings.admin_only().text == "⛔ Только для администратора."
    assert "Администратор" in settings.admin_home().text
    assert settings.ADMIN_RUN_NOTIF_TITLE in settings.admin_run_notifications().text

    users = settings.admin_users([("1", "Ann <Boss>", True), ("2", "", False)], pending_count=2)
    assert "Ann <Boss>" in users.text
    assert "Активных инвайтов: 2" in users.text


@pytest.mark.unit
def test_settings_admin_status_messages_keep_dynamic_parts_verbatim():
    cost = settings.admin_cost_summary(
        2,
        1234,
        [("OpenAI", True, 1000, "80%"), ("Claude", False, 0, "0%")],
        [("💬 Чат", 1000, "80%")],
    )
    assert "Токенов: ~1,234" in cost.text
    assert "Claude: —" in cost.text

    health = settings.admin_health([("TOKEN<", False)], [("OPT", True)], ["  ❌ DB: bad & down"])
    assert "TOKEN<" in health.text
    assert "bad & down" in health.text
    assert any(e.type == "code" for e in health.entities)

    llm = settings.admin_llm_check([("OpenAI", False, "bad <key>")])
    assert "bad <key>" in llm.text

    invite = settings.admin_invite("https://t.me/bot?start=a<b")
    assert "https://t.me/bot?start=a<b" in invite.text
    assert any(e.type == "text_link" and e.url == "https://t.me/bot?start=a<b" for e in invite.entities)
