"""«Вечернее повторение»: викторина Telegram по слову утренней рассылки."""
import asyncio
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import dictionary_morning

TODAY = date(2026, 11, 26)
IMMERS = {"id": "w1", "lang": "nl", "term": "immers", "translation": "ведь · же", "pos": "наречие",
          "essence": "Подчёркивает известный факт.", "insight": "Обычно в середине предложения.",
          "examples": [{"text": "Het is immers weekend!", "translation": "Сейчас ведь выходные!"},
                       {"text": "Je bent immers mijn vriend.", "translation": "Ты же мой друг."}],
          "exercise_ru": "Я ведь читаю.", "exercise_answer": "Ik lees immers.", "study_card_version": 1}


def _setup(monkeypatch, words, state):
    profile = {dictionary_morning.QUIZ_KEY: state}
    monkeypatch.setattr(dictionary_morning, "_ensure_dict", lambda _cid: words)
    monkeypatch.setattr(dictionary_morning.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(dictionary_morning.store, "mutate_profile",
                        lambda _cid, change: profile.update(change(dict(profile))[0]))
    return profile


def test_quiz_asks_the_morning_word_with_its_first_example(monkeypatch):
    _setup(monkeypatch, [IMMERS], {"date": TODAY.isoformat(), "id": "w1", "lang": "nl"})
    import ai
    monkeypatch.setattr(ai, "llm_json", lambda *_a, **_k: {"options": [
        "Скоро будут выходные.", "Выходные уже закончились.", "Я жду выходных."]})
    quiz = dictionary_morning.build_evening_quiz("42", TODAY)
    assert quiz["question"] == "🇳🇱 Повторение · Immers\n\nHet is immers weekend! → …"
    assert sorted(quiz["options"]) == sorted(["Сейчас ведь выходные!", "Скоро будут выходные",
                                              "Выходные уже закончились", "Я жду выходных"])  # без точки
    assert quiz["options"][quiz["correct"]] == "Сейчас ведь выходные!"


def test_quiz_without_ai_uses_other_dictionary_examples_and_is_sent_once(monkeypatch):
    others = [{"id": f"o{i}", "lang": "nl", "term": f"w{i}", "translation": "x",
               "examples": [{"text": f"Zin {i}.", "translation": f"Другая фраза {i}."}]} for i in range(3)]
    profile = _setup(monkeypatch, [IMMERS, *others], {"date": TODAY.isoformat(), "id": "w1", "lang": "nl"})
    import ai
    monkeypatch.setattr(ai, "llm_json", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(dictionary_morning, "datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 11, 26, 19, 30))}))
    sent = []

    class Bot:
        async def send_poll(self, **kwargs):
            sent.append(kwargs)

    assert asyncio.run(dictionary_morning.send_evening_quiz(Bot(), "42")) is True
    assert sent[0]["type"] == "quiz" and len(sent[0]["options"]) == 4
    assert sent[0]["options"][sent[0]["correct_option_id"]] == "Сейчас ведь выходные!"
    assert profile[dictionary_morning.QUIZ_KEY]["sent"] is True
    assert asyncio.run(dictionary_morning.send_evening_quiz(Bot(), "42")) is False  # второй раз — нет


def test_no_quiz_without_a_morning_word_today(monkeypatch):
    _setup(monkeypatch, [IMMERS], {"date": "2026-11-25", "id": "w1", "lang": "nl"})
    assert dictionary_morning.build_evening_quiz("42", TODAY) is None
