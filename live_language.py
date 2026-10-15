"""Фраза дня «Моего дня» (data/daily_phrases.json) и грамматика дня для экрана «Обучение» (grammar_topics.json)."""

import functools
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path

import config
import store

_TOPICS_FILE = Path(__file__).with_name("grammar_topics.json")
# Темы уровня из настроек обучения: «Простой» — A1–A2, «Сложный» — B1–B2.
_LEVEL_TOPICS = {"simple": ("A1", "A2"), "hard": ("B1", "B2")}
_LANGUAGE_NAMES = {"nl": "нидерландский", "en": "английский"}  # уровень хранится под названием языка


@functools.lru_cache(maxsize=None)
def grammar_topics(language) -> tuple:
    """Темы грамматики языка (300+ на nl и en): {id, level, topic, meaning, explain, details, example, translation}."""
    try:
        data = json.loads(_TOPICS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(data.get(language) or ())


def grammar_of_day(cid, language="nl", renew=False) -> dict:
    """Тема грамматики на сегодня: держится до завтра, на следующий день — следующая ещё не показанная.

    Только темы уровня из настроек («Простой» — A1–A2, «Сложный» — B1–B2); сменили уровень — тема дня
    подбирается заново. Когда показаны все темы уровня — круг заново. Прогресс — в профиле по языку.
    renew — «Новая тема»: сразу следующая несыгранная, она становится темой дня.
    """
    topics = grammar_topics(language) or grammar_topics("nl")
    if not topics:
        return {}
    today = datetime.now(config.TZ).date().isoformat()
    progress = dict(((store.get_profile(cid) or {}).get("grammar_progress") or {}).get(language) or {})
    level = store.get_level(cid, _LANGUAGE_NAMES.get(language, language))
    allowed = _LEVEL_TOPICS.get(level, _LEVEL_TOPICS["simple"])
    by_id = {topic["id"]: topic for topic in topics}
    if (not renew and progress.get("date") == today and progress.get("id") in by_id
            and by_id[progress["id"]]["level"] in allowed):
        return _as_card(by_id[progress["id"]])
    ranked = sorted((topic for topic in topics if topic["level"] in allowed),
                    key=lambda topic: allowed.index(topic["level"])) or list(topics)
    seen = set(progress.get("seen") or [])
    fresh = [topic for topic in ranked if topic["id"] not in seen]
    if not fresh:  # пройдены все темы уровня — новый круг по этому уровню
        seen -= {topic["id"] for topic in ranked}
        fresh = ranked
    chosen = fresh[0]

    def save(profile):
        all_progress = dict(profile.get("grammar_progress") or {})
        all_progress[language] = {"date": today, "id": chosen["id"], "seen": [*sorted(seen), chosen["id"]]}
        return {**profile, "grammar_progress": all_progress}, None

    store.mutate_profile(cid, save)
    return _as_card(chosen)


def _as_card(topic) -> dict:
    """Поля карточки «Обучения»: тема → значение, объяснение по пунктам (details, цитатой; без них — explain),
    пример → перевод."""
    return {"topic": topic["topic"], "meaning": topic["meaning"], "explain": topic["explain"],
            "details": list(topic.get("details") or []),
            "more": topic["example"], "more_translation": topic["translation"], "level": topic["level"]}


_PHRASES_FILE = Path(__file__).with_name("data") / "daily_phrases.json"


@functools.lru_cache(maxsize=None)
def daily_phrases(language) -> tuple:
    """Запас разговорных фраз дня (300+ на nl и en): {text, translation}."""
    try:
        data = json.loads(_PHRASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(item for item in data.get(language) or () if item.get("text") and item.get("translation"))


def _phrase_key(phrase) -> str:
    """Короткий стабильный ключ фразы для истории в профиле: порядок в файле можно менять."""
    return hashlib.sha1(phrase["text"].casefold().encode("utf-8")).hexdigest()[:10]


def daily_phrase(cid, language="nl") -> dict:
    """Фраза дня для «Моего дня»: держится до завтра, на следующий день — случайная ещё не показанная из запаса.

    Повторов нет, пока не пройден весь запас; потом — новый круг, вчерашняя фраза первой не вернётся.
    Прогресс — в профиле по языку; без AI и сети.
    """
    phrases = daily_phrases(language) or daily_phrases("nl")
    if not phrases:
        return {}
    today = datetime.now(config.TZ).date().isoformat()
    by_key = {_phrase_key(phrase): phrase for phrase in phrases}
    progress = dict(((store.get_profile(cid) or {}).get("phrase_progress") or {}).get(language) or {})
    if progress.get("date") == today and progress.get("key") in by_key:
        return dict(by_key[progress["key"]])
    seen = [key for key in progress.get("seen") or [] if key in by_key]
    shown = set(seen)
    fresh = [key for key in by_key if key not in shown]
    if not fresh:  # пройден весь запас — новый круг
        seen = seen[-1:]
        fresh = [key for key in by_key if key not in seen] or list(by_key)
    chosen = random.choice(fresh)

    def save(profile):
        all_progress = dict(profile.get("phrase_progress") or {})
        all_progress[language] = {"date": today, "key": chosen, "seen": [*seen, chosen]}
        return {**profile, "phrase_progress": all_progress}, None

    store.mutate_profile(cid, save)
    return dict(by_key[chosen])
