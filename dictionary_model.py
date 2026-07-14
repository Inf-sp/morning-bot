"""Чистая модель записи учебного словаря без Telegram и хранилища."""

import re


PHRASE_CORRECTIONS = {
    "waar wacht je op": {
        "term": "Waar wacht je op?",
        "translation": "Что ты ждёшь?",
        "english": "What are you waiting for?",
        "bad_translation": "На что ты ждешь",
        "unneeded_preposition": "на",
    },
}


def language_code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def entry_term(entry):
    if not isinstance(entry, dict):
        return str(entry)
    return str(entry.get("term") or entry.get("word") or entry.get("base_form") or "")


def entry_translation(entry):
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("translation") or entry.get("ru") or "")


def entry_language(entry):
    return str(entry.get("lang") or "nl") if isinstance(entry, dict) else "nl"


def normalize_key(text):
    return " ".join(re.findall(
        r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or "").lower(), re.UNICODE))


def normalize_entry(entry, *, language=None):
    """Возвращает единую схему поверх legacy term/word/base_form и ru."""
    source = dict(entry) if isinstance(entry, dict) else {"term": str(entry)}
    source["term"] = entry_term(source)
    source["translation"] = entry_translation(source)
    source["lang"] = language or entry_language(source)
    source.setdefault("kind", "phrase" if " " in source["term"].strip() else "word")
    source.setdefault("examples", [])
    source.setdefault("srs_history", [])
    return source
