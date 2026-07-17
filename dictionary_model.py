"""Чистая модель записи учебного словаря без Telegram и хранилища."""

import re


_LEADING_ARTICLE_RE = re.compile(r"^(?:de|het|een|the|a|an)\s+", re.I)


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


def is_dictionary_word(term, kind=""):
    """True для одиночной словарной единицы, включая вариант с артиклем."""
    if str(kind or "").strip().casefold() in {"word", "слово"}:
        return True
    text = _LEADING_ARTICLE_RE.sub("", " ".join(str(term or "").split()))
    return len(text.split()) <= 1


def normalize_term_case(term, kind=""):
    """Одиночные слова — строчными; регистр фраз и предложений не меняется."""
    text = " ".join(str(term or "").split()).strip()
    return text.lower() if text and is_dictionary_word(text, kind) else text


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
