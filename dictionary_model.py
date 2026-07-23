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

# Каноническое оформление пользовательской записи. Ключи сравниваются без
# учёта регистра, чтобы исправить и новые, и legacy-копии записи.
CANONICAL_ENTRY_OVERRIDES = {
    "bewonderen": ("Bewonderen", "Восхищаться"),
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
    translation = str(entry.get("translation") or entry.get("ru") or "")
    override = CANONICAL_ENTRY_OVERRIDES.get(normalize_key(entry_term(entry)))
    return override[1] if override else translation


def capitalize_initial(text):
    """Единое пользовательское оформление: заглавна только первая буква."""
    value = " ".join(str(text or "").split()).strip()
    return value[:1].upper() + value[1:] if value else ""


def display_term(term, article=""):
    """Отображает термин без грамматически неверного ``Het Gevolg``.

    Базовая словарная форма хранится с заглавной буквы. Если у
    существительного есть артикль, заглавной остаётся начало всей записи:
    ``Het gevolg``, а не ``Het Gevolg``.
    """
    value = " ".join(str(term or "").split()).strip()
    article = " ".join(str(article or "").split()).strip()
    if article:
        prefix = article.casefold() + " "
        if value.casefold().startswith(prefix):
            value = value[len(article):].strip()
        if value:
            value = value[:1].lower() + value[1:]
        return capitalize_initial(f"{article} {value}")
    return capitalize_initial(value)


def normalize_translation_case(text):
    return capitalize_initial(text)


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
    """Сохраняет одиночные словарные формы с заглавной первой буквой."""
    text = " ".join(str(term or "").split()).strip()
    override = CANONICAL_ENTRY_OVERRIDES.get(normalize_key(text))
    if override:
        return override[0]
    return text.lower().capitalize() if text and is_dictionary_word(text, kind) else text


def normalize_entry(entry, *, language=None):
    """Возвращает единую схему поверх legacy term/word/base_form и ru."""
    source = dict(entry) if isinstance(entry, dict) else {"term": str(entry)}
    raw_term = entry_term(source)
    source["term"] = normalize_term_case(raw_term, source.get("kind", ""))
    source["translation"] = normalize_translation_case(entry_translation(source))
    source["lang"] = language or entry_language(source)
    source.setdefault("kind", "phrase" if " " in source["term"].strip() else "word")
    # Legacy AI records sometimes marked a whole construction as a noun and
    # attached ``de/het``.  An article belongs only to a single noun entry.
    if not is_dictionary_word(source["term"], source.get("kind", "")):
        source.pop("article", None)
    source.setdefault("examples", [])
    source.setdefault("srs_history", [])
    return source
