"""Короткая консультация по здоровью: risk → optional sources → Gemini → Groq."""
import asyncio
import logging
import re
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import medicine
import research
import secure
import store
import verify
from ui import doctor as doctor_ui

_log = logging.getLogger(__name__)
_TRUSTED_DOMAINS = (
    "thuisarts.nl", "rivm.nl", "apotheek.nl", "cbg-meb.nl",
    "nhs.uk", "who.int", "cdc.gov", "mayoclinic.org", "medlineplus.gov",
    "fda.gov", "ema.europa.eu",
)
_EMERGENCY_RE = re.compile(
    r"потер(?:ял|яла) сознани|без сознани|не могу дышать|сильн\w* затруднен\w* дыхан|"
    r"сильн\w* боль\w* в груди|перекосил\w* лиц|не могу поднять рук|нарушен\w* реч|"
    r"сильн\w* кровотеч|судорог|анафилак|от[её]к горла|передоз|слишком много таблет|двойн\w* доз",
    re.I,
)
_SYMPTOM_MARKERS = (
    "болит", "боль", "температур", "диаре", "понос", "тошн", "рвот", "головокруж",
    "сып", "каш", "одыш", "зуд", "отек", "отёк", "слабост", "кров", "сердцебиен",
)
_SEARCH_MARKERS = (
    "можно ли", "безопасно ли", "что рекоменду", "как леч", "правила леч", "редкий",
    "редкая", "необычн", "нормально ли", "можно тренир", "заразен", "сколько дней",
)


def _back_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])


async def send_prompt(bot, cid):
    store.doctor_context[str(cid)] = []
    store.pending_input[str(cid)] = "role_doctor"
    msg = doctor_ui.prompt_screen()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_back_keyboard())


def classify(text):
    lowered = (text or "").casefold()
    if _EMERGENCY_RE.search(lowered):
        return "emergency"
    if medicine.is_medicine_question(lowered):
        return "medication"
    if any(marker in lowered for marker in _SYMPTOM_MARKERS):
        return "symptoms"
    return "general_health"


def _needs_search(text, request_type):
    lowered = (text or "").casefold()
    return any(marker in lowered for marker in _SEARCH_MARKERS) or (
        request_type == "symptoms" and any(marker in lowered for marker in ("редк", "необыч", "что означает"))
    )


def _is_netherlands(cid):
    current = store.get_settings(cid)
    return str(current.get("cc") or "").upper() == "NL" or "нидерланд" in str(current.get("country") or "").casefold()


def _official_url(url):
    host = (urlparse(url or "").hostname or "").casefold()
    return any(host == domain or host.endswith("." + domain) for domain in _TRUSTED_DOMAINS)


def _medical_context(text, netherlands):
    domains = list(_TRUSTED_DOMAINS)
    if netherlands:
        domains = ["thuisarts.nl", "rivm.nl", "apotheek.nl", "cbg-meb.nl"] + [
            domain for domain in domains if not domain.endswith(".nl")
        ]
    query = f"{text} Thuisarts Netherlands medical guidance" if netherlands else text
    rows = research.web_search(
        query, max_results=3, include_domains=domains,
        scenario="medicine_official", allow_tavily=True, search_priority="tavily",
    )
    snippets = []
    for row in rows:
        if not _official_url(row.get("url")):
            continue
        content = " ".join(str(row.get("content") or "").split())
        if content:
            snippets.append(content[:1200])
    return snippets[:3]


def _context(cid):
    rows = store.doctor_context.get(str(cid), [])
    return rows if isinstance(rows, list) else []


def _remember(cid, role, text):
    rows = _context(cid)
    rows.append({"role": role, "text": str(text or "")[:600]})
    store.doctor_context[str(cid)] = rows[-6:]


def _context_text(cid):
    labels = {"user": "Пользователь", "assistant": "Врач"}
    return "\n".join(f"{labels.get(row.get('role'), 'Контекст')}: {row.get('text', '')}" for row in _context(cid)[-4:])


def _fallback_reason(exc):
    text = f"{type(exc).__name__} {exc}".casefold()
    if "429" in text or "limit" in text or "quota" in text or "rate" in text:
        return "limit"
    if "timeout" in text or "deadline" in text or "timed out" in text:
        return "timeout"
    return "api_error"


async def _ask_ai(prompt):
    try:
        result = await ai.allm_json(prompt, 900, module="doctor",
                                    privacy_level="sensitive", budget_seconds=10)
        return result, "utility", ""
    except Exception as exc:
        reason = _fallback_reason(exc)
        return {}, "utility_fallback", reason


def _normalize(data):
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    return {
        "direct": str(data.get("direct") or "")[:240],
        "likely": str(data.get("likely") or "")[:120],
        "actions": [str(x)[:90] for x in actions[:3]],
        "help_if": str(data.get("help_if") or "")[:120],
        "questions": [str(x)[:80] for x in questions[:2]],
    }


async def answer(bot, cid, text):
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health", back="m_balance")
        store.doctor_context.pop(str(cid), None)
        return
    request_type = classify(text)
    if request_type == "medication":
        await medicine.answer(bot, cid, text)
        return
    netherlands = _is_netherlands(cid)
    if request_type == "emergency":
        msg = doctor_ui.emergency_card(netherlands)
        await bot.send_message(chat_id=cid, text=msg.text, reply_markup=_back_keyboard())
        store.doctor_context.pop(str(cid), None)
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    _remember(cid, "user", text)
    snippets = []
    if _needs_search(text, request_type):
        snippets = await asyncio.to_thread(_medical_context, text, netherlands)
    source_block = "\n---\n".join(snippets)[:3600]
    care = ("Пользователь в Нидерландах: 112 только при угрозе жизни; срочно вне часов huisarts — huisartsenpost; "
            "обычная помощь — huisarts." if netherlands else
            "При угрозе жизни укажи местную экстренную службу; для обычной помощи — местного врача.")
    prompt = f"""Ты отвечаешь как грамотный врач-консультант, но не ставишь диагноз.
Тип запроса: {request_type}. Сначала дай прямой ответ. Затем максимум 1-3 вероятных объяснения,
только если данных достаточно, и 1-3 конкретных действия. Не перечисляй десятки диагнозов.
Не начинай с дисклеймера. Не пиши шаблоны «по описанию нельзя оценить», «следите за состоянием»,
«больше отдыхайте» или «пейте воду», если это не связано напрямую с вопросом.
Уточняющих вопросов максимум два и только если ответ изменится. Блок помощи заполняй только
конкретными тревожными признаками, относящимися к ситуации. {care}
Стандартная длина 250-600 символов, максимум 900.
Контекст текущей консультации:
{secure.wrap_untrusted(_context_text(cid), 'контекст консультации')}
Проверенные медицинские фрагменты (могут отсутствовать):
{secure.wrap_untrusted(source_block, 'официальные источники') if source_block else 'Не запрашивались: вопрос не требует внешнего подтверждения.'}
Если фрагменты переданы, подтверждаемые медицинские факты основывай на них и не противоречь им.
Верни JSON: {{"direct":"прямой ответ","likely":"вероятное объяснение или пусто",
"actions":["1-3 действия"],"help_if":"конкретные красные флаги или пусто",
"questions":["0-2 важных вопроса"]}}."""
    try:
        raw, provider, fallback_reason = await _ask_ai(prompt)
        data = _normalize(raw)
    except Exception as exc:
        _log.warning("doctor AI chain failed: %r", exc)
        data = {"direct": "Сейчас не удалось подготовить надёжный разбор.", "likely": "",
                "actions": [], "help_if": "", "questions": ["Попробуешь повторить вопрос чуть позже?"]}
        provider, fallback_reason = "none", _fallback_reason(exc)
    msg = doctor_ui.answer_card(data)
    _remember(cid, "assistant", msg.text)
    store.pending_input[str(cid)] = "role_doctor"
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_surface[str(cid)] = "health"
    store.last_action[str(cid)] = ("role", "doctor", text)
    try:
        import tracking
        tracking.annotate_action(provider=provider, fallback=fallback_reason)
    except Exception:
        pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_back_keyboard())
