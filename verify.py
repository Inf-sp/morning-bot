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
        tracking.log_error(src, str(exc), kind=type(exc).__name__)
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
