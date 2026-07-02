import pytest
from telegram import MessageEntity

from ui import learning


@pytest.mark.unit
def test_learning_phrase_poll_question_message_spec():
    msg = learning.phrase_poll_question("Ik maak me zorgen om ____", "Я переживаю за тебя")

    assert msg.text.startswith("Фраза-тренажёр\n\nIk maak me zorgen om ____")
    assert "Перевод: Я переживаю за тебя" in msg.text
    assert "Выбери пропущенное слово" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)


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
def test_learning_translate_and_levels_messages():
    prompt = learning.translate_prompt("🇳🇱", "Как дела?", "нидерландский")
    result = learning.translate_result("🇳🇱", "нидерландский", "Как дела?", "Hoe gaat het?", {
        "ok": False,
        "error": "нужен jij",
        "correct": "Hoe gaat het met jou?",
        "note": "met jou = с тобой",
    })
    levels = learning.levels("Лёгкий (A1–A2)", "Сложный (B1+)")

    assert prompt.parse_mode == "HTML"
    assert "Фраза: «Как дела?»" in prompt.text
    assert "❌ Ошибка: нужен jij" in result.text
    assert "🎚 <b>Уровень языков</b>" in levels.text


@pytest.mark.unit
def test_learning_morning_words_message():
    msg = learning.morning_words("🇳🇱", "<i>Повтори</i>", [("Je hand opsteken", "Поднять руку")], [("Huis", "Дом")])

    assert msg.parse_mode == "HTML"
    assert "💬 <b>Фразы</b>" in msg.text
    assert "• Je hand opsteken → Поднять руку" in msg.text
    assert "📖 <b>Слова</b>" in msg.text


@pytest.mark.unit
def test_learning_game_messages():
    ui = {"title": "Игра", "suspect": "Подозреваемый", "who": "Кто это", "found": "Нашёл", "answer": "Ответ", "hint": "Подсказка"}
    card = learning.game_card(ui, "• clue")
    found = learning.game_found(ui, "Sherlock <Holmes>", "детектив")
    hint = learning.game_hint(ui, "британец")

    assert card.parse_mode == "HTML"
    assert "<b>Игра</b>" in card.text
    assert "Sherlock &lt;Holmes&gt;" in found.text
    assert "<b>британец</b>" in hint.text
