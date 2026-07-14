import logging
import random
import re
from datetime import datetime
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
import config
from cleanup import open_cleanup, send_cleanup, handle_cleanup  # noqa: F401

_HERE = Path(__file__).parent
import store
import ai
import verify
import secure
from ui import dictionary as dict_ui
from ui import learning as learning_ui

_log = logging.getLogger(__name__)

LEVELS = ["simple", "medium", "hard"]
LEVEL_LABELS = {"simple": "Простой", "medium": "Средний", "hard": "Сложный"}

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

def _level_label(level):
    return LEVEL_LABELS.get(level, "Средний")


_DAILY_MATERIAL_CACHE = {}  # cid -> {"date": iso, "entry": dict, "lang": code}


def daily_material_type(entry):
    """'rule' — устойчивая конструкция, 'phrase' — многословный term без
    конструкции, 'word' — одно слово. Определяет заголовок карточки материала
    дня ("Слово дня"/"Фраза дня"/"Правило дня")."""
    if str(entry.get("construction") or "").strip():
        return "rule"
    term = _entry_term(entry)
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

    words = _ensure_dict(cid)
    pool = [w for w in words if _entry_term(w) and _entry_translation(w) and _dict_lang(w) == lang]
    if not pool:
        _DAILY_MATERIAL_CACHE[str(cid)] = {"date": today, "lang": lang, "entry": None}
        return None

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
        if _dict_lang(w) == lang and _entry_term(w) == _entry_term(entry):
            words[idx] = entry
            store.set_list(config.DICT_KEY, cid, words)
            break

    _DAILY_MATERIAL_CACHE[str(cid)] = {"date": today, "lang": lang, "entry": entry}
    return entry


def _daily_focus_text(entry):
    """'Сегодня в фокусе' на главном экране — вытекает из SRS-уровня материала
    дня (0-1: узнать перевод; 2-3: вспомнить без вариантов; 4-5: применить
    самостоятельно), без AI-вызова — правило по уже посчитанному уровню."""
    level = int(entry.get("srs_level") or 0)
    if level <= 1:
        return "узнать перевод и запомнить пример."
    if level <= 3:
        return "вспомнить слово без вариантов и применить его в предложении."
    return "использовать это в предложении самостоятельно, без подсказок."


def build_learning_home(cid):
    """Данные для главного экрана раздела 'Обучение': материал дня + короткий
    фокус тренировки. UI (ui/menu.py) только рендерит эти поля, не читает
    store и не выбирает материал сам — см. §8 CLAUDE.md."""
    entry = select_daily_material(cid)
    lang_code = _active_language_code(cid)
    if not entry:
        return {"has_material": False, "lang_code": lang_code}
    kind = daily_material_type(entry)
    examples = entry.get("examples") or []
    example = examples[0] if examples else {}
    return {
        "has_material": True,
        "lang_code": lang_code,
        "kind": kind,  # "word" | "phrase" | "rule"
        "term": _cap(_entry_term(entry)),
        "translation": _entry_translation(entry).replace(";", ","),
        "example_text": str(example.get("text") or "").strip(),
        "example_translation": str(example.get("translation") or "").strip(),
        "note": str(entry.get("breakdown") or "").strip(),
        "focus": _daily_focus_text(entry),
    }


# ================= ЕДИНЫЙ ТРЕНАЖЁР =================
# Один режим "Тренажёр": сам выбирает материал, формат задания и сложность
# (см. docs/word-trainer.md, spec-learning-rework). Прогресс/уровни/интервалы
# считает srs.py — этот модуль только оркестрирует UI и очередь.

EXERCISE_CHOOSE_TRANSLATION = "choose_translation"
EXERCISE_RECALL_FREE = "recall_free"
EXERCISE_BUILD_SENTENCE = "build_sentence"
EXERCISE_FIND_ERROR = "find_error"
EXERCISE_CHOOSE_NATURAL = "choose_natural"
EXERCISE_FILL_GAP = "fill_gap"
EXERCISE_TRANSLATE_CONTEXT = "translate_context"
EXERCISE_CHOOSE_REACTION = "choose_reaction"
EXERCISE_CONTINUE_DIALOGUE = "continue_dialogue"

_ALL_EXERCISES = (
    EXERCISE_CHOOSE_TRANSLATION, EXERCISE_RECALL_FREE, EXERCISE_BUILD_SENTENCE,
    EXERCISE_FIND_ERROR, EXERCISE_CHOOSE_NATURAL, EXERCISE_FILL_GAP,
    EXERCISE_TRANSLATE_CONTEXT, EXERCISE_CHOOSE_REACTION, EXERCISE_CONTINUE_DIALOGUE,
)

_QUEUE_SIZE = 12  # заданий на одну тренировку


def _strip_article(lang, term):
    term = (term or "").strip()
    if lang == "nl":
        return re.sub(r"^(de|het|een)\s+", "", term, flags=re.I)
    if lang == "en":
        return re.sub(r"^(to|the|a|an)\s+", "", term, flags=re.I)
    return term


def _blank_from_example(term, example_text):
    """Вставляет пропуск на месте term в сохранённом примере — регистронезависимо.
    Сначала пробует полный term (с артиклем), затем term без артикля — чтобы
    сохранить в blank_phrase ровно то, что реально стоит в примере. Возвращает
    (blank_phrase, correct) или ("", "") если term не найден в тексте буквально."""
    if not example_text:
        return "", ""
    term = (term or "").strip()
    term_bare = _strip_article("nl", _strip_article("en", term)).strip()
    for candidate in (term, term_bare):
        if not candidate:
            continue
        pattern = re.compile(re.escape(candidate), re.I)
        m = pattern.search(example_text)
        if m:
            return pattern.sub("____", example_text, count=1), m.group(0)
    return "", ""


_UI_PLACEHOLDER_TOKENS = {"todo", "n/a", "none", "null"}


def _phrase_tokens(text):
    return [m.group(0).lower() for m in re.finditer(r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or ""), flags=re.UNICODE)]


def _normalize_phrase_for_compare(text):
    return " ".join(_phrase_tokens(text))


def _looks_like_ui_placeholder(text):
    value = str(text or "").strip()
    if not value:
        return False
    tokens = set(_phrase_tokens(value.lower()))
    return bool(tokens & _UI_PLACEHOLDER_TOKENS)


def _phrase_option_is_junk(option):
    option = str(option or "").strip()
    if not option:
        return True
    if _looks_like_ui_placeholder(option):
        return True
    if "____" in option or len(option) > 40:
        return True
    return False


def _clean_phrase_options(correct_answer, wrong, needed=3):
    clean_wrong = []
    seen = {str(correct_answer).lower()}
    for item in wrong:
        item = str(item).strip()
        if item and item.lower() not in seen and not _phrase_option_is_junk(item):
            clean_wrong.append(item)
            seen.add(item.lower())
        if len(clean_wrong) >= needed:
            break
    return clean_wrong


def _dict_distractors(entry, correct, other_entries, needed=3):
    """Дистракторы для wrong-варианта — термины других слов того же словаря,
    без LLM (см. §11 CLAUDE.md — LLM не нужен для выбора из фиксированных списков)."""
    term_self = _entry_term(entry)
    pool = [
        _cap(_entry_term(w)) for w in other_entries
        if _entry_term(w) != term_self and _entry_term(w)
    ]
    random.shuffle(pool)
    return _clean_phrase_options(correct, pool, needed=needed)


def _train_full_entries(cid, language):
    """Полные записи словаря нужного языка с переводом — материал для тренировки."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) != code:
            continue
        if _entry_term(w) and _entry_translation(w):
            out.append(w)
    return out


def _train_back_target(language=None):
    code = _code(language) if language else None
    return "m_learn"


# ---------- построение очереди тренировки ----------

def build_training_queue(cid, language):
    """Очередь материалов на одну тренировку: 60% на повторение (due),
    20% сложное/недавние ошибки, 20% новое — доли плавают, если материала
    одного типа не хватает (см. docs/word-trainer.md, 'Как выбирать задания').
    Каждый элемент очереди — {"entry": словарная запись, "exercise_type": ...}.
    Формат выбирается сразу при построении очереди (не на лету), чтобы не
    повторять один формат подряд для одного материала (srs_last_exercise_type)."""
    import srs
    entries = _train_full_entries(cid, language)
    if not entries:
        return []
    today = datetime.now(config.TZ).date()

    due = [e for e in entries if srs.is_due(_entry_srs_state(e), today)]
    mistakes = [e for e in due if int(e.get("srs_level") or 0) <= 1]
    due_ok = [e for e in due if e not in mistakes]
    new_material = [e for e in entries if not e.get("srs_history")]
    new_material = [e for e in new_material if e not in due]

    target_due = round(_QUEUE_SIZE * 0.6)
    target_mistakes = round(_QUEUE_SIZE * 0.2)
    target_new = _QUEUE_SIZE - target_due - target_mistakes

    # Если ошибок накопилось больше обычного — их доля растёт за счёт "нового"
    # материала (см. ТЗ: "если накопилось много ошибок, доля повторения
    # должна временно увеличиваться").
    if len(mistakes) > target_mistakes:
        extra = min(len(mistakes) - target_mistakes, target_new)
        target_mistakes += extra
        target_new -= extra

    picked = []

    def _take(pool, n):
        random.shuffle(pool)
        chunk = pool[:n]
        picked.extend(chunk)
        return chunk

    _take(mistakes, target_mistakes)
    _take(due_ok, target_due)
    _take(new_material, target_new)

    # Очередь не набралась (маленький словарь) — добираем чем есть, без дублей.
    if len(picked) < _QUEUE_SIZE:
        picked_terms = {_entry_term(e) for e in picked}
        rest = [e for e in entries if _entry_term(e) not in picked_terms]
        random.shuffle(rest)
        picked.extend(rest[:_QUEUE_SIZE - len(picked)])

    random.shuffle(picked)
    return [{"entry": e, "exercise_type": select_exercise_type(e)} for e in picked]


def select_exercise_type(entry):
    """Формат задания по srs_level материала и типу материала (слово/фраза/
    конструкция/ситуация) — не повторяет srs_last_exercise_type, если есть
    из чего выбрать другой (см. docs/word-trainer.md, таблица уровней)."""
    level = int(entry.get("srs_level") or 0)
    kind = daily_material_type(entry)
    last = entry.get("srs_last_exercise_type") or ""

    if level <= 1:
        candidates = [EXERCISE_CHOOSE_TRANSLATION]
        if entry.get("examples"):
            candidates.append(EXERCISE_FILL_GAP)
    elif level <= 3:
        candidates = [EXERCISE_RECALL_FREE, EXERCISE_FILL_GAP]
        if kind == "phrase" and len(_phrase_tokens(_entry_term(entry))) >= 3:
            candidates.append(EXERCISE_BUILD_SENTENCE)
        if entry.get("situation_type"):
            candidates.append(EXERCISE_CHOOSE_REACTION)
        if kind == "rule":
            candidates.append(EXERCISE_FIND_ERROR)
    else:
        candidates = [EXERCISE_TRANSLATE_CONTEXT, EXERCISE_RECALL_FREE]
        if entry.get("situation_type"):
            candidates.append(EXERCISE_CONTINUE_DIALOGUE)
        if kind == "phrase":
            candidates.append(EXERCISE_CHOOSE_NATURAL)

    filtered = [c for c in candidates if c != last] or candidates
    return random.choice(filtered)


# ---------- сборка данных задания (без Telegram-специфики) ----------

def _example_of(entry):
    examples = entry.get("examples") or []
    return examples[0] if examples else {}


def _build_choose_translation(entry, other_entries):
    correct = _entry_translation(entry).split(";")[0].split(",")[0].strip()
    wrong_pool = [
        _entry_translation(w).split(";")[0].split(",")[0].strip()
        for w in other_entries if _entry_term(w) != _entry_term(entry)
    ]
    wrong = _clean_phrase_options(correct, wrong_pool, needed=3)
    if len(wrong) < 3:
        return None
    return {"term": _cap(_entry_term(entry)), "correct": correct, "wrong": wrong}


def _build_recall_free(entry):
    ru = _entry_translation(entry).split(";")[0].split(",")[0].strip()
    correct = _cap(_entry_term(entry))
    hint = entry.get("construction") or entry.get("pos") or ""
    return {"ru": ru, "correct": correct, "hint": hint}


def _build_build_sentence(entry):
    correct = _cap(_entry_term(entry))
    tokens = _entry_term(entry).split()
    if len(tokens) < 3:
        return None
    shuffled = list(tokens)
    random.shuffle(shuffled)
    ru = _entry_translation(entry).split(";")[0].split(",")[0].strip()
    return {"ru": ru, "correct": correct, "tokens": tokens, "shuffled": shuffled}


_FIND_ERROR_DROPPABLE = {"de", "het", "een", "the", "a", "an"}


def _build_find_error(entry):
    """Реально ломает предложение (убирает артикль/служебное слово), а не
    указывает случайный индекс в корректном тексте — иначе задание не имеет
    решения (баг, найденный при ручной проверке): предложение без реальной
    ошибки не может честно проверяться на 'найди ошибку'."""
    example = _example_of(entry)
    text = str(example.get("text") or "").strip()
    if not text:
        return None
    tokens = text.split()
    if len(tokens) < 3:
        return None
    droppable_idx = [i for i, t in enumerate(tokens) if t.lower().strip(".,!?") in _FIND_ERROR_DROPPABLE]
    if not droppable_idx:
        return None
    drop_idx = random.choice(droppable_idx)
    broken_tokens = tokens[:drop_idx] + tokens[drop_idx + 1:]
    correct_text = text
    ru = str(example.get("translation") or _entry_translation(entry)).split(";")[0].strip()
    # Индекс "ошибки" в укороченном списке — слово ПОСЛЕ пропущенного артикля,
    # так пользователь указывает на место, где артикля не хватает.
    error_idx = min(drop_idx, len(broken_tokens) - 1)
    return {"tokens": broken_tokens, "broken_idx": error_idx, "correct_text": correct_text, "ru": ru,
            "note": entry.get("breakdown") or f"пропущен артикль «{tokens[drop_idx]}»"}


def _build_choose_natural(entry, other_entries):
    correct = _cap(_entry_term(entry))
    ru = _entry_translation(entry).split(";")[0].split(",")[0].strip()
    wrong_pool = [_cap(_entry_term(w)) for w in other_entries if _entry_term(w) != _entry_term(entry)]
    wrong = _clean_phrase_options(correct, wrong_pool, needed=3)
    if len(wrong) < 3:
        return None
    return {"ru": ru, "correct": correct, "wrong": wrong}


def _build_fill_gap(entry, other_entries):
    example = _example_of(entry)
    blank_phrase, correct = _blank_from_example(_entry_term(entry), str(example.get("text") or ""))
    if not blank_phrase:
        return None
    wrong = _dict_distractors(entry, correct, other_entries, needed=3)
    if len(wrong) < 3:
        return None
    return {"blank_phrase": blank_phrase, "correct": correct, "wrong": wrong,
            "ru": str(example.get("translation") or _entry_translation(entry)).strip(),
            "note": entry.get("breakdown") or ""}


def _build_translate_context(entry):
    ru = _entry_translation(entry).split(";")[0].split(",")[0].strip()
    correct = _cap(_entry_term(entry))
    alt = list(entry.get("alt_translations") or [])
    situation = entry.get("situation_type") or ""
    return {"ru": ru, "correct": correct, "alt": alt, "situation": situation}


async def _generate_situation_line(entry, language):
    """Одна реплика собеседника + правильная реакция термином записи — общая
    AI-генерация для choose_reaction/continue_dialogue (см. ТЗ: 'создание
    похожей жизненной ситуации' разрешено §11 CLAUDE.md). Кэшируется в ai.py
    по input_hash, поэтому повтор той же записи не тратит лимит повторно."""
    term = _entry_term(entry)
    prompt = f"""Ты методист разговорной практики для языка: {language}.
Целевое слово/фраза: «{term}» — {_entry_translation(entry)}.

Придумай ОДНУ короткую реплику собеседника на {language}, в ответ на которую
естественно употребить именно «{term}» (как реакцию, согласие, отказ или ответ
по смыслу — в зависимости от того, что это за слово/фраза).

Верни JSON: {{"line": "реплика собеседника на {language}", "line_ru": "перевод реплики"}}"""
    try:
        d = await ai.allm_json(prompt, 300, tier="cheap", module="learning_trainer")
        line = str(d.get("line") or "").strip()
        line_ru = str(d.get("line_ru") or "").strip()
    except Exception:
        line, line_ru = "", ""
    if not line:
        return None
    return {"line": line, "line_ru": line_ru}


async def _build_choose_reaction(entry, other_entries, language):
    correct = _cap(_entry_term(entry))
    wrong_pool = [_cap(_entry_term(w)) for w in other_entries if _entry_term(w) != _entry_term(entry)]
    wrong = _clean_phrase_options(correct, wrong_pool, needed=3)
    if len(wrong) < 3:
        return None
    situation = await _generate_situation_line(entry, language)
    if not situation:
        return None
    return {"situation": situation["line"], "situation_ru": situation["line_ru"],
            "correct": correct, "wrong": wrong}


async def _build_continue_dialogue(entry, other_entries, language):
    correct = _cap(_entry_term(entry))
    wrong_pool = [_cap(_entry_term(w)) for w in other_entries if _entry_term(w) != _entry_term(entry)]
    wrong = _clean_phrase_options(correct, wrong_pool, needed=3)
    if len(wrong) < 3:
        return None
    situation = await _generate_situation_line(entry, language)
    if not situation:
        return None
    return {"line": situation["line"], "line_ru": situation["line_ru"], "correct": correct, "wrong": wrong}


async def build_exercise_data(cid, item):
    """Данные конкретного задания по элементу очереди {"entry", "exercise_type"}.
    Возвращает dict со всем нужным для render_exercise/check_user_answer, или
    None если для этой записи формат собрать не удалось (вызывающий код должен
    пропустить задание и взять следующее из очереди, а не показывать пустоту)."""
    entry = item["entry"]
    ex_type = item["exercise_type"]
    language = _language_for_code(_dict_lang(entry))
    other_entries = _train_full_entries(cid, entry.get("lang") or "nl")
    data = None
    if ex_type == EXERCISE_CHOOSE_TRANSLATION:
        data = _build_choose_translation(entry, other_entries)
    elif ex_type == EXERCISE_RECALL_FREE:
        data = _build_recall_free(entry)
    elif ex_type == EXERCISE_BUILD_SENTENCE:
        data = _build_build_sentence(entry)
    elif ex_type == EXERCISE_FIND_ERROR:
        data = _build_find_error(entry)
    elif ex_type == EXERCISE_CHOOSE_NATURAL:
        data = _build_choose_natural(entry, other_entries)
    elif ex_type == EXERCISE_FILL_GAP:
        data = _build_fill_gap(entry, other_entries)
    elif ex_type == EXERCISE_TRANSLATE_CONTEXT:
        data = _build_translate_context(entry)
    elif ex_type == EXERCISE_CHOOSE_REACTION:
        data = await _build_choose_reaction(entry, other_entries, language)
    elif ex_type == EXERCISE_CONTINUE_DIALOGUE:
        data = await _build_continue_dialogue(entry, other_entries, language)
    if data is None:
        return None
    data["exercise_type"] = ex_type
    data["term"] = _entry_term(entry)
    data["lang"] = _dict_lang(entry)
    return data


# ---------- проверка ответа ----------

def _fuzzy_match(a, b):
    a = _normalize_phrase_for_compare(a)
    b = _normalize_phrase_for_compare(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # Небольшие опечатки допустимы (см. ТЗ формат 2) — сравниваем по расстоянию
    # Левенштейна, без внешних зависимостей.
    if abs(len(a) - len(b)) > max(2, len(b) // 4):
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    distance = prev[len(b)]
    return distance <= max(1, len(b) // 6)


def check_choice_answer(data, chosen_idx, options):
    """Для форматов с фиксированными вариантами (choose_translation/choose_natural/
    fill_gap/choose_reaction/continue_dialogue): idx варианта -> (is_correct, quality)."""
    import srs
    if chosen_idx < 0 or chosen_idx >= len(options):
        return False, srs.NOT_REMEMBERED
    is_correct = str(options[chosen_idx]).strip().lower() == str(data["correct"]).strip().lower()
    return is_correct, (srs.CHOSE_OPTION if is_correct else srs.NOT_REMEMBERED)


def check_free_text_answer(data, user_text, used_hint=False):
    """Для форматов со свободным вводом (recall_free/translate_context/find_error
    на высоком уровне): текст пользователя -> (is_correct, quality)."""
    import srs
    correct_variants = [data["correct"]] + list(data.get("alt") or [])
    is_correct = any(_fuzzy_match(user_text, v) for v in correct_variants)
    if not is_correct:
        return False, srs.NOT_REMEMBERED
    if used_hint:
        return True, srs.HINT_USED
    return True, srs.RECALLED_FREE


def check_build_sentence_answer(data, chosen_tokens):
    """Собранное предложение из токенов — допускает грамматически другой
    порядок, если это тот же набор слов, что и в эталоне (ТЗ: 'проверять не
    только один заранее заданный порядок')."""
    import srs
    correct_tokens = data["tokens"]
    if sorted(t.lower() for t in chosen_tokens) != sorted(t.lower() for t in correct_tokens):
        return False, srs.NOT_REMEMBERED
    is_exact_order = [t.lower() for t in chosen_tokens] == [t.lower() for t in correct_tokens]
    return True, (srs.RECALLED_FREE if is_exact_order else srs.USED_IN_SENTENCE)


# ---------- session / точка входа ----------

def _new_session():
    return {"consolidated": [], "returning": [], "no_hint_count": 0, "total": 0}


async def train_start(bot, cid, language, mode=None):
    """Единый тренажёр: одна очередь на тренировку, сам решает формат и
    сложность (см. docs/word-trainer.md). mode сохранён в сигнатуре для
    обратной совместимости вызовов — режимов больше нет, игнорируется."""
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    lang_code = _code(language)
    if not _train_full_entries(cid, language):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Открыть словарь", callback_data=f"a_dictlang_{lang_code}_from_menu")]])
        await bot.send_message(chat_id=cid,
            text=f"{_flag(language)} В словаре нет слов или фраз с переводом. Добавь записи через словарь.",
            reply_markup=kb)
        return
    await migrate_dict_entries_for_srs(cid, lang_code)
    queue = build_training_queue(cid, language)
    if not queue:
        await bot.send_message(chat_id=cid, text="Не получилось собрать тренировку. Попробуй ещё раз.")
        return
    store.train_state[str(cid)] = {
        "lang": language, "queue": queue, "queue_idx": 0,
        "session": _new_session(), "current": None,
    }
    await _render_next_exercise(bot, cid)


async def _render_next_exercise(bot, cid):
    """Берёт следующий элемент очереди, пытается собрать по нему задание —
    если не вышло (недостаточно данных для формата), пропускает и пробует
    следующий, а не показывает пустой экран. Очередь исчерпана -> итог."""
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    queue = st["queue"]
    while st["queue_idx"] < len(queue):
        item = queue[st["queue_idx"]]
        st["queue_idx"] += 1
        data = await build_exercise_data(cid, item)
        if data is None:
            continue
        data["hint_shown"] = False
        st["current"] = data
        await _send_exercise(bot, cid, data)
        return
    await _finish_training(bot, cid, st)


def _ex_kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])


async def _send_exercise(bot, cid, data):
    ex_type = data["exercise_type"]
    if ex_type == EXERCISE_CHOOSE_TRANSLATION:
        await _send_choose_translation(bot, cid, data)
    elif ex_type == EXERCISE_RECALL_FREE:
        await _send_recall_free(bot, cid, data)
    elif ex_type == EXERCISE_BUILD_SENTENCE:
        await _send_build_sentence(bot, cid, data)
    elif ex_type == EXERCISE_FIND_ERROR:
        await _send_find_error(bot, cid, data)
    elif ex_type == EXERCISE_CHOOSE_NATURAL:
        await _send_choose_options(bot, cid, data, learning_ui.exercise_choose_natural)
    elif ex_type == EXERCISE_FILL_GAP:
        await _send_choose_options(bot, cid, data, learning_ui.exercise_fill_gap)
    elif ex_type == EXERCISE_TRANSLATE_CONTEXT:
        await _send_translate_context(bot, cid, data)
    elif ex_type == EXERCISE_CHOOSE_REACTION:
        await _send_choose_options(bot, cid, data, learning_ui.exercise_choose_reaction)
    elif ex_type == EXERCISE_CONTINUE_DIALOGUE:
        await _send_choose_options(bot, cid, data, learning_ui.exercise_continue_dialogue)


def _options_for(data):
    options = [data["correct"]] + list(data.get("wrong") or [])
    random.shuffle(options)
    return options


async def _send_choose_translation(bot, cid, data):
    """Единственный формат на native Telegram quiz poll (см. spec-learning-rework:
    'Только формат Выбрать перевод использует нативный quiz poll') — авто-проверка
    от Telegram, ответ приходит в handle_train_poll_answer."""
    options = _options_for(data)
    data["_options"] = options
    correct_idx = options.index(data["correct"])
    msg = await bot.send_poll(
        chat_id=cid,
        question=f"Что значит: {data['term']}?",
        options=[str(o)[:100] for o in options[:10]],
        type="quiz",
        correct_option_id=correct_idx,
        # Telegram присылает PollAnswer боту только для
        # неанонимных опросов. Без этого тренажёр не узнает,
        # что пользователь ответил, и не может показать следующий шаг.
        is_anonymous=False,
    )
    if getattr(msg, "poll", None):
        store.train_polls[msg.poll.id] = str(cid)


async def handle_train_poll_answer(bot, poll_answer):
    cid = store.train_polls.pop(poll_answer.poll_id, None)
    if not cid:
        return
    option_ids = list(getattr(poll_answer, "option_ids", []) or [])
    if not option_ids:
        return
    await handle_pick(bot, cid, int(option_ids[0]))


async def _send_choose_options(bot, cid, data, render_fn):
    options = _options_for(data)
    data["_options"] = options
    msg = render_fn(data)
    rows = [[(opt, f"ex_pick_{i}")] for i, opt in enumerate(options)]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_ex_kb(rows))


async def _send_recall_free(bot, cid, data):
    msg = learning_ui.exercise_recall_free(data, hint_shown=data.get("hint_shown"))
    rows = []
    if data.get("hint") and not data.get("hint_shown"):
        rows.append([("💡 Подсказка", "ex_hint"), ("⌨️ Ответить", "ex_answer")])
    else:
        rows.append([("⌨️ Ответить", "ex_answer")])
    rows.append([("Не помню", "ex_giveup")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_ex_kb(rows))


async def _send_translate_context(bot, cid, data):
    msg = learning_ui.exercise_translate_context(data)
    rows = [[("⌨️ Ответить", "ex_answer")], [("Показать ответ", "ex_giveup")]]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_ex_kb(rows))


async def _send_build_sentence(bot, cid, data):
    data.setdefault("_picked", [])
    msg = learning_ui.exercise_build_sentence(data)
    remaining = [t for i, t in enumerate(data["shuffled"]) if i not in data.get("_picked_idx", [])]
    rows = [[(t, f"ex_tok_{data['shuffled'].index(t)}")] for t in remaining[:6]]
    if data.get("_picked"):
        rows.append([("↩️ Сбросить", "ex_tok_reset")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_ex_kb(rows))


async def _send_find_error(bot, cid, data):
    msg = learning_ui.exercise_find_error(data)
    rows = [[(t, f"ex_word_{i}")] for i, t in enumerate(data["tokens"][:6])]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_ex_kb(rows))


# ---------- обработка ответов ----------

async def _apply_result(bot, cid, st, is_correct, quality, feedback_text, feedback_entities=None):
    """Общая финализация ответа: SRS-запись, session-статистика, reinsert при
    ошибке, кнопка 'Дальше'."""
    import srs
    data = st["current"]
    if data.get("_answered"):
        return
    data["_answered"] = True
    entry_term = data["term"]
    _update_entry_srs(cid, data["lang"], entry_term, data["exercise_type"], quality)

    session = st["session"]
    session["total"] += 1
    if quality in (srs.RECALLED_FREE, srs.USED_IN_SENTENCE, srs.CONFIDENT_NO_HINT):
        session["no_hint_count"] += 1
    if is_correct and quality in (srs.USED_IN_SENTENCE, srs.CONFIDENT_NO_HINT):
        session["consolidated"].append(entry_term)
    if not is_correct:
        session["returning"].append(entry_term)
        reinsert_failed_material(st, data)

    kb = _ex_kb([[('Следующее задание', "ex_next")]])
    await bot.send_message(chat_id=cid, text=feedback_text, entities=feedback_entities, reply_markup=kb)


def _update_entry_srs(cid, lang, term, exercise_type, quality):
    import srs
    words = store.get_list(config.DICT_KEY, cid)
    for idx, w in enumerate(words):
        if _dict_lang(w) == lang and _entry_term(w) == term:
            state = _entry_srs_state(w)
            updated = srs.record_answer(state, exercise_type, quality)
            words[idx] = {**w, **updated}
            store.set_list(config.DICT_KEY, cid, words)
            return


def reinsert_failed_material(st, data):
    """Материал, на котором ошиблись, возвращается ПОЗЖЕ В ЭТОЙ ЖЕ тренировке
    другим форматом — без ручной кнопки 'Повторить' (см. ТЗ 'Поведение после
    ошибки'). Вставляется на 3-5 позиций вперёд в очередь, не сразу следующим."""
    entry = None
    for item in st["queue"]:
        if _entry_term(item["entry"]) == data["term"]:
            entry = item["entry"]
            break
    if entry is None:
        return
    used_types = {data["exercise_type"]}
    other_types = [t for t in _ALL_EXERCISES if t not in used_types]
    next_type = random.choice(other_types) if other_types else data["exercise_type"]
    insert_at = min(len(st["queue"]), st["queue_idx"] + random.randint(2, 4))
    st["queue"].insert(insert_at, {"entry": entry, "exercise_type": next_type})


async def handle_pick(bot, cid, idx):
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    data = st["current"]
    options = data.get("_options") or []
    is_correct, quality = check_choice_answer(data, idx, options)
    msg = learning_ui.exercise_result(data, is_correct, chosen=options[idx] if idx < len(options) else "")
    await _apply_result(bot, cid, st, is_correct, quality, msg.text, msg.entities)


async def handle_hint(bot, cid):
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        return
    st["current"]["hint_shown"] = True
    await _send_exercise(bot, cid, st["current"])


async def handle_answer_prompt(bot, cid):
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        return
    store.pending_input[str(cid)] = "trainer_answer"
    await bot.send_message(chat_id=cid, text="Напиши свой ответ следующим сообщением.")


async def handle_giveup(bot, cid):
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    import srs
    data = st["current"]
    msg = learning_ui.exercise_result(data, False, chosen="")
    await _apply_result(bot, cid, st, False, srs.NOT_REMEMBERED, msg.text, msg.entities)


async def handle_text_answer(bot, cid, text):
    """Свободный текстовый ответ — вызывается из общего текстового роутера,
    когда pending_input == 'trainer_answer'. Возвращает True, если сообщение
    было ответом тренажёра (роутер не должен обрабатывать его иначе)."""
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        return False
    store.pending_input.pop(str(cid), None)
    data = st["current"]
    used_hint = bool(data.get("hint_shown"))
    if data["exercise_type"] == EXERCISE_TRANSLATE_CONTEXT:
        is_correct, quality = await _check_translate_context_ai(data, text)
    else:
        is_correct, quality = check_free_text_answer(data, text, used_hint=used_hint)
    msg = learning_ui.exercise_result(data, is_correct, chosen=text)
    await _apply_result(bot, cid, st, is_correct, quality, msg.text, msg.entities)
    return True


async def _check_translate_context_ai(data, text):
    """Перевод в контексте принимает несколько формулировок — сверка смысла
    через AI (короткий вызов, разрешён §11 CLAUDE.md для разбора свободного
    текста), не только точное совпадение с эталоном."""
    import srs
    if check_free_text_answer(data, text)[0]:
        return True, srs.RECALLED_FREE
    prompt = (
        f"Ученик переводит на {('нидерландский' if data['lang'] == 'nl' else 'английский')}: "
        f"{secure.wrap_untrusted(data['ru'], 'фраза для перевода')}\n"
        f"Ответ ученика: {secure.wrap_untrusted(text, 'ответ ученика')}\n"
        f"Эталон: {data['correct']}\n"
        'Верни JSON: {"ok": true/false} — true, если смысл и грамматика ученика приемлемы, '
        "даже если формулировка отличается от эталона."
    )
    try:
        r = await ai.allm_json(prompt, 300, tier="cheap", module="learning_trainer")
        ok = bool(r.get("ok"))
    except Exception:
        ok = False
    return ok, (srs.RECALLED_FREE if ok else srs.NOT_REMEMBERED)


async def handle_token_pick(bot, cid, token_idx):
    """Для build_sentence/find_error — набор токена по индексу в data['shuffled']
    или data['tokens']."""
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        return
    data = st["current"]
    if data["exercise_type"] == EXERCISE_BUILD_SENTENCE:
        picked_idx = data.setdefault("_picked_idx", [])
        picked = data.setdefault("_picked", [])
        if token_idx not in picked_idx and token_idx < len(data["shuffled"]):
            picked_idx.append(token_idx)
            picked.append(data["shuffled"][token_idx])
        if len(picked) == len(data["tokens"]):
            is_correct, quality = check_build_sentence_answer(data, picked)
            msg = learning_ui.exercise_result(data, is_correct, chosen=" ".join(picked))
            await _apply_result(bot, cid, st, is_correct, quality, msg.text, msg.entities)
            return
        await _send_exercise(bot, cid, data)
    elif data["exercise_type"] == EXERCISE_FIND_ERROR:
        is_correct = token_idx == data["broken_idx"]
        import srs
        quality = srs.CHOSE_OPTION if is_correct else srs.NOT_REMEMBERED
        msg = learning_ui.exercise_result(data, is_correct, chosen=data["tokens"][token_idx])
        await _apply_result(bot, cid, st, is_correct, quality, msg.text, msg.entities)


async def handle_token_reset(bot, cid):
    st = store.train_state.get(str(cid))
    if not st or not st.get("current"):
        return
    data = st["current"]
    data["_picked"] = []
    data["_picked_idx"] = []
    await _send_exercise(bot, cid, data)


async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    if not st.get("current") or not st["current"].get("_answered"):
        return
    st["current"] = None
    await _render_next_exercise(bot, cid)


async def send_train_lang_select(bot, cid):
    language = active_language(cid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️ {_language_display(language)}", callback_data=f"a_train_{_code(language)}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])
    msg = learning_ui.train_lang_select()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def _finish_training(bot, cid, st):
    session = st["session"]
    store.train_state.pop(str(cid), None)
    msg = learning_ui.training_result(session)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m_learn"),
        InlineKeyboardButton("🏠 Меню", callback_data="m_menu"),
    ]])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def build_progress_screen(cid):
    """Данные экрана прогресса — доля самостоятельных ответов без подсказок
    важнее процента правильных ответов в quiz (см. docs/word-trainer.md,
    'Экран прогресса'). Только чтение уже посчитанного SRS-состояния, без AI."""
    language = active_language(cid)
    lang_code = _code(language)
    entries = _train_full_entries(cid, language)
    total = len(entries)
    confident = sum(1 for e in entries if int(e.get("srs_level") or 0) >= 4)
    due_count = sum(1 for e in entries if int(e.get("srs_level") or 0) <= 1)

    no_hint_answers = 0
    total_answers = 0
    by_exercise_ok = {}
    by_exercise_total = {}
    for e in entries:
        for h in (e.get("srs_history") or []):
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
        InlineKeyboardButton("🏠 Меню", callback_data="m_menu"),
    ]])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])

def _proverb_entities_card(flag, original, analogs=None, meaning="", examples=None, example_ru=""):
    msg = learning_ui.proverb_card(flag, original, analogs, meaning, examples, example_ru)
    return msg.text, msg.entities


_PROVERB_FALLBACKS = {
    "nl": [
        {
            "nl": "Dat is de druppel!",
            "en": "",
            "analogs": ["это последняя капля"],
            "type": "идиома",
            "meaning": "Когда мелкие неприятности копятся, и очередная мелочь окончательно добивает.",
            "example": "Eerst was mijn trein te laat, toen morste ik koffie over mijn shirt... En nu dit?! Dat is de druppel!",
            "example_ru": "Сначала поезд опоздал, потом я залил кофе рубашку... А теперь еще и это?! Ну всё, это последняя капля!",
        },
        {
            "nl": "Geen probleem.",
            "en": "",
            "analogs": ["без проблем"],
            "type": "разговорная фраза",
            "meaning": "Когда спокойно соглашаются помочь или показывают, что всё нормально.",
            "example": "Kun je me straks even bellen? Geen probleem.",
            "example_ru": "Можешь потом мне позвонить? Без проблем.",
        },
        {
            "nl": "Komt goed.",
            "en": "",
            "analogs": ["всё будет нормально"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят коротко успокоить человека или показать, что вопрос решится.",
            "example": "Maak je geen zorgen, ik regel het morgen. Komt goed.",
            "example_ru": "Не переживай, завтра я всё улажу. Всё будет нормально.",
        },
        {
            "nl": "Doe maar rustig aan.",
            "en": "",
            "analogs": ["не торопись"],
            "type": "разговорная фраза",
            "meaning": "Когда человеку предлагают не спешить и действовать спокойнее.",
            "example": "Je hoeft niet te rennen. Doe maar rustig aan.",
            "example_ru": "Тебе не нужно бежать. Не торопись.",
        },
        {
            "nl": "Ik zie wel.",
            "en": "",
            "analogs": ["посмотрим"],
            "type": "разговорная фраза",
            "meaning": "Когда пока не принимают решение и оставляют всё открытым.",
            "example": "Misschien ga ik mee, maar ik zie wel.",
            "example_ru": "Может, я пойду с вами, но пока посмотрим.",
        },
        {
            "nl": "Laat maar.",
            "en": "",
            "analogs": ["забей"],
            "type": "разговорная фраза",
            "meaning": "Когда больше не хотят объяснять, спорить или продолжать тему.",
            "example": "Nee, het lukt niet meer. Laat maar.",
            "example_ru": "Нет, уже не получится. Забей.",
        },
        {
            "nl": "Het valt mee.",
            "en": "",
            "analogs": ["всё не так плохо"],
            "type": "разговорная фраза",
            "meaning": "Когда ситуация оказалась легче или приятнее, чем ожидалось.",
            "example": "Ik dacht dat het examen moeilijk zou zijn, maar het valt mee.",
            "example_ru": "Я думал, экзамен будет сложным, но всё оказалось не так плохо.",
        },
        {
            "nl": "Ik ben er klaar mee.",
            "en": "",
            "analogs": ["с меня хватит"],
            "type": "разговорная фраза",
            "meaning": "Когда человек устал от ситуации и больше не хочет с ней мириться.",
            "example": "Elke week hetzelfde gedoe. Ik ben er klaar mee.",
            "example_ru": "Каждую неделю одна и та же возня. С меня хватит.",
        },
        {
            "nl": "Dat komt goed uit.",
            "en": "",
            "analogs": ["это как раз кстати"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то удобно совпало с планами или ситуацией.",
            "example": "Je bent morgen vrij? Dat komt goed uit.",
            "example_ru": "Ты завтра свободен? Это как раз кстати.",
        },
        {
            "nl": "Daar heb ik geen zin in.",
            "en": "",
            "analogs": ["мне совсем не хочется"],
            "type": "разговорная фраза",
            "meaning": "Когда прямо говорят, что нет желания что-то делать.",
            "example": "Nog een vergadering van twee uur? Daar heb ik geen zin in.",
            "example_ru": "Еще одно двухчасовое совещание? Мне совсем не хочется.",
        },
    ],
    "en": [
        {
            "nl": "",
            "en": "No worries.",
            "analogs": ["не переживай"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят показать, что всё нормально и проблемы нет.",
            "example": "Sorry, I forgot to reply yesterday. No worries.",
            "example_ru": "Прости, я вчера забыл ответить. Не переживай.",
        },
        {
            "nl": "",
            "en": "That makes sense.",
            "analogs": ["логично"],
            "type": "разговорная фраза",
            "meaning": "Когда объяснение звучит понятно и разумно.",
            "example": "You took the earlier train to avoid the rain? That makes sense.",
            "example_ru": "Ты сел на поезд пораньше, чтобы не попасть под дождь? Логично.",
        },
        {
            "nl": "",
            "en": "I'm in.",
            "analogs": ["я с вами"],
            "type": "разговорная фраза",
            "meaning": "Когда человек соглашается участвовать в плане.",
            "example": "Pizza after work? I'm in.",
            "example_ru": "Пицца после работы? Я с вами.",
        },
        {
            "nl": "",
            "en": "Fair enough.",
            "analogs": ["справедливо"],
            "type": "разговорная фраза",
            "meaning": "Когда принимают чужой аргумент, даже если не спорят дальше.",
            "example": "I need more time before I decide. Fair enough.",
            "example_ru": "Мне нужно больше времени, прежде чем решить. Справедливо.",
        },
        {
            "nl": "",
            "en": "It slipped my mind.",
            "analogs": ["я совсем забыл"],
            "type": "разговорная фраза",
            "meaning": "Когда человек забыл что-то не специально.",
            "example": "I meant to call you back, but it slipped my mind.",
            "example_ru": "Я собирался тебе перезвонить, но совсем забыл.",
        },
        {
            "nl": "",
            "en": "Give me a sec.",
            "analogs": ["дай секунду"],
            "type": "разговорная фраза",
            "meaning": "Когда просят немного подождать.",
            "example": "Give me a sec, I'm just finding the address.",
            "example_ru": "Дай секунду, я как раз ищу адрес.",
        },
        {
            "nl": "",
            "en": "That was close.",
            "analogs": ["чуть не случилось"],
            "type": "разговорная фраза",
            "meaning": "Когда неприятность почти произошла, но её удалось избежать.",
            "example": "The cup almost fell off the table. That was close.",
            "example_ru": "Чашка почти упала со стола. Чуть не случилось.",
        },
        {
            "nl": "",
            "en": "I'm not feeling it.",
            "analogs": ["мне не заходит"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то не нравится или не подходит по настроению.",
            "example": "Everyone likes this song, but I'm not feeling it.",
            "example_ru": "Всем нравится эта песня, но мне не заходит.",
        },
        {
            "nl": "",
            "en": "Let's call it a day.",
            "analogs": ["давай на сегодня закончим"],
            "type": "разговорная фраза",
            "meaning": "Когда предлагают закончить работу или дело на сегодня.",
            "example": "We've been fixing this for hours. Let's call it a day.",
            "example_ru": "Мы чиним это уже несколько часов. Давай на сегодня закончим.",
        },
        {
            "nl": "",
            "en": "I'm running late.",
            "analogs": ["я опаздываю"],
            "type": "разговорная фраза",
            "meaning": "Когда человек сообщает, что не успевает прийти вовремя.",
            "example": "I'm running late, but I'll be there in ten minutes.",
            "example_ru": "Я опаздываю, но буду через десять минут.",
        },
    ],
}


def _proverb_fallback(language):
    return dict(random.choice(_PROVERB_FALLBACKS[_code(language)]))


def _proverb_prompt(language):
    code = _code(language)
    target = "нидерландский" if code == "nl" else "английский"
    field = "nl" if code == "nl" else "en"
    other = "en" if code == "nl" else "nl"
    language_rule = (
        "Для Dutch выбирай выражения, которые реально звучат в Нидерландах. "
        if code == "nl"
        else "Для English выбирай живой разговорный аналог, а не буквальный перевод. "
    )
    return (
        "Ты эксперт по живой разговорной речи. "
        "Выдай одно естественное выражение для короткой карточки Telegram-бота. "
        "Это может быть идиома, фразовый глагол или частая разговорная фраза. "
        "Выражение должно реально использоваться в живой речи. "
        "Не придумывай кальки. Не используй редкие выражения. "
        "Русский перевод должен передавать смысл, а не буквальный перевод. "
        "Пример должен звучать как обычная жизненная ситуация. "
        "Пиши коротко. Без учебникового стиля. "
        f"Целевой язык карточки: {target}. Заполни поле {field}; поле {other} можно оставить пустым. "
        f"{language_rule}"
        "analogs[0] — главный русский перевод; другие варианты не нужны. "
        "meaning максимум 1 короткое предложение. "
        "example максимум 1-2 предложения. "
        "example_ru должен переводить смысл, а не слово в слово. "
        'JSON: {'
        '"nl":"NL expression or empty string",'
        '"en":"English expression or empty string",'
        '"analogs":["главный русский перевод"],'
        '"type":"идиома / разговорная фраза / фразовый глагол",'
        '"meaning":"когда так говорят, коротко по-русски",'
        '"example":"короткий пример на языке выражения",'
        '"example_ru":"естественный перевод примера на русский"'
        '}'
    )


def _split_proverb_example(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value or "").strip()
    if "→" not in value:
        return value, ""
    example, example_ru = value.split("→", 1)
    return example.strip(), example_ru.strip()


def _proverb_normalized(raw, language):
    raw = raw if isinstance(raw, dict) else {}
    code = _code(language)
    original = str(raw.get(code) or raw.get("original") or "").strip()
    analogs = raw.get("analogs") or raw.get("literal") or raw.get("ru") or []
    if isinstance(analogs, str):
        analogs = [analogs]
    analogs = [str(x).strip() for x in analogs if str(x).strip()][:1]
    example, parsed_example_ru = _split_proverb_example(raw.get("example") or raw.get("examples") or "")
    return {
        "original": _cap(original),
        "analogs": analogs,
        "meaning": str(raw.get("meaning") or "").strip(),
        "example": example,
        "example_ru": str(raw.get("example_ru") or parsed_example_ru or "").strip(),
    }


def _plain_for_match(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").lower(), flags=re.UNICODE)).strip()


def _example_mentions_original(original, example):
    original_plain = _plain_for_match(original)
    example_plain = _plain_for_match(example)
    if not original_plain or not example_plain:
        return False
    if original_plain in example_plain:
        return True
    tokens = [token for token in original_plain.split() if len(token) > 2]
    return bool(tokens) and all(token in example_plain.split() for token in tokens)


def _valid_proverb(data):
    return (
        bool(data.get("original"))
        and bool(data.get("analogs"))
        and bool(data.get("example"))
        and _example_mentions_original(data.get("original"), data.get("example"))
        and len(data.get("meaning") or "") <= 160
        and len(data.get("example") or "") <= 240
        and len(data.get("example_ru") or "") <= 240
    )


async def _generate_proverb(language):
    try:
        raw = await ai.allm_json(_proverb_prompt(language), 500, tier="cheap", route="gemini", module="learning")
    except Exception:
        raw = _proverb_fallback(language)
    data = _proverb_normalized(raw, language)
    if not _valid_proverb(data):
        data = _proverb_normalized(_proverb_fallback(language), language)
    return data


async def send_proverb(bot, cid, language=None, with_kb=True):
    language = language or active_language(cid)
    data = await _generate_proverb(language)
    txt, entities = _proverb_entities_card(
        _flag(language),
        data["original"],
        data["analogs"],
        _cap(data["meaning"]),
        data["example"],
        data["example_ru"],
    )
    reply_markup = _proverb_kb(_code(language)) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=reply_markup)


async def send_proverb_both(bot, cid, with_kb=True, language=None):
    """Compatibility wrapper: live language uses the single active learning language."""
    await send_proverb(bot, cid, language or active_language(cid), with_kb=with_kb)


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
- translation: 1-2 самых точных значения на русском, через "; ".
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


# ================= ИГРА-ДЕТЕКТИВ =================
# Служебные заголовки локализованы под язык игры (см. game_lang_kb/gamelang_*) —
# улики и служебный UI на одном языке, а не в смеси.
GAME_UI = {
    "русский": {
        "diff_q": "Выбери сложность:",
        "easy": "Лёгкая",
        "hard": "Тяжёлая",
        "title": "Игра-детектив",
        "who": "Кто это?",
        "hint": "💡 Подсказка",
        "reveal": "😞 Сдаюсь",
        "suspect": "Подозреваемый:",
        "found": "✅ Дело раскрыто!",
        "answer": "Ответ",
        "analyse": "Анализ:",
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
        "nohint": "Подсказок больше нет.",
        "wrong": "❌ Не то",
        "retry": "Ещё попытка - напиши ответ или возьми подсказку.",
    },
    "английский": {
        "diff_q": "Choose difficulty:",
        "easy": "Easy",
        "hard": "Hard",
        "title": "Detective Game",
        "who": "Who am I?",
        "hint": "💡 Hint",
        "reveal": "😞 Give up",
        "suspect": "Suspect:",
        "found": "✅ Case solved!",
        "answer": "Answer",
        "analyse": "Analysis:",
        "again": "✨ Again",
        "back": "⬅️ Back",
        "nohint": "No more hints.",
        "wrong": "❌ Not quite",
        "retry": "One more try - write the answer or take a hint.",
    },
    "нидерландский": {
        "diff_q": "Kies de moeilijkheidsgraad:",
        "easy": "Makkelijk",
        "hard": "Moeilijk",
        "title": "Detectivespel",
        "who": "Wie ben ik?",
        "hint": "💡 Hint",
        "reveal": "😞 Opgeven",
        "suspect": "Verdachte:",
        "found": "✅ Zaak opgelost!",
        "answer": "Antwoord",
        "analyse": "Analyse:",
        "again": "✨ Nog een",
        "back": "⬅️ Terug",
        "nohint": "Geen hints meer.",
        "wrong": "❌ Niet juist",
        "retry": "Nog een poging - schrijf het antwoord of neem een hint.",
    },
}

def _game_ui(lang=None):
    return GAME_UI.get(lang) or GAME_UI["русский"]


def _dot(s):
    """Гарантирует точку в конце предложения/подсказки."""
    s = (s or "").strip()
    if s and s[-1] not in ".!?…:":
        s += "."
    return s


def _game_norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", "", (s or "").lower())


def _game_same(a, b):
    a, b = _game_norm(a), _game_norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        return True
    if abs(len(a) - len(b)) <= 2:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 2
    return False


def _game_is_recent(d, recent):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    return any(_game_same(name, old) for name in names for old in (recent or []))


def _game_recent(cid):
    prof = store.get_profile(cid)
    persisted = prof.get("game_recent", []) if isinstance(prof, dict) else []
    mem = store.game_recent.get(str(cid), [])
    out = []
    for name in list(persisted) + list(mem):
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in out):
            out.append(name)
    out = out[-80:]
    store.game_recent[str(cid)] = out
    return out


def _set_game_recent(cid, rec):
    rec = [str(x).strip() for x in (rec or []) if str(x).strip()]
    rec = rec[-80:]
    store.game_recent[str(cid)] = rec
    prof = store.get_profile(cid)
    prof["game_recent"] = rec
    store.set_profile(cid, prof)


def _remember_game_answer(cid, d):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    rec = _game_recent(cid)
    for name in names:
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in rec):
            rec.append(name)
    _set_game_recent(cid, rec)


def game_data(clue_lang, difficulty, recent, attempt=0):
    if difficulty == "easy":
        subject = ("животное, птицу, рыбу, насекомое, фрукт, овощ, бытовой предмет или транспортное средство "
                   "(примеры: слон, орёл, акула, яблоко, велосипед, холодильник). "
                   "НЕ загадывай людей, знаменитостей или абстрактные понятия.")
        diff_desc = ("подсказки через внешность, размер, цвет, звук, поведение, где живёт или для чего используется. "
                     "Очень простые и конкретные, угадывается легко")
    elif difficulty == "hard":
        subject = "персонажа, историческую личность или абстрактное понятие"
        diff_desc = "редкие персонажи или абстрактные понятия, специфичная лексика, хитрые подсказки"
    else:
        subject = "известного персонажа или историческую личность (кино, наука, история, музыка, литература)"
        diff_desc = "исторические личности, актёры, более тонкие подсказки"
    avoid = ("Не загадывай ничего из этого списка и их переводы/синонимы: " + ", ".join(recent[-80:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай: {subject}.
Сложность: {diff_desc}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Попытка генерации: {attempt + 1}. Если сомневаешься, выбирай менее очевидный вариант, которого не было в списке.
Каждая подсказка и каждое предложение заканчивается точкой.
Стиль: улики должны быть атмосферными и чуть кинематографичными, но короткими. Не сухой список фактов.
Добавь 1 деталь действия/сцены в каждой улике: след, привычка, жест, звук, место, предмет, последствия.
Не повторяй одинаковые формулировки между уликами.
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 улики на языке {clue_lang}, через | , от косвенной к более явной — конкретные детали (форма, цвет, происхождение, функция, ощущения), без имени/названия
ANSWER: название на языке {clue_lang}
ALIASES: то же название на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка (но без названия), на языке {clue_lang}
EXPLAIN: 2 живых предложения — что это такое и почему улики вели именно к нему (на языке {clue_lang})"""
    raw = ai.llm(prompt, 900, 1.0, tier="cheap")
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("HINT", "hint"), ("HINT2", "hint2"), ("EXPLAIN", "explain")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+\d*:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    out["aliases"] = [x.strip() for x in out.get("aliases", "").split("|") if x.strip()]
    return out

def game_lang_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="gamelang_en")],
        [InlineKeyboardButton("🇳🇱 Nederlands", callback_data="gamelang_nl")],
    ])

async def game_start(bot, cid):
    store.challenge_state.pop(str(cid), None)
    msg = learning_ui.game_start()
    await bot.send_message(chat_id=cid, text=msg.text, reply_markup=game_lang_kb())

async def ask_difficulty(bot, cid, lang):
    ui = _game_ui(lang)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["easy"], callback_data="gamediff_easy")],
        [InlineKeyboardButton(ui["hard"], callback_data="gamediff_hard")],
    ])
    await bot.send_message(chat_id=cid, text=ui["diff_q"], reply_markup=kb)

async def send_game(bot, cid):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "английский", "difficulty": "easy"})
    lang = cfg["lang"]
    ui = _game_ui(lang)
    recent = _game_recent(cid)
    try:
        d = {}
        for attempt in range(5):
            cand = game_data(lang, cfg["difficulty"], recent, attempt=attempt)
            if cand.get("answer") and not _game_is_recent(cand, recent):
                d = cand
                break
            if cand.get("answer"):
                recent = recent + [cand.get("answer", "")] + list(cand.get("aliases") or [])
        if not d:
            await bot.send_message(chat_id=cid, text="Не смог загадать новое без повтора. Попробуй ещё раз через минуту.")
            return
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    _remember_game_answer(cid, d)
    hints = [_dot(h) for h in [d.get("hint"), d.get("hint2")] if (h or "").strip()]
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hints": hints, "hint_i": 0,
                                  "explain": _dot(d.get("explain", "")), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="game_change"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])
    clues = "\n".join(f"• {c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    msg = learning_ui.game_card(ui, clues)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

def _fuzzy(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if abs(len(a) - len(b)) <= 3:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 3
    return False

async def game_answer(bot, cid, text):
    st = store.game_state.get(str(cid))
    if not st:
        return False
    cfg = store.game_config.get(str(cid), {"lang": "русский"})
    ui = _game_ui(cfg["lang"])
    guess = text.lower().strip()
    names = [st["answer"]] + st.get("aliases", [])
    pool = []
    for n in names:
        n = (n or "").lower().strip()
        pool += [n] + n.split()
    correct = any(_fuzzy(guess, p) for p in pool if p)
    if correct:
        store.game_state.pop(str(cid), None)
        _remember_game_answer(cid, st)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(ui["again"], callback_data="game_again")],
            [InlineKeyboardButton(ui["back"], callback_data="m_learn"),
             InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
        ])
        body = st.get("explain") or st.get("quote", "")
        msg = learning_ui.game_found(ui, st["answer"], body)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        store.game_state.pop(str(cid), None)
        _remember_game_answer(cid, st)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {st['answer']}.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
                                    InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {ui['retry']}", reply_markup=kb)
    return True


async def game_hint(bot, cid, q):
    st = store.game_state.get(str(cid))
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    hints = (st or {}).get("hints") or []
    i = (st or {}).get("hint_i", 0)
    if st and i < len(hints):
        st["hint_i"] = i + 1
        msg = learning_ui.game_hint(ui, hints[i])
        await q.message.reply_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
    else:
        await q.message.reply_text(ui["nohint"])


async def game_reveal(bot, cid, q):
    st = store.game_state.pop(str(cid), None)
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    if not st:
        return
    _remember_game_answer(cid, st)
    body = st.get("explain") or st.get("quote", "")
    msg = learning_ui.game_found(ui, st.get("answer", ""), body)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["again"], callback_data="game_again")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn"),
         InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= НАСТРОЙКИ ОБУЧЕНИЯ =================
def learning_settings_kb(active_lang, active_level, back="set_home"):
    row = []
    for level in LEVELS:
        mark = "✅ " if level == active_level else ""
        row.append(InlineKeyboardButton(f"{mark}{LEVEL_LABELS[level]}", callback_data=f"set_learning_level_{level}"))
    code = _code(active_lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Мой словарь", callback_data=f"a_dictlang_{code}_from_learnset")],
        [InlineKeyboardButton(f"📚 Язык: {_language_display(active_lang)}", callback_data="toggle_learning_language")],
        row,
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])


async def send_learning_settings(bot, cid, q=None, back="set_home"):
    active_lang = active_language(cid)
    active_level = store.get_level(cid, active_lang)
    msg = learning_ui.learning_settings(_language_display(active_lang), _level_label(active_level))
    kb = learning_settings_kb(active_lang, active_level, back)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_levels(bot, cid, q=None, back="set_home"):
    await send_learning_settings(bot, cid, q=q, back=back)


async def handle_learning_settings_callback(bot, cid, q, data):
    back = "m_learn"
    if data == "set_learning":
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data == "toggle_learning_language":
        old_code = _active_language_code(cid)
        new_code = "en" if old_code == "nl" else "nl"
        store.set_learning_language(cid, new_code)
        store.ensure_level(cid, _language_for_code(old_code), "medium")
        store.ensure_level(cid, _language_for_code(new_code), "medium")
        prof = store.get_profile(cid)
        prof.pop("_myday_seed_prompted", None)
        store.set_profile(cid, prof)
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data.startswith("set_learning_level_"):
        level = data[len("set_learning_level_"):]
        if level in LEVELS:
            language = active_language(cid)
            old_level = store.get_level(cid, language)
            store.set_level(cid, language, level)
            await send_learning_settings(bot, cid, q=q, back=back)
            if old_level != level:
                await offer_seed_for_level_change(bot, cid, language, level)
            return
        await send_learning_settings(bot, cid, q=q, back=back)


SYSTEM_TOPICS = {
    "нидерландский": {
        "A1": [
            "Порядок слов (SVO)",
            "Артикли de/het",
            "Спряжение глаголов в настоящем",
            "Отрицание niet/geen",
            "Вопросительные предложения",
            "Личные местоимения",
            "Множественное число существительных",
            "Числительные и время",
            "Притяжательные местоимения",
            "Предлоги места",
        ],
        "A2": [
            "Perfectum (voltooide tijd)",
            "Инверсия",
            "Разделяемые глаголы",
            "Er-конструкции",
            "Степени сравнения прилагательных",
            "Imperfectum (onvoltooid verleden)",
            "Придаточные с dat/omdat",
            "Возвратные глаголы (zich)",
            "Предлоги времени",
            "Сочинительные союзы",
        ],
        "B1": [
            "Страдательный залог (passief)",
            "Косвенная речь",
            "Придаточные с omdat/want",
            "Модальные глаголы (moeten/mogen/kunnen)",
            "Относительные местоимения (die/dat/wie/wat)",
            "Futurum (zullen/gaan)",
            "Условные предложения с als",
            "Отделяемые и неотделяемые приставки",
            "Плюсквамперфект",
            "Инфинитивные обороты с te",
        ],
    },
    "английский": {
        "A1": [
            "Present Simple",
            "Артикли a/an/the",
            "Вопросы с do/does",
            "Отрицание don't/doesn't",
            "There is/are",
            "Личные и притяжательные местоимения",
            "Множественное число существительных",
            "Предлоги места (in/on/at/under)",
            "Числительные и время",
            "Глагол to be",
        ],
        "A2": [
            "Present Continuous",
            "Past Simple",
            "Going to (планы)",
            "Модальные can/must/should",
            "Степени сравнения прилагательных",
            "Past Continuous",
            "Future Simple (will)",
            "Предлоги времени (in/on/at/since/for)",
            "Союзы but/because/so/although",
            "Вопросительные слова (who/what/where/when/why/how)",
        ],
        "B1": [
            "Present Perfect",
            "Passive Voice",
            "Reported Speech",
            "Conditionals 1 & 2",
            "Придаточные времени и условия",
            "Past Perfect",
            "Модальные could/would/might",
            "Герундий и инфинитив",
            "Относительные придаточные (who/which/that)",
            "Фразовые глаголы (phrasal verbs)",
        ],
    },
}
