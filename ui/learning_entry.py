"""Единая центральная часть карточки учебной словарной записи."""

import re

from telegram import MessageEntity

from dictionary_model import example_matches_term, present_conjugation, study_card_data

from dictionary_model import display_term

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")


def _mixed_script(value):
    text = str(value or "")
    return bool(_CYRILLIC_RE.search(text) and _LATIN_RE.search(text))


def _term(entry, fallback=None):
    term = str(entry.get("term") or entry.get("word") or fallback or "").strip()
    article = entry.get("article") or ""
    # A legacy record can carry a noun article even though the actual term is
    # a multi-word phrase.  Never prepend that article to a phrase card.
    if len(term.split()) > 1:
        article = ""
    return display_term(term, article)


def _breakdown(entry):
    raw = str(entry.get("breakdown") or "").strip().casefold()
    pos = str(entry.get("pos") or "").strip().casefold()
    term = str(entry.get("term") or entry.get("word") or "").strip()
    entry_type = str(entry.get("entry_type") or entry.get("kind") or "").strip().casefold()
    if len(term.split()) > 1 and not entry.get("construction") and entry_type not in {"construction", "expression", "разговорная фраза"}:
        return "разговорная фраза" if "разговор" in raw else "фраза"
    if entry.get("construction") or "глагол + предлог" in raw:
        return "глагольная конструкция"
    if "разговор" in raw:
        return "разговорная фраза"
    is_verb = pos in {"глагол", "verb", "werkwoord"} or "глагол" in raw or "werkwoord" in raw
    if is_verb:
        if entry.get("related_noun"):
            return "глагол"
        verb_type = str(entry.get("verb_type") or "").strip().casefold()
        return {"strong": "сильный глагол", "weak": "слабый глагол", "irregular": "неправильный глагол"}.get(verb_type, "глагол")
    is_noun = pos in {"существительное", "noun", "zelfstandig naamwoord"} or "существительн" in raw
    if is_noun:
        article = str(entry.get("article") or "").strip().casefold()
        return f"существительное · {article}-слово" if article in {"de", "het"} else "существительное"
    return {
        "adj": "прилагательное", "adjective": "прилагательное", "прилагательное": "прилагательное",
        "adverb": "наречие", "наречие": "наречие", "preposition": "предлог", "предлог": "предлог",
        "phrase": "выражение", "фраза": "выражение", "expression": "выражение",
    }.get(pos) or (raw.replace(",", " · ") if raw else (
        "слово" if len(term.split()) <= 1 else "выражение"
    ))


def _verified_forms(entry):
    try:
        confidence = float(entry.get("analysis_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    forms = [str(entry.get(key) or "").strip() for key in ("infinitive", "past_singular", "perfect_form")]
    if entry.get("related_noun"):
        forms = [str(entry.get(key) or "").strip()
                 for key in ("infinitive", "past_singular", "past_participle")]
    return forms if confidence >= 0.75 and all(forms) and not any(_mixed_script(form) for form in forms) else []


def _example(entry, term):
    candidates = list(entry.get("examples") or [])
    if entry.get("example_nl") and entry.get("example_ru"):
        candidates.append({"text": entry["example_nl"], "translation": entry["example_ru"]})
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("nl") or item.get("sentence") or "").strip()
        translation = str(
            item.get("translation") or item.get("ru")
            or item.get("sentence_ru") or item.get("translation_ru") or ""
        ).strip()
        if not text or not translation or _mixed_script(text):
            continue
        if example_matches_term({**entry, "term": term}, {"text": text, "translation": translation}):
            return text, translation
    return "", ""


def _without_terminal_period(value):
    """Карточка уже визуально завершена: точка в конце примера лишняя."""
    return str(value or "").strip().rstrip(". ")


def _meaning_case(value):
    """Значение после стрелки начинается со строчной, но аббревиатуры сохраняются."""
    text = str(value or "").strip()
    if len(text) > 1 and text[:2].isupper():
        return text
    return text[:1].lower() + text[1:] if text else ""


def render_study_card(builder, entry, *, include_exercise=True):
    """Единая сохранённая карточка для словаря и ежедневной рассылки."""
    card = study_card_data(entry)
    term = _term(entry, card.get("term"))
    translation = re.sub(r"\s*;\s*", " · ", card.get("translation") or "").strip()
    builder.spacer()
    builder.bold(term)
    builder.text_line(" → ")
    builder.line(" · ".join(
        part for part in (card.get("pronunciation"), _meaning_case(translation)) if part
    ))

    explanation = " ".join(
        part for part in (card.get("essence"), card.get("insight")) if part
    )
    builder.spacer()
    builder.label("В чём суть", explanation, lowercase=False)
    builder.newline()

    conjugation = present_conjugation(entry)
    if conjugation:
        builder.labeled_line("Спряжение", " · ".join(conjugation), lowercase=False)

    builder.spacer()
    builder.bold("Живые примеры:")
    builder.newline()
    for example in card.get("examples", [])[:2]:
        text = str(example.get("text") or "").strip()
        meaning = str(example.get("translation") or "").strip()
        context = str(example.get("context") or "").strip()
        line = f"{text} → {meaning}"
        builder.line(f"{line} ({context})" if context else line)

    if include_exercise:
        builder.spacer()
        builder.text_line("🎯 ")
        builder.bold("Твоя очередь:")
        builder.text_line(f" «{card.get('exercise_ru')}» → ")
        builder.add(card.get("exercise_answer"), MessageEntity.SPOILER)
        builder.newline()
    return builder


def render_learning_entry(
    builder, entry, *, fallback_term="", fallback_translation="",
):
    """Рендерит термин, нужную грамматику и связанный пример без заголовка."""
    term = _term(entry, fallback_term)
    translation = str(entry.get("translation") or entry.get("ru") or fallback_translation or "").strip()
    translation = re.sub(r"\s*\([^)]*\)", "", translation).strip()
    translation = re.sub(r"\s*;\s*", " · ", translation)
    if term or translation:
        builder.spacer()
        builder.bold(term)
        if translation:
            builder.text_line(f" → {translation[:1].upper() + translation[1:]}")
        builder.newline()
    breakdown = _breakdown(entry)
    if breakdown:
        builder.spacer()
        builder.labeled_line("Разбор", breakdown, lowercase=False)
    plural = str(entry.get("plural") or "").strip()
    if _mixed_script(plural):
        plural = ""
    if plural and breakdown.startswith("существительное"):
        if not plural.casefold().startswith("de "):
            plural = f"de {plural}"
        builder.labeled_line("Множественное число", plural, lowercase=False)
    forms = _verified_forms(entry)
    if forms:
        builder.labeled_line("Формы", " · ".join(forms), lowercase=False)
    conjugation = present_conjugation(entry)
    if conjugation:
        builder.labeled_line("Спряжение", " · ".join(conjugation), lowercase=False)
    example, example_translation = _example(entry, term)
    if example and example_translation:
        related_noun = entry.get("related_noun")
        if isinstance(related_noun, dict) and related_noun.get("term") and related_noun.get("translation"):
            builder.labeled_line("Пример", lowercase=False)
            builder.text_line(
                f"{str(example).strip()} → {str(example_translation).strip()}"
            )
            builder.newline()
            builder.spacer()
            builder.text_line("💡 ")
            builder.bold("Полезно:")
            builder.text_line(" ")
            builder.bold(str(related_noun["term"]).strip())
            builder.text_line(" → ")
            builder.bold(str(related_noun["translation"]).strip())
            related_plural = str(related_noun.get("plural") or "").strip()
            if related_plural:
                builder.text_line(f" (множественное число: {related_plural})")
            builder.newline()
        else:
            builder.spacer()
            builder.text_line("💡 ")
            builder.bold("Полезно:")
            builder.text_line(
                f" {_without_terminal_period(example)} → {_without_terminal_period(example_translation)}"
            )
            builder.newline()
