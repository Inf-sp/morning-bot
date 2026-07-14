"""Учебный словарь: схема, репозиторий, нормализация, миграции и экраны."""

import logging
import random
import re
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

import ai
import config
import secure
import srs
import store
import verify
from dictionary_model import (
    PHRASE_CORRECTIONS,
    entry_language,
    entry_term,
    entry_translation,
    language_code as _code,
    normalize_entry,
    normalize_key,
)
from dictionary_repository import DictionaryRepository
from ui import dictionary as dict_ui
from ui import learning as learning_ui

_HERE = Path(__file__).parent
_log = logging.getLogger(__name__)


def _active_language_code(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings
    return _code(settings.study_lang(cid))


# ================= СЛОВАРЬ (раздельно NL / EN) =================
def _cap(s):
    """Первая буква термина - заглавная (с учётом орфографии), остальное не трогаем."""
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s

def migrate_dict_caps():
    """Разовая миграция: приводит уже сохранённые слова словаря к виду с заглавной буквы."""
    data = store._load(config.DICT_KEY)
    changed = False
    for cid, words in (data or {}).items():
        if not isinstance(words, list):
            continue
        for w in words:
            if isinstance(w, dict) and w.get("word"):
                capped = _cap(w["word"])
                if capped != w["word"]:
                    w["word"] = capped
                    changed = True
    if changed:
        store._save(config.DICT_KEY, data)
    return changed

def _kind_of(term):
    """Слово или фраза: считаем по термину без ведущего артикля (de/het/een/the/a/an)."""
    t = re.sub(r"^(de|het|een|the|a|an)\s+", "", (term or "").strip().lower())
    return "word" if len(t.split()) <= 1 else "phrase"

_NL_IK_INFINITIVE_FIXES = {
    "begrijpen": "begrijp",
    "beginnen": "begin",
    "behalen": "behaal",
    "beïnvloeden": "beïnvloed",
    "bekijken": "bekijk",
    "benadrukken": "benadruk",
    "beoordelen": "beoordeel",
    "beperken": "beperk",
    "bereiken": "bereik",
    "beschouwen": "beschouw",
    "beschrijven": "beschrijf",
    "beslissen": "beslis",
    "bespreken": "bespreek",
    "betalen": "betaal",
    "betekenen": "beteken",
    "bevorderen": "bevorder",
    "bewijzen": "bewijs",
    "blijven": "blijf",
    "denken": "denk",
    "doen": "doe",
    "eisen": "eis",
    "gaan": "ga",
    "gebruiken": "gebruik",
    "geven": "geef",
    "halen": "haal",
    "handhaven": "handhaaf",
    "hebben": "heb",
    "helpen": "help",
    "herhalen": "herhaal",
    "herkennen": "herken",
    "hoeven": "hoef",
    "houden": "houd",
    "kiezen": "kies",
    "kijken": "kijk",
    "kloppen": "klop",
    "komen": "kom",
    "kopen": "koop",
    "kunnen": "kan",
    "leren": "leer",
    "lezen": "lees",
    "liggen": "lig",
    "lopen": "loop",
    "luisteren": "luister",
    "maken": "maak",
    "mogen": "mag",
    "moeten": "moet",
    "nemen": "neem",
    "onderbouwen": "onderbouw",
    "onderzoeken": "onderzoek",
    "onderscheiden": "onderscheid",
    "ontmoeten": "ontmoet",
    "ontwikkelen": "ontwikkel",
    "overtuigen": "overtuig",
    "overwegen": "overweeg",
    "praten": "praat",
    "proberen": "probeer",
    "reageren": "reageer",
    "rechtvaardigen": "rechtvaardig",
    "reizen": "reis",
    "schatten": "schat",
    "slapen": "slaap",
    "spreken": "spreek",
    "staan": "sta",
    "streven": "streef",
    "veranderen": "verander",
    "verbeteren": "verbeter",
    "vergeten": "vergeet",
    "vermijden": "vermijd",
    "veronderstellen": "veronderstel",
    "voorkomen": "voorkom",
    "vragen": "vraag",
    "wachten": "wacht",
    "werken": "werk",
    "weten": "weet",
    "willen": "wil",
    "zeggen": "zeg",
    "zien": "zie",
    "zijn": "ben",
    "zitten": "zit",
    "zoeken": "zoek",
    "zullen": "zal",
}

_NL_IK_INFINITIVE_RE = re.compile(r"^(\s*ik\s+)([A-Za-zÀ-ÖØ-öø-ÿ]+)(\b.*)$", re.I)

def _normalize_dutch_phrase(term):
    """Correct the high-confidence learner error "Ik + infinitive"."""
    phrase = re.sub(r"\s+", " ", (term or "").strip())
    m = _NL_IK_INFINITIVE_RE.match(phrase)
    if not m:
        return phrase, ""
    fixed_verb = _NL_IK_INFINITIVE_FIXES.get(m.group(2).casefold())
    if not fixed_verb:
        return phrase, ""
    fixed = f"{m.group(1)}{fixed_verb}{m.group(3)}"
    return _cap(fixed.strip()), "После ik нужен личный глагол, а не инфинитив."

def _normalize_dict_term(lang, kind, term):
    term = re.sub(r"\s+", " ", (term or "").strip())
    if lang == "nl" and kind == "phrase":
        return _normalize_dutch_phrase(term)
    return term, ""

_DICT_ADD_VERB_RE = re.compile(
    r"\b(добавь|добавить|занеси|запиши|сохрани|сохранить|запомни|запомнить|внеси|закинь|"
    r"add|save|remember)\b", re.I)
_DICT_WORD_RE = re.compile(r"\b(?:в\s+)?(?:мой\s+)?(?:словар[ьяьею]*|обучени[еяю]|тренировк[ауиах]*)\b", re.I)
_DICT_LEADING_RE = re.compile(r"^\s*в\s+(?:мой\s+)?словар[ьяьею]*\b", re.I)
_DICT_LANG_RE = re.compile(
    r"\b(?:на\s+)?("
    r"нидерландск(?:ом|ое|ого|ий|ую|ая|ие|их)|голландск(?:ом|ое|ого|ий|ую|ая|ие|их)|dutch|nl|"
    r"английск(?:ом|ое|ого|ий|ую|ая|ие|их)|english|en"
    r")\b",
    re.I,
)
_DICT_KIND_RE = re.compile(r"\b(слово|слова|фразу|фраза|выражение|выражения|термин)\b", re.I)
_DICT_QUESTION_PAYLOAD_RE = re.compile(r"^(?:како(?:е|й|ую)|что|что-то)\b", re.I)
_DICT_PAYLOAD_PREFIX_RE = re.compile(
    r"^(?:(?:ну|пожалуйста|плиз|нужно|надо|можешь|можно|мне|нам|хочу|давай|нов(?:ое|ый|ую|ая|ые)|эту|это|его|её|ее)\s+)+",
    re.I,
)
_DICT_EMPTY_PAYLOAD = {"", "в", "на", "для", "туда", "это", "эту", "его", "её", "ее"}

_DICT_LEADING_ADD_VERB_RE = re.compile(
    r"^\s*(добавь|добавить|занеси|запиши|сохрани|сохранить|запомни|запомнить|внеси|закинь|"
    r"add|save|remember)\s+", re.I)


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


def _add_term_run(b, term):
    """Термин жирным курсивом (правило проекта: термин выделен, перевод — через жирную стрелку)."""
    from ui.builder import u16_len
    offset = u16_len(b.text)
    b.add(term)
    length = u16_len(term)
    b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
    b._entities.append(MessageEntity(MessageEntity.ITALIC, offset, length))


def _dict_entry_message(entry, status="added"):
    """Карточка после добавления/обновления/поиска: заголовок статуса отдельной
    строкой, термин жирным курсивом с большой буквы + перевод одной строкой
    через жирную стрелку "→", разбор, пример полностью курсивом через "→"."""
    from ui.builder import MessageBuilder

    b = MessageBuilder()
    term = entry.get("term") or ""
    if entry.get("article") and not term.lower().startswith(entry["article"].lower() + " "):
        term = f"{entry['article']} {term}"
    term = _cap(term)

    if status == "duplicate":
        b.text_line("📚 ")
        b.bold(f"Уже есть в {_lang_loc_title(entry.get('lang'))} словаре")
        b.newline()
        b.spacer()
        _add_term_run(b, term)
        if entry.get("translation"):
            b.text_line(" ")
            b.bold("→")
            b.text_line(f" {entry['translation']}")
        b.newline()
        return b.build_stripped()

    titles = {"updated": "Обновлено", "found": "Найдено"}
    emoji = "✅" if status in ("added", "updated") else "📚"
    b.text_line(f"{emoji} ")
    b.bold(titles.get(status, "Добавлено"))
    b.newline()
    b.spacer()
    _add_term_run(b, term)
    if entry.get("translation"):
        b.text_line(" ")
        b.bold("→")
        b.text_line(f" {entry['translation']}")
    b.newline()
    if entry.get("breakdown"):
        b.spacer()
        b.line(f"Разбор: {entry['breakdown']}")
    usage = entry.get("usage") or []
    if usage:
        b.spacer()
        b.line("Когда так говорят:")
        for u in usage:
            b.line(f"• {u.get('situation', '')} → {u.get('example', '')}")
    examples = entry.get("examples") or []
    if examples:
        b.spacer()
        b.line("Пример:" if len(examples) == 1 else "Примеры:")
        for ex in examples:
            example_line = f"{ex.get('text', '')} → {ex.get('translation', '')}"
            b.italic(example_line)
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
    """Единая точка добавления: нормализация + перевод + короткий разбор + 1-2 примера.
    Один AI-вызов на запись, кэшируется в ai.py по input_hash (module="learning_dict_add",
    TTL 30 дней) — повторное добавление того же слова не тратит лимит повторно.
    lang_hint — nl/en/None. None означает, что язык не определён ни явной командой,
    ни активным языком обучения, ни признаками de/het — LLM определяет его сам,
    без принудительного fallback на nl.
    avoid_translations — уже показанные пользователю варианты (кнопка «Другой перевод»);
    меняет текст промпта, чтобы не попасть в тот же кэш и получить другой вариант."""
    if lang_hint in ("nl", "en"):
        language_line = f"Подсказка языка: {_lang_title(lang_hint)} ({lang_hint})."
    else:
        language_line = "Язык не подсказан — определи его сам по слову/фразе."
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
- examples: 1-2 примера предложений на изучаемом языке с переводом на русский, естественных
  и коротких.
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
- usage: ТОЛЬКО для разговорных фраз/выражений с несколькими разными значениями в зависимости
  от ситуации (не для обычных слов и не для фраз с одним понятным смыслом) — до 4 пар
  {{"situation": "коротко когда так говорят", "example": "короткий пример употребления в этом
  значении на изучаемом языке"}}. Если у фразы одно чёткое значение, верни пустой список.
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
  "usage": [],
  "needs_confirmation": false,
  "reason": "короткая причина уточнения или пусто"
}}
Если это не похоже на нидерландскую или английскую учебную запись, верни {{"ok": false, "reason": "коротко почему"}}.
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
    examples = []
    for ex in (d.get("examples") or [])[:2]:
        if not isinstance(ex, dict):
            continue
        text = re.sub(r"\s+", " ", str(ex.get("text") or "").strip())
        ex_ru = re.sub(r"\s+", " ", str(ex.get("translation") or "").strip())
        if text and ex_ru:
            examples.append({"text": text[:200], "translation": ex_ru[:200]})
    breakdown = re.sub(r"\s+", " ", str(d.get("breakdown") or "").strip())[:180]
    article = str(d.get("article") or "").strip() if lang == "nl" else ""
    if article and "глагол" in breakdown.lower():
        # У глаголов нет артикля de/het — модель иногда всё равно его возвращает.
        article = ""
    usage = []
    for u in (d.get("usage") or [])[:4]:
        if not isinstance(u, dict):
            continue
        situation = re.sub(r"\s+", " ", str(u.get("situation") or "").strip())
        example = re.sub(r"\s+", " ", str(u.get("example") or "").strip())
        if situation and example:
            usage.append({"situation": situation[:60], "example": example[:80]})
    return {
        "lang": lang,
        "term": term[:120],
        "article": article,
        "translation": translation[:180],
        "breakdown": breakdown,
        "examples": examples,
        "usage": usage,
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
                "usage": entry.get("usage", []),
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
        "usage": entry.get("usage", []),
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
    """Старая запись без разбора/примеров — донасытим при первом обращении (ленивая миграция)."""
    if not isinstance(item, dict):
        return False
    return not item.get("breakdown") or not item.get("examples")


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
        [InlineKeyboardButton("🔄 Другой перевод", callback_data="a_dictconfirm_retry")],
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdelok_{lang}_{term_key}")],
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
    saved["_payload"] = payload
    saved["_source_text"] = source_text
    saved["_seen_translations"] = [entry["translation"]]
    store.dict_pending_add[str(cid)] = saved
    msg = _dict_entry_message(saved, status=status)
    term_key = _dict_item_key(saved["lang"], "", _entry_term(saved))[2]
    if status == "duplicate":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdelok_{saved['lang']}_{term_key}"),
             InlineKeyboardButton("✅ Оставить", callback_data="noop")],
        ])
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    kb = _dict_saved_kb(saved["lang"], term_key)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def retry_pending_dict_add(bot, cid):
    """Кнопка «Другой перевод»: перегенерирует перевод, исключая уже показанные
    варианты, и обновляет уже сохранённую запись на месте (слово уже в словаре)."""
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdelok_{entry['lang']}_{term_key}")]])
        await bot.send_message(chat_id=cid, text="Больше вариантов перевода не нашлось.", reply_markup=kb)
        return
    updated = _overwrite_dict_entry_fields(cid, entry["lang"], entry["term"], {
        "translation": new_entry["translation"],
        "breakdown": new_entry.get("breakdown", ""),
        "examples": new_entry.get("examples", []),
        "usage": new_entry.get("usage", []),
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
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)


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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить всё", callback_data="a_dictbatch_add"),
        InlineKeyboardButton("❌ Не добавлять", callback_data="a_dictbatch_cancel"),
    ]])


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


def _w_field(w, *keys):
    for k in keys:
        if isinstance(w, dict) and w.get(k):
            return w[k]
    return ""

def _ensure_dict(cid):
    """Возвращает словарь пользователя (без авто-сида)."""
    return store.get_list(config.DICT_KEY, cid)


_DICT_SEED_PROFILE_KEY = "_dict_seed"
_DICT_SEED_SEEN_PROFILE_KEY = "_dict_seed_seen"
_DICT_SEED_PAGE_SIZE = 5
_DICT_SEED_LIMIT = 30
_SEED_LEVELS = ["simple", "medium", "hard"]
_DICT_SEED_SOURCE_NOTE = (
    "Списки собраны как частотный старт: Oxford 3000/5000, Cambridge/English "
    "Vocabulary Profile и частотные разговорные списки; редкие книжные слова исключены."
)

_EN_SEED_WORDS = {
    "simple": [
        ("about", "о, про", ""), ("always", "всегда", ""), ("because", "потому что", ""),
        ("before", "до, перед", ""), ("between", "между", ""), ("bring", "приносить", ""),
        ("city", "город", ""), ("clean", "чистый; убирать", ""), ("different", "разный", ""),
        ("enough", "достаточно", ""), ("family", "семья", ""), ("friend", "друг", ""),
        ("important", "важный", ""), ("learn", "учить", ""), ("listen", "слушать", ""),
        ("maybe", "может быть", ""), ("morning", "утро", ""), ("often", "часто", ""),
        ("place", "место", ""), ("question", "вопрос", ""), ("remember", "помнить", ""),
        ("something", "что-то", ""), ("sometimes", "иногда", ""), ("together", "вместе", ""),
        ("understand", "понимать", ""), ("usually", "обычно", ""), ("want", "хотеть", ""),
        ("water", "вода", ""), ("week", "неделя", ""), ("work", "работать; работа", ""),
        ("almost", "почти", ""), ("already", "уже", ""), ("arrive", "прибывать", ""),
        ("believe", "верить", ""), ("borrow", "занимать", ""), ("change", "менять; изменение", ""),
        ("comfortable", "удобный", ""), ("continue", "продолжать", ""), ("decide", "решать", ""),
        ("during", "во время", ""), ("explain", "объяснять", ""), ("finally", "наконец", ""),
        ("follow", "следовать", ""), ("happen", "случаться", ""), ("include", "включать", ""),
        ("instead", "вместо этого", ""), ("invite", "приглашать", ""), ("journey", "поездка", ""),
        ("later", "позже", ""), ("necessary", "необходимый", ""), ("opinion", "мнение", ""),
        ("perhaps", "возможно", ""), ("prepare", "готовить; подготавливать", ""), ("quite", "довольно", ""),
        ("receive", "получать", ""), ("reason", "причина", ""), ("return", "возвращаться", ""),
        ("several", "несколько", ""), ("spend", "тратить; проводить время", ""), ("without", "без", ""),
    ],
    "medium": [
        ("achieve", "достигать", ""), ("although", "хотя", ""), ("avoid", "избегать", ""),
        ("challenge", "вызов; трудная задача", ""), ("compare", "сравнивать", ""), ("consider", "считать; рассматривать", ""),
        ("create", "создавать", ""), ("depend", "зависеть", ""), ("develop", "развивать", ""),
        ("effort", "усилие", ""), ("especially", "особенно", ""), ("experience", "опыт; переживать", ""),
        ("focus", "фокусироваться", ""), ("improve", "улучшать", ""), ("increase", "увеличивать", ""),
        ("involve", "включать; вовлекать", ""), ("knowledge", "знание", ""), ("likely", "вероятный", ""),
        ("manage", "справляться; управлять", ""), ("notice", "замечать", ""), ("opportunity", "возможность", ""),
        ("provide", "предоставлять", ""), ("purpose", "цель", ""), ("reduce", "снижать", ""),
        ("require", "требовать", ""), ("result", "результат", ""), ("similar", "похожий", ""),
        ("support", "поддерживать; поддержка", ""), ("therefore", "поэтому", ""), ("whether", "ли", ""),
    ],
    "hard": [
        ("accurate", "точный", ""), ("approach", "подход", ""), ("assume", "предполагать", ""),
        ("benefit", "польза; приносить пользу", ""), ("complex", "сложный", ""), ("concern", "беспокойство; касаться", ""),
        ("consistent", "последовательный", ""), ("define", "определять", ""), ("demand", "требование; требовать", ""),
        ("encourage", "поощрять", ""), ("evidence", "доказательство", ""), ("expand", "расширять", ""),
        ("feature", "особенность", ""), ("impact", "влияние", ""), ("indicate", "указывать", ""),
        ("maintain", "поддерживать", ""), ("method", "метод", ""), ("obvious", "очевидный", ""),
        ("participate", "участвовать", ""), ("perspective", "точка зрения", ""), ("predict", "предсказывать", ""),
        ("previous", "предыдущий", ""), ("principle", "принцип", ""), ("range", "диапазон", ""),
        ("reliable", "надёжный", ""), ("respond", "отвечать; реагировать", ""), ("significant", "значительный", ""),
        ("specific", "конкретный", ""), ("strategy", "стратегия", ""), ("task", "задача", ""),
        ("acknowledge", "признавать", ""), ("adapt", "адаптироваться; адаптировать", ""),
        ("adequate", "достаточный", ""), ("advocate", "выступать за", ""), ("allocate", "распределять", ""),
        ("anticipate", "предвидеть", ""), ("apparent", "очевидный; кажущийся", ""), ("attribute", "приписывать", ""),
        ("clarify", "прояснять", ""), ("constraint", "ограничение", ""), ("contribute", "вносить вклад", ""),
        ("derive", "получать; происходить", ""), ("emphasis", "акцент", ""), ("enhance", "улучшать", ""),
        ("evaluate", "оценивать", ""), ("framework", "структура; рамка", ""), ("imply", "подразумевать", ""),
        ("incentive", "стимул", ""), ("inevitable", "неизбежный", ""), ("insight", "понимание; инсайт", ""),
        ("justify", "обосновывать", ""), ("prioritize", "расставлять приоритеты", ""), ("prohibit", "запрещать", ""),
        ("resolve", "решать; разрешать", ""), ("retain", "сохранять", ""), ("shift", "сдвиг; менять", ""),
        ("subtle", "тонкий; едва заметный", ""), ("sustain", "поддерживать длительно", ""), ("undergo", "претерпевать", ""),
        ("whereas", "тогда как", ""),
    ],
}

_NL_SEED_WORDS = {
    "simple": [
        ("altijd", "всегда", ""), ("begrijpen", "понимать", ""), ("betalen", "платить", ""),
        ("blijven", "оставаться", ""), ("boodschap", "покупка; сообщение", ""), ("buiten", "снаружи", ""),
        ("denken", "думать", ""), ("dichtbij", "рядом", ""), ("familie", "семья", ""),
        ("genoeg", "достаточно", ""), ("graag", "охотно; с удовольствием", ""), ("helpen", "помогать", ""),
        ("kiezen", "выбирать", ""), ("kijken", "смотреть", ""), ("kopen", "покупать", ""),
        ("leren", "учить", ""), ("luisteren", "слушать", ""), ("misschien", "может быть", ""),
        ("nodig", "нужный", ""), ("plaats", "место", ""), ("praten", "говорить", ""),
        ("samen", "вместе", ""), ("schoon", "чистый", ""), ("soms", "иногда", ""),
        ("vragen", "спрашивать", ""), ("vriend", "друг", ""), ("wachten", "ждать", ""),
        ("werken", "работать", ""), ("weten", "знать", ""), ("zoeken", "искать", ""),
        ("aanbieden", "предлагать", ""), ("afspraak", "встреча; запись", ""), ("beginnen", "начинать", ""),
        ("beslissen", "решать", ""), ("bereiken", "достигать", ""), ("beschrijven", "описывать", ""),
        ("betekenen", "значить", ""), ("bijna", "почти", ""), ("daarom", "поэтому", ""),
        ("duidelijk", "понятный", ""), ("eigenlijk", "вообще-то", ""), ("ervaring", "опыт", ""),
        ("gebruiken", "использовать", ""), ("gebeuren", "случаться", ""), ("gezellig", "уютный; приятный", ""),
        ("halen", "забирать; доставать", ""), ("herhalen", "повторять", ""), ("hoeven", "быть должным", "часто с niet/geen"),
        ("kloppen", "быть верным; стучать", ""), ("makkelijk", "лёгкий", ""), ("mening", "мнение", ""),
        ("mogelijk", "возможный", ""), ("ontmoeten", "встречать", ""), ("proberen", "пробовать", ""),
        ("reizen", "путешествовать", ""), ("rustig", "спокойный", ""), ("terug", "назад", ""),
        ("uitleggen", "объяснять", ""), ("vergeten", "забывать", ""), ("veranderen", "менять", ""),
    ],
    "medium": [
        ("aanpassen", "адаптировать; подстраивать", ""), ("aanraden", "советовать", ""),
        ("afhankelijk", "зависимый", ""), ("behalen", "достигать", ""), ("beïnvloeden", "влиять", ""),
        ("belangrijk", "важный", ""), ("bespreken", "обсуждать", ""), ("betrouwbaar", "надёжный", ""),
        ("bewijzen", "доказывать", ""), ("bijdragen", "вносить вклад", ""), ("doel", "цель", ""),
        ("gevolg", "последствие", ""), ("herkennen", "узнавать; распознавать", ""), ("inmiddels", "тем временем; уже", ""),
        ("kans", "шанс; возможность", ""), ("kennis", "знание", ""), ("namelijk", "а именно; ведь", ""),
        ("onderzoeken", "исследовать", ""), ("ontwikkelen", "развивать", ""), ("opletten", "внимательно следить", ""),
        ("oplossen", "решать проблему", ""), ("overwegen", "обдумывать", ""), ("rekening houden met", "учитывать", ""),
        ("resultaat", "результат", ""), ("samenwerken", "сотрудничать", ""), ("toestaan", "разрешать", ""),
        ("uitdaging", "вызов; трудность", ""), ("vermijden", "избегать", ""), ("verbeteren", "улучшать", ""),
        ("waarschijnlijk", "вероятно", ""),
    ],
    "hard": [
        ("aantonen", "показывать; доказывать", ""), ("benadering", "подход", ""), ("beperken", "ограничивать", ""),
        ("bevorderen", "способствовать", ""), ("complex", "сложный", ""), ("consequent", "последовательный", ""),
        ("daadwerkelijk", "действительно", ""), ("desondanks", "несмотря на это", ""), ("doeltreffend", "эффективный", ""),
        ("eisen", "требовать", ""), ("ernstig", "серьёзный", ""), ("gedrag", "поведение", ""),
        ("geschikt", "подходящий", ""), ("inschatten", "оценивать", ""), ("maatregel", "мера", ""),
        ("nadruk", "акцент", ""), ("ondersteunen", "поддерживать", ""), ("ontbreken", "отсутствовать", ""),
        ("overtuigen", "убеждать", ""), ("perspectief", "перспектива", ""), ("principe", "принцип", ""),
        ("reageren", "реагировать", ""), ("relevant", "релевантный", ""), ("schatten", "оценивать", ""),
        ("specifiek", "конкретный", ""), ("strategie", "стратегия", ""), ("toepassen", "применять", ""),
        ("uitbreiden", "расширять", ""), ("voorkomen", "предотвращать; случаться", ""), ("zorgvuldig", "тщательный", ""),
        ("aanscherpen", "уточнять; усиливать", ""), ("aanzienlijk", "значительный", ""), ("benadrukken", "подчёркивать", ""),
        ("beoordelen", "оценивать", ""), ("belemmeren", "препятствовать", ""), ("beschouwen", "рассматривать", ""),
        ("bewustwording", "осознание", ""), ("daarentegen", "напротив", ""), ("doorslaggevend", "решающий", ""),
        ("duurzaam", "устойчивый", ""), ("genuanceerd", "нюансированный", ""), ("grondig", "основательный", ""),
        ("handhaven", "поддерживать; обеспечивать соблюдение", ""), ("in aanmerking komen", "подходить; иметь право", ""),
        ("inzicht", "понимание", ""), ("kenmerk", "характерная черта", ""), ("noodzakelijk", "необходимый", ""),
        ("onderbouwen", "обосновывать", ""), ("onderscheiden", "различать", ""), ("onvermijdelijk", "неизбежный", ""),
        ("overeenkomen", "соответствовать; договариваться", ""), ("prioriteit", "приоритет", ""), ("rechtvaardigen", "оправдывать", ""),
        ("streven naar", "стремиться к", ""), ("subtiel", "тонкий; едва заметный", ""), ("toereikend", "достаточный", ""),
        ("uitgangspunt", "исходная точка", ""), ("veronderstellen", "предполагать", ""), ("voortvloeien uit", "следовать из", ""),
        ("wezenlijk", "существенный", ""),
    ],
}

_EN_SEED_PHRASES = {
    "simple": [("How are you?", "Как дела?", ""), ("I don't understand.", "Я не понимаю.", ""), ("Can you help me?", "Можете помочь?", ""), ("How much is it?", "Сколько это стоит?", ""), ("See you later.", "Увидимся позже.", ""), ("I would like...", "Я бы хотел...", ""), ("Where is the station?", "Где вокзал?", ""), ("I am sorry.", "Извините.", ""), ("No problem.", "Без проблем.", ""), ("What does it mean?", "Что это значит?", ""),
        ("Could you repeat that?", "Не могли бы повторить?", ""), ("I am looking for...", "Я ищу...", ""), ("It depends on...", "Это зависит от...", ""), ("I have already done it.", "Я уже это сделал.", ""), ("What do you think?", "Что ты думаешь?", ""), ("I need to change it.", "Мне нужно это изменить.", ""), ("Can I borrow this?", "Можно это одолжить?", ""), ("Let me know.", "Дай знать.", ""), ("I am on my way.", "Я уже в пути.", ""), ("That sounds good.", "Звучит хорошо.", "")],
    "medium": [("I see your point.", "Я понимаю твою мысль.", ""), ("It is worth trying.", "Это стоит попробовать.", ""), ("I need to improve this.", "Мне нужно это улучшить.", ""), ("Although it is difficult, it is useful.", "Хотя это сложно, это полезно.", ""), ("What is the main challenge?", "В чём главная сложность?", ""), ("I would rather avoid it.", "Я бы предпочёл этого избежать.", ""), ("It depends on the situation.", "Это зависит от ситуации.", ""), ("That is a good opportunity.", "Это хорошая возможность.", ""), ("Could you explain it briefly?", "Можешь кратко объяснить?", ""), ("I have noticed that...", "Я заметил, что...", "")],
    "hard": [("From my perspective...", "С моей точки зрения...", ""), ("The evidence suggests that...", "Данные указывают на то, что...", ""), ("We need a reliable method.", "Нам нужен надёжный метод.", ""), ("It has a significant impact.", "Это оказывает значительное влияние.", ""), ("Let me clarify one point.", "Позволь уточнить один момент.", ""), ("The previous approach did not work.", "Предыдущий подход не сработал.", ""), ("This strategy is more consistent.", "Эта стратегия более последовательна.", ""), ("What are the main concerns?", "Какие главные опасения?", ""), ("It is not that obvious.", "Это не так очевидно.", ""), ("We should define the task first.", "Сначала нужно определить задачу.", ""),
        ("I acknowledge the concern.", "Я признаю эту обеспокоенность.", ""), ("That implies a different approach.", "Это подразумевает другой подход.", ""), ("We need to prioritize the issue.", "Нужно расставить приоритеты в вопросе.", ""), ("The outcome was inevitable.", "Исход был неизбежен.", ""), ("Let me justify this decision.", "Позволь обосновать это решение.", ""), ("This framework is too narrow.", "Эта рамка слишком узкая.", ""), ("It requires a subtle shift.", "Это требует тонкого сдвига.", ""), ("The incentive is not clear.", "Стимул неясен.", ""), ("We should evaluate the impact.", "Нужно оценить влияние.", ""), ("Whereas the first option is faster...", "Тогда как первый вариант быстрее...", "")],
}

_NL_SEED_PHRASES = {
    "simple": [("Hoe gaat het?", "Как дела?", ""), ("Ik begrijp het niet.", "Я не понимаю.", ""), ("Kunt u mij helpen?", "Можете мне помочь?", ""), ("Hoeveel kost het?", "Сколько это стоит?", ""), ("Tot later.", "До встречи.", ""), ("Ik wil graag...", "Я хотел бы...", ""), ("Waar is het station?", "Где вокзал?", ""), ("Het spijt me.", "Мне жаль.", ""), ("Geen probleem.", "Без проблем.", ""), ("Wat betekent dat?", "Что это значит?", ""),
        ("Kunt u dat herhalen?", "Можете это повторить?", ""), ("Ik ben op zoek naar...", "Я ищу...", ""), ("Het hangt af van...", "Это зависит от...", ""), ("Ik heb het al gedaan.", "Я уже это сделал.", ""), ("Wat vind je ervan?", "Что ты об этом думаешь?", ""), ("Ik moet het veranderen.", "Мне нужно это изменить.", ""), ("Mag ik dit lenen?", "Можно это одолжить?", ""), ("Laat het me weten.", "Дай мне знать.", ""), ("Ik ben onderweg.", "Я в пути.", ""), ("Dat klinkt goed.", "Звучит хорошо.", "")],
    "medium": [("Ik begrijp je punt.", "Я понимаю твою мысль.", ""), ("Het is de moeite waard.", "Это того стоит.", ""), ("Ik wil dit verbeteren.", "Я хочу это улучшить.", ""), ("Hoewel het moeilijk is, is het nuttig.", "Хотя это сложно, это полезно.", ""), ("Wat is de grootste uitdaging?", "В чём главная трудность?", ""), ("Ik wil dat liever vermijden.", "Я предпочёл бы этого избежать.", ""), ("Het hangt van de situatie af.", "Это зависит от ситуации.", ""), ("Dat is een goede kans.", "Это хорошая возможность.", ""), ("Kun je het kort uitleggen?", "Можешь кратко объяснить?", ""), ("Ik heb gemerkt dat...", "Я заметил, что...", "")],
    "hard": [("Vanuit mijn perspectief...", "С моей точки зрения...", ""), ("Dat toont aan dat...", "Это показывает, что...", ""), ("We hebben een betrouwbare methode nodig.", "Нам нужен надёжный метод.", ""), ("Het heeft een grote invloed.", "Это оказывает большое влияние.", ""), ("Laat me één punt verduidelijken.", "Позволь уточнить один момент.", ""), ("De vorige aanpak werkte niet.", "Предыдущий подход не сработал.", ""), ("Deze strategie is consequenter.", "Эта стратегия более последовательна.", ""), ("Wat zijn de belangrijkste zorgen?", "Какие основные опасения?", ""), ("Dat is niet zo vanzelfsprekend.", "Это не так очевидно.", ""), ("We moeten eerst de taak bepalen.", "Сначала нужно определить задачу.", ""),
        ("Ik erken die zorg.", "Я признаю это опасение.", ""), ("Dat veronderstelt een andere aanpak.", "Это предполагает другой подход.", ""), ("We moeten dit prioriteit geven.", "Нужно дать этому приоритет.", ""), ("De uitkomst was onvermijdelijk.", "Исход был неизбежен.", ""), ("Laat me deze beslissing onderbouwen.", "Позволь обосновать это решение.", ""), ("Dit uitgangspunt is te beperkt.", "Эта исходная рамка слишком ограничена.", ""), ("Dat vraagt om een subtiele verschuiving.", "Это требует тонкого сдвига.", ""), ("De prikkel is niet duidelijk.", "Стимул неясен.", ""), ("We moeten de impact beoordelen.", "Нужно оценить влияние.", ""), ("Daarentegen is de eerste optie sneller.", "Напротив, первый вариант быстрее.", "")],
}


def _seed_dataset(lang, kind):
    if kind == "phrase":
        return _NL_SEED_PHRASES if lang == "nl" else _EN_SEED_PHRASES
    return _NL_SEED_WORDS if lang == "nl" else _EN_SEED_WORDS


def _seed_language(cid, lang=None):
    if lang in ("nl", "en"):
        code = lang
    else:
        import settings as _s
        code = _code(_s.study_lang(cid))
    language = "нидерландский" if code == "nl" else "английский"
    level = store.get_level(cid, language)
    if level not in LEVELS:
        level = "medium"
    return code, language, level


def _seed_existing_keys(cid):
    return {
        _dict_item_key(_dict_lang(w), _dict_kind(w), _w_field(w, "word", "nl", "en"))
        for w in _ensure_dict(cid)
    }


def _seed_seen_keys(cid):
    prof = store.get_profile(cid)
    raw = prof.get(_DICT_SEED_SEEN_PROFILE_KEY) or []
    return {tuple(x) for x in raw if isinstance(x, (list, tuple)) and len(x) == 3}


def _seed_mark_seen(cid, items):
    if not items:
        return
    prof = store.get_profile(cid)
    seen = _seed_seen_keys(cid)
    for item in items:
        seen.add(_dict_item_key(item.get("lang"), item.get("kind"), item.get("word")))
    prof[_DICT_SEED_SEEN_PROFILE_KEY] = [list(x) for x in sorted(seen)]
    store.set_profile(cid, prof)


def _seed_candidates(cid, lang, level, kind="word"):
    blocked = _seed_existing_keys(cid) | _seed_seen_keys(cid)
    out = []
    for word, ru, note in _seed_dataset(lang, kind).get(level, []):
        item = {"lang": lang, "word": _cap(word), "ru": ru, "kind": kind, "note": note}
        key = _dict_item_key(lang, kind, item["word"])
        if key not in blocked:
            out.append(item)
        if len(out) >= _DICT_SEED_LIMIT:
            break
    return out


def _seed_state_get(cid):
    prof = store.get_profile(cid)
    st = prof.get(_DICT_SEED_PROFILE_KEY)
    return st if isinstance(st, dict) else {}


def _seed_state_set(cid, st):
    prof = store.get_profile(cid)
    prof[_DICT_SEED_PROFILE_KEY] = st
    store.set_profile(cid, prof)


def _seed_state_clear(cid):
    prof = store.get_profile(cid)
    prof.pop(_DICT_SEED_PROFILE_KEY, None)
    store.set_profile(cid, prof)


def _seed_item_line(item):
    text = f"{item.get('word')} — {item.get('ru')}"
    if item.get("note"):
        text += f" ({item['note']})"
    return text


def _seed_render_text(st):
    level = st.get("level", "medium")
    kind = st.get("kind", "word")
    items = st.get("items") or []
    selected = set(st.get("selected") or [])
    page = int(st.get("page") or 0)
    total_pages = max(1, (len(items) + _DICT_SEED_PAGE_SIZE - 1) // _DICT_SEED_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DICT_SEED_PAGE_SIZE
    chunk = items[start:start + _DICT_SEED_PAGE_SIZE]
    level_label = LEVEL_LABELS.get(level, level)
    header = f"🧩 Стартовые фразы · {level_label}" if kind == "phrase" else f"📚 Популярные слова · {level_label}"
    lines = [
        header,
        f"Страница {page + 1} из {total_pages}",
        "",
        "Отметьте слова, которые хотите добавить в словарь:" if kind == "word" else "Отметьте фразы, которые хотите добавить в словарь:",
        "",
    ]
    for offset, item in enumerate(chunk):
        idx = start + offset
        mark = "✅" if idx in selected else "□"
        lines.append(f"{mark} {_seed_item_line(item)}")
    lines.extend(["", _DICT_SEED_SOURCE_NOTE])
    return "\n".join(lines)


def _seed_render_kb(st):
    items = st.get("items") or []
    selected = set(st.get("selected") or [])
    page = int(st.get("page") or 0)
    total_pages = max(1, (len(items) + _DICT_SEED_PAGE_SIZE - 1) // _DICT_SEED_PAGE_SIZE)
    start = page * _DICT_SEED_PAGE_SIZE
    chunk = items[start:start + _DICT_SEED_PAGE_SIZE]
    rows = []
    for offset, item in enumerate(chunk):
        idx = start + offset
        mark = "✅" if idx in selected else "□"
        rows.append([InlineKeyboardButton(f"{mark} {item.get('word')[:38]}", callback_data=f"a_dictseed_toggle_{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"a_dictseed_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️ Далее", callback_data=f"a_dictseed_page_{page + 1}"))
    if nav:
        rows.append(nav)
    level_label = LEVEL_LABELS.get(st.get("level"), "Средний")
    rows.append([InlineKeyboardButton(f"📶 Другой уровень ({level_label})", callback_data="a_dictseed_level")])
    add_label = f"✅ Добавить отмеченные ({len(selected)})" if selected else "✅ Добавить отмеченные"
    rows.append([InlineKeyboardButton(add_label, callback_data="a_dictseed_add")])
    return InlineKeyboardMarkup(rows)


async def send_seed_intro(bot, cid, lang=None):
    code, language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        await send_dict_lang(bot, cid, code)
        return
    text = (
        "Для эффективного обучения сначала наполним ваш словарь.\n\n"
        f"Я подобрал слова уровня «{LEVEL_LABELS.get(level, level)}». Просмотрите список и отметьте те, "
        "которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Наполнить словарь", callback_data=f"a_dictseed_start_{code}")],
        [InlineKeyboardButton("✏️ Добавить свои слова", callback_data=f"a_dictadd_smart_{code}")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def offer_seed_for_level_change(bot, cid, language, level):
    code = _code(language)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        return
    level_label = LEVEL_LABELS.get(level, level)
    text = (
        f"📚 Уровень обновлён до «{level_label}»\n\n"
        f"Хотите добавить стартовые слова уровня «{level_label}»?\n"
        "Я покажу список, а вы отметите те, которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✨ Добавить слова ({level_label})", callback_data=f"a_dictseed_start_{code}")],
        [InlineKeyboardButton("Позже", callback_data="a_dictseed_later")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_later(bot, cid):
    _seed_state_clear(cid)
    await send_dict(bot, cid)


async def seed_start(bot, cid, lang=None, kind="word", q=None):
    code, _language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, kind)
    if not items:
        text = (
            "📚 Словарь уже заполнен\n\n"
            "Для вашего уровня пока нет новых стартовых слов.\n"
            "Можно добавить свои слова вручную или перейти к фразам."
        )
        if q is not None:
            try:
                await q.message.edit_text(text)
                return
            except Exception:
                pass
        await bot.send_message(chat_id=cid, text=text)
        return
    st = {
        "lang": code,
        "level": level,
        "kind": kind,
        "items": items,
        "selected": [],
        "page": 0,
        "created_at": datetime.now(config.TZ).isoformat(),
        "confirmed": False,
    }
    _seed_state_set(cid, st)
    text = _seed_render_text(st)
    kb = _seed_render_kb(st)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_toggle(bot, cid, idx, q=None):
    st = _seed_state_get(cid)
    items = st.get("items") or []
    if not (0 <= idx < len(items)):
        return
    selected = set(st.get("selected") or [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.add(idx)
    st["selected"] = sorted(selected)
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


async def seed_page(bot, cid, page, q=None):
    st = _seed_state_get(cid)
    if not st:
        return
    st["page"] = max(0, int(page))
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


def _seed_level_kb(cid, code):
    _l, _language, current = _seed_language(cid, code)
    row = []
    for level in _SEED_LEVELS:
        mark = "✅ " if level == current else ""
        row.append(InlineKeyboardButton(f"{mark}{LEVEL_LABELS[level]}", callback_data=f"a_dictseedlvl_{code}_{level}"))
    rows = [row, [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictseed_start_{code}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]]
    return InlineKeyboardMarkup(rows)


async def seed_choose_level(bot, cid, q=None):
    st = _seed_state_get(cid)
    code = st.get("lang") if st else None
    code = code or _seed_language(cid)[0]
    text = "📶 Выбери уровень слов для добавления."
    kb = _seed_level_kb(cid, code)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_set_level(bot, cid, lang, level, q=None):
    if level not in _SEED_LEVELS:
        return
    st = _seed_state_get(cid)
    kind = st.get("kind", "word") if st else "word"
    language = "нидерландский" if lang == "nl" else "английский"
    store.set_level(cid, language, level)
    await seed_start(bot, cid, lang, kind=kind, q=q)


async def seed_add_selected(bot, cid, q=None):
    st = _seed_state_get(cid)
    if not st:
        await bot.send_message(chat_id=cid, text="Подборка устарела. Открой словарь заново.")
        return
    if st.get("confirmed"):
        await bot.send_message(chat_id=cid, text="Эта подборка уже обработана.")
        return
    st["confirmed"] = True
    _seed_state_set(cid, st)
    selected = set(st.get("selected") or [])
    existing = _seed_existing_keys(cid)
    added = []
    for idx, item in enumerate(st.get("items") or []):
        if idx not in selected:
            continue
        key = _dict_item_key(item["lang"], item["kind"], item["word"])
        if key in existing:
            continue
        legacy = {k: item[k] for k in ("lang", "word", "ru", "kind") if item.get(k)}
        store.add_to_list(config.DICT_KEY, cid, legacy)
        existing.add(key)
        added.append(legacy)
    kind = st.get("kind", "word")
    lang = st.get("lang", "en")
    _seed_mark_seen(cid, added)
    _seed_state_clear(cid)
    # Сразу генерируем пример/разбор для тренажёра — та же ленивая миграция,
    # что при первом обращении к старой записи, но выполненная сейчас, а не
    # отложенная до первого показа в тренажёре.
    for legacy in added:
        await _refresh_dict_entry(cid, legacy)
    noun = "фраз" if kind == "phrase" else "слов"
    if added:
        terms = ", ".join(a.get("word", "") for a in added[:10])
        more = f" и ещё {len(added) - 10}" if len(added) > 10 else ""
        text = f"✅ Добавлено {len(added)} {noun}: {terms}{more}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Начать обучение", callback_data=f"a_train_{lang}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
        ])
    else:
        text = "Ничего не отмечено — словарь не изменился."
        kb = None
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
    else:
        await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


def _dict_kind(w):
    if isinstance(w, dict) and w.get("kind"):
        return w["kind"]
    word = w.get("word", "") if isinstance(w, dict) else str(w)
    return "phrase" if " " in word.strip() else "word"

def _dict_lang(w):
    return w.get("lang", "nl") if isinstance(w, dict) else "nl"

def _dict_counts(cid):
    """Количество записей словаря по языку — единый счётчик, без деления
    на слова и фразы."""
    words = _ensure_dict(cid)
    out = {"nl": 0, "en": 0}
    for w in words:
        lang = "en" if _dict_lang(w) == "en" else "nl"
        out[lang] += 1
    return out


_SRS_MIGRATION_BATCH_SIZE = 40  # ограничивает размер одного промпта на очень больших словарях


def _srs_migration_prompt(lang, entries):
    """Промпт батч-миграции: доопределяет поля тренажёра (pos/construction/...)
    для записей словаря, у которых их ещё нет — одним запросом на пачку,
    а не по одному слову (см. spec-learning-rework: 'Миграция')."""
    lang_title = "нидерландский" if lang == "nl" else "английский"
    lines = "\n".join(
        f'{i}. term="{_entry_term(e)}" translation="{_entry_translation(e)}" breakdown="{e.get("breakdown", "")}"'
        for i, e in enumerate(entries)
    )
    return f"""Ты лексикограф учебного словаря. Язык записей: {lang_title}.
Для каждой записи ниже доопредели поля тренажёра. Не меняй term/translation — только
доопредели недостающее по ним.

Записи:
{secure.wrap_untrusted(lines, "словарь пользователя")}

Для каждой записи верни:
- pos: часть речи одним словом.
- plural: множественное число, если применимо к существительному, иначе пусто.
- forms: до 3 других форм слова, если уместно, иначе пустой список.
- topic: одна короткая тема.
- difficulty: уровень CEFR одной меткой ("A1".."C1").
- construction: если это устойчивая конструкция/идиома — сама конструкция целиком,
  иначе пусто.
- situation_type: если это фраза для конкретной жизненной ситуации — короткий тип
  ситуации, иначе пусто.
- alt_translations: до 2 дополнительных вариантов перевода, если уместны, иначе пустой список.

Верни строго JSON-объект с ключом "items" — массив в ТОМ ЖЕ ПОРЯДКЕ, что записи выше,
без markdown:
{{"items": [{{"pos": "...", "plural": "", "forms": [], "topic": "...", "difficulty": "B1",
   "construction": "", "situation_type": "", "alt_translations": []}}, ...]}}"""


async def migrate_dict_entries_for_srs(cid, lang):
    """Батч-миграция словаря на новую структуру тренажёра: доопределяет поля
    (pos/construction/...) и проставляет SRS-дефолты одним AI-запросом на всю
    пачку записей без srs_due_at (а не лениво по одной). Вызывается один раз
    при первом заходе в новый тренажёр (см. train_start). Если батч не удался —
    записи участвуют в тренажёре с дефолтными SRS-полями и пустыми новыми
    текстовыми полями (не блокирует тренажёр), повторная попытка — при
    следующем заходе, т.к. записи без srs_due_at останутся немигрированными."""
    words = store.get_list(config.DICT_KEY, cid)
    pending_idx = [
        i for i, w in enumerate(words)
        if _dict_lang(w) == lang and _entry_needs_srs_migration(w)
    ]
    if not pending_idx:
        return
    for batch_start in range(0, len(pending_idx), _SRS_MIGRATION_BATCH_SIZE):
        batch_idx = pending_idx[batch_start:batch_start + _SRS_MIGRATION_BATCH_SIZE]
        entries = [words[i] for i in batch_idx]
        try:
            prompt = _srs_migration_prompt(lang, entries)
            results = await ai.allm_json(prompt, 2000, module="learning_srs_migration")
            results = results if isinstance(results, list) else results.get("items", [])
        except Exception as e:
            _log.warning("srs migration batch failed, using defaults: %r", e, exc_info=True)
            results = []
        for pos, idx in enumerate(batch_idx):
            fields = results[pos] if pos < len(results) and isinstance(results[pos], dict) else {}
            extra = _extract_srs_fields(fields)
            for k, v in extra.items():
                words[idx].setdefault(k, v)
    store.set_list(config.DICT_KEY, cid, words)

async def _show_screen(bot, cid, text, entities=None, reply_markup=None, q=None):
    """Навигация внутри словаря: редактирует текущее сообщение, если есть callback
    query, иначе (первый вход, текстовая команда) шлёт новое."""
    if q is not None:
        try:
            await q.message.edit_text(text, entities=entities, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=reply_markup)


_DICT_ORIGIN_TO_BACK = {
    "notes": "m_notes",
    "menu": "m_learn",
    "mydata": "set_home",
    "learnset": "set_learning",
}
_DICT_BACK_TO_ORIGIN = {v: k for k, v in _DICT_ORIGIN_TO_BACK.items()}


async def send_dict(bot, cid, back="m_notes", q=None):
    c = _dict_counts(cid)
    nl_total = c["nl"]
    en_total = c["en"]
    msg = dict_ui.dict_overview(nl_total, en_total)
    origin = _DICT_BACK_TO_ORIGIN.get(back, "notes")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data=f"a_dictlang_nl_from_{origin}")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data=f"a_dictlang_en_from_{origin}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ]
    await _show_screen(bot, cid, msg.text, msg.entities, InlineKeyboardMarkup(rows), q=q)

async def send_dict_lang(bot, cid, lang, back="m_learn", q=None, page=0):
    """Главный экран словаря — короткое меню без списка слов: Найти/Добавить-удалить
    (список слов теперь внутри этой вкладки)/Сгенерировать, «⬅️ Назад» ведёт туда,
    откуда открыли словарь (раздел «Обучение»)."""
    count = len(_dict_lang_entries(cid, lang))
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    rows = [
        [InlineKeyboardButton("🔍 Найти в словаре", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("✏️ Добавить или удалить слово", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("✨ Сгенерировать набор слов", callback_data=f"a_dictseed_start_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ]
    text = f"{flag} Мой словарь · {count} слов и фраз"
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


async def send_dict_manage(bot, cid, lang, back="m_learn", q=None, page=0):
    """Вкладка «Добавить или удалить слово»: список слов (тап открывает карточку
    с удалением) + приглашение написать слово текстом, чтобы добавить его."""
    store.pending_input[str(cid)] = f"dictadd_smart_{lang}"
    entries = _dict_lang_entries(cid, lang)
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    lang_title = "нидерландского" if lang == "nl" else "английского"
    add_hint = (
        "Пришли слово или фразу для изучения — можно сразу несколько, каждую с новой строки.\n"
        "Я сам приведу в правильную форму, переведу и разберу."
    )
    if not entries:
        rows = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]]
        text = f"{flag} Словарь {lang_title} языка пока пуст.\n\n{add_hint}"
        await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)
        return
    total_pages = max(1, (len(entries) + _DICT_LIST_PAGE_SIZE - 1) // _DICT_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DICT_LIST_PAGE_SIZE
    chunk = entries[start:start + _DICT_LIST_PAGE_SIZE]
    word_buttons = []
    for item in chunk:
        term_key = _dict_item_key(lang, "", _entry_term(item))[2]
        word_buttons.append(InlineKeyboardButton(
            _cap(_entry_term(item))[:20],
            callback_data=f"a_dictview_{lang}_{page}_{term_key}",
        ))
    word_rows = [word_buttons[i:i + 2] for i in range(0, len(word_buttons), 2)]
    nav_rows = []
    if total_pages > 1:
        next_page = page + 1 if page < total_pages - 1 else 0
        nav_rows.append([InlineKeyboardButton("Следующее слово", callback_data=f"a_dicteditpage_{lang}_{next_page}")])
    rows = word_rows + nav_rows + [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]]
    text = (
        f"{flag} Показаны {start + 1}–{start + len(chunk)} из {len(entries)}. "
        "Нажми на слово, чтобы посмотреть перевод, пример и удалить его.\n\n"
        f"{add_hint}"
    )
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("✏️ Добавить или удалить слово", callback_data=f"a_dictadd_smart_{lang}")],
    ])


async def send_dict_search_prompt(bot, cid, lang, q=None):
    store.pending_input[str(cid)] = f"dictsearch_{lang}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]])
    await _show_screen(bot, cid, "🔍 Введи слово или фразу для поиска.", None, kb, q=q)


def _dict_search_kb(lang, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdel_{lang}_{term_key}")],
        [InlineKeyboardButton("🔍 Искать ещё", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])


async def handle_dict_search(bot, cid, lang, query):
    """Ищет по подстроке термина в словаре, показывает карточку с кнопкой удаления."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(chat_id=cid, text="Пришли слово или часть фразы для поиска.")
        return
    words = _ensure_dict(cid)
    match = None
    for item in words:
        if _dict_lang(item) != lang:
            continue
        term = _entry_term(item)
        if query_norm in term.casefold():
            match = item
            break
    if not match:
        await bot.send_message(
            chat_id=cid,
            text="Не нашла в словаре. Попробуй другое слово или посмотри весь список.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Мои слова и фразы", callback_data=f"a_dictedit_{lang}")],
            ]),
        )
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    term_key = _dict_item_key(lang, "", _entry_term(match))[2]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                            reply_markup=_dict_search_kb(lang, term_key))


async def confirm_delete_dict_entry(bot, cid, lang, term_key, q=None):
    await _show_screen(
        bot, cid, "Точно удалить это из словаря?", None,
        InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"a_dictdelok_{lang}_{term_key}"),
            InlineKeyboardButton("Отмена", callback_data=f"a_dictlang_{lang}"),
        ]]),
        q=q,
    )


async def del_dict_entry_by_term(bot, cid, lang, term_key, page=None, q=None):
    words = store.get_list(config.DICT_KEY, cid)
    removed = ""
    kept = []
    for item in words:
        if _dict_lang(item) == lang and _dict_item_key(lang, "", _entry_term(item))[2] == term_key and not removed:
            removed = _entry_term(item)
            continue
        kept.append(item)
    if removed:
        store.set_list(config.DICT_KEY, cid, kept)
    msg = dict_ui.dict_deleted(removed or "")
    if page is not None:
        await _show_screen(
            bot, cid, msg.text, msg.entities,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад к списку", callback_data=f"a_dictedit_{lang}_{page}")]]),
            q=q,
        )
        return
    await _show_screen(bot, cid, msg.text, msg.entities, _dict_manage_kb(lang), q=q)


_DICT_LIST_PAGE_SIZE = 10


def _dict_lang_entries(cid, lang):
    """Слова языка, отсортированные по алфавиту — стабильный порядок для
    постраничного списка «Мои слова и фразы»."""
    entries = [w for w in _ensure_dict(cid) if _dict_lang(w) == lang]
    return sorted(entries, key=lambda w: _cap(_entry_term(w)).casefold())


def _dict_entry_view_kb(lang, page, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictviewdel_{lang}_{page}_{term_key}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}_{page}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])


async def send_dict_entry_view(bot, cid, lang, page, term_key, q=None):
    """Карточка слова из списка — тот же вид, что при добавлении, плюс удаление."""
    entries = _dict_lang_entries(cid, lang)
    match = next((w for w in entries if _dict_item_key(lang, "", _entry_term(w))[2] == term_key), None)
    if not match:
        await send_dict_lang(bot, cid, lang, page=page, q=q)
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    await _show_screen(bot, cid, msg.text, msg.entities, _dict_entry_view_kb(lang, page, term_key), q=q)


async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    removed = ""
    if i < len(words):
        removed_item = words.pop(i)
        removed = _cap(_entry_term(removed_item))
        store.set_list(config.DICT_KEY, cid, words)
    import settings as _s
    lang = _code(_s.study_lang(cid))
    msg = dict_ui.dict_deleted(removed or "")
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=_dict_manage_kb(lang),
    )

WEEK_TRACK = {
    0: ("Свежая кровь", "Загрузка",
        "Прочитай вслух, покрути в голове. Больше ничего."),
    1: ("Первый повтор", "Эффект генерации",
        "Повтори вчерашнее. Посмотри на русский - вспомни перевод. Придумай ОДНО смешное предложение."),
    2: ("День разгрузки", "Микро-доза",
        "Повтори только фразы за понедельник. Слова не трогай. Есть силы - добавь 2 новых слова."),
    3: ("Проверка боем", "Активное вспоминание",
        "Повторяем всё за Пн и Ср. Закрой перевод рукой, вспоминай. Ошибся - отметь крестиком."),
    4: ("Финал недели", "Зачистка хвостов",
        "Повтори только слова, где вчера были крестики. Короткий спринт."),
    5: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Мозгу нужен чистый отдых для переноса в долговременную память."),
    6: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Дай мозгу отдохнуть - это часть процесса."),
}

def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _morning_method_line(method, entries):
    if not entries:
        return "В словаре пока нет записей на этом языке. Сегодня можно добавить что-то через словарь."
    return method


def _entries_priority_sorted(pool):
    """Сортировка по приоритету: сначала никогда не показанные, потом давно
    показанные, потом невыученные — используется и для утренней подборки."""
    def _key(w):
        shown = w.get("last_shown_at")
        never_shown = 0 if not shown else 1
        not_known = 0 if w.get("status") != "known" else 1
        return (never_shown, not_known, shown or "")
    return sorted(pool, key=_key)


def _build_morning_word(cid, language):
    """Собирает карточку слова дня (без отправки) -> (MessageSpec, del_row[InlineKeyboardButton])."""
    import random as _r
    from datetime import datetime
    lang_code = _code(language)
    flag = _flag(language)
    wd = datetime.now(config.TZ).weekday()
    _title, _phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)
    pool = [w for w in words if _dict_lang(w) == lang_code and _entry_term(w) and _entry_translation(w)]
    if wd >= 5 or not pool:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), empty_hint=True)
        return msg, []
    method = _morning_method_line(method, pool)
    ranked = _entries_priority_sorted(pool)
    top_n = ranked[:max(5, len(ranked) // 2)]
    chosen = _r.sample(top_n, min(5, len(top_n)))
    if not chosen:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"))
        return msg, []

    now_iso = datetime.now(config.TZ).isoformat()
    del_row = []
    lines = []
    for w in chosen:
        term = _cap(_entry_term(w))
        ru = _entry_translation(w)
        lines.append((term, ru))
        try:
            idx = words.index(w)
            words[idx]["last_shown_at"] = now_iso
            del_row.append(InlineKeyboardButton(f"❌ {term[:20]}", callback_data=f"worddel_{idx}"))
        except ValueError:
            pass
    try:
        store.set_list(config.DICT_KEY, cid, words)
    except Exception:
        pass

    msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), words=lines)
    return msg, del_row


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 - Daily Words: метод дня недели + порция из 5 записей словаря,
    без деления на слова и фразы — приоритет давно не показанным."""
    import settings
    language = language or settings.study_lang(cid)
    msg, del_row = _build_morning_word(cid, language)
    rows = _chunks(del_row, 3) if with_kb else []
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


async def send_daily_practice(bot, cid):
    """11:00 - "Практика языка": слово дня и живая фраза активного языка одним сообщением."""
    import settings
    from ui.builder import MessageBuilder
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    proverb_data = await _generate_proverb(language)
    proverb_msg = learning_ui.proverb_card(
        _flag(language), proverb_data["original"], proverb_data["analogs"],
        _cap(proverb_data["meaning"]), proverb_data["example"], proverb_data["example_ru"],
    )
    combined = MessageBuilder()
    combined.embed(word_msg)
    combined.embed(proverb_msg)
    msg = combined.build_stripped()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
