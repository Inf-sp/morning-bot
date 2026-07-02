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
