"""Conservative LanguageTool checks and migrations for learning records."""

from __future__ import annotations

import asyncio
import copy
import re
import unicodedata
import uuid

import language_tool
from dictionary_model import (
    entry_language, entry_term, entry_translation, normalize_term_case,
    normalize_translation_case,
)

_SPACE_RE = re.compile(r"\s+")
_DOUBLE_PUNCT_RE = re.compile(r"([.!?])\1+")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_ARTICLE_RE = re.compile(r"^(de|het|een)\s+", re.I)
_MAX_CONCURRENCY = 2

_NL_FIXED_PREPOSITIONS = {
    "aan", "achter", "bij", "in", "met", "naar", "om", "onder", "op",
    "over", "tegen", "tot", "uit", "van", "voor",
}
_NL_VERB_WITH_PREPOSITION_RE = re.compile(
    r"^((?:[a-zà-öø-ÿĳ]+en|gaan|staan|zien|doen|zijn))\s+("
    + "|".join(sorted(_NL_FIXED_PREPOSITIONS)) + r")$",
    re.I,
)
_NL_LOCAL_FIXED_VERBS = {
    ("wennen", "aan"): {
        "past_singular": "wende",
        "past_participle": "gewend",
        "auxiliary": "hebben",
        "perfect_form": "heeft gewend",
        "verb_type": "weak",
        "example_nl": "Ik moet wennen aan het nieuwe bed.",
        "example_ru": "Мне нужно привыкнуть к новой кровати.",
    },
}


def _clean(value) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _SPACE_RE.sub(" ", text).strip()
    return _DOUBLE_PUNCT_RE.sub(r"\1", text)


def _sentence(value) -> str:
    text = _clean(value)
    if text and len(text.split()) >= 3 and text[-1] not in ".!?…":
        text += "."
    return text


def _normalize_term(value, kind="") -> str:
    term = _clean(value)
    if term.endswith(".") and len(term.split()) <= 3:
        term = term[:-1].rstrip()
    normalized_case = normalize_term_case(term, kind)
    if normalized_case != term:
        term = normalized_case
    elif term.isupper():
        term = term.lower()
    return term


def _normalize_lang(value) -> str:
    value = str(value or "").strip().casefold()
    if value in ("en", "en-us", "en-gb", "english", "английский"):
        return "en"
    return "nl"


def dutch_verb_with_preposition(term) -> tuple[str, str] | None:
    """Распознаёт учебную форму `инфинитив + фиксированный предлог`."""
    match = _NL_VERB_WITH_PREPOSITION_RE.fullmatch(_clean(term).casefold())
    return (match.group(1), match.group(2)) if match else None


def known_dutch_fixed_verb(infinitive, preposition) -> dict:
    """Локально проверенные формы для конструкций, где ошибка особенно критична."""
    return dict(_NL_LOCAL_FIXED_VERBS.get(
        (_clean(infinitive).casefold(), _clean(preposition).casefold()),
        {},
    ))


def _safe_fixed_verb_example(text, infinitive, preposition) -> bool:
    text = _clean(text).casefold()
    modal = r"(?:moet|wil|kan|mag|zal|gaat|probeer|probeert)"
    return bool(re.search(
        rf"\b{modal}\s+{re.escape(infinitive)}\s+{re.escape(preposition)}\b",
        text,
    ))


def normalize_dutch_grammar(entry: dict) -> tuple[dict, bool]:
    """Исправляет надёжно определяемую грамматику до LanguageTool и сохранения."""
    normalized = dict(entry or {})
    if entry_language(normalized) != "nl":
        return normalized, False
    structure = dutch_verb_with_preposition(entry_term(normalized))
    if not structure:
        return normalized, False
    before = copy.deepcopy(normalized)
    infinitive, preposition = structure
    normalized.update({
        "term": f"{infinitive} {preposition}",
        "article": "",
        "pos": "глагол",
        "breakdown": f"глагол + предлог {preposition}",
        "construction": f"{infinitive} {preposition}",
        "infinitive": infinitive,
    })

    local = _NL_LOCAL_FIXED_VERBS.get(structure)
    if local:
        normalized.update(local)
        normalized.update({
            "forms": [local["past_singular"], local["perfect_form"]],
            "examples": [{
                "text": local["example_nl"],
                "translation": local["example_ru"],
            }],
            "analysis_confidence": 1.0,
            "analysis_provider": "local_grammar",
        })
        normalized.pop("verb_analysis_failed", None)
    else:
        examples = [
            example for example in normalized.get("examples") or []
            if isinstance(example, dict) and _safe_fixed_verb_example(
                example.get("text", ""), infinitive, preposition,
            )
        ]
        normalized["examples"] = examples
        if not _safe_fixed_verb_example(
                normalized.get("example_nl", ""), infinitive, preposition):
            normalized.pop("example_nl", None)
            normalized.pop("example_ru", None)
    return normalized, normalized != before


def _normalize_examples(raw_examples) -> list[dict]:
    result = []
    for raw in raw_examples or []:
        if isinstance(raw, str):
            text, translation = raw, ""
        elif isinstance(raw, dict):
            text = raw.get("text") or raw.get("sentence") or raw.get("nl") or raw.get("en") or ""
            translation = raw.get("translation") or raw.get("ru") or ""
        else:
            continue
        text = _sentence(text)
        translation = _sentence(translation)
        if text:
            example = {"text": text}
            if translation:
                example["translation"] = translation
            result.append(example)
    return result


def normalize_entry(raw) -> tuple[dict, bool]:
    """Migrate legacy field names and deterministic formatting only."""
    source = dict(raw) if isinstance(raw, dict) else {"term": str(raw or "")}
    before = dict(source)
    term = _normalize_term(entry_term(source), source.get("kind", ""))
    article = _clean(source.get("article")).casefold()
    article_match = _ARTICLE_RE.match(term)
    if article_match:
        article = article or article_match.group(1).casefold()
        term = _normalize_term(term[article_match.end():], "word")
    if article not in ("de", "het", "een"):
        article = ""

    normalized = dict(source)
    normalized["id"] = str(source.get("id") or uuid.uuid4().hex)
    normalized["lang"] = _normalize_lang(source.get("lang") or source.get("language"))
    normalized["term"] = term
    normalized["translation"] = normalize_translation_case(_clean(entry_translation(source)))
    normalized["examples"] = _normalize_examples(source.get("examples"))
    if article:
        normalized["article"] = article
    else:
        normalized.pop("article", None)

    forms = source.get("forms") or []
    if isinstance(forms, str):
        forms = re.split(r"[,;·]", forms)
    forms = [_clean(item) for item in forms if _clean(item)]
    if forms:
        normalized["forms"] = list(dict.fromkeys(forms))
    else:
        normalized.pop("forms", None)

    for key in ("infinitive", "past_singular", "past_participle", "perfect_form", "plural"):
        value = _clean(source.get(key))
        if value:
            normalized[key] = value
        else:
            normalized.pop(key, None)
    for key in ("construction", "rule", "learning_note"):
        value = _clean(source.get(key))
        if value:
            normalized[key] = value
        else:
            normalized.pop(key, None)
    for key in ("usage", "rules"):
        values = source.get(key) or []
        if isinstance(values, str):
            values = [values]
        values = [_clean(value) for value in values if isinstance(value, str) and _clean(value)]
        if values:
            normalized[key] = values
        else:
            normalized.pop(key, None)
    example_nl = _sentence(source.get("example_nl"))
    if example_nl:
        normalized["example_nl"] = example_nl
    elif "example_nl" in normalized:
        normalized.pop("example_nl", None)

    for legacy in (
        "word", "base_form", "ru", "language",
        "pending_language_check", "language_check_status", "language_review_required",
    ):
        normalized.pop(legacy, None)
    for key in list(normalized):
        if normalized[key] is None or normalized[key] == "" or normalized[key] == []:
            normalized.pop(key, None)
    normalized, grammar_changed = normalize_dutch_grammar(normalized)
    return normalized, grammar_changed or normalized != before


def _language_code(entry) -> str:
    return "en-US" if entry_language(entry) == "en" else "nl-NL"


def _targets(entry) -> list[dict]:
    targets = []
    term = str(entry.get("term") or "")
    if term:
        # Регистр словаря — это оформление, а не часть проверки орфографии.
        # Проверяем одиночную лексему в обычной форме, затем возвращаем ей
        # единый вид с заглавной буквы перед сохранением.
        check_term = term.lower() if len(term.split()) == 1 else term
        targets.append({"field": "term", "text": check_term, "safe": True})
        article = str(entry.get("article") or "")
        if article:
            targets.append({"field": "article_term", "text": f"{article} {term}", "safe": False})
    for index, example in enumerate(entry.get("examples") or []):
        text = str(example.get("text") or "") if isinstance(example, dict) else ""
        if text:
            targets.append({"field": f"examples.{index}.text", "text": text, "safe": True})
    if entry.get("example_nl"):
        targets.append({"field": "example_nl", "text": str(entry["example_nl"]), "safe": True})
    for field in ("plural", "infinitive", "past_singular", "past_participle", "perfect_form"):
        if entry.get(field):
            targets.append({"field": field, "text": str(entry[field]), "safe": False})
    for index, value in enumerate(entry.get("forms") or []):
        targets.append({"field": f"forms.{index}", "text": str(value), "safe": False})
    for field in ("construction", "rule", "learning_note"):
        value = str(entry.get(field) or "")
        if _LATIN_RE.search(value) and not _CYRILLIC_RE.search(value):
            targets.append({"field": field, "text": value, "safe": True})
    for field in ("usage", "rules"):
        for index, value in enumerate(entry.get(field) or []):
            value = str(value or "")
            if _LATIN_RE.search(value) and not _CYRILLIC_RE.search(value):
                targets.append({"field": f"{field}.{index}", "text": value, "safe": True})
    return targets


def _set_field(entry: dict, path: str, value: str) -> None:
    if path.startswith("examples."):
        _, index, _ = path.split(".", 2)
        entry["examples"][int(index)]["text"] = value
    elif path.startswith("forms."):
        entry["forms"][int(path.split(".", 1)[1])] = value
    elif path.startswith("usage.") or path.startswith("rules."):
        field, index = path.split(".", 1)
        entry[field][int(index)] = value
    elif path != "article_term":
        entry[path] = value


async def check_entry(entry: dict, *, semaphore=None) -> tuple[dict, dict]:
    """Безопасно исправляет только однозначные ошибки в изучаемом языке."""
    checked = copy.deepcopy(entry)
    stats = {"checked_fields": 0, "fixed_fields": 0, "available": True}
    targets = _targets(checked)
    reports = await asyncio.gather(*(
        language_tool.check_text_retry(
            target["text"], _language_code(checked), retries=1, semaphore=semaphore,
        )
        for target in targets
    ))
    for target, report in zip(targets, reports):
        stats["checked_fields"] += 1
        if not report.get("available"):
            stats["available"] = False
            continue
        issues = language_tool.meaningful_issues(report)
        is_term_target = target["field"] in ("term", "article_term")
        safe_issues = [
            issue for issue in issues
            if target["safe"] and language_tool.is_safe_issue(issue)
            and not (
                is_term_target
                and "SENTENCE_START" in str(issue.get("rule_id") or "").upper()
            )
        ]
        corrected = language_tool.apply_safe_replacements(target["text"], safe_issues)
        if target["field"].startswith("examples.") or target["field"] == "example_nl":
            corrected = _sentence(corrected)
        if corrected != target["text"]:
            _set_field(checked, target["field"], corrected)
            stats["fixed_fields"] += 1
    checked, _ = normalize_entry(checked)
    return checked, stats


async def check_new_entry(entry: dict) -> dict:
    """Best-effort pre-save check; an outage never blocks adding a word."""
    if entry_language(entry) != "nl":
        return entry
    normalized, _ = normalize_entry(entry)
    try:
        checked, _stats = await check_entry(
            normalized, semaphore=asyncio.Semaphore(_MAX_CONCURRENCY),
        )
        return checked
    except Exception:
        return normalized
