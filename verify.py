"""Verification-слой: грейдеры качества ответов + безопасная отправка и обработка ошибок.

Грейдеры привязаны к surface скилла (см. skills.py):
  chat    - свободный диалог: html + не больше 1 эмодзи
  health  - медразбор: html + обязательный дисклеймер (эмодзи-маркеры разрешены)
  card    - карточки/советы с эмодзи-заголовками: только html
  weather - сводка/лук: html + предупреждение про «зонт без дождя»

Верхний уровень импортирует только stdlib, чтобы чистые грейдеры тестировались
без telegram/env. util/config/traceback импортируются лениво внутри функций.
"""
import logging
import re

_log = logging.getLogger(__name__)

SURFACES = ("chat", "health", "card", "weather")

# --- детектор эмодзи: соседние эмодзи-символы (с ZWJ/VS16/тоном кожи) = один кластер ---
_EMOJI_CHAR = (
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\u2190-\u21FF\u2300-\u23FF\u2B00-\u2BFF\ufe0f\u200d\U0001F3FB-\U0001F3FF]"
)
_EMOJI_CLUSTER = re.compile(_EMOJI_CHAR + r"+")

_DISCLAIMER = ("ℹ️ Это общая справочная информация, не диагноз и не назначение - "
               "при тревожных симптомах обратись к врачу.")
_DISC_MARKERS = ("не диагноз", "не назначение", "справочн", "к врачу", "обратись к специалист")


# ================= ЧИСТЫЕ ГРЕЙДЕРЫ (без I/O, тестируемые) =================
def grade_emoji(text, max_n=1):
    """Не больше max_n эмодзи. Лишние кластеры убираем, оставляя первый. -> (text, warnings)."""
    clusters = list(_EMOJI_CLUSTER.finditer(text or ""))
    if len(clusters) <= max_n:
        return text, []
    keep_end = clusters[max_n - 1].end() if max_n > 0 else 0
    out = _EMOJI_CLUSTER.sub(lambda m: "" if m.start() >= keep_end else m.group(0), text)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, [f"emoji>{max_n}: trimmed {len(clusters) - max_n}"]


def grade_disclaimer(text):
    """Медицинский ответ обязан содержать дисклеймер; если нет - дописываем. -> (text, warnings)."""
    low = (text or "").lower()
    if any(m in low for m in _DISC_MARKERS):
        return text, []
    return (text or "").rstrip() + "\n\n" + _DISCLAIMER, ["health: disclaimer appended"]


def grade_umbrella(text, rain_real):
    """Предупреждаем, если упомянут зонт, а дождя по сути нет. Текст НЕ меняем. -> (text, warnings)."""
    if rain_real is False and re.search(r"зонт|umbrella", text or "", re.I):
        return text, ["weather: umbrella mentioned but rain_real=False"]
    return text, []


def grade_html(html):
    """Проверка баланса разрешённых тегов в готовом HTML. -> warnings."""
    warnings = []
    for tag in ("b", "i", "u", "s", "code", "pre", "a"):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", html or "", re.I))
        closes = len(re.findall(rf"</{tag}>", html or "", re.I))
        if opens != closes:
            warnings.append(f"html: <{tag}> unbalanced {opens}/{closes}")
    return warnings


def grade_text(text, surface, rain_real=None):
    """Публичная обёртка: прогнать текстовые грейдеры под surface. -> (text, warnings)."""
    return _apply_graders(text, surface, rain_real)


def _apply_graders(text, surface, rain_real):
    """Прогоняет текстовые грейдеры по surface. -> (text, warnings)."""
    warnings = []
    if surface == "chat":
        text, w = grade_emoji(text, 1); warnings += w
    elif surface == "health":
        text, w = grade_disclaimer(text); warnings += w
    elif surface == "weather":
        _, w = grade_umbrella(text, rain_real); warnings += w
    return text, warnings


# ================= БЕЗОПАСНАЯ ОТПРАВКА / ОШИБКИ =================
async def safe_send(bot, cid, text, *, surface="card", rain_real=None, reply_markup=None):
    """Прогоняет грейдеры под surface, чистит markdown->HTML и шлёт с откатом на plain."""
    import util
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, warnings = _apply_graders(text, surface, rain_real)
    html = util.tg_html(text)
    warnings += grade_html(html)
    for w in warnings:
        _log.warning("[verify] %s: %s", surface, w)
    # Telegram Bot API 2026 supports long bot messages with client-side "Show More".
    # Keep a safety margin below the documented 32768 chars.
    chunks = [html[i:i + 32000] for i in range(0, len(html), 32000)] or [html]
    for i, c in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=c, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=c, reply_markup=markup)


_SKIP_MODULES = frozenset({"verify", "ai", "bot", "asyncio", "concurrent"})


def _origin_module(exc) -> str:
    """Имя модуля проекта, где реально возникло исключение (последний фрейм
    traceback вне verify.py/ai.py/библиотек) - чтобы в админке было видно, какой
    раздел сломался, а не только тип ошибки."""
    import os
    import traceback
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        fname = os.path.basename(frame.filename)
        if fname.endswith(".py"):
            m = fname[:-3]
            if m not in _SKIP_MODULES:
                return m
    return ""


async def safe_error(bot, cid, exc, *, skill=None):
    """Полную ошибку - в логи, пользователю - нейтральный текст. Никогда не показываем str(exc)."""
    import traceback
    _log.error("[error] %r", exc, exc_info=True)
    traceback.print_exc()
    try:
        import tracking
        msg = str(exc)
        src = "llm" if (
            getattr(skill, "name", "")
            or "JSON" in msg
            or "ИИ" in msg
            or "llm" in msg.lower()
        ) else "app"
        origin = _origin_module(exc)
        kind = f"{origin}: {type(exc).__name__}" if origin else type(exc).__name__
        tracking.log_error(src, str(exc), kind=kind)
    except Exception:
        pass
    msg = str(exc)
    if msg.startswith(("⏳", "⚠️")):          # уже безопасный текст из ai._friendly
        out = msg
    elif skill is not None and getattr(skill, "fallback", ""):
        out = skill.fallback
    else:
        out = "⚠️ Что-то пошло не так. Попробуй ещё раз через минуту."
    try:
        await bot.send_message(chat_id=cid, text=out)
    except Exception:
        pass


# ================= АУДИТ CALLBACK'ОВ (advisory eval) =================
_CB_ALLOW = {"noop"}   # известные «динамические»/служебные, не считаем нарушением

def audit_callbacks(paths=None):
    """Best-effort: каждый ЛИТЕРАЛЬНЫЙ callback_data должен где-то обрабатываться.
    f-string callback'и не проверяем (их префиксы ловятся через startswith). -> list[str] нарушений."""
    import glob
    import os
    if paths is None:
        root = os.path.dirname(os.path.abspath(__file__))
        paths = [p for p in glob.glob(os.path.join(root, "*.py"))
                 if os.path.basename(p) not in ("verify.py",)]
    literals, exact, prefixes = set(), set(_CB_ALLOW), set()
    for p in paths:
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for m in re.finditer(r"""callback_data\s*=\s*["']([^"'{}]+)["']""", src):
            literals.add(m.group(1))
        for m in re.finditer(r"""data\s*==\s*["']([^"']+)["']""", src):
            exact.add(m.group(1))
        for m in re.finditer(r"""data\s+in\s*\(([^)]*)\)""", src):
            exact |= set(re.findall(r"""["']([^"']+)["']""", m.group(1)))
        for m in re.finditer(r"""data\.startswith\(\s*\(?([^)]*?)\)?\s*\)""", src):
            prefixes |= set(re.findall(r"""["']([^"']+)["']""", m.group(1)))
        # ветка act = data[2:] -> мапим на префикс "a_"
        for m in re.finditer(r"""act\s*==\s*["']([^"']+)["']""", src):
            exact.add("a_" + m.group(1))
        for m in re.finditer(r"""act\s+in\s*\(([^)]*)\)""", src):
            exact |= {"a_" + x for x in re.findall(r"""["']([^"']+)["']""", m.group(1))}
        for m in re.finditer(r"""act\.startswith\(\s*["']([^"']+)["']""", src):
            prefixes.add("a_" + m.group(1))

    def handled(cb):
        return cb in exact or any(cb.startswith(p) for p in prefixes if p)
    return sorted(cb for cb in literals if not handled(cb))


# ================= АУДИТ АРХИТЕКТУРНЫХ ГРАНИЦ =================
def audit_architecture(root=None):
    """Статически проверяет ключевые границы модульного монолита.

    Это часть штатной диагностики запуска, а не test-suite: она не импортирует
    приложение, не обращается к Telegram и не изменяет пользовательские данные.
    Возвращает список нарушений; пустой список означает, что инварианты соблюдены.
    """
    import ast
    import os

    root = root or os.path.dirname(os.path.abspath(__file__))
    findings = []
    required = {
        "trainer.py", "trainer_engine.py", "trainer_exercises.py",
        "trainer_grading.py", "trainer_session.py", "learning_dictionary.py",
        "dictionary_model.py", "dictionary_repository.py", "dictionary_seed_state.py",
        "dictionary_seed_ui.py",
        "live_language.py", "learning_game.py", "learning_settings.py",
        "cooking.py", "leisure_movies.py", "leisure_books.py",
        "leisure_music.py", "leisure_concerts.py", "saved_items.py",
        "storage_driver.py", "runtime_state.py", "repositories.py",
        "response_delivery.py", "retry_flow.py",
    }
    missing = sorted(name for name in required if not os.path.exists(os.path.join(root, name)))
    findings.extend(f"missing module: {name}" for name in missing)

    forbidden = {"telegram", "store", "ai"}
    for name in ("trainer_engine.py", "trainer_exercises.py", "trainer_grading.py"):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for module in sorted(imports & forbidden):
            findings.append(f"{name}: forbidden import {module}")

    boundary_rules = {
        "dictionary_model.py": {"telegram", "store", "ai", "config", "repositories"},
        "dictionary_repository.py": {"telegram", "ai"},
        "dictionary_seed_state.py": {"telegram", "ai"},
        "response_delivery.py": {"ai"},
    }
    for name, denied in boundary_rules.items():
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for module in sorted(imports & denied):
            findings.append(f"{name}: forbidden import {module}")

    learning_path = os.path.join(root, "learning.py")
    if os.path.exists(learning_path):
        source = open(learning_path, encoding="utf-8").read()
        if re.search(r"[\"']ex_", source):
            findings.append("learning.py: owns ex_* callback_data")
        if "trainer_session" in source:
            findings.append("learning.py: owns trainer session internals")

    dictionary_path = os.path.join(root, "learning_dictionary.py")
    if os.path.exists(dictionary_path):
        source = open(dictionary_path, encoding="utf-8").read()
        for function in ("normalize_entry", "migrate_dict_entries_for_srs", "send_dict"):
            if function == "normalize_entry":
                if not re.search(r"from dictionary_model import \(", source) or function not in source:
                    findings.append("learning_dictionary.py: normalize_entry re-export missing")
            elif not re.search(rf"(?:async\s+)?def\s+{function}\s*\(", source):
                findings.append(f"learning_dictionary.py: {function} missing")

    repository_path = os.path.join(root, "dictionary_repository.py")
    if os.path.exists(repository_path):
        source = open(repository_path, encoding="utf-8").read()
        if "class DictionaryRepository" not in source:
            findings.append("dictionary_repository.py: DictionaryRepository missing")

    ownership_rules = {
        "balance.py": ("def enter_meal(", "def send_fridge(", "import cooking", "from cooking import"),
        "settings.py": ("def send_notes(", "def handle_notes_callback("),
        "leisure.py": ("def send_movie_home(", "def send_listen(", "def find_concerts("),
        "store.py": ("def db(", "def load(", "def mutate("),
    }
    for name, forbidden_fragments in ownership_rules.items():
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        for fragment in forbidden_fragments:
            if fragment in source:
                findings.append(f"{name}: still owns {fragment[:-1]}")
    return findings


def audit_trainer_contracts():
    """Проверяет чистые контракты тренажёра на безопасных локальных данных."""
    import random
    import srs
    import trainer_engine as engine
    import trainer_exercises as exercises
    import trainer_grading as grading
    import trainer_session

    findings = []
    base = {
        "term": "ik vergelijk deze boeken",
        "translation": "я сравниваю эти книги",
        "lang": "nl",
        "examples": [{"text": "Ik vergelijk deze boeken.",
                      "translation": "Я сравниваю эти книги."}],
    }
    error_entry = {**base, "examples": [{
        "text": "Ik vergelijk de boeken.", "translation": "Я сравниваю книги."}]}
    others = [
        base,
        {"term": "de tafel", "translation": "стол", "lang": "nl"},
        {"term": "het huis", "translation": "дом", "lang": "nl"},
        {"term": "goedemorgen", "translation": "доброе утро", "lang": "nl"},
        {"term": "tot straks", "translation": "до скорого", "lang": "nl"},
    ]
    situation = {"line": "Welke boeken kies je?", "line_ru": "Какие книги ты выбираешь?"}
    for kind in engine.ALL_EXERCISES:
        entry = error_entry if kind == engine.EXERCISE_FIND_ERROR else base
        if not exercises.build_exercise(
                entry, others, kind, situation=situation, rng=random.Random(7)):
            findings.append(f"exercise cannot build: {kind}")

    queue = engine.build_training_queue(
        [{**entry, "srs_level": 0} for entry in others], rng=random.Random(4))
    if not queue or any("exercise_type" not in item for item in queue):
        findings.append("training queue contract failed")

    grade = grading.grade_free_text({"correct": "goedemorgen"}, "goedemorgen")
    if not grade.correct:
        findings.append("free-text grading contract failed")
    else:
        state = srs.record_answer(
            srs.default_srs_state(), engine.EXERCISE_RECALL_FREE, grade.quality)
        if state["srs_history"][-1]["result"] != "recalled_free":
            findings.append("grading → SRS contract failed")

    cid = "__architecture_session_audit__"
    trainer_session.start(cid, "nl", queue[:1], {"total": 0})
    session = trainer_session.get(cid)
    if not session or session.get("queue_idx") != 0:
        findings.append("trainer session start contract failed")
    trainer_session.finish(cid)
    if trainer_session.get(cid) is not None:
        findings.append("trainer session finish contract failed")
    return findings
