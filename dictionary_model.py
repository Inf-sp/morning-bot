"""Чистая модель записи учебного словаря без Telegram и хранилища."""

import re


DICTIONARY_FORMAT_VERSION = 4
STUDY_CARD_VERSION = 1
DICTIONARY_REBUILD_VERSION = 2

_STUDY_CARD_PLACEHOLDERS = (
    "русская транскрипция", "1-2 значения", "2-3 коротких предложения",
    "короткая яркая ассоциация", "один важный нюанс",
    "ассоциация или один важный нюанс", "...",
)


_LEADING_ARTICLE_RE = re.compile(r"^(?:de|het|een|the|a|an|to)\s+", re.I)
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


def study_card_data(entry):
    """Возвращает единые учебные поля записи, включая legacy-разбор рассылки."""
    if not isinstance(entry, dict):
        return {}
    legacy = entry.get("daily_word_deep_dive")
    legacy = legacy if isinstance(legacy, dict) else {}
    examples = entry.get("examples")
    if not isinstance(examples, list) or len(examples) < 2:
        examples = legacy.get("examples") or examples or []
    return {
        "term": entry_term(entry),
        "translation": str(entry.get("translation") or legacy.get("translation") or "").strip(),
        "pronunciation": str(entry.get("pronunciation") or legacy.get("pronunciation") or "").strip(),
        "essence": str(entry.get("essence") or legacy.get("essence") or "").strip(),
        "insight": str(
            entry.get("insight") or entry.get("usage_note") or entry.get("memory_hook")
            or legacy.get("insight") or legacy.get("usage_note") or legacy.get("memory_hook")
            or ""
        ).strip(),
        "examples": examples,
        "exercise_ru": str(entry.get("exercise_ru") or legacy.get("exercise_ru") or "").strip(),
        "exercise_answer": str(
            entry.get("exercise_answer") or legacy.get("exercise_answer") or ""
        ).strip(),
    }


def study_card_is_complete(entry):
    """Проверяет, что карточку можно целиком показать без догадок и шаблонов."""
    card = study_card_data(entry)
    required = (
        card.get("term"), card.get("translation"), card.get("pronunciation"),
        card.get("essence"), card.get("insight"), card.get("exercise_ru"),
        card.get("exercise_answer"),
    )
    if not all(str(value or "").strip() for value in required):
        return False
    combined = " ".join(str(value) for value in required).casefold()
    if any(marker in combined for marker in _STUDY_CARD_PLACEHOLDERS):
        return False
    if len(card["essence"]) < 20 or len(card["insight"]) < 12:
        return False
    pronunciation = card["pronunciation"]
    if not re.search(r"[А-Яа-яЁё]", pronunciation) or not (
        "\u0301" in pronunciation or "ё" in pronunciation.casefold()
    ):
        return False
    if not re.search(r"[А-Яа-яЁё]", card["essence"]):
        return False
    examples = card.get("examples")
    if not isinstance(examples, list) or len(examples) < 2:
        return False
    for example in examples[:2]:
        if not isinstance(example, dict):
            return False
        text = str(example.get("text") or "").strip()
        translation = str(example.get("translation") or "").strip()
        context = str(example.get("context") or "").strip()
        if (len(text) < 4 or len(translation) < 4 or len(context) < 3
                or "..." in (text, translation, context)):
            return False
        if not re.search(r"[А-Яа-яЁё]", translation):
            return False
    return True


def migrate_legacy_study_card(entry):
    """Переносит валидный daily-разбор в канонические поля той же записи."""
    if not isinstance(entry, dict) or not study_card_is_complete(entry):
        return False
    card = study_card_data(entry)
    changed = False
    for field in (
        "pronunciation", "essence", "insight", "examples",
        "exercise_ru", "exercise_answer",
    ):
        if entry.get(field) != card[field]:
            entry[field] = card[field]
            changed = True
    if entry.get("study_card_version") != STUDY_CARD_VERSION:
        entry["study_card_version"] = STUDY_CARD_VERSION
        changed = True
    if "daily_word_deep_dive" in entry:
        entry.pop("daily_word_deep_dive", None)
        changed = True
    return changed


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

    def dutch_verb_stem(infinitive):
        """Best-effort spelling stem for regular Dutch infinitives."""
        word = str(infinitive or "").casefold()
        if not word.endswith("en") or len(word) < 5:
            return ""
        stem = word[:-2]
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            return stem[:-1]
        vowels = "aeiou"
        if (len(stem) >= 3 and stem[-1] not in vowels
                and stem[-2] in vowels and stem[-3] not in vowels):
            return stem[:-1] + stem[-2] + stem[-1]
        return stem

    def matches_separable_dutch_verb(infinitive):
        prefixes = (
            "achter", "binnen", "boven", "buiten", "tegen", "terug",
            "voor", "door", "over", "onder", "samen", "vast", "verder",
            "aan", "af", "bij", "in", "mee", "na", "om", "op", "toe",
            "uit", "weg",
        )
        for prefix in prefixes:
            if not infinitive.startswith(prefix) or prefix not in candidate_words:
                continue
            stem = dutch_verb_stem(infinitive[len(prefix):])
            if stem and any(related_word(stem, candidate) for candidate in candidate_words):
                return True
        return False

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

    if any(
        related_word(search_word, candidate)
        for search_word in search_words for candidate in candidate_words
    ):
        return True
    return entry_language(entry) == "nl" and matches_separable_dutch_verb(term_words[0])


def is_dictionary_word(term, kind=""):
    """True для одиночной словарной единицы, включая вариант с артиклем."""
    text = _LEADING_ARTICLE_RE.sub("", " ".join(str(term or "").split()))
    return bool(text) and len(text.split()) == 1


def normalize_term_case(term, kind=""):
    """Сохраняет одиночные словарные формы с заглавной первой буквой."""
    text = " ".join(str(term or "").split()).strip()
    override = CANONICAL_ENTRY_OVERRIDES.get(normalize_key(text))
    if override:
        return override[0]
    return text.lower().capitalize() if text and is_dictionary_word(text, kind) else text


_POS_PATTERNS = (
    ("прилагательное", r"\b(?:прилагательное|adjective|adj|bijvoeglijk naamwoord)\b"),
    ("глагол", r"\b(?:глагол|verb|werkwoord)\b"),
    ("существительное", r"\b(?:существительное|noun|zelfstandig naamwoord)\b"),
    ("местоимение", r"\b(?:местоимение|pronoun|voornaamwoord)\b"),
    ("наречие", r"\b(?:наречие|adverb|bijwoord)\b"),
    ("предлог", r"\b(?:предлог|preposition|voorzetsel)\b"),
    ("фраза", r"\b(?:фраза|предложение|выражение|конструкция|phrase|sentence|expression|construction)\b"),
)


def canonical_part_of_speech(entry) -> str:
    """Сводит pos и грамматический разбор к одному значению.

    Разбор появляется после лексикографической проверки, поэтому при
    конфликте он исправляет legacy-pos. Глагольная конструкция остаётся глаголом.
    """
    source = entry if isinstance(entry, dict) else {}
    breakdown = " ".join(str(source.get("breakdown") or "").casefold().split())
    for canonical, pattern in _POS_PATTERNS:
        if re.search(pattern, breakdown, re.I):
            return canonical
    raw_pos = " ".join(str(source.get("pos") or "").casefold().split())
    for canonical, pattern in _POS_PATTERNS:
        if re.fullmatch(pattern, raw_pos, re.I):
            return canonical
    return raw_pos


def entry_is_dictionary_word(entry) -> bool:
    """Отделяет одиночные слова от архивных фраз и конструкций."""
    source = entry if isinstance(entry, dict) else {"term": str(entry or "")}
    entry_type = str(source.get("entry_type") or source.get("kind") or "").casefold()
    if canonical_part_of_speech(source) == "фраза" or entry_type in {
        "phrase", "sentence", "expression", "construction",
    }:
        return False
    return is_dictionary_word(entry_term(source))


def normalize_entry(entry, *, language=None):
    """Возвращает единую схему поверх legacy term/word/base_form и ru."""
    source = dict(entry) if isinstance(entry, dict) else {"term": str(entry)}
    raw_term = entry_term(source)
    source["term"] = normalize_term_case(raw_term, source.get("kind", ""))
    source["translation"] = normalize_translation_case(entry_translation(source))
    source["lang"] = language or entry_language(source)
    source.setdefault("kind", "phrase" if " " in source["term"].strip() else "word")
    canonical_pos = canonical_part_of_speech(source)
    if canonical_pos:
        source["pos"] = canonical_pos
    # Legacy AI records sometimes marked a whole construction as a noun and
    # attached ``de/het``.  An article belongs only to a single noun entry.
    if not is_dictionary_word(source["term"], source.get("kind", "")):
        source.pop("article", None)
    source.setdefault("examples", [])
    source.setdefault("srs_history", [])
    return source
