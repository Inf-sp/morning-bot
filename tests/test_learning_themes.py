"""Темы обучения: 40 тем B1, включение и выключение, «Пропустить», подпись темы внизу «Обучения»."""
import json
import os
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import learning_themes
import live_language
from ui import menu as menu_ui


def _settings(monkeypatch):
    saved = {}
    import settings
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))
    profile = {}
    monkeypatch.setattr(live_language.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(live_language.store, "mutate_profile",
                        lambda _cid, change: profile.update(change(dict(profile))[0]))
    monkeypatch.setattr(live_language.store, "get_level", lambda *_a: "simple")
    return saved, profile


def test_every_grammar_topic_belongs_to_exactly_one_of_40_themes():
    topics = json.loads(Path("grammar_topics.json").read_text(encoding="utf-8"))["nl"]
    mapped = [topic_id for theme in learning_themes.THEMES for topic_id in theme[4]]
    assert len(learning_themes.THEMES) == 40 and len(learning_themes.CATEGORIES) == 5
    assert sorted(mapped) == sorted(topic["id"] for topic in topics)  # каждая — ровно в одной теме
    assert learning_themes.theme_of("nl-201") == 22 and learning_themes.label(22) == "Связь предложений (Aanvulling)"


def test_topic_of_day_follows_enabled_themes_and_skip_marks_the_theme_learned(monkeypatch):
    saved, _profile = _settings(monkeypatch)
    first = live_language.grammar_of_day("42", "nl")
    assert learning_themes.theme_of(first["id"]) == 1  # по порядку: Werkwoordstijden
    learning_themes.mark_done("42", 1)  # «Пропустить»
    nxt = live_language.grammar_of_day("42", "nl", renew=True)
    assert learning_themes.theme_of(nxt["id"]) == 2 and not learning_themes.is_on("42", 1)
    learning_themes.toggle("42", 2)  # выключил в настройках
    assert learning_themes.theme_of(live_language.grammar_of_day("42", "nl", renew=True)["id"]) == 3
    learning_themes.toggle("42", 1)  # включил снова — изученная снова в обучении
    assert learning_themes.is_on("42", 1) and 1 not in learning_themes.state("42")["done"]
    for number in range(1, 29):
        if learning_themes.is_on("42", number):
            learning_themes.toggle("42", number)
    lexical = live_language.grammar_of_day("42", "nl", renew=True)
    assert lexical["id"] == "nl-theme-29" and lexical["topic"] == "Werk en carrière"  # тема лексики — из самой темы


def test_learning_screen_has_skip_and_theme_in_the_footer():
    msg = menu_ui.learning_menu({"has_material": True, "lang_code": "nl",
                                 "live_language": {"rule": "_Zodra_ — как только. Глагол в конце."},
                                 "stats": {"words": 192, "topics": 37, "theme": "Связь предложений (Tijd)"}})
    assert msg.text.endswith("Твой нидерландский · 192 слова · Связь предложений (Tijd)")
    first = msg.reply_markup.inline_keyboard[0][0]
    assert first.text == "Пропустить" and first.callback_data == "a_learn_skip"
    assert not first.api_kwargs  # стандартного цвета


def test_explanation_ends_with_a_period():
    import live_language as ll
    card = ll._clean_card({"explain": "Используй trouwens, когда хочешь добавить мысль. Сразу после него идёт глагол",
                           "examples": [{"text": "Trouwens, heb je hem gebeld?", "translation": "Кстати, звонил?"}]})
    text = menu_ui.learning_menu({"has_material": True, "lang_code": "nl",
                                  "live_language": {"id": "nl-201", "topic": "Trouwens", "card": card}}).text
    assert "Сразу после него идёт глагол.\n" in text
