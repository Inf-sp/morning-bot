"""Чистая модель записи учебного словаря без Telegram и хранилища."""

import re


_LEADING_ARTICLE_RE = re.compile(r"^(?:de|het|een|the|a|an)\s+", re.I)
_EXAMPLE_STOP_WORDS = {
    "de", "het", "een", "the", "a", "an", "wat", "wie", "waar",
    "je", "jij", "jou", "u", "you", "ik", "i", "we", "wij", "ze",
    "zij", "hij", "zij", "is", "zijn", "ben", "be", "doe", "do", "does",
    "daar", "there", "op", "in", "aan", "van", "voor", "to", "of", "and",
    "en", "that", "this", "it",
}


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
    "wat balen": ("Wat balen", "Вот досада!"),
    "wat doe je daar": ("Wat doe je daar?", "Что ты делаешь там?"),
}


def language_code(language):
    if language in ("nl", "en"):
        return language
    if language in ("", "none", "не изучаю"):
        return "nl"
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


def example_matches_term(entry, example):
    """Проверяет, что пример действительно относится к записи словаря.

    Для фраз одного совпадения общего служебного слова недостаточно: иначе
    пример для ``Wat doe je daar?`` ошибочно привязывается к ``Wat balen``
    только из-за слова ``wat``.
    """
    if not isinstance(entry, dict) or not isinstance(example, dict):
        return False
    term = entry_term(entry)
    text = str(example.get("text") or example.get("nl") or example.get("sentence") or "")
    term_words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'-]+", term.casefold())
    candidate_words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'-]+", text.casefold())
    if not term_words or not candidate_words:
        return False

    search_words = list(term_words)
    for key in ("infinitive", "past_singular", "past_participle", "perfect_form"):
        search_words.extend(re.findall(
            r"[\wÀ-ÖØ-öø-ÿ'-]+", str(entry.get(key) or "").casefold()))
    forms = entry.get("forms")
    if isinstance(forms, list):
        for form in forms:
            search_words.extend(re.findall(
                r"[\wÀ-ÖØ-öø-ÿ'-]+", str(form or "").casefold()))
    search_words = list(dict.fromkeys(search_words))

    def related_word(left, right):
        if left == right:
            return True
        if len(left) < 5 or len(right) < 4:
            return False
        common = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            common += 1
        return common >= 4

    if len(term_words) > 1:
        normalized_term = " ".join(term_words)
        normalized_text = " ".join(candidate_words)
        if normalized_term in normalized_text:
            return True
        meaningful = [word for word in term_words if word not in _EXAMPLE_STOP_WORDS]
        if not meaningful:
            meaningful = term_words
        # Каждый смысловой компонент фразы должен быть подтверждён примером.
        return all(
            any(related_word(word, candidate) for candidate in candidate_words)
            for word in meaningful
        )

    return any(
        related_word(search_word, candidate)
        for search_word in search_words for candidate in candidate_words
    )


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
