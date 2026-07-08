import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import asyncio

import learning
import store
from ui import learning as learning_ui


class Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return None


def _button_texts(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _self_check():
    return {
        "translation_matches_learning_phrase": True,
        "pattern_present_in_learning_phrase": True,
        "target_token_role_ok": True,
        "learning_phrase_natural": True,
        "test_checks_same_rule": True,
        "test_is_new_not_copy": True,
        "no_mixed_meanings": True,
    }


def test_phrase_fallback_uses_semantic_block_for_niet_te_doen():
    card = learning._fallback_phrase_quiz_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        "нидерландский",
    )

    assert card["construction"] == "niet te doen"
    assert card["construction"] != "doen"
    assert learning._phrase_card_is_consistent("Het is niet te doen", "Это невозможно сделать", card)


def test_phrase_intro_card_does_not_show_learning_placeholder():
    card = learning._fallback_phrase_quiz_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        "нидерландский",
    )
    msg = learning_ui.phrase_intro_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        card["construction"],
        card["construction_meaning"],
        card["short_rule"],
    )

    assert "значение из учебной фразы в новом контексте" not in msg.text


def test_phrase_intro_card_does_not_show_translation_placeholder():
    card = learning._fallback_phrase_quiz_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        "нидерландский",
    )
    msg = learning_ui.phrase_intro_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        card["construction"],
        card["construction_meaning"],
        card["short_rule"],
    )

    assert "смотри значение в переводе фразы" not in msg.text
    assert "смотри перевод фразы" not in msg.text


def test_llm_placeholder_card_is_rejected_and_fallback_is_used(monkeypatch):
    async def fake_gen(*_args, **_kwargs):
        return {
            "blank_phrase": "Deze opdracht is niet te ____.",
            "correct": "doen",
            "target_token": "doen",
            "wrong": ["maken", "gaan", "zijn"],
            "sentence_ru": "Это задание невозможно выполнить.",
            "test_full_phrase": "Deze opdracht is niet te doen.",
            "construction": "doen",
            "construction_meaning": "значение из учебной фразы в новом контексте",
            "short_rule": "doen = смотри значение в переводе фразы",
            "detail": "placeholder",
            "other_forms": [],
            "explanation": "placeholder",
            "self_check": _self_check(),
        }

    monkeypatch.setattr(learning, "_gen_phrase_quiz_card", fake_gen)

    card = asyncio.run(
        learning._gen_consistent_phrase_card(
            "Het is niet te doen",
            "Это невозможно сделать",
            "нидерландский",
            attempts=1,
        )
    )

    assert card["construction"] == "niet te doen"
    assert "значение из учебной фразы в новом контексте" not in str(card)
    assert "смотри значение в переводе фразы" not in str(card)


def test_phrase_unavailable_message_is_safe_when_fallback_is_impossible(monkeypatch):
    async def fake_gen(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(learning, "_gen_consistent_phrase_card", fake_gen)
    cid = "phrase-fallback-impossible"
    store.set_list("dict.json", cid, [
        {"lang": "nl", "word": "Op tafel", "ru": "На столе", "kind": "phrase"},
    ])
    store.train_state[str(cid)] = {
        "lang": "нидерландский",
        "mode": "phrase",
        "next_mode": "phrase",
        "locked_mode": "phrase",
        "used_phrases": [],
    }
    bot = Bot()

    asyncio.run(learning._render_phrase_quiz(bot, cid))

    payload = bot.messages[0]
    assert payload["text"] == "Не получилось собрать хорошую карточку.\nПопробуй следующую фразу."
    assert "согласованную карточку" not in payload["text"]
    assert _button_texts(payload["reply_markup"]) == [
        ["Следующая фраза"],
        ["Повторить позже"],
        ["⬅️ Назад"],
    ]


def test_phrase_poll_question_has_no_redundant_instruction():
    text, _entities = learning._phrase_poll_question("Deze opdracht is niet te ____.", "")

    assert "Выбери подходящее слово:" not in text


def test_phrase_test_sentence_does_not_repeat_source_phrase():
    card = learning._fallback_phrase_quiz_card(
        "Het is niet te doen",
        "Это невозможно сделать",
        "нидерландский",
    )

    assert learning._normalize_phrase_for_compare(card["blank_phrase"]) != learning._normalize_phrase_for_compare("Het is niet te doen")
    assert learning._normalize_phrase_for_compare(card["test_full_phrase"]) != learning._normalize_phrase_for_compare("Het is niet te doen")
