"""Research-first: слой доверенных данных (Wikipedia, Wikidata, локальные факты о странах).

Принцип: сначала получить факты из источника, затем дать их LLM как источник истины -
вместо «уверенной фантазии». Источники бесплатные, без ключей. TTL-кеш по образцу
weather._WX_CACHE.
"""
import json
import logging
import re
import time
import random
import requests

_log = logging.getLogger(__name__)
import util
import config
import api_usage

_WIKI_UA = {"User-Agent": "morning-bot/1.0"}

_CF_CACHE = {}          # name.lower() -> (ts, dict)
_CF_TTL = 86400         # факты о стране стабильны - сутки



# ================= WIKIPEDIA =================
def _wiki_ru_title(name):
    """Русский заголовок статьи через langlink из англ. Википедии (точнее ловит место)."""
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "prop": "langlinks",
            "lllang": "ru", "lllimit": 1, "redirects": 1, "titles": name,
        }, headers=_WIKI_UA, timeout=10)
        for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
            if "missing" in p:
                continue
            for ll in (p.get("langlinks") or []):
                return ll.get("*") or ll.get("title") or ""
    except Exception:
        pass
    return ""


def _wiki_search_en(name):
    """English Wikipedia title через opensearch — работает с русскими именами городов."""
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", params={
            "action": "opensearch", "search": name, "limit": 1,
            "format": "json", "namespace": 0
        }, headers=_WIKI_UA, timeout=8)
        arr = r.json()
        return arr[1][0] if len(arr) > 1 and arr[1] else ""
    except Exception:
        return ""


def wiki_summary(title, lang):
    """Интро статьи по точному заголовку - только реальный текст Википедии."""
    try:
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "prop": "extracts",
            "exintro": 1, "explaintext": 1, "redirects": 1, "titles": title,
        }, headers=_WIKI_UA, timeout=10)
        for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
            if "missing" in p:
                continue
            extract = (p.get("extract") or "").strip()
            if extract:
                return extract
    except Exception:
        pass
    return ""

def _clean_wiki(s):
    """Чистит артефакты explaintext: языковые пометки, пустые скобки, сноски."""
    s = re.sub(r"\(\s*(?:нид|англ|МФА|лат|нем|фр|Dutch|IPA)\.?[^)]*\)", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s.strip()

_BUREAUCRATIC = re.compile(
    r'\b(classif(?:ied|ication)|global\s+city|gawc|gamma\s*\+?|tier|'
    r'member(?:ship)?\s+of|ranked\s+(?:as|in)|ranking|network\s+of|'
    r'designation|listed\s+as|status\s+of|organisation|organization|'
    r'association\s+of|index(?:ed)?|municipal(?:ity|ities))\b',
    re.I
)

def _is_dubious_record(s):
    """Предложения с конкретными историческими рекордами (температуры, даты) неверифицируемы для конкретного города."""
    sl = s.lower()
    has_superlative = bool(re.search(r'\bсам(?:ый|ая|ое|ые)\b|\bнаибол', sl))
    has_temp_number = bool(re.search(r'[-−]\s*\d+[,.]?\d*\s*(?:°|градус)', sl))
    has_record = bool(re.search(r'\bрекорд', sl))
    return (has_superlative and has_temp_number) or has_record

def _extract_sents(extract):
    """Предложения из вики-интро: чистим, фильтруем дефинитивные и дубиозные."""
    if not extract:
        return []
    clean = _clean_wiki(extract)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if len(s.strip()) > 40]
    # Дефинитивные: "X — город..." (рус) и "X is a city..." (англ)
    sents = [s for s in sents if not re.match(r"^.{0,60}[—–\-]", s)]
    sents = [s for s in sents if not re.match(r"^.{0,80}\bis\s+a(?:n)?\s+\w+", s, re.I)]
    sents = [s for s in sents if not _is_dubious_record(s)]
    sents = [s for s in sents if not _BUREAUCRATIC.search(s)]
    return sents

def wiki_sentences(name):
    """Список кандидатов-предложений из RU + EN Википедии (до 8 штук)."""
    if not name:
        return []
    all_sents, seen = [], set()

    # 1) RU Wikipedia
    ru_title = name if re.search(r"[А-Яа-яЁё]", name) else _wiki_ru_title(name)
    if ru_title:
        for s in _extract_sents(wiki_summary(ru_title, "ru")):
            if s not in seen:
                all_sents.append(s); seen.add(s)

    # 2) EN Wikipedia — обычно богаче для европейских городов
    en_title = _wiki_search_en(name)
    if en_title:
        for s in _extract_sents(wiki_summary(en_title, "en")):
            if s not in seen:
                all_sents.append(s); seen.add(s)

    return all_sents[:8]

def wiki_fact(name):
    """Реальный факт о месте/стране из Википедии. Источник правды - Wikipedia, без LLM."""
    sents = wiki_sentences(name)
    if not sents:
        return ""
    return random.choice(sents)


# ================= WIKIDATA =================
_WDF_CACHE = {}   # name -> (ts, dict[str,str])
_WDF_TTL = 86400


def _wd_qid(name_clean: str) -> str:
    """QID города из Wikidata по имени (поиск по ru+en)."""
    for lang in ("ru", "en"):
        try:
            r = requests.get("https://www.wikidata.org/w/api.php", params={
                "action": "wbsearchentities", "search": name_clean,
                "language": lang, "type": "item", "limit": 3, "format": "json"
            }, headers=_WIKI_UA, timeout=8)
            items = r.json().get("search", [])
            # берём первый результат у которого description содержит city/municipality/город
            for it in items:
                desc = (it.get("description") or "").lower()
                if any(w in desc for w in ("city", "town", "municipality", "город", "gemeente", "stad")):
                    return it["id"]
            if items:
                return items[0]["id"]
        except Exception:
            pass
    return ""


def wikidata_city_facts(name: str) -> dict:
    """Структурированные факты о городе из Wikidata: {тип: предложение}.

    Без LLM, без ключей. Типы: founded, population, area.
    """
    name_clean = (name or "").strip()
    if not name_clean:
        return {}
    key = name_clean.lower()
    hit = _WDF_CACHE.get(key)
    if hit and time.time() - hit[0] < _WDF_TTL:
        return hit[1]
    qid = _wd_qid(name_clean)
    facts: dict = {}
    if not qid:
        _WDF_CACHE[key] = (time.time(), facts)
        return facts
    try:
        r = requests.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
                         headers=_WIKI_UA, timeout=12)
        claims = r.json().get("entities", {}).get(qid, {}).get("claims", {})

        # P571 — год основания
        p571 = claims.get("P571", [])
        if p571:
            tstr = (p571[0].get("mainsnak", {}).get("datavalue", {})
                    .get("value", {}).get("time", ""))
            year = tstr.lstrip("+").split("-")[0]
            if year.isdigit() and int(year) > 0:
                facts["founded"] = f"{name_clean} основан в {year} году."

        # P1082 — население (берём последнее/наибольшее значение)
        p1082 = claims.get("P1082", [])
        if p1082:
            amounts = []
            for c in p1082:
                amt = (c.get("mainsnak", {}).get("datavalue", {})
                       .get("value", {}).get("amount", ""))
                try:
                    amounts.append(int(float(amt)))
                except (ValueError, TypeError):
                    pass
            if amounts:
                pop = max(amounts)
                if pop > 500:
                    facts["population"] = f"Население {name_clean} — {pop:,} человек.".replace(",", " ")

        # P2046 — площадь (км²)
        p2046 = claims.get("P2046", [])
        if p2046:
            amt = (p2046[0].get("mainsnak", {}).get("datavalue", {})
                   .get("value", {}).get("amount", ""))
            try:
                area = float(amt)
                if area > 0:
                    facts["area"] = f"Площадь {name_clean} — {area:.0f} км²."
            except (ValueError, TypeError):
                pass

    except Exception as e:
        _log.warning("research: wikidata_city_facts(%s/%s) failed: %s", name_clean, qid, e)

    _WDF_CACHE[key] = (time.time(), facts)
    return facts


# ================= COUNTRY FACTS =================
_COUNTRY_FACTS = {
    "NL": {"capital": "Amsterdam", "languages": ["Dutch"], "region": "Europe", "currency": "EUR"},
    "BE": {"capital": "Brussels", "languages": ["Dutch", "French", "German"], "region": "Europe", "currency": "EUR"},
    "DE": {"capital": "Berlin", "languages": ["German"], "region": "Europe", "currency": "EUR"},
    "FR": {"capital": "Paris", "languages": ["French"], "region": "Europe", "currency": "EUR"},
    "GB": {"capital": "London", "languages": ["English"], "region": "Europe", "currency": "GBP"},
    "ES": {"capital": "Madrid", "languages": ["Spanish"], "region": "Europe", "currency": "EUR"},
    "IT": {"capital": "Rome", "languages": ["Italian"], "region": "Europe", "currency": "EUR"},
    "AT": {"capital": "Vienna", "languages": ["German"], "region": "Europe", "currency": "EUR"},
    "CH": {"capital": "Bern", "languages": ["German", "French", "Italian", "Romansh"], "region": "Europe", "currency": "CHF"},
    "PL": {"capital": "Warsaw", "languages": ["Polish"], "region": "Europe", "currency": "PLN"},
    "SE": {"capital": "Stockholm", "languages": ["Swedish"], "region": "Europe", "currency": "SEK"},
    "DK": {"capital": "Copenhagen", "languages": ["Danish"], "region": "Europe", "currency": "DKK"},
    "PT": {"capital": "Lisbon", "languages": ["Portuguese"], "region": "Europe", "currency": "EUR"},
    "US": {"capital": "Washington, D.C.", "languages": ["English"], "region": "Americas", "currency": "USD"},
    "CA": {"capital": "Ottawa", "languages": ["English", "French"], "region": "Americas", "currency": "CAD"},
    "JP": {"capital": "Tokyo", "languages": ["Japanese"], "region": "Asia", "currency": "JPY"},
}

def country_facts(name):
    """Проверенные факты о стране -> {cc, capital, languages, region, currency} или {}."""
    name = (name or "").strip()
    if not name:
        return {}
    key = name.lower()
    hit = _CF_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CF_TTL:
        return hit[1]
    cc = util.cc_of(name)
    facts = dict(_COUNTRY_FACTS.get((cc or "").upper(), {}))
    out = {"cc": (cc or "").upper(), **facts} if cc or facts else {}
    _CF_CACHE[key] = (time.time(), out)
    return out

def facts_block(d):
    """Строка-граундинг для промпта из фактов о стране."""
    if not d:
        return ""
    parts = []
    if d.get("capital"):
        parts.append(f"столица: {d['capital']}")
    if d.get("languages"):
        parts.append("язык(и): " + ", ".join(d["languages"][:4]))
    if d.get("region"):
        parts.append(f"регион: {d['region']}")
    if d.get("currency"):
        parts.append(f"валюта: {d['currency']}")
    return "; ".join(parts)

def grounded(d):
    """Есть ли реальные данные (для advisory-лога «ответ без источника»)."""
    return bool(d and (d.get("capital") or d.get("languages")))


# ================= NL WORLD RECORDS =================
_NL_RECORDS_CACHE: dict = {}
_NL_RECORDS_TTL = 86400 * 7  # неделя


def _extract_record_sents(extract: str) -> list:
    """Предложения с конкретными данными из вики-статьи (числа / рекорды / мировые показатели)."""
    if not extract:
        return []
    clean = _clean_wiki(extract)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if len(s.strip()) > 40]
    sents = [s for s in sents if not re.match(r"^.{0,60}[—–\-]", s)]
    sents = [s for s in sents if not re.match(r"^.{0,80}\bis\s+a(?:n)?\s+\w+", s, re.I)]
    return [s for s in sents
            if re.search(r'\d|\bfirst\b|\blargest\b|\bmost\b|\brecord\b|\bworld\b', s, re.I)]


def nl_world_records() -> list:
    """Факты-рекорды о Нидерландах из Википедии."""
    key = "nl_records"
    hit = _NL_RECORDS_CACHE.get(key)
    if hit and time.time() - hit[0] < _NL_RECORDS_TTL:
        return hit[1]

    seen: set = set()
    result: list = []
    for page in ("Records of the Netherlands", "Netherlands"):
        en_title = _wiki_search_en(page) or page
        extract = wiki_summary(en_title, "en")
        for s in _extract_record_sents(extract):
            if s not in seen:
                result.append(s)
                seen.add(s)

    _NL_RECORDS_CACHE[key] = (time.time(), result)
    _log.info("research: nl_world_records → %d sentences", len(result))
    return result


# ================= GEMINI SEARCH =================
_GSR_CACHE = {}   # place_key -> (ts, str)
_GSR_TTL = 3600   # 1 час


_GSR_BAD = re.compile(
    r"не подходит|не является|не относится|ошибка|вместо этого|"
    r"does not|instead|however|this text|incorrect",
    re.I,
)


def gemini_search_fact(city: str, country: str, cc: str = "",
                       avoid: list[str] | None = None) -> str:
    """Реальный факт о городе через Gemini + Google Search grounding.

    Промпт на английском для точного поиска, cc исключает путаницу городов.
    Ответ запрашиваем на русском. Валидирует что ответ — факт, а не мета-объяснение.
    """
    if not config.GEMINI_API_KEY:
        return ""
    place = f"{city}, {country}" if country else city
    cache_key = place.lower()
    hit = _GSR_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _GSR_TTL:
        return hit[1]

    avoid_block = ""
    if avoid:
        previews = "; ".join(a[:80] for a in avoid[:5])
        avoid_block = f" Do not repeat facts similar to: {previews}."

    cc_hint = f" Country ISO code: {cc}." if cc else ""
    prompt = (
        f"Find one real, little-known, surprising fact specifically about the city {city}, {country}.{cc_hint} "
        "This must be about THIS city only — not any other city with a similar name. "
        "Requirements: "
        "(1) local specifics — history, laws, architecture, infrastructure, or local mentality; "
        "(2) wow effect — even a long-term local resident learns something new; "
        "(3) max 2 short sentences, no filler; "
        "(4) output only the fact itself — no preamble like 'Here is a fact:'; "
        "(5) answer in Russian language."
        + avoid_block
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            f"?key={config.GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"maxOutputTokens": 200, "temperature": 0.3},
            },
            timeout=20,
        )
        if r.status_code == 200:
            parts = (r.json().get("candidates", [{}])[0]
                     .get("content", {}).get("parts", []))
            text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
            if text and not _GSR_BAD.search(text):
                _GSR_CACHE[cache_key] = (time.time(), text)
                return text
            if text:
                _log.warning("research: gemini_search_fact discarded bad response for %s", place)
        else:
            _log.warning("research: gemini_search_fact %s → HTTP %s", place, r.status_code)
    except Exception as e:
        _log.warning("research: gemini_search_fact(%s) failed: %s", place, e)
    return ""


# ================= GEMINI MULTI-FACT =================

_GMULTI_BAD = re.compile(
    r"не подходит|не является|не относится|ошибка|вместо этого|"
    r"does not|instead|however|this text|incorrect",
    re.I,
)


def _parse_json_list(text: str) -> list:
    """Извлекает JSON-массив из текста (Gemini может добавить ```json ... ``` вокруг)."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.M)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.M).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        pass
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group())
            return parsed if isinstance(parsed, list) else []
        except Exception:
            pass
    return []


def gemini_search_facts_multi(city: str, country: str, cc: str = "",
                               aspect: str = "history",
                               avoid: list | None = None) -> list[str]:
    """3-5 фактов о городе через Gemini + Google Search по одному аспекту.

    Возвращает список строк (факты на русском).
    """
    if not config.GEMINI_API_KEY:
        return []
    avoid = avoid or []
    place = f"{city}, {country}" if country else city
    cc_hint = f" Country ISO code: {cc}." if cc else ""
    avoid_block = ""
    if avoid:
        previews = "; ".join(a[:60] for a in avoid[:8])
        avoid_block = f" Do not repeat facts similar to: {previews}."
    prompt = (
        f"Give 3-5 real, little-known, surprising facts about the {aspect} of {city}, {country}.{cc_hint} "
        "Must be specifically about THIS city only — not any other city with a similar name. "
        "Requirements: each fact max 2 sentences, prefer specific numbers/dates/names, wow effect. "
        "Skip generic phrases like 'rich history' or 'cultural center'. "
        "Output ONLY a JSON array of strings in Russian: [\"fact1\",\"fact2\",...]"
        + avoid_block
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            f"?key={config.GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"maxOutputTokens": 700, "temperature": 0.5},
            },
            timeout=25,
        )
        if r.status_code != 200:
            _log.warning("research: gemini_search_facts_multi %s → HTTP %s", place, r.status_code)
            return []
        parts_list = (r.json().get("candidates", [{}])[0]
                      .get("content", {}).get("parts", []))
        text = " ".join(p.get("text", "") for p in parts_list if p.get("text")).strip()
        arr = _parse_json_list(text)
        return [
            f for f in arr
            if isinstance(f, str) and len(f.strip()) > 20 and not _GMULTI_BAD.search(f)
        ]
    except Exception as e:
        _log.warning("research: gemini_search_facts_multi(%s, %s) failed: %s", city, aspect, e)
        return []


# ================= TAVILY =================

_TV_CACHE: dict = {}    # query -> (ts, results)
_TV_TTL = 86400         # 24h — запросы дорогие, кешируем на сутки
def tavily_search(query: str, max_results: int = 5) -> list:
    """Поиск через Tavily. Возвращает list[{title, url, content}] или [] при ошибке/нет ключа."""
    if not config.TAVILY_API_KEY:
        return []
    key = f"{query}:{max_results}"
    cached = _TV_CACHE.get(key)
    if cached and time.time() - cached[0] < _TV_TTL:
        return cached[1]
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": config.TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=15,
        )
        ok = 200 <= r.status_code < 300
        api_usage.record_request("tavily", ok=ok, status_code=r.status_code,
                                 units={"credits": 1} if ok else {},
                                 error="" if ok else f"HTTP {r.status_code}")
        if not ok:
            _log.warning("tavily_search failed: HTTP %s", r.status_code)
            return []
        results = r.json().get("results", [])
        _TV_CACHE[key] = (time.time(), results)
        return results
    except Exception as e:
        api_usage.record_request("tavily", ok=False, error=type(e).__name__)
        _log.warning("tavily_search failed: %s", str(e)[:120])
        return []


def tavily_snippet(query: str, max_chars: int = 1200) -> str:
    """Top-3 Tavily сниппета, склеенные для LLM-промпта. Пустая строка если ключа нет."""
    results = tavily_search(query, max_results=3)
    parts, total = [], 0
    for r in results:
        chunk = (r.get("content") or "").strip()
        if chunk and total + len(chunk) < max_chars:
            parts.append(chunk)
            total += len(chunk)
    return "\n---\n".join(parts)


def firecrawl_search(query: str, max_results: int = 5) -> list:
    """Поиск через Firecrawl — второй независимый источник рядом с Tavily.
    Возвращает list[{title, url, content}] или [] при ошибке/нет ключа."""
    if not config.FIRECRAWL_API_KEY:
        return []
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/search",
            json={"query": query, "limit": max_results, "sources": ["web"]},
            headers={"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"},
            timeout=18,
        )
        ok = 200 <= r.status_code < 300
        api_usage.record_request("firecrawl", ok=ok, status_code=r.status_code,
                                 error="" if ok else f"HTTP {r.status_code}")
        if not ok:
            _log.warning("firecrawl_search failed: HTTP %s", r.status_code)
            return []
        data = r.json().get("data") or []
        return [{
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "content": row.get("description") or row.get("markdown") or "",
        } for row in data if isinstance(row, dict)]
    except Exception as e:
        api_usage.record_request("firecrawl", ok=False, error=type(e).__name__)
        _log.warning("firecrawl_search failed: %s", str(e)[:120])
        return []


def firecrawl_snippet(query: str, max_chars: int = 1200) -> str:
    """Top-3 Firecrawl сниппета, склеенные для LLM-промпта. Пустая строка если ключа нет."""
    results = firecrawl_search(query, max_results=3)
    parts, total = [], 0
    for r in results:
        chunk = (r.get("content") or "").strip()
        if chunk and total + len(chunk) < max_chars:
            parts.append(chunk)
            total += len(chunk)
    return "\n---\n".join(parts)




def web_search(query: str, max_results: int = 5) -> list:
    """Web search через Tavily, один формат результата."""
    out, seen = [], set()
    for item in tavily_search(query, max_results=max_results):
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(item)
        if len(out) >= max_results:
            return out
    return out
