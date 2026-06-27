"""Research-first: слой доверенных данных (Wikipedia, Wikidata, REST Countries, Perplexity).

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


def wikidata_city_sentence(name: str) -> str:
    """Один факт из Wikidata (обратная совместимость)."""
    facts = wikidata_city_facts(name)
    return random.choice(list(facts.values())) if facts else ""


# ================= WEATHER ARCHIVE =================
_WR_CACHE: dict = {}   # (lat, lon) -> (ts, dict)
_WR_TTL = 86400


def weather_records(lat: float, lon: float, tz: str = "UTC", years: int = 10) -> dict:
    """Реальные погодные рекорды за N лет из Open-Meteo Archive API.

    Возвращает {тип: строка} — heat/cold/rain. Без LLM, реальные данные.
    """
    from datetime import date
    key = (round(lat, 2), round(lon, 2))
    hit = _WR_CACHE.get(key)
    if hit and time.time() - hit[0] < _WR_TTL:
        return hit[1]

    end = date.today()
    start = date(end.year - years, 1, 1)
    months = util._MONTHS
    facts: dict = {}
    try:
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": lat, "longitude": lon,
            "start_date": str(start), "end_date": str(end),
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": tz,
        }, timeout=30)
        if r.status_code != 200:
            _log.warning("research: weather_records → HTTP %s", r.status_code)
            return {}
        d = r.json().get("daily", {})
        times = d.get("time", [])
        tmaxs = d.get("temperature_2m_max", [])
        tmins = d.get("temperature_2m_min", [])
        rains = d.get("precipitation_sum", [])

        def _fmt(date_str: str) -> str:
            from datetime import datetime as _dt
            dt = _dt.strptime(date_str, "%Y-%m-%d")
            return f"{dt.day} {months[dt.month - 1]} {dt.year}"

        heat = [(t, v) for t, v in zip(times, tmaxs) if v is not None]
        if heat:
            ds, val = max(heat, key=lambda x: x[1])
            facts["heat"] = f"Рекорд жары за {years} лет: {val:+.0f}°C · {_fmt(ds)}"

        cold = [(t, v) for t, v in zip(times, tmins) if v is not None]
        if cold:
            ds, val = min(cold, key=lambda x: x[1])
            facts["cold"] = f"Рекорд холода за {years} лет: {val:+.0f}°C · {_fmt(ds)}"

        rain = [(t, v) for t, v in zip(times, rains) if v is not None and v > 5]
        if rain:
            ds, val = max(rain, key=lambda x: x[1])
            facts["rain"] = f"Рекордный ливень за {years} лет: {val:.0f} мм · {_fmt(ds)}"

    except Exception as e:
        _log.warning("research: weather_records(%.2f, %.2f) failed: %s", lat, lon, e)
        return {}

    _WR_CACHE[key] = (time.time(), facts)
    return facts


# ================= REST COUNTRIES =================
def country_facts(name):
    """Проверенные факты о стране -> {cc, capital, languages, region, currency} или {}."""
    name = (name or "").strip()
    if not name:
        return {}
    key = name.lower()
    hit = _CF_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CF_TTL:
        return hit[1]
    cc = util.cc_of(name)   # офлайн ru/en -> ISO; для известных стран запрос точнее
    url = (f"https://restcountries.com/v3.1/alpha/{cc}" if cc
           else f"https://restcountries.com/v3.1/name/{name}")
    out = {}
    try:
        r = requests.get(url, params={"fields": "cca2,capital,languages,region,currencies"}, timeout=12)
        if r.status_code == 200:
            arr = r.json()
            c = arr[0] if isinstance(arr, list) and arr else (arr if isinstance(arr, dict) else None)
            if c:
                langs = list((c.get("languages") or {}).values())
                cur = list((c.get("currencies") or {}).keys())
                cap = c.get("capital") or []
                out = {"cc": c.get("cca2", "") or cc, "capital": cap[0] if cap else "",
                       "languages": langs, "region": c.get("region", ""),
                       "currency": cur[0] if cur else ""}
    except Exception as e:
        _log.warning("research: country_facts(%s) failed, not caching: %s", name, e)
        return {}
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
