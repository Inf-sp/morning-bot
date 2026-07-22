"""Единая центральная часть карточки учебной словарной записи."""

import re


def _term(entry, fallback=None):
    term = str(entry.get("term") or entry.get("word") or fallback or "").strip()
    article = str(entry.get("article") or "").strip()
    if article and not term.casefold().startswith(article.casefold() + " "):
        term = f"{article} {term}"
    return term[:1].upper() + term[1:] if term else ""


def _breakdown(entry):
    raw = str(entry.get("breakdown") or "").strip().casefold()
    pos = str(entry.get("pos") or "").strip().casefold()
    if entry.get("construction") or "глагол + предлог" in raw:
        return "глагольная конструкция"
    if "разговор" in raw:
        return "разговорная фраза"
    is_verb = pos in {"глагол", "verb", "werkwoord"} or "глагол" in raw or "werkwoord" in raw
    if is_verb:
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
    }.get(pos) or (raw.replace(",", " · ") if raw else "выражение")


def _verified_forms(entry):
    try:
        confidence = float(entry.get("analysis_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    forms = [str(entry.get(key) or "").strip() for key in ("infinitive", "past_singular", "perfect_form")]
    return forms if confidence >= 0.75 and all(forms) else []


def _example(entry, term):
    candidates = list(entry.get("examples") or [])
    if entry.get("example_nl") and entry.get("example_ru"):
        candidates.append({"text": entry["example_nl"], "translation": entry["example_ru"]})
    term_words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'-]+", term.casefold())
    bare = [word for word in term_words if word not in {"de", "het", "een", "to", "the", "a", "an"}]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        translation = str(item.get("translation") or "").strip()
        words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'-]+", text.casefold())
        related = (len(bare) > 1 and " ".join(bare) in " ".join(words)) or any(
            word in words or (len(word) > 4 and any(candidate.startswith(word[:-2]) for candidate in words))
            for word in bare
        )
        if text and translation and related:
            return text, translation
    return "", ""


def render_learning_entry(builder, entry, *, fallback_term="", fallback_translation=""):
    """Рендерит термин, нужную грамматику и связанный пример без заголовка."""
    term = _term(entry, fallback_term)
    translation = str(entry.get("translation") or entry.get("ru") or fallback_translation or "").strip()
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
    if plural and breakdown.startswith("существительное"):
        if not plural.casefold().startswith("de "):
            plural = f"de {plural}"
        builder.labeled_line("Множественное число", plural, lowercase=False)
    forms = _verified_forms(entry)
    if forms:
        builder.labeled_line("Формы", " · ".join(forms), lowercase=False)
    example, example_translation = _example(entry, term)
    if example and example_translation:
        builder.spacer()
        builder.text_line("💡 ")
        builder.bold("Полезно:")
        builder.text_line(f" {example} → {example_translation}")
        builder.newline()
