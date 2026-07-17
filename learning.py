import random
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import srs
import store
import trainer_engine
from learning_dictionary import DictionaryRepository, entry_language, entry_term, entry_translation
from dictionary_model import normalize_term_case
from trainer_engine import (
    EXERCISE_CHOOSE_TRANSLATION, EXERCISE_RECALL_FREE,
    EXERCISE_BUILD_SENTENCE, EXERCISE_FIND_ERROR,
    EXERCISE_CHOOSE_NATURAL, EXERCISE_FILL_GAP,
    EXERCISE_TRANSLATE_CONTEXT, EXERCISE_CHOOSE_REACTION,
    EXERCISE_CONTINUE_DIALOGUE,
)
from ui import learning as learning_ui

def _cap(value):
    value = str(value or "").strip()
    return value[:1].upper() + value[1:] if value else value

def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"

def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"

def _active_language_code(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings as _s
    return _code(_s.study_lang(cid))

def active_language(cid):
    return _language_for_code(_active_language_code(cid))

def _language_display(language):
    return f"{_flag(language)} {'Нидерландский' if _code(language) == 'nl' else 'Английский'}"

def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"

_DAILY_MATERIAL_CACHE = {}  # cid -> {"date": iso, "entry": dict, "lang": code}


def _save_daily_material(cid, today, lang, entry):
    cached = {"date": today, "lang": lang, "entry": entry}
    _DAILY_MATERIAL_CACHE[str(cid)] = cached
    profile = store.get_profile(cid)
    profile["learning_daily_material"] = cached
    store.set_profile(cid, profile)
    return entry


def daily_material_type(entry):
    """Пользовательский тип материала для короткой карточки дня."""
    if str(entry.get("kind") or "").strip().casefold() == "rule":
        return "rule"
    if str(entry.get("construction") or "").strip():
        return "construction"
    term = entry_term(entry)
    return "phrase" if " " in term.strip() else "word"


def select_daily_material(cid):
    """Материал дня для карточек 'Мой день' и 'Обучение' — ОДИН и тот же выбор
    на календарный день, без похода в AI. Приоритет — давно не показанные и
    ещё не выученные записи словаря активного языка. Кэшируется по дате
    (см. _DAILY_MATERIAL_CACHE), чтобы оба экрана показывали одно и то же и не
    выбирали заново при каждом открытии. Возвращает саму запись (dict) или None,
    если словарь на активном языке пуст — вызывающий код решает, как это
    показать."""
    lang = _active_language_code(cid)
    today = datetime.now(config.TZ).date().isoformat()
    cached = _DAILY_MATERIAL_CACHE.get(str(cid))
    if cached and cached.get("date") == today and cached.get("lang") == lang:
        return cached.get("entry")
    saved = store.get_profile(cid).get("learning_daily_material")
    if (isinstance(saved, dict) and saved.get("date") == today
            and saved.get("lang") == lang and "entry" in saved):
        _DAILY_MATERIAL_CACHE[str(cid)] = saved
        return saved.get("entry")

    repository = DictionaryRepository(cid)
    words = repository.all()
    pool = [w for w in words if entry_term(w) and entry_translation(w) and entry_language(w) == lang]
    if not pool:
        return _save_daily_material(cid, today, lang, None)

    def _priority_key(w):
        shown = w.get("last_shown_at")
        never_shown = 0 if not shown else 1
        not_known = 0 if w.get("status") != "known" else 1
        return (never_shown, not_known, shown or "")

    pool.sort(key=_priority_key)
    top_n = pool[:max(1, len(pool) // 3)] or pool
    entry = random.choice(top_n)

    entry = dict(entry)
    entry["last_shown_at"] = datetime.now(config.TZ).isoformat()
    for idx, w in enumerate(words):
        if entry_language(w) == lang and entry_term(w) == entry_term(entry):
            words[idx] = entry
            repository.save_all(words)
            break

    return _save_daily_material(cid, today, lang, entry)


def _daily_focus_text(entry, kind):
    """'Сегодня в фокусе' на главном экране — вытекает из SRS-уровня материала
    дня (0-1: узнать перевод; 2-3: вспомнить без вариантов; 4-5: применить
    самостоятельно), без AI-вызова — правило по уже посчитанному уровню."""
    level = srs.normalize_state(entry)["srs_level"]
    if level <= 1:
        return "вспомнить перевод до открытия спойлера."
    if kind == "word":
        return "использовать новое слово в своём предложении."
    if kind == "rule":
        return "применить правило в одном своём предложении."
    if kind == "phrase":
        return "вспомнить фразу без подсказки."
    return "вспомнить конструкцию без подсказки."


def build_learning_home(cid):
    """Данные для главного экрана раздела 'Обучение': материал дня + короткий
    фокус тренировки. UI (ui/menu.py) только рендерит эти поля, не читает
    store и не выбирает материал сам — см. §8 CLAUDE.md."""
    entry = select_daily_material(cid)
    lang_code = _active_language_code(cid)
    progress = build_progress_screen(cid)
    if not entry:
        return {
            "has_material": False,
            "lang_code": lang_code,
            "progress": progress,
        }
    kind = daily_material_type(entry)
    raw_term = entry.get("rule") or entry_term(entry)
    display_term = (
        _cap(raw_term) if kind == "rule"
        else normalize_term_case(raw_term, kind) if kind == "word"
        else _cap(raw_term)
    )
    examples = entry.get("examples") or []
    example = examples[0] if examples else {}
    return {
        "has_material": True,
        "lang_code": lang_code,
        "kind": kind,  # "word" | "phrase" | "construction" | "rule"
        "term": display_term,
        "translation": entry_translation(entry).replace(";", ","),
        "example_text": str(example.get("text") or "").strip(),
        "example_translation": str(example.get("translation") or "").strip(),
        "note": str(entry.get("breakdown") or "").strip(),
        "focus": _daily_focus_text(entry, kind),
        "progress": progress,
    }


def warm_home_cache(cid):
    """Фиксирует материал дня; AI и сеть для этого не используются."""
    select_daily_material(cid)
    return True


def reset_daily_material_cache(cid):
    """Сбрасывает материал дня после ручной нормализации словаря."""
    _DAILY_MATERIAL_CACHE.pop(str(cid), None)
    profile = store.get_profile(cid)
    if profile.pop("learning_daily_material", None) is not None:
        store.set_profile(cid, profile)


# ================= ЕДИНЫЙ ТРЕНАЖЁР =================
# Один режим "Тренажёр": сам выбирает материал, формат задания и сложность
# (см. docs/word-trainer.md, spec-learning-rework). Прогресс/уровни/интервалы
# считает srs.py — этот модуль только оркестрирует UI и очередь.

_ALL_EXERCISES = trainer_engine.ALL_EXERCISES

_TRAINER_PHRASE_CORRECTIONS = {
    "waar wacht je op": {
        "term": "Waar wacht je op?",
        "translation": "Что ты ждёшь?",
        "english": "What are you waiting for?",
        "bad_translation": "На что ты ждешь",
        "unneeded_preposition": "на",
    },
}


def _train_full_entries(cid, language):
    """Полные записи словаря нужного языка с переводом — материал для тренировки."""
    return DictionaryRepository(cid).training_entries(_code(language))


async def send_train_lang_select(bot, cid):
    language = active_language(cid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️ {_language_display(language)}", callback_data=f"a_train_{_code(language)}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = learning_ui.train_lang_select()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
def build_progress_screen(cid):
    """Данные экрана прогресса — доля самостоятельных ответов без подсказок
    важнее процента правильных ответов в quiz (см. docs/word-trainer.md,
    'Экран прогресса'). Только чтение уже посчитанного SRS-состояния, без AI."""
    language = active_language(cid)
    lang_code = _code(language)
    entries = _train_full_entries(cid, language)
    total = len(entries)
    states = [srs.normalize_state(entry) for entry in entries]
    confident = sum(1 for state in states if state["srs_level"] >= 4)
    due_count = sum(1 for state in states if srs.is_due(state))

    no_hint_answers = 0
    total_answers = 0
    by_exercise_ok = {}
    by_exercise_total = {}
    for state in states:
        for h in state["srs_history"]:
            total_answers += 1
            quality = h.get("result", "")
            ex_type = h.get("exercise_type", "")
            if quality in ("recalled_free", "used_in_sentence", "confident_no_hint"):
                no_hint_answers += 1
            by_exercise_total[ex_type] = by_exercise_total.get(ex_type, 0) + 1
            if quality not in ("not_remembered",):
                by_exercise_ok[ex_type] = by_exercise_ok.get(ex_type, 0) + 1

    no_hint_pct = round(100 * no_hint_answers / total_answers) if total_answers else 0
    strongest = weakest = ""
    if by_exercise_total:
        rates = {
            k: by_exercise_ok.get(k, 0) / v
            for k, v in by_exercise_total.items() if v >= 3
        }
        if rates:
            strongest = _EXERCISE_LABELS.get(max(rates, key=rates.get), "")
            weakest = _EXERCISE_LABELS.get(min(rates, key=rates.get), "")

    return {
        "lang_code": lang_code,
        "lang_title": "Английский" if lang_code == "en" else "Нидерландский",
        "total": total,
        "confident": confident,
        "due_count": due_count,
        "strongest": strongest,
        "weakest": weakest,
        "no_hint_pct": no_hint_pct,
    }


_EXERCISE_LABELS = {
    EXERCISE_CHOOSE_TRANSLATION: "перевод и понимание",
    EXERCISE_RECALL_FREE: "самостоятельное вспоминание",
    EXERCISE_BUILD_SENTENCE: "порядок слов в предложении",
    EXERCISE_FIND_ERROR: "поиск ошибок",
    EXERCISE_CHOOSE_NATURAL: "естественность формулировок",
    EXERCISE_FILL_GAP: "грамматику в контексте",
    EXERCISE_TRANSLATE_CONTEXT: "перевод в контексте",
    EXERCISE_CHOOSE_REACTION: "реакции в разговоре",
    EXERCISE_CONTINUE_DIALOGUE: "поддержание диалога",
}


async def send_progress(bot, cid):
    data = build_progress_screen(cid)
    msg = learning_ui.progress_screen(data)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m_learn"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ]])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
