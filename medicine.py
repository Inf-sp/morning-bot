"""Источник-ориентированный разбор лекарств: DailyMed → Firecrawl/Tavily → Gemini/Groq."""
import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import api_usage
import config
import research
import secure
import store
from ui import medicine as medicine_ui

_log = logging.getLogger(__name__)
_DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_CACHE_TTL = 30 * 86400
_CACHE_VERSION = 1
_AUDIT_LIMIT = 300

_EMERGENCY_RE = re.compile(
    r"передоз|слишком много|двойн(?:ую|ая) доз|потер(?:ял|яла) сознани|без сознани|"
    r"не могу дышать|трудно дышать|сильн\w* боль\w* в груди|судорог|анафилак|от[её]к горла",
    re.I,
)
_STOPWORDS = {
    "можно", "ли", "когда", "как", "что", "если", "принять", "принимать", "выпить",
    "лекарство", "лекарства", "таблетку", "таблетки", "препарат", "дозу", "доза",
    "позже", "раньше", "вместе", "едой", "еды", "после", "до", "мне", "я", "у",
    "какие", "побочные", "эффекты", "пропустил", "пропустила", "сегодня", "завтра",
}
_RELEASE_FORM_RE = re.compile(
    r"\b(?:XR|XL|CR|ER|SR|MR|LA|retard|extended[- ]release|prolonged[- ]release|"
    r"пролонгированн\w*|таблетк\w*|капсул\w*|раствор\w*|суспензи\w*|пластыр\w*)\b",
    re.I,
)
_MEDICINE_MARKERS = (
    "лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
    "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
    "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать",
    "с едой", "пропустил", "пропустила", "совмест", "взаимодейств", "противопоказ",
)
_DRUG_ALIASES = (
    (("метилфенид", "methylphenid"), "methylphenidate", ""),
    (("concerta", "концерт"), "methylphenidate", "Concerta"),
    (("medikinet", "медикинет"), "methylphenidate", "Medikinet"),
    (("ritalin", "риталин"), "methylphenidate", "Ritalin"),
    (("ибупроф", "ibuprofen"), "ibuprofen", ""),
    (("парацетам", "acetaminophen", "paracetamol"), "acetaminophen", ""),
    (("амоксиц", "amoxicillin"), "amoxicillin", ""),
    (("омепраз", "omeprazole"), "omeprazole", ""),
    (("сертралин", "sertraline"), "sertraline", ""),
)
_INTENT_MARKERS = (
    ("drug_missed_dose", ("пропуст", "забыл принять", "missed dose")),
    ("drug_interactions", ("алкогол", "вместе", "совмест", "взаимодейств", "сочет")),
    ("drug_side_effects", ("побоч", "реакц", "опасн", "side effect")),
    ("drug_contraindications", ("нельзя", "противопоказ", "contraindicat")),
    ("drug_food", ("с едой", "натощак", "до еды", "после еды", "food")),
    ("drug_timing", ("во сколько", "вместо", "позже", "раньше", "время при", "когда принять")),
    ("drug_dosage", ("сколько", "дозиров", "увелич", "уменьш", "дозу", "dose")),
)
_INTENT_SECTIONS = {
    "drug_overview": ("DESCRIPTION", "INDICATIONS AND USAGE", "CLINICAL PHARMACOLOGY"),
    "drug_timing": ("DOSAGE AND ADMINISTRATION", "PATIENT INFORMATION", "MEDICATION GUIDE"),
    "drug_dosage": ("DOSAGE AND ADMINISTRATION",),
    "drug_food": ("DOSAGE AND ADMINISTRATION", "PATIENT INFORMATION", "MEDICATION GUIDE"),
    "drug_missed_dose": ("PATIENT INFORMATION", "MEDICATION GUIDE", "DOSAGE AND ADMINISTRATION"),
    "drug_side_effects": ("ADVERSE REACTIONS", "WARNINGS AND PRECAUTIONS"),
    "drug_interactions": ("DRUG INTERACTIONS",),
    "drug_contraindications": ("CONTRAINDICATIONS",),
    "drug_other": ("PATIENT INFORMATION", "MEDICATION GUIDE", "DESCRIPTION"),
}
_OFFICIAL_DOMAINS = (
    "dailymed.nlm.nih.gov", "fda.gov", "nhs.uk", "ema.europa.eu", "ec.europa.eu",
    "cbg-meb.nl", "government.nl", "gov.uk",
)


def _back_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])


async def send_prompt(bot, cid):
    store.pending_input[str(cid)] = "role_medicine"
    msg = medicine_ui.prompt_screen()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_back_keyboard())


def is_medicine_question(text):
    lowered = (text or "").casefold()
    return (any(marker in lowered for marker in _MEDICINE_MARKERS)
            or any(stem in lowered for stems, _generic, _brand in _DRUG_ALIASES for stem in stems))


def _is_emergency(text):
    return bool(_EMERGENCY_RE.search(text or ""))


def _extract_drug_query(text):
    dosage = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:mg|мг|mcg|мкг|ml|мл)\b", text or "", re.I)
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё-]{2,}", text or "")
    candidates = [word for word in words if word.casefold() not in _STOPWORDS]
    drug = candidates[0] if candidates else ""
    return drug, (dosage.group(0) if dosage else "")


def _dailymed_name(name):
    lowered = (name or "").casefold()
    return next((generic for stems, generic, _brand in _DRUG_ALIASES
                 if any(stem in lowered for stem in stems)), name)


def _intent(text, *, has_question=False):
    lowered = (text or "").casefold()
    for intent, markers in _INTENT_MARKERS:
        if any(marker in lowered for marker in markers):
            return intent
    return "drug_other" if has_question else "drug_overview"


def _local_normalization(text):
    dosage = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:mg|мг|mcg|мкг|ml|мл)\b", text or "", re.I)
    release = _RELEASE_FORM_RE.search(text or "")
    cleaned = text or ""
    if dosage:
        cleaned = cleaned.replace(dosage.group(0), " ")
    if release:
        cleaned = cleaned.replace(release.group(0), " ")
    lowered = cleaned.casefold()
    matched = next(((generic, brand) for stems, generic, brand in _DRUG_ALIASES
                    if any(stem in lowered for stem in stems)), None)
    generic, brand = matched or ("", "")
    raw_name, _unused_dose = _extract_drug_query(cleaned)
    question_words = re.search(
        r"(?:\?|\b(?:можно|что|как|когда|сколько|будет|принять|принимать|совместим)\b)",
        cleaned, re.I,
    )
    return {
        "generic_name": generic,
        "brand_name": brand,
        "raw_name": brand or raw_name,
        "dose": dosage.group(0) if dosage else "",
        "release_form": release.group(0) if release else "",
        "intent": _intent(text, has_question=bool(question_words)),
    }


async def _normalize_drug_request(text):
    local = _local_normalization(text)
    if local["generic_name"]:
        return local
    prompt = f"""Нормализуй название лекарства из пользовательского сообщения.
Не отвечай на медицинский вопрос. Верни только JSON с полями generic_name
(международное английское название), brand_name, dose, release_form и intent.
intent — один из: drug_overview, drug_timing, drug_dosage, drug_food,
drug_missed_dose, drug_side_effects, drug_interactions, drug_contraindications,
drug_other. Дозировка и форма не входят в generic_name.
Сообщение: {secure.wrap_untrusted(text, 'запрос пользователя')}
JSON: {{"generic_name":"","brand_name":"","dose":"","release_form":"","intent":"drug_other"}}"""
    try:
        data = await ai.allm_json(prompt, 350, order=("cohere", "gemini"), module="medicine",
                                  privacy_level="sensitive", budget_seconds=8)
    except Exception:
        return local
    if not isinstance(data, dict):
        return local
    generic = re.sub(r"[^A-Za-z0-9 '\-]", "", str(data.get("generic_name") or "")).strip()
    intent = str(data.get("intent") or local["intent"])
    if intent not in _INTENT_SECTIONS:
        intent = local["intent"]
    return {
        "generic_name": generic,
        "brand_name": str(data.get("brand_name") or "").strip()[:80],
        "raw_name": local["raw_name"],
        "dose": str(data.get("dose") or local["dose"]).strip()[:40],
        "release_form": str(data.get("release_form") or local["release_form"]).strip()[:80],
        "intent": intent,
    }


def _cache_load():
    data = store._load(config.MEDICINE_LABEL_CACHE_KEY)
    return data if isinstance(data, dict) else {}


def _cache_get(name):
    data = _cache_load()
    setid = (data.get("names") or {}).get(name.casefold())
    entry = (data.get("sets") or {}).get(setid, {}) if setid else {}
    if (entry.get("content_version") == _CACHE_VERSION
            and time.time() - int(entry.get("updated_at") or 0) < _CACHE_TTL):
        return entry
    return None


def _cache_save(query_name, entry):
    setid = entry["setid"]

    def change(data):
        data.setdefault("names", {})[query_name.casefold()] = setid
        data.setdefault("sets", {})[setid] = entry
        return data, None

    store.mutate_kv(config.MEDICINE_LABEL_CACHE_KEY, change)


def _request(url, **kwargs):
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=10, **kwargs)
        ok = 200 <= response.status_code < 300
        api_usage.record_request("dailymed", ok=ok, status_code=response.status_code,
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 error="" if ok else f"HTTP {response.status_code}")
        return response if ok else None
    except Exception as exc:
        api_usage.record_request("dailymed", ok=False, error=type(exc).__name__,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        return None


def _xml_sections(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    sections = {}
    for section in root.iter():
        if section.tag.rsplit("}", 1)[-1] != "section":
            continue
        title = ""
        text_node = None
        for child in section:
            local = child.tag.rsplit("}", 1)[-1]
            if local == "title" and not title:
                title = " ".join("".join(child.itertext()).split())
            elif local == "text" and text_node is None:
                text_node = child
        body = " ".join(" ".join(text_node.itertext()).split()) if text_node is not None else ""
        if title and body and title.upper() not in sections:
            sections[title.upper()] = body[:16000]
    return sections


def _drug_form(title):
    upper = (title or "").upper()
    forms = (
        ("EXTENDED RELEASE", "пролонгированная форма"), ("TABLET", "таблетки"),
        ("CAPSULE", "капсулы"), ("SOLUTION", "раствор"), ("SUSPENSION", "суспензия"),
        ("CREAM", "крем"), ("OINTMENT", "мазь"), ("PATCH", "пластырь"),
    )
    return next((label for marker, label in forms if marker in upper), "")


def _fetch_dailymed(name):
    cached = _cache_get(name)
    if cached:
        return cached
    search = _request(f"{_DAILYMED_BASE}/spls.json", params={"drug_name": name, "pagesize": 5})
    if search is None:
        return None
    try:
        rows = search.json().get("data") or []
    except Exception:
        rows = []
    if not rows:
        return None
    row = rows[0]
    setid = str(row.get("setid") or "").strip()
    if not setid:
        return None
    label = _request(f"{_DAILYMED_BASE}/spls/{setid}.xml")
    if label is None:
        return None
    title = str(row.get("title") or name).strip()
    entry = {"setid": setid, "drug_name": title.split("[")[0].strip(), "title": title,
             "drug_form": _drug_form(title), "sections": _xml_sections(label.text),
             "content_version": _CACHE_VERSION, "updated_at": int(time.time())}
    _cache_save(name, entry)
    return entry


def _wanted_titles(intent):
    return _INTENT_SECTIONS.get(intent, _INTENT_SECTIONS["drug_other"])


def _relevant_sections(entry, intent):
    selected = []
    for wanted in _wanted_titles(intent):
        for title, body in (entry.get("sections") or {}).items():
            if wanted in title and body:
                selected.append({"title": title.title(), "text": body[:2200]})
                break
    return selected[:2]


def _official_url(url):
    host = (urlparse(url or "").hostname or "").casefold()
    known = any(host == domain or host.endswith("." + domain) for domain in _OFFICIAL_DOMAINS)
    government = host.endswith(".gov") or bool(re.search(r"(^|\.)(?:gov|gouv|government)\.", host))
    return known or government


def _official_search_context(drug_name, question, intent="drug_other"):
    intent_query = intent.removeprefix("drug_").replace("_", " ")
    query = (f"{drug_name} {intent_query} {question} official medicine label "
             "site:dailymed.nlm.nih.gov OR site:fda.gov OR site:nhs.uk OR site:ema.europa.eu")
    rows = research.web_search(query, max_results=6, include_domains=_OFFICIAL_DOMAINS)
    context, sources = [], []
    for row in rows:
        if not _official_url(row.get("url")):
            continue
        content = " ".join(str(row.get("content") or "").split())
        if content:
            context.append(content[:1200])
            sources.append(row.get("url") or "")
        if len(context) >= 3:
            break
    return context, sources


def _fallback_reason(exc):
    text = f"{type(exc).__name__} {exc}".casefold()
    if "429" in text or "limit" in text or "quota" in text or "rate" in text:
        return "limit"
    if "timeout" in text or "deadline" in text or "timed out" in text:
        return "timeout"
    if "unavailable" in text or "not configured" in text:
        return "unavailable"
    return "api_error"


async def _format_with_ai(prompt):
    try:
        data = await ai.allm_json(prompt, 850, order=("gemini",), module="medicine",
                                  privacy_level="sensitive", budget_seconds=10)
        return data, "gemini", ""
    except Exception as exc:
        reason = _fallback_reason(exc)
        data = await ai.allm_json(prompt, 850, order=("groq",), module="medicine",
                                  privacy_level="sensitive", budget_seconds=10)
        return data, "groq_fallback", reason


def _normalize_result(data, question, normalized):
    data = data if isinstance(data, dict) else {}
    details = data.get("details") if isinstance(data.get("details"), list) else []
    result = {"query": str(data.get("query") or question)[:120],
              "answer": str(data.get("answer") or "Не удалось безопасно сформировать краткий ответ.")[:360],
              "details": [str(x)[:110] for x in details[:2]],
              "important": str(data.get("important") or "")[:140],
              "disclaimer": str(data.get("disclaimer") or "")[:160],
              "drug_name": str(data.get("drug_name") or normalized.get("display_name") or "")[:100],
              "dose": str(normalized.get("dose") or "")[:40]}
    return result


def _display_drug_name(normalized, entry=None):
    brand = str(normalized.get("brand_name") or "").strip()
    raw = str(normalized.get("raw_name") or "").strip()
    generic = str(normalized.get("generic_name") or "").strip()
    value = brand or raw or (entry or {}).get("drug_name") or generic or "Лекарство"
    return value[:1].upper() + value[1:] if value else "Лекарство"


def _source_fallback(normalized, entry, *, identified=False):
    display = normalized.get("display_name") or _display_drug_name(normalized, entry)
    intent = normalized.get("intent")
    if identified and intent == "drug_overview":
        generic = normalized.get("generic_name") or display
        answer = f"Действующее вещество — {generic}. Точные правила приёма зависят от лекарственной формы."
        important = "Укажи название с упаковки и форму препарата, чтобы уточнить правила приёма."
    else:
        answer = "Не удалось найти надёжную информацию об этом препарате."
        important = "Проверь название на упаковке и попробуй ещё раз."
    return {
        "query": display, "drug_name": display, "dose": normalized.get("dose") or "",
        "answer": answer, "details": [], "important": important, "disclaimer": "",
    }


def _audit(**entry):
    entry = {"ts": int(time.time()), **entry}

    def change(data):
        log = data.get("log", [])
        log.append(entry)
        data["log"] = log[-_AUDIT_LIMIT:]
        return data, None

    store.mutate_kv(config.MEDICINE_AUDIT_LOG_KEY, change)


async def answer(bot, cid, question):
    if _is_emergency(question) or secure.is_dangerous_med(question):
        msg = medicine_ui.emergency_card()
        await bot.send_message(chat_id=cid, text=msg.text, reply_markup=_back_keyboard())
        _audit(medicine_source="none", ai_provider="none", drug_name="", drug_form="",
               source_found=False, emergency=True)
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    normalized = await _normalize_drug_request(question)
    generic_name = normalized.get("generic_name") or ""
    brand_name = normalized.get("brand_name") or ""
    intent = normalized.get("intent") or "drug_other"

    # Для бренда сначала пробуем найти именно его инструкцию: форма выпуска в
    # ней точнее. Если бренд не найден, повторяем поиск по действующему веществу.
    lookup_names = []
    for value in (brand_name, generic_name, normalized.get("raw_name")):
        value = str(value or "").strip()
        if value and value.casefold() not in {item.casefold() for item in lookup_names}:
            lookup_names.append(value)
    entry = None
    used_lookup = ""
    for lookup_name in lookup_names:
        entry = await asyncio.to_thread(_fetch_dailymed, lookup_name)
        if entry:
            used_lookup = lookup_name
            break
    sections = _relevant_sections(entry, intent) if entry else []
    if sum(len(item.get("text", "")) for item in sections) < 200:
        sections = []
    medicine_source = "dailymed" if sections else ""
    source_name = "DailyMed"
    context = [f"{item['title']}: {item['text']}" for item in sections]
    if not context:
        search_name = generic_name or brand_name or normalized.get("raw_name") or ""
        snippets, _urls = await asyncio.to_thread(
            _official_search_context, search_name, question, intent,
        )
        context = snippets
        if snippets:
            medicine_source, source_name = "official_search", "официальные медицинские источники"
    drug_name = (entry or {}).get("drug_name") or generic_name or brand_name or normalized.get("raw_name")
    drug_form = normalized.get("release_form") or (entry or {}).get("drug_form") or ""
    normalized["display_name"] = _display_drug_name(normalized, entry)
    if not context:
        result = _source_fallback(normalized, entry, identified=bool(entry or generic_name))
        provider, fallback_reason = "none", ""
    else:
        source_context = "\n\n".join(context)[:5200]
        prompt = f"""Ты кратко объясняешь официальную информацию о лекарстве.
Отвечай ТОЛЬКО по контексту ниже. Не добавляй дозировки, интервалы, противопоказания,
взаимодействия или правила пропуска дозы, которых нет в контексте. Если не хватает формы
или дозировки — прямо скажи, что уточнить. Обычно 300-700 символов, максимум 3 абзаца.
Дай максимально короткий прямой ответ. Используй только подтверждённые данные.
Если неизвестна форма препарата, сначала дай информацию, одинаковую для всех форм,
затем одним предложением укажи, что нужно уточнить. Недостаток деталей не означает,
что нужно полностью отказаться от ответа.
Исходный вопрос: {secure.wrap_untrusted(question, 'вопрос пользователя')}
Intent: {intent}. Generic name: {generic_name or 'не определено'}.
Brand name: {brand_name or 'не указан'}. Найденный препарат: {drug_name}.
Форма: {drug_form or 'не определена'}. Дозировка: {normalized.get('dose') or 'не указана'}.
Источник: {source_name}.
Релевантные выдержки:
{secure.wrap_untrusted(source_context, 'официальные выдержки')}
Верни JSON: {{"drug_name":"краткое название на языке пользователя","query":"кратко сформулированный запрос","answer":"прямой ответ без вступления",
"details":["0-2 важных уточнения"],"important":"важный нюанс или пусто",
"disclaimer":"только если требуется: не менять назначенную схему без врача/фармацевта, иначе пусто"}}."""
        try:
            raw, provider, fallback_reason = await _format_with_ai(prompt)
            result = _normalize_result(raw, question, normalized)
        except Exception as exc:
            _log.warning("medicine AI chain failed: %r", exc)
            result = {"query": question[:180], "drug_name": normalized["display_name"],
                      "dose": normalized.get("dose") or "",
                      "answer": "Официальные данные найдены, но сейчас не удалось безопасно сформировать краткий ответ.",
                      "details": [], "important": "Попробуй ещё раз позже или уточни у фармацевта.",
                      "disclaimer": ""}
            provider, fallback_reason = "none", _fallback_reason(exc)
    _audit(medicine_source=medicine_source or "none", ai_provider=provider,
           drug_name=drug_name or "", drug_form=drug_form, source_found=bool(context),
           fallback_reason=fallback_reason, intent=intent, lookup_name=used_lookup)
    msg = medicine_ui.medicine_card(result)
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Здоровье · Лекарство"
    store.last_surface[str(cid)] = "health"
    store.last_action[str(cid)] = ("role", "medicine", question)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_back_keyboard())
