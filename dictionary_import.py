"""Добавление, нормализация и пакетный импорт словарных записей."""

import logging
import random
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import secure
import store
import verify
import learning_dictionary as dictionary
from dictionary_model import entry_language, entry_term, entry_translation, normalize_entry, normalize_key
from ui import dictionary as dict_ui
from ui.constants import delete_label

_log = logging.getLogger(__name__)
_cap = dictionary._cap
_kind_of = dictionary._kind_of
_normalize_dutch_phrase = dictionary._normalize_dutch_phrase
_normalize_dict_term = dictionary._normalize_dict_term
_active_language_code = dictionary._active_language_code
_dict_lang = dictionary._dict_lang
_dict_kind = dictionary._dict_kind
_ensure_dict = dictionary._ensure_dict
send_dict_lang = dictionary.send_dict_lang
_DICT_ADD_VERB_RE = dictionary._DICT_ADD_VERB_RE
_DICT_WORD_RE = dictionary._DICT_WORD_RE
_DICT_LEADING_RE = dictionary._DICT_LEADING_RE
_DICT_LANG_RE = dictionary._DICT_LANG_RE
_DICT_KIND_RE = dictionary._DICT_KIND_RE
_DICT_QUESTION_PAYLOAD_RE = dictionary._DICT_QUESTION_PAYLOAD_RE
_DICT_PAYLOAD_PREFIX_RE = dictionary._DICT_PAYLOAD_PREFIX_RE
_DICT_EMPTY_PAYLOAD = dictionary._DICT_EMPTY_PAYLOAD
_DICT_LEADING_ADD_VERB_RE = dictionary._DICT_LEADING_ADD_VERB_RE

def _strip_leading_add_verb(line):
    """Убирает командный глагол (add/добавь/...) ТОЛЬКО в начале строки — пользователь
    внутри уже открытого диалога добавления ('Пришли слово или фразу') иногда по
    привычке начинает со слова-команды, как в общем чате (см. try_add_dict_from_chat).
    Не трогает середину строки, чтобы не откусить часть настоящей фразы."""
    return _DICT_LEADING_ADD_VERB_RE.sub("", line, count=1).strip()

def _dict_lang_hint_explicit(text):
    """Язык, явно названный в самой команде («на английском», «dutch» и т.п.).
    None, если язык явно не назван — тогда решение принимает вызывающий код
    по активному языку обучения, признакам de/het или сам LLM."""
    t = (text or "").lower()
    if any(x in t for x in ("английск", "english", " en ")):
        return "en"
    if any(x in t for x in ("нидерланд", "голланд", "dutch", " nl ")):
        return "nl"
    return None


_DUTCH_ARTICLE_RE = re.compile(r"\b(de|het)\s+\w+", re.I)


def _dict_lang_hint(text, cid=None):
    """Порядок определения языка (без безусловного fallback на nl):
    1. Язык, явно указанный в самой команде.
    2. Признаки de/het (нидерландский артикль) в тексте — прямое доказательство
       в самих словах, сильнее предположения по активному языку обучения.
    3. Активный язык обучения пользователя.
    4. Иначе — не подсказываем язык явно, финальное решение остаётся за LLM
       (промпт _normalize_dict_entry_full сам определяет lang по слову)."""
    explicit = _dict_lang_hint_explicit(text)
    if explicit:
        return explicit
    if _DUTCH_ARTICLE_RE.search(text or ""):
        return "nl"
    if cid is not None:
        try:
            return _active_language_code(cid)
        except Exception:
            pass
    return None


def _clean_chat_dict_payload(text):
    payload = _DICT_ADD_VERB_RE.sub(" ", text or "", count=1)
    payload = _DICT_WORD_RE.sub(" ", payload)
    payload = _DICT_KIND_RE.sub(" ", payload)
    payload = _DICT_LANG_RE.sub(" ", payload)
    payload = re.sub(r"\b(?:эту|это|его|её|ее)\b", " ", payload, flags=re.I)
    payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
    payload = _DICT_PAYLOAD_PREFIX_RE.sub("", payload).strip(" \t\n\r:;,.-–—")
    return payload


def _extract_chat_dict_add(text, cid=None):
    """Команда из свободного чата: «добавь в словарь слово ...» -> полезная часть."""
    text = text or ""
    if _DICT_LEADING_RE.search(text):
        lang = _dict_lang_hint(f" {text} ", cid)
        payload = _clean_chat_dict_payload(_DICT_LEADING_RE.sub(" ", text, count=1))
        if payload.casefold() in _DICT_EMPTY_PAYLOAD:
            return "", lang
        return payload, lang
    has_add_verb = bool(_DICT_ADD_VERB_RE.search(text))
    has_dict_word = bool(_DICT_WORD_RE.search(text))
    has_kind_word = bool(_DICT_KIND_RE.search(text))
    if not has_add_verb:
        return None, None
    lang = _dict_lang_hint(f" {text} ", cid)
    payload = _clean_chat_dict_payload(text)
    has_foreign_payload = bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", payload)) and not _CYRILLIC_RE.search(payload)
    if not (has_dict_word or has_kind_word or has_foreign_payload):
        return None, None
    if _DICT_QUESTION_PAYLOAD_RE.search(payload):
        return None, None
    if payload.casefold() in _DICT_EMPTY_PAYLOAD:
        return "", lang
    return payload, lang

async def try_add_dict_from_chat(bot, cid, text):
    """Перехватывает явную просьбу добавить слово/фразу в словарь из обычного чата.
    Явная команда («добавь в словарь ...») — чёткое намерение добавить именно эту
    фразу целиком, даже если она длинная или заканчивается на «?»/«!» — поэтому
    здесь НЕ проверяем payload на «похоже на связный текст» (в отличие от
    add_words_batch, куда текст мог попасть без явной команды на конкретную фразу).
    _normalize_dict_entry_full сам исправит опечатки и приведёт фразу к
    естественной форме, а единый confirm-экран покажет итог на подтверждение."""
    payload, lang = _extract_chat_dict_add(text, cid)
    if payload is None:
        return False
    if not payload:
        await bot.send_message(
            chat_id=cid,
            text="Пришли само слово или фразу: например «добавь в словарь de kater».",
        )
        return True
    await add_dict_entry_from_chat(bot, cid, payload, lang, source_text=text)
    return True


def _lang_title(lang):
    return "нидерландский" if lang == "nl" else "английский"


def _lang_loc_title(lang):
    return "нидерландском" if lang == "nl" else "английском"


def _dict_example(entry):
    """Один короткий пример для карточки; из старых записей берём самый компактный."""
    candidates = []
    for example in (entry.get("examples") or []):
        if not isinstance(example, dict):
            continue
        text = re.sub(r"\s+", " ", str(example.get("text") or "")).strip()
        translation = re.sub(r"\s+", " ", str(example.get("translation") or "")).strip()
        if (text and translation and len(text) <= 140 and len(translation) <= 140
                and len(text.split()) <= 16 and len(translation.split()) <= 16):
            candidates.append((text, translation))
    return min(candidates, key=lambda pair: len(pair[0]) + len(pair[1])) if candidates else None


def _dict_entry_message(entry, status="added"):
    """Единая карточка слова или фразы: статус, перевод, разбор и один пример."""
    from ui.builder import MessageBuilder

    b = MessageBuilder()
    term = entry.get("term") or ""
    if entry.get("article") and not term.lower().startswith(entry["article"].lower() + " "):
        term = f"{entry['article']} {term}"
    term = _cap(term)
    translation = _entry_translation(entry)

    if status == "duplicate":
        title = f"Уже есть в {_lang_loc_title(entry.get('lang'))} словаре"
        emoji = "📚"
    else:
        titles = {"updated": "Обновлено", "found": "Найдено"}
        title = titles.get(status, "Добавлено")
        emoji = "✅" if status in ("added", "updated") else "📚"
    b.text_line(f"{emoji} ")
    b.bold(title)
    b.newline()
    b.spacer()
    b.bold(term)
    if translation:
        b.text_line(f" → {translation}")
    b.newline()
    if entry.get("breakdown"):
        b.spacer()
        b.labeled_line("Разбор", entry["breakdown"])
    example = _dict_example(entry)
    if example:
        example_text, example_ru = example
        example_text = example_text.rstrip(".")
        if example_ru[-1] not in ".!?…":
            example_ru += "."
        b.spacer()
        b.text_line("💡 ")
        b.bold("Полезно:")
        b.text_line(f" {example_text} → {example_ru}")
        b.newline()
    return b.build_stripped()


def _dict_loose_key(lang, entry_type, word):
    base = re.sub(r"\s+", " ", (word or "").strip()).casefold()
    if lang == "nl":
        base = re.sub(r"^(de|het|een)\s+", "", base)
    if lang == "en":
        base = re.sub(r"^(to|the|a|an)\s+", "", base)
    return lang, entry_type or "word", base


def _dict_loose_text(lang, word):
    return _dict_loose_key(lang, "word", word)[2]


_DIFFICULTY_LEVELS = ("A1", "A2", "B1", "B2", "C1")


def _extract_srs_fields(d):
    """Достаёт новые поля тренажёра (часть речи, конструкция, SRS-состояние по
    умолчанию) из ответа AI. Общий парсер для добавления одной записи
    (_normalize_dict_entry_full) и батч-миграции старых записей
    (migrate_dict_entries_for_srs) — единый источник правды на формат этих
    полей, чтобы не разойтись между двумя точками входа."""
    import srs
    if not isinstance(d, dict):
        d = {}
    forms = [str(f).strip() for f in (d.get("forms") or []) if str(f).strip()][:3]
    alt_translations = [str(t).strip() for t in (d.get("alt_translations") or []) if str(t).strip()][:2]
    difficulty = str(d.get("difficulty") or "").strip().upper()
    if difficulty not in _DIFFICULTY_LEVELS:
        difficulty = ""
    return {
        "pos": str(d.get("pos") or "").strip()[:40],
        "plural": str(d.get("plural") or "").strip()[:60],
        "forms": forms,
        "topic": str(d.get("topic") or "").strip()[:40],
        "difficulty": difficulty,
        "construction": str(d.get("construction") or "").strip()[:120],
        "situation_type": str(d.get("situation_type") or "").strip()[:40],
        "alt_translations": alt_translations,
        **srs.default_srs_state(),
    }


async def _normalize_dict_entry_full(payload, lang_hint=None, source_text="", avoid_translations=None):
    """Единая точка добавления: нормализация, перевод, разбор и один пример.
    Один AI-вызов на запись, кэшируется в ai.py по input_hash (module="learning_dict_add",
    TTL 30 дней) — повторное добавление того же слова не тратит лимит повторно.
    lang_hint — nl/en/None. None означает, что язык не определён ни явной командой,
    ни активным языком обучения, ни признаками de/het — LLM определяет его сам,
    без принудительного fallback на nl.
    avoid_translations — уже показанные варианты из старых совместимых карточек;
    меняет текст промпта, чтобы не попасть в тот же кэш и получить другой вариант."""
    russian_source = bool(_CYRILLIC_RE.search(payload or ""))
    if russian_source and lang_hint in ("nl", "en"):
        language_line = (
            f"Исходная запись дана на русском. Целевой язык: "
            f"{_lang_title(lang_hint)} ({lang_hint}). Переведи значение на целевой язык."
        )
        if lang_hint == "nl":
            russian_source_rule = (
                '- Русский ввод — это значение, а не иностранное написание. Дай настоящий '
                'нидерландский перевод: "Уверенность" → "het zelfvertrouwen" (в себе) '
                'или "de zekerheid" (определённость), НИКОГДА не транслитерацию вроде '
                '"de Uverenheid".\n'
            )
        else:
            russian_source_rule = (
                '- Русский ввод — это значение, а не иностранное написание. Дай настоящий '
                'английский перевод: "Уверенность" → "confidence", НИКОГДА не транслитерацию.\n'
            )
    elif lang_hint in ("nl", "en"):
        language_line = f"Подсказка языка: {_lang_title(lang_hint)} ({lang_hint})."
        russian_source_rule = ""
    else:
        language_line = "Язык не подсказан — определи его сам по слову/фразе."
        russian_source_rule = ""
    avoid_line = ""
    if avoid_translations:
        avoid_line = (
            "\nПользователь уже видел эти варианты перевода и просит другой — "
            "НЕ повторяй их, предложи следующее по точности значение: "
            + "; ".join(avoid_translations) + "."
        )
    prompt = f"""
Ты лексикограф для учебного словаря Telegram-бота. Всё учится как фраза: короткая
запись (одно слово) и длинная (выражение/предложение) хранятся одинаково.

Пользователь хочет добавить: {secure.wrap_untrusted(payload, 'запись')}
Полное сообщение пользователя: {secure.wrap_untrusted(source_text or payload, 'сообщение')}
{language_line}{avoid_line}

Определи и нормализуй РОВНО ОДНУ учебную запись.

Правила:
- lang: nl или en.
{russian_source_rule}- Если исходная запись дана по-русски и целевой язык указан, это корректный
  запрос на перевод для словаря: не отклоняй его и не копируй звучание русскими словами латиницей.
- term: правильная учебная форма (без перевода).
  - Нидерландские существительные — с артиклем de/het.
  - Глаголы — в инфинитиве; английские глаголы словарной формой — с to.
  - Прилагательные — в базовой форме.
  - Устойчивые выражения — целиком в базовой форме.
  - Фразы/предложения — естественно и грамматически правильно, без изменения смысла.
  - Для нидерландских фраз проверяй согласование подлежащего и сказуемого:
    "Ik bereiken mijn doel" нельзя; правильно "Ik bereik mijn doel".
  - Если во фразе явная опечатка (например лишняя/пропущенная буква, не меняющая
    смысл: "wat doc je daar" → "wat doe je daar"), исправь её молча — term должен
    быть уже исправленной, естественной формой, а не сырым вводом с ошибкой.
- article: артикль "de"/"het" ТОЛЬКО для нидерландских существительных. У глаголов, прилагательных,
  фраз и предложений артикля нет и не может быть — всегда пусто.
- translation: 1-2 самых точных и естественных значения на русском, через "; ".
  Не кальируй иностранные предлоги: "Waar wacht je op?" → "Что ты ждёшь?",
  а не "На что ты ждёшь?".
- breakdown: короткий разбор — часть речи, род/артикль, особенность формы (одна строка,
  без пояснений сверх необходимого).
- examples: ровно один короткий пример на изучаемом языке с переводом на русский.
  Пример должен быть естественным, частотным и пригодным для обычной речи. Не используй
  редкие, книжные, сложные или искусственно составленные конструкции.
- pos: часть речи одним словом ("существительное", "глагол", "прилагательное", "фраза" и т.п.).
- plural: множественное число, если применимо к существительному, иначе пусто.
- forms: до 3 других форм слова (склонения/спряжения), если это уместно, иначе пустой список.
- topic: одна короткая тема ("быт", "работа", "путешествия" и т.п.).
- difficulty: оценка уровня CEFR одной меткой ("A1".."C1") по сложности слова/фразы.
- construction: если это устойчивая конструкция/идиома — сама конструкция целиком
  (например "zin hebben om te + infinitief"), иначе пусто. Для одиночных слов — пусто.
- situation_type: если term — фраза для конкретной жизненной ситуации, короткий тип ситуации
  ("отказ", "согласие", "извинение" и т.п.), иначе пусто.
- alt_translations: до 2 дополнительных естественных вариантов перевода, отличных от translation,
  если они реально уместны, иначе пустой список.
- Не выдумывай значение. Если слово многозначное, редкое, написано с ошибкой, не хватает
  артикля для нидерландского существительного или есть риск неверного перевода, поставь
  needs_confirmation=true и дай наиболее вероятную трактовку.

Верни JSON:
{{
  "ok": true,
  "lang": "nl|en",
  "term": "правильная учебная форма",
  "article": "de|het|",
  "translation": "перевод",
  "breakdown": "короткий разбор",
  "examples": [{{"text": "...", "translation": "..."}}],
  "pos": "часть речи",
  "plural": "",
  "forms": [],
  "topic": "",
  "difficulty": "A1|A2|B1|B2|C1",
  "construction": "",
  "situation_type": "",
  "alt_translations": [],
  "needs_confirmation": false,
  "reason": "короткая причина уточнения или пусто"
}}
Если ввод не является ни нидерландской/английской записью, ни русским значением для
перевода на явно указанный целевой язык, верни {{"ok": false, "reason": "коротко почему"}}.
"""
    d = await ai.allm_json(prompt, 900, module="learning_dict_add")
    if not isinstance(d, dict) or not d.get("ok"):
        return None
    lang = "en" if d.get("lang") == "en" else "nl"
    term = re.sub(r"\s+", " ", str(d.get("term") or "").strip())
    translation = re.sub(r"\s+", " ", str(d.get("translation") or "").strip())
    term, _grammar_note = _normalize_dict_term(lang, _kind_of(term), term)
    if not term or not translation or _is_bad_dict_item(term, translation):
        return None
    if russian_source and not _CYRILLIC_RE.search(translation):
        return None
    examples = []
    for ex in (d.get("examples") or [])[:1]:
        if not isinstance(ex, dict):
            continue
        text = re.sub(r"\s+", " ", str(ex.get("text") or "").strip())
        ex_ru = re.sub(r"\s+", " ", str(ex.get("translation") or "").strip())
        if (text and ex_ru and len(text) <= 140 and len(ex_ru) <= 140
                and len(text.split()) <= 16 and len(ex_ru.split()) <= 16):
            examples.append({"text": text, "translation": ex_ru})
    if not examples:
        return None
    breakdown = re.sub(r"\s+", " ", str(d.get("breakdown") or "").strip())[:180]
    if not breakdown:
        return None
    article = str(d.get("article") or "").strip() if lang == "nl" else ""
    if article and "глагол" in breakdown.lower():
        # У глаголов нет артикля de/het — модель иногда всё равно его возвращает.
        article = ""
    return {
        "lang": lang,
        "term": term[:120],
        "article": article,
        "translation": translation[:180],
        "breakdown": breakdown,
        "examples": examples,
        "source_text": source_text or payload,
        "added_at": datetime.now(config.TZ).isoformat(),
        "status": "new",
        "last_shown_at": None,
        "needs_confirmation": bool(d.get("needs_confirmation")),
        "reason": str(d.get("reason") or "").strip(),
        **_extract_srs_fields(d),
    }


_SRS_FIELD_KEYS = (
    "pos", "plural", "forms", "topic", "difficulty", "construction",
    "situation_type", "alt_translations",
    "srs_level", "srs_easiness", "srs_interval_days", "srs_due_at",
    "srs_history", "srs_last_exercise_type",
)


def _save_normalized_dict_entry(cid, entry):
    """Сохраняет запись единого словаря (структура из спеки: term/article/translation/
    breakdown/examples/status + поля тренажёра pos/construction/SRS-состояние,
    см. _extract_srs_fields). Возвращает (status, saved_entry) где status —
    added/updated/duplicate."""
    entry = dict(entry)
    srs_fields = {k: entry[k] for k in _SRS_FIELD_KEYS if k in entry}
    words = store.get_list(config.DICT_KEY, cid)
    loose_text = _dict_loose_text(entry["lang"], entry["term"])
    for idx, item in enumerate(words):
        existing_term = _entry_term(item)
        if _dict_lang(item) != entry["lang"]:
            continue
        if existing_term.casefold() == entry["term"].casefold():
            duplicate = dict(item)
            changed = False
            for field in ("breakdown", "examples"):
                missing = not duplicate.get(field)
                if field == "examples":
                    missing = _dict_example(duplicate) is None
                if missing and entry.get(field):
                    duplicate[field] = entry[field]
                    changed = True
            for key, value in srs_fields.items():
                if key not in duplicate:
                    duplicate[key] = value
                    changed = True
            if changed:
                words[idx] = duplicate
                store.set_list(config.DICT_KEY, cid, words)
            return "duplicate", duplicate
        if _dict_loose_text(entry["lang"], existing_term) == loose_text:
            updated = dict(item)
            updated.update({
                "lang": entry["lang"],
                "term": entry["term"],
                "article": entry.get("article", ""),
                "translation": entry["translation"],
                "breakdown": entry.get("breakdown", ""),
                "examples": entry.get("examples", []),
                "source_text": entry.get("source_text", ""),
                "added_at": item.get("added_at") or entry["added_at"],
                "status": item.get("status") or "new",
                "last_shown_at": item.get("last_shown_at"),
                "updated_at": datetime.now(config.TZ).isoformat(),
            })
            # SRS-прогресс существующей записи не затирается повторным добавлением —
            # только доопределяем поля, которых у записи ещё нет вовсе.
            for k, v in srs_fields.items():
                updated.setdefault(k, v)
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return "updated", updated
    saved = {
        "lang": entry["lang"],
        "term": entry["term"],
        "article": entry.get("article", ""),
        "translation": entry["translation"],
        "breakdown": entry.get("breakdown", ""),
        "examples": entry.get("examples", []),
        "source_text": entry.get("source_text", ""),
        "added_at": entry["added_at"],
        "status": entry.get("status") or "new",
        "last_shown_at": entry.get("last_shown_at"),
        **srs_fields,
    }
    store.add_to_list(config.DICT_KEY, cid, saved)
    return "added", saved


def _entry_term(item):
    """Термин записи с фолбэком на legacy-поля (word/base_form) для старых записей."""
    if not isinstance(item, dict):
        return str(item)
    return item.get("term") or item.get("word") or item.get("base_form") or ""


def _entry_translation(item):
    if not isinstance(item, dict):
        return ""
    return item.get("translation") or item.get("ru") or ""


def _entry_srs_state(item):
    """SRS-состояние записи с фолбэком на дефолт для записей, ещё не прошедших
    миграцию (см. migrate_dict_entries_for_srs)."""
    import srs
    if not isinstance(item, dict) or "srs_due_at" not in item:
        return srs.default_srs_state()
    return {k: item.get(k) for k in (
        "srs_level", "srs_easiness", "srs_interval_days", "srs_due_at",
        "srs_history", "srs_last_exercise_type",
    )}


def _entry_needs_srs_migration(item):
    """True, если запись ещё не прошла батч-миграцию на новые поля тренажёра
    (см. migrate_dict_entries_for_srs) — нет SRS-состояния вообще."""
    return isinstance(item, dict) and "srs_due_at" not in item


def _entry_needs_ai_refresh(item):
    """Старую запись без разбора или короткого примера донасытим при обращении."""
    if not isinstance(item, dict):
        return False
    return not item.get("breakdown") or _dict_example(item) is None


async def _refresh_dict_entry(cid, item):
    """Ленивая миграция одной старой записи в новый формат при первом обращении.
    Обновляет запись на месте по индексу — не через _save_normalized_dict_entry,
    т.к. та считает совпадение термина дубликатом и не заменит поля."""
    term = _entry_term(item)
    lang = _dict_lang(item)
    try:
        entry = await _normalize_dict_entry_full(term, lang, source_text=term)
    except Exception:
        return item
    if not entry or entry.get("needs_confirmation"):
        return item
    words = store.get_list(config.DICT_KEY, cid)
    for idx, w in enumerate(words):
        if w is item or (_dict_lang(w) == lang and _entry_term(w) == term):
            updated = dict(w)
            updated.update({
                "lang": entry["lang"],
                "term": entry["term"],
                "article": entry.get("article", ""),
                "translation": entry["translation"],
                "breakdown": entry.get("breakdown", ""),
                "examples": entry.get("examples", []),
                "status": w.get("status") or "new",
                "last_shown_at": w.get("last_shown_at"),
                "updated_at": datetime.now(config.TZ).isoformat(),
            })
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return updated
    return item


def _dict_saved_kb(lang, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdel_{lang}_{term_key}")],
        [InlineKeyboardButton("📖 Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _dict_duplicate_kb(lang, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdel_{lang}_{term_key}")],
        [InlineKeyboardButton("📖 Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _overwrite_dict_entry_fields(cid, lang, term, fields):
    """Обновляет уже сохранённую запись на месте по точному совпадению term
    (используется "Другим переводом" после мгновенного сохранения)."""
    words = store.get_list(config.DICT_KEY, cid)
    for idx, item in enumerate(words):
        if _dict_lang(item) == lang and _entry_term(item).casefold() == term.casefold():
            updated = dict(item)
            updated.update(fields)
            updated["updated_at"] = datetime.now(config.TZ).isoformat()
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return updated
    return None


async def add_dict_entry_from_chat(bot, cid, payload, lang=None, source_text=""):
    """Сохраняет запись в словарь сразу, без ожидания кнопки "Добавить" - если разбор
    ошибся, запись можно удалить одной кнопкой, а не потерять, забыв подтвердить."""
    try:
        entry = await _normalize_dict_entry_full(payload, lang, source_text=source_text)
    except Exception:
        await bot.send_message(chat_id=cid, text="⚠️ Не получилось разобрать слово. Попробуй ещё раз.")
        return
    if not entry:
        await bot.send_message(
            chat_id=cid,
            text="Не уверена в форме или переводе. Пришли так: de kater → похмелье.",
        )
        return
    status, saved = _save_normalized_dict_entry(cid, entry)
    msg = _dict_entry_message(saved, status=status)
    term_key = _dict_item_key(saved["lang"], "", _entry_term(saved))[2]
    if status == "duplicate":
        kb = _dict_duplicate_kb(saved["lang"], term_key)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    kb = _dict_saved_kb(saved["lang"], term_key)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def retry_pending_dict_add(bot, cid):
    """Совместимость со старыми сообщениями, где ещё была смена перевода."""
    entry = store.dict_pending_add.get(str(cid))
    if not entry:
        await bot.send_message(chat_id=cid, text="Уточнение устарело. Пришли слово ещё раз.")
        return
    seen = entry.get("_seen_translations") or [entry.get("translation", "")]
    try:
        new_entry = await _normalize_dict_entry_full(
            entry.get("_payload", entry.get("term", "")), entry.get("lang", "nl"),
            source_text=entry.get("_source_text", ""), avoid_translations=seen,
        )
    except Exception:
        await bot.send_message(chat_id=cid, text="⚠️ Не получилось получить другой вариант. Попробуй ещё раз.")
        return
    if not new_entry or new_entry["translation"] in seen:
        term_key = _dict_item_key(entry["lang"], "", _entry_term(entry))[2]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdelok_{entry['lang']}_{term_key}")]])
        await bot.send_message(chat_id=cid, text="Больше вариантов перевода не нашлось.", reply_markup=kb)
        return
    updated = _overwrite_dict_entry_fields(cid, entry["lang"], entry["term"], {
        "translation": new_entry["translation"],
        "breakdown": new_entry.get("breakdown", ""),
        "examples": new_entry.get("examples", []),
    }) or new_entry
    updated["_payload"] = entry.get("_payload", "")
    updated["_source_text"] = entry.get("_source_text", "")
    updated["_seen_translations"] = seen + [new_entry["translation"]]
    store.dict_pending_add[str(cid)] = updated
    msg = _dict_entry_message(updated, status="updated")
    term_key = _dict_item_key(updated["lang"], "", _entry_term(updated))[2]
    kb = _dict_saved_kb(updated["lang"], term_key)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def cancel_pending_dict_add(bot, cid):
    store.dict_pending_add.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="Отменено.")


async def confirm_pending_dict_add(bot, cid):
    entry = store.dict_pending_add.pop(str(cid), None)
    if not entry:
        await bot.send_message(chat_id=cid, text="Уточнение устарело. Пришли слово ещё раз.")
        return
    status, saved = _save_normalized_dict_entry(cid, entry)
    msg = _dict_entry_message(saved, status=status)
    term_key = _dict_item_key(saved["lang"], "", _entry_term(saved))[2]
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=_dict_saved_kb(saved["lang"], term_key),
    )


def _dict_item_key(lang, kind, word):
    normalized = re.sub(r"\s+", " ", (word or "").strip()).casefold()
    return lang, kind, normalized

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_PLACEHOLDER_RU_RE = re.compile(r"^\??\.?\.?\.?\??$")

def _is_bad_dict_item(word, ru):
    """True, если перевод отсутствует/заглушка, или word перепутан с ru (кириллица вместо иностранного слова)."""
    word = (word or "").strip()
    ru = (ru or "").strip()
    if not ru or _PLACEHOLDER_RU_RE.match(ru):
        return True
    if ru.casefold() == word.casefold():
        return True
    if _CYRILLIC_RE.search(word):
        return True
    return False

_BATCH_CARD_LIMIT = 5  # больше строк — не спамим карточками, шлём короткую сводку
_DICT_TOPIC_LIMIT = 5  # сколько кандидатов максимум предлагать из свободного текста

_SENTENCE_LINE_RE = re.compile(r"[.!?…]\s*$")


def _looks_like_free_text(lines):
    """True, если ввод похож на связный текст (предложения), а не на список
    отдельных слов/фраз — тогда нельзя добавлять построчно без разбора темы."""
    if len(lines) == 1:
        words = lines[0].split()
        return len(words) > 6 or bool(_SENTENCE_LINE_RE.search(lines[0]))
    sentence_like = sum(
        1 for ln in lines
        if len(ln.split()) > 6 or _SENTENCE_LINE_RE.search(ln)
    )
    return sentence_like >= max(2, len(lines) // 2)


async def _extract_dict_topics(text, lang="nl"):
    """LLM выбирает до _DICT_TOPIC_LIMIT ключевых слов/фраз из свободного текста
    вместо добавления всего подряд построчно — см. правило превью+подтверждение."""
    language_hint = _lang_title(lang)
    prompt = f"""
Пользователь прислал текст в Telegram-бот с изучением языков. Подсказка языка
изучения: {language_hint} ({lang}).
Текст: {secure.wrap_untrusted(text, 'текст')}

Найди основную тему текста и выбери не больше {_DICT_TOPIC_LIMIT} самых полезных
для учебного словаря слов или коротких фраз на языке {language_hint}, которые
встречаются в тексте по смыслу (переведи на {language_hint}, если текст на русском).
Не включай случайные малополезные слова — только те, что реально стоит выучить.
Если в тексте нет ничего подходящего для словаря, верни пустой список.

Верни JSON:
{{"items": [{{"term": "...", "translation": "..."}}]}}
"""
    try:
        d = await ai.allm_json(prompt, 500, module="learning")
    except Exception:
        d = {}
    items = (d or {}).get("items") or []
    out = []
    for item in items[:_DICT_TOPIC_LIMIT]:
        if not isinstance(item, dict):
            continue
        term = re.sub(r"\s+", " ", str(item.get("term") or "").strip())
        translation = re.sub(r"\s+", " ", str(item.get("translation") or "").strip())
        if term and translation:
            out.append({"term": term, "translation": translation})
    return out


def _dict_batch_preview_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Добавить всё", callback_data="a_dictbatch_add")],
        [InlineKeyboardButton("❌ Не добавлять", callback_data="a_dictbatch_cancel")],
    ])


async def offer_dict_topics_from_text(bot, cid, text, lang="nl"):
    """Свободный текст (несколько предложений) — не добавляем слепо: LLM находит
    тему, показываем превью до 5 кандидатов и добавляем только по подтверждению."""
    topics = await _extract_dict_topics(text, lang)
    if not topics:
        await bot.send_message(
            chat_id=cid,
            text="Не нашла в тексте ничего подходящего для словаря.",
        )
        return
    store.dict_pending_batch[str(cid)] = {"lang": lang, "items": topics, "source_text": text}
    lines = "\n".join(f"• {it['term']} — {it['translation']}" for it in topics)
    await bot.send_message(
        chat_id=cid,
        text=f"📚 Добавить в словарь?\n\n{lines}",
        reply_markup=_dict_batch_preview_kb(),
    )


async def confirm_dict_batch(bot, cid):
    pending = store.dict_pending_batch.pop(str(cid), None)
    if not pending:
        await bot.send_message(chat_id=cid, text="Подборка устарела. Пришли текст ещё раз.")
        return
    lang = pending.get("lang", "nl")
    text = "\n".join(it["term"] for it in pending.get("items") or [])
    await add_words_batch(bot, cid, text, lang, detailed_confirmation=True)


async def cancel_dict_batch(bot, cid):
    store.dict_pending_batch.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="Хорошо, не добавляю.")


async def _offer_manual_batch_preview(bot, cid, lines, lang):
    """Явный список слов/фраз пользователя (2+ строки, каждая — отдельная запись):
    показываем превью как есть и просим общее подтверждение перед AI-разбором и
    сохранением — единый стиль добавления, без исключений для «очевидных» слов."""
    store.dict_pending_batch[str(cid)] = {"lang": lang, "items": [{"term": ln} for ln in lines], "source_text": "\n".join(lines)}
    preview = "\n".join(f"• {ln}" for ln in lines)
    await bot.send_message(
        chat_id=cid,
        text=f"📚 Добавить в словарь?\n\n{preview}",
        reply_markup=_dict_batch_preview_kb(),
    )


async def add_words_batch(bot, cid, text, lang="nl", detailed_confirmation=False):
    """Добавляет одну или несколько записей: каждая строка проходит полный AI-разбор
    (нормализация + перевод + разбор + пример), см. _normalize_dict_entry_full.
    При <= 5 строках — карточка на каждую запись; иначе короткая сводка.

    Единый стиль подтверждения: одиночное слово — карточка «Ты имеешь в виду X — Y?»
    (см. add_dict_entry_from_chat), несколько строк — превью списка с общим
    подтверждением (см. _offer_manual_batch_preview). detailed_confirmation=True —
    это уже подтверждённый список, идём сразу к AI-разбору и сохранению."""
    lines = [_strip_leading_add_verb(x) for x in re.split(r"[\n;]+", text or "")]
    lines = [x for x in lines if x]
    if not lines:
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз.")
        return
    if not detailed_confirmation and _looks_like_free_text(lines):
        await offer_dict_topics_from_text(bot, cid, text, lang)
        return
    if not detailed_confirmation and len(lines) == 1:
        await add_dict_entry_from_chat(bot, cid, lines[0], lang, source_text=lines[0])
        return
    if not detailed_confirmation and len(lines) > 1:
        await _offer_manual_batch_preview(bot, cid, lines, lang)
        return

    added_entries = []
    duplicate_entries = []
    unrecognized_lines = []
    for line in lines:
        try:
            entry = await _normalize_dict_entry_full(line, lang, source_text=line)
        except Exception:
            entry = None
        if not entry:
            unrecognized_lines.append(line[:60])
            continue
        status, saved = _save_normalized_dict_entry(cid, entry)
        if status == "duplicate":
            duplicate_entries.append(saved)
        else:
            added_entries.append(saved)

    if not added_entries:
        if duplicate_entries:
            await bot.send_message(chat_id=cid, text="Эти слова или фразы уже есть в словаре."); return
        if unrecognized_lines:
            await bot.send_message(chat_id=cid,
                text="Не уверена в форме или переводе: " + ", ".join(unrecognized_lines[:10]) +
                     ". Пришли так: de kater → похмелье.")
            return
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз."); return

    if len(added_entries) <= _BATCH_CARD_LIMIT:
        for saved in added_entries:
            msg = _dict_entry_message(saved, status="added")
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        terms = ", ".join(e.get("term", "") for e in added_entries[:10])
        more = f" и ещё {len(added_entries) - 10}" if len(added_entries) > 10 else ""
        await bot.send_message(chat_id=cid,
            text=f"📚 Добавлено {len(added_entries)}: {terms}{more}")
    if unrecognized_lines:
        await bot.send_message(chat_id=cid,
            text="⚠️ Не удалось распознать: " + ", ".join(unrecognized_lines[:10]))
    await send_dict_lang(bot, cid, lang)


async def add_smart_batch(bot, cid, text, lang="nl"):
    """Алиас для единого пути добавления (сохранён для совместимости вызовов)."""
    await add_words_batch(bot, cid, text, lang, detailed_confirmation=False)
