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
from ui.constants import delete_label
from ui.navigation import back_menu_keyboard

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


def _entry_term(item):
    from dictionary_import import _entry_term as implementation
    return implementation(item)


def _entry_translation(item):
    from dictionary_import import _entry_translation as implementation
    return implementation(item)


def _entry_needs_srs_migration(item):
    from dictionary_import import _entry_needs_srs_migration as implementation
    return implementation(item)


def _entry_needs_ai_refresh(item):
    from dictionary_import import _entry_needs_ai_refresh as implementation
    return implementation(item)


async def _refresh_dict_entry(cid, item):
    from dictionary_import import _refresh_dict_entry as implementation
    return await implementation(cid, item)


def _extract_srs_fields(data):
    from dictionary_import import _extract_srs_fields as implementation
    return implementation(data)


def _dict_entry_message(entry, status="added"):
    from dictionary_import import _dict_entry_message as implementation
    return implementation(entry, status=status)


def _dict_item_key(lang, kind, word):
    from dictionary_import import _dict_item_key as implementation
    return implementation(lang, kind, word)

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


def _w_field(w, *keys):
    for k in keys:
        if isinstance(w, dict) and w.get(k):
            return w[k]
    return ""

def _ensure_dict(cid):
    """Возвращает словарь пользователя (без авто-сида)."""
    return store.ensure_list_ids(config.DICT_KEY, cid)


_DICT_SEED_LIMIT = 30


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
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    await _show_screen(bot, cid, msg.text, msg.entities, InlineKeyboardMarkup(rows), q=q)

async def send_dict_lang(bot, cid, lang, back="m_learn", q=None, page=0):
    """Главный экран словаря — короткое меню без списка слов: сначала Добавить,
    затем Найти/Сгенерировать; список слов доступен из сценария добавления. «⬅️ Назад» ведёт туда,
    откуда открыли словарь (раздел «Обучение»)."""
    count = len(_dict_lang_entries(cid, lang))
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    rows = [
        [InlineKeyboardButton("🆕 Добавить слово", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("🔍 Найти в словаре", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("✨ Сгенерировать набор слов", callback_data=f"a_dictseed_start_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
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
        rows = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]]
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
        nav_rows.append([InlineKeyboardButton("🔄 Далее", callback_data=f"a_dicteditpage_{lang}_{next_page}")])
    rows = word_rows + nav_rows + [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]]
    text = (
        f"{flag} Показаны {start + 1}–{start + len(chunk)} из {len(entries)}. "
        "Нажми на слово, чтобы посмотреть перевод, пример и удалить его.\n\n"
        f"{add_hint}"
    )
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Добавить слово", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("📚 Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


async def send_dict_search_prompt(bot, cid, lang, q=None):
    store.pending_input[str(cid)] = f"dictsearch_{lang}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
    await _show_screen(bot, cid, "🔍 Введи слово или фразу для поиска.", None, kb, q=q)


def _dict_tts_row(entry):
    if entry.get("lang") == "nl" and entry.get("id"):
        return [[InlineKeyboardButton("🔊 Прослушать", callback_data=f"tts_word:{entry['id']}")]]
    return []


def _dict_search_kb(entry, term_key):
    lang = _dict_lang(entry)
    return InlineKeyboardMarkup(_dict_tts_row(entry) + [
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdel_{lang}_{term_key}")],
        [InlineKeyboardButton("🔍 Искать ещё", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


async def handle_dict_search(bot, cid, lang, query):
    """Ищет по подстроке термина в словаре, показывает карточку с кнопкой удаления."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(
            chat_id=cid,
            text="Пришли слово или часть фразы для поиска.",
            reply_markup=back_menu_keyboard(f"a_dictedit_{lang}"),
        )
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
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
                 InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
            ]),
        )
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    term_key = _dict_item_key(lang, "", _entry_term(match))[2]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                            reply_markup=_dict_search_kb(match, term_key))


async def confirm_delete_dict_entry(bot, cid, lang, term_key, q=None):
    await _show_screen(
        bot, cid, "Точно удалить это из словаря?", None,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(delete_label("Да, удалить"), callback_data=f"a_dictdelok_{lang}_{term_key}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
             InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ]),
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
            back_menu_keyboard(f"a_dictedit_{lang}_{page}"),
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


def _dict_entry_view_kb(entry, page, term_key):
    lang = _dict_lang(entry)
    return InlineKeyboardMarkup(_dict_tts_row(entry) + [
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictviewdel_{lang}_{page}_{term_key}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}_{page}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
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
    await _show_screen(bot, cid, msg.text, msg.entities, _dict_entry_view_kb(match, page, term_key), q=q)


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
