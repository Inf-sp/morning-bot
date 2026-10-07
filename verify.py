"""Verification-слой: грейдеры качества ответов + безопасная отправка и обработка ошибок.

Грейдеры привязаны к типу пользовательской поверхности:
  chat    - свободный диалог: html + не больше 1 эмодзи
  card    - карточки/советы с эмодзи-заголовками: только html
  weather - сводка/лук: html + предупреждение про «зонт без дождя»

Верхний уровень импортирует только stdlib, чтобы чистые грейдеры тестировались
без telegram/env. util/config/traceback импортируются лениво внутри функций.
"""
import logging
import re

_log = logging.getLogger(__name__)


# --- детектор эмодзи: соседние эмодзи-символы (с ZWJ/VS16/тоном кожи) = один кластер ---
_EMOJI_CHAR = (
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\u2190-\u21FF\u2300-\u23FF\u2B00-\u2BFF\ufe0f\u200d\U0001F3FB-\U0001F3FF]"
)
_EMOJI_CLUSTER = re.compile(_EMOJI_CHAR + r"+")

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
    elif surface == "weather":
        _, w = grade_umbrella(text, rain_real); warnings += w
    return text, warnings


# ================= БЕЗОПАСНАЯ ОТПРАВКА / ОШИБКИ =================
async def safe_send(bot, cid, text, *, surface="card", rain_real=None, reply_markup=None,
                    back="m_menu"):
    """Прогоняет грейдеры под surface, чистит markdown->HTML и шлёт с откатом на plain."""
    import util
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, warnings = _apply_graders(text, surface, rain_real)
    html = util.tg_html(text)
    warnings += grade_html(html)
    for w in warnings:
        _log.warning("[verify] %s: %s", surface, w)
    chunks = util.telegram_text_chunks(text, 4000)
    if reply_markup is None:
        from ui.navigation import back_menu_keyboard
        reply_markup = back_menu_keyboard(back)
    for i, (chunk_text, chunk_entities) in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await bot.send_message(
                chat_id=cid, text=chunk_text, entities=chunk_entities, reply_markup=markup,
            )
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk_text, reply_markup=markup)


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


async def safe_error(bot, cid, exc, *, skill=None, back="m_menu"):
    """Полную ошибку - в логи, пользователю - нейтральный текст. Никогда не показываем str(exc)."""
    msg = str(exc)
    expected_ai_outage = msg == "Сейчас не удалось подготовить ответ. Попробуй ещё раз чуть позже."
    if not expected_ai_outage:
        # exc_info=exc: traceback именно этой ошибки, даже вне блока except.
        _log.error("[error] %r", exc, exc_info=exc)
    try:
        if not expected_ai_outage:
            import tracking
            # Слово JSON бывает и в обычных сбоях сохранения (например,
            # сериализация set), поэтому не считаем его признаком ошибки AI.
            # AI-цепочка передаёт skill, а свои ограничения логирует сама.
            src = "llm" if getattr(skill, "name", "") else "app"
            origin = _origin_module(exc)
            kind = f"{origin}: {type(exc).__name__}" if origin else type(exc).__name__
            tracking.log_error(src, msg, kind=kind, exc=exc)
    except Exception:
        _log.debug("safe_error: ignored error", exc_info=True)
    if msg.startswith(("⏳", "⚠️")):          # уже безопасный текст из ai._friendly
        out = msg
    elif skill is not None and getattr(skill, "fallback", ""):
        out = skill.fallback
    else:
        out = "⚠️ Что-то пошло не так. Попробуй ещё раз через минуту."
    try:
        from ui.navigation import back_menu_keyboard
        await bot.send_message(
            chat_id=cid, text=out, reply_markup=back_menu_keyboard(back))
    except Exception:
        try:
            await bot.send_message(chat_id=cid, text=out)
        except Exception:
            _log.debug("safe_error: ignored error", exc_info=True)
