import pytest
from telegram import MessageEntity

from ui import learning


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_learning_phrase_poll_question_message_spec():
    msg = learning.phrase_poll_question("Ik maak me zorgen om ____", "Я переживаю за тебя")

    assert msg.text.startswith("🧩 Проверь себя\n\nIk maak me zorgen om ____")
    assert "Перевод:" not in msg.text
    assert "Я переживаю за тебя" not in msg.text
    assert "Выбери пропущенное слово" not in msg.text
    assert "Выбери подходящее слово:" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)


@pytest.mark.unit
def test_learning_phrase_poll_question_clips_entities_when_truncated_to_300():
    long_phrase = "x" * 400
    msg = learning.phrase_poll_question(long_phrase, "перевод")

    assert len(msg.text) == 300
    u16_len = len(msg.text.encode("utf-16-le")) // 2
    for e in msg.entities:
        assert 0 <= e.offset
        assert e.offset + e.length <= u16_len
        # entity text must still resolve to real content, not garbage past the cut
        assert _slice_u16(msg.text, e.offset, e.length)


@pytest.mark.unit
def test_learning_train_question_bolds_word():
    msg = learning.train_question("Toevoegen")

    assert msg.text == "Переведи слово «Toevoegen»"
    assert any(e.type == MessageEntity.BOLD for e in msg.entities)


@pytest.mark.unit
def test_learning_proverb_card_message_spec():
    msg = learning.proverb_card(
        "🇳🇱",
        "Geen gedoe",
        ["без лишней возни", "без заморочек"],
        "простая бытовая фраза",
        ["Ik wil gewoon geen gedoe. → Я просто хочу без лишней возни."],
    )

    assert msg.text.startswith("💭🇳🇱 Живой язык")
    assert "Geen gedoe" in msg.text
    assert "Как это переводится?" in msg.text
    assert "Как говорить ПРАВИЛЬНО" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)
    assert any(e.type == MessageEntity.ITALIC for e in msg.entities)


@pytest.mark.unit
def test_learning_phrase_intro_card_has_current_cta():
    msg = learning.phrase_intro_card(
        "Ik leer Nederlands",
        "Я учу нидерландский",
        "Nederlands leren",
        "учить нидерландский",
        [],
    )

    assert "Дальше проверим это правило на новом примере." in msg.text


@pytest.mark.unit
def test_learning_phrase_intro_card_other_forms_are_short():
    msg = learning.phrase_intro_card(
        "Deze auto is bijzonder duur.",
        "Эта машина необычно дорогая.",
        "bijzonder + прилагательное",
        "особенно, необычно + прилагательное",
        [
            {"pos": "bijzonder", "meaning": "особенный, необычный"},
            {"pos": "bijzonderheid", "meaning": "особенность"},
        ],
    )

    assert "Другие значения:" in msg.text
    assert "• bijzonder = особенный, необычный" in msg.text
    assert "bijzonderheid" not in msg.text


@pytest.mark.unit
def test_learning_phrase_quiz_result_is_compact():
    state = {
        "meaning": "door",
        "phrase_test_full": "Zij blijft thuis door de regen.",
        "sentence_ru": "Она остаётся дома из-за дождя.",
        "phrase_short_rule": "door = из-за, по причине чего-то",
    }

    correct = learning.phrase_quiz_result(state, True)
    wrong = learning.phrase_quiz_result(state, False)

    assert correct.text == (
        "✅ Верно\n\n"
        "Zij blijft thuis door de regen.\n"
        "Она остаётся дома из-за дождя."
    )
    assert "❌ Правильный ответ: door" in wrong.text
    assert "door = из-за, по причине чего-то" in wrong.text


@pytest.mark.unit
def test_learning_translate_and_levels_messages():
    prompt = learning.translate_prompt("🇳🇱", "Как дела?", "нидерландский")
    result = learning.translate_result("🇳🇱", "нидерландский", "Как дела?", "Hoe gaat het?", {
        "ok": False,
        "error": "нужен jij",
        "correct": "Hoe gaat het met jou?",
        "note": "met jou = с тобой",
    })
    levels = learning.levels("Лёгкий (A1–A2)", "Сложный (B1+)")

    assert prompt.parse_mode is None
    assert "📝 🇳🇱 Обратный перевод" in _entities_of_type(prompt, MessageEntity.BOLD)
    assert "Фраза: «Как дела?»" in prompt.text
    assert "❌ Ошибка: нужен jij" in result.text
    assert "🎚 Уровень языков" in levels.text
    assert "🎚 Уровень языков" in _entities_of_type(levels, MessageEntity.BOLD)


@pytest.mark.unit
def test_learning_morning_words_message_with_phrases_and_words():
    msg = learning.morning_words(
        "🇳🇱",
        "Повтори",
        is_read_aloud=True,
        phrases=[("Je hand opsteken", "Поднять руку")],
        words=[("Huis", "Дом")],
    )

    assert msg.parse_mode is None
    assert msg.text == (
        "📚🇳🇱 Слова и фразы дня\n"
        "Повтори\n\n"
        "💬 Фразы\n"
        "• Je hand opsteken → Поднять руку\n\n"
        "📖 Слова\n"
        "• Huis → Дом\n\n"
        "Попробуй использовать 1-2 элемента сегодня в сообщениях, мыслях или разговоре."
    )
    assert _entities_of_type(msg, MessageEntity.BOLD) == ["📚🇳🇱 Слова и фразы дня", "💬 Фразы", "📖 Слова"]
    assert _entities_of_type(msg, MessageEntity.ITALIC) == [
        "Повтори",
        "Попробуй использовать 1-2 элемента сегодня в сообщениях, мыслях или разговоре.",
    ]


@pytest.mark.unit
def test_learning_morning_words_empty_hint_plain_method():
    msg = learning.morning_words("🇳🇱", "Обычный метод", empty_hint=True)

    assert msg.text == (
        "📚🇳🇱 Слова и фразы дня\n"
        "Обычный метод\n\n"
        "📖 Открой словарь, если хочешь добавить что-то новое или быстро повторить текущее."
    )
    assert _entities_of_type(msg, MessageEntity.BOLD) == ["📚🇳🇱 Слова и фразы дня"]
    assert _entities_of_type(msg, MessageEntity.ITALIC) == []


@pytest.mark.unit
def test_learning_morning_words_empty_hint_read_aloud_method():
    msg = learning.morning_words("🇳🇱", "Прочитай вслух метод", is_read_aloud=True, empty_hint=True)

    assert msg.text == (
        "📚🇳🇱 Слова и фразы дня\n"
        "Прочитай вслух метод\n\n"
        "📖 Открой словарь, если хочешь добавить что-то новое или быстро повторить текущее."
    )
    assert _entities_of_type(msg, MessageEntity.ITALIC) == ["Прочитай вслух метод"]


@pytest.mark.unit
def test_learning_morning_words_only_phrases_no_words():
    msg = learning.morning_words("🇳🇱", "Обычный метод", phrases=[("Je hand opsteken", "Поднять руку")])

    assert "💬 Фразы" in msg.text
    assert "• Je hand opsteken → Поднять руку" in msg.text
    assert "📖 Слова" not in msg.text
    assert _entities_of_type(msg, MessageEntity.BOLD) == ["📚🇳🇱 Слова и фразы дня", "💬 Фразы"]


@pytest.mark.unit
def test_learning_morning_words_only_words_no_phrases():
    msg = learning.morning_words("🇳🇱", "Обычный метод", words=[("Huis", "Дом")])

    assert "📖 Слова" in msg.text
    assert "• Huis → Дом" in msg.text
    assert "💬 Фразы" not in msg.text
    assert _entities_of_type(msg, MessageEntity.BOLD) == ["📚🇳🇱 Слова и фразы дня", "📖 Слова"]


@pytest.mark.unit
def test_learning_morning_words_no_phrases_or_words_no_tip():
    msg = learning.morning_words("🇳🇱", "Обычный метод")

    assert msg.text == "📚🇳🇱 Слова и фразы дня\nОбычный метод"
    assert _entities_of_type(msg, MessageEntity.ITALIC) == []


@pytest.mark.unit
def test_learning_game_messages():
    ui = {"title": "Игра", "suspect": "Подозреваемый", "who": "Кто это", "found": "Нашёл", "answer": "Ответ", "hint": "Подсказка"}
    card = learning.game_card(ui, "• clue")
    found = learning.game_found(ui, "Sherlock <Holmes>", "детектив")
    hint = learning.game_hint(ui, "британец")

    assert card.parse_mode is None
    assert "Игра" in _entities_of_type(card, MessageEntity.BOLD)
    assert "Sherlock <Holmes>" in found.text
    assert "Sherlock <Holmes>" in _entities_of_type(found, MessageEntity.BOLD)
    assert "британец" in _entities_of_type(hint, MessageEntity.BOLD)


@pytest.mark.unit
def test_learning_train_result_word_mode_correct():
    state = {
        "word": "Toevoegen",
        "sentence": "Ik ga het toevoegen.",
        "sentence_ru": "Я собираюсь это добавить.",
        "meaning": "добавлять",
        "mode": "word",
    }

    msg = learning.train_result(state, 0, 0, ["добавлять", "убирать"])

    assert msg.parse_mode is None
    assert "✅ Верно." in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Toevoegen" in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Ik ga het toevoegen. → Я собираюсь это добавить." in msg.text
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_learning_train_result_word_mode_wrong_with_translation():
    state = {"word": "Toevoegen", "sentence": "", "sentence_ru": "", "meaning": "добавлять", "mode": "word"}

    msg = learning.train_result(state, 1, 0, ["добавлять", "убирать"], chosen_fl="NL")

    assert "❌ Не совсем так." in _entities_of_type(msg, MessageEntity.BOLD)
    assert "NL" in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Твой ответ: «убирать» — это NL." in msg.text


@pytest.mark.unit
def test_learning_train_result_phrase_mode():
    state = {
        "word": "zullen",
        "sentence": "Ik zullen komen",
        "sentence_ru": "Я приду",
        "meaning": "ходить",
        "mode": "phrase",
        "phrase_explanation": "zullen = буду",
    }

    msg = learning.train_result(state, 1, 0, ["zullen", "zal"])

    assert "❌ Не совсем так." in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Твой ответ: «zal»." in msg.text
    assert "zullen = буду" in msg.text
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_learning_train_lang_select_message():
    msg = learning.train_lang_select()

    assert msg.parse_mode is None
    assert "🧠 Тренажёр" in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Словарь" in _entities_of_type(msg, MessageEntity.BOLD)
    assert "Выбери язык для тренировки 👇" in _entities_of_type(msg, MessageEntity.BOLD)
    assert not msg.text.startswith("\n")
    assert not msg.text.endswith("\n")
