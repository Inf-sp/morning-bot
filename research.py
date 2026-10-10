"""Research-first: слой доверенных данных (Wikidata, веб-поиск).

Принцип: сначала получить факты из источника, затем дать их LLM как источник истины -
вместо «уверенной фантазии». Источники бесплатные, без ключей. TTL-кеш по образцу
weather._WX_CACHE.
"""
import logging
import re
import time

import requests

_log = logging.getLogger(__name__)
import config
import tracking
import api_usage
import provider_runtime

_WIKI_UA = {"User-Agent": "morning-bot/1.0"}

_ENGLISH_SEARCH_SCENARIOS = {
    "concert_specific", "game_releases",
}
_ENGLISH_PLACE_NAMES = {
    "нидерланды": "Netherlands", "алкмар": "Alkmaar", "амстердам": "Amsterdam",
    "германия": "Germany", "франция": "France", "бельгия": "Belgium",
    "испания": "Spain", "италия": "Italy", "великобритания": "United Kingdom",
    "сша": "United States", "польша": "Poland", "швеция": "Sweden",
    "дания": "Denmark", "португалия": "Portugal", "исландия": "Iceland",
}
_CYRILLIC_TRANSLITERATION = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def english_search_query(query: str, scenario: str = "") -> str:
    """Нормализует автоматический продуктовый поиск до английской латиницы."""
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    if scenario not in _ENGLISH_SEARCH_SCENARIOS or not re.search(r"[А-Яа-яЁё]", text):
        return text
    for russian, english in _ENGLISH_PLACE_NAMES.items():
        text = re.sub(rf"\b{re.escape(russian)}\b", english, text, flags=re.I)
    parts = []
    for char in text:
        lower = char.casefold()
        replacement = _CYRILLIC_TRANSLITERATION.get(ord(lower))
        if replacement is None:
            parts.append(char)
        elif char.isupper():
            parts.append(replacement[:1].upper() + replacement[1:])
        else:
            parts.append(replacement)
    return re.sub(r"\s+", " ", "".join(parts)).strip()

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
            }, headers=_WIKI_UA, timeout=tracking.bounded_timeout(8))
            items = r.json().get("search", [])
            # берём первый результат у которого description содержит city/municipality/город
            for it in items:
                desc = (it.get("description") or "").lower()
                if any(w in desc for w in ("city", "town", "municipality", "город", "gemeente", "stad")):
                    return it["id"]
            if items:
                return items[0]["id"]
        except Exception:
            _log.debug("_wd_qid: ignored error", exc_info=True)
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
                         headers=_WIKI_UA, timeout=tracking.bounded_timeout(12))
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


# ================= TAVILY =================

_TV_CACHE: dict = {}    # query -> (ts, results)
_TAVILY_SCENARIOS = {
    "explicit_research": {"ttl": 24 * 3600, "economy": True, "advanced": False},
    "explicit_research_advanced": {"ttl": 24 * 3600, "economy": True, "advanced": True},
    "concert_specific": {"ttl": 12 * 3600, "economy": False, "advanced": False},
    "game_releases": {"ttl": 7 * 86400, "economy": False, "advanced": False},
}
_EXPLICIT_RESEARCH_RE = re.compile(
    r"\b(?:найди\s+(?:актуальн|источник)|что\s+сейчас\s+известно|проверь\s+в\s+интернете|"
    r"какие\s+новые|что\s+изменил|найди\s+источники)\b", re.I,
)


def requires_explicit_web_search(text: str) -> bool:
    return bool(_EXPLICIT_RESEARCH_RE.search(str(text or "")))


def _tavily_allowed(scenario: str, *, search_depth: str = "basic") -> bool:
    policy = _TAVILY_SCENARIOS.get(str(scenario or ""))
    if (not policy or search_depth not in ("basic", "advanced")
            or (search_depth == "advanced" and not policy["advanced"])):
        api_usage.record_tavily_event(scenario, "skipped_policy")
        return False
    budget = api_usage.tavily_budget()
    if budget["mode"] == "blocked" or provider_runtime.tavily_monthly_quota_exhausted():
        api_usage.record_tavily_event(scenario, "skipped_quota")
        return False
    if budget["mode"] == "economy" and not policy["economy"]:
        api_usage.record_tavily_event(scenario, "skipped_policy")
        return False
    return True


def tavily_search(query: str, max_results: int = 5, include_domains=None, *,
                  scenario: str = "", search_depth: str = "basic",
                  topic: str = "general", time_range: str = "",
                  start_date: str = "", end_date: str = "") -> list:
    """Поиск через Tavily. Возвращает list[{title, url, content}] или [] при ошибке/нет ключа."""
    query = english_search_query(query, scenario)
    if not config.TAVILY_API_KEY or not _tavily_allowed(scenario, search_depth=search_depth):
        return []
    domains = tuple(str(x).strip().casefold() for x in (include_domains or []) if str(x).strip())
    ttl = _TAVILY_SCENARIOS[scenario]["ttl"]
    normalized_query = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    key = (
        f"{normalized_query}:{max_results}:{','.join(domains)}:{search_depth}:"
        f"{topic}:{time_range}:{start_date}:{end_date}"
    )
    cached = _TV_CACHE.get(key)
    if cached and time.time() - cached[0] < ttl:
        api_usage.record_tavily_event(scenario, "cache_hits")
        return cached[1]
    try:
        payload = {
            "api_key": config.TAVILY_API_KEY,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if topic in {"general", "finance"}:
            payload["topic"] = topic
        if time_range in {"day", "week", "month", "year", "d", "w", "m", "y"}:
            payload["time_range"] = time_range
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date or ""):
            payload["start_date"] = start_date
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date or ""):
            payload["end_date"] = end_date
        if domains:
            payload["include_domains"] = list(domains)
        r = requests.post(
            "https://api.tavily.com/search",
            json=payload,
            timeout=tracking.bounded_timeout(15),
        )
        ok = 200 <= r.status_code < 300
        error = "" if ok else f"HTTP {r.status_code} {r.text[:240]}"
        api_usage.record_request("tavily", ok=ok, status_code=r.status_code,
                                 units={"credits": 1} if ok else {},
                                 error=error)
        if not ok:
            _log.warning("tavily_search failed: HTTP %s", r.status_code)
            return []
        results = r.json().get("results", [])
        _TV_CACHE[key] = (time.time(), results)
        api_usage.record_tavily_event(scenario, search_depth, credits=1)
        return results
    except Exception as e:
        api_usage.record_request("tavily", ok=False, error=type(e).__name__)
        _log.warning("tavily_search failed: %s", str(e)[:120])
        return []


def web_snippet(query: str, max_chars: int = 1200, *, scenario: str = "", allow_tavily=False,
                search_priority: str = "firecrawl") -> str:
    """Top snippets for an explicitly declared web-search scenario."""
    results = web_search(query, max_results=3, scenario=scenario, allow_tavily=allow_tavily,
                         search_priority=search_priority)
    parts, total = [], 0
    for r in results:
        chunk = (r.get("content") or "").strip()
        if chunk and total + len(chunk) < max_chars:
            parts.append(chunk)
            total += len(chunk)
    return "\n---\n".join(parts)


def firecrawl_search(query: str, max_results: int = 5, *, topic: str = "general",
                     time_range: str = "") -> list:
    """Основной внешний поиск; Tavily подключается выше только как резерв.
    Возвращает list[{title, url, content}] или [] при ошибке/нет ключа."""
    if not config.FIRECRAWL_API_KEY:
        return []
    try:
        payload = {"query": query, "limit": max_results}
        r = requests.post(
            "https://api.firecrawl.dev/v1/search",
            json=payload,
            headers={"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"},
            timeout=tracking.bounded_timeout(18),
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
            "content": (
                row.get("snippet") or row.get("description")
                or row.get("markdown") or ""
            ),
            "published_date": (
                row.get("date") or row.get("published_date")
                or row.get("publishedAt") or ""
            ),
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




def web_search(query: str, max_results: int = 5, include_domains=None, *, scenario: str = "",
               allow_tavily: bool = False, search_priority: str = "firecrawl",
               topic: str = "general", time_range: str = "",
               start_date: str = "", end_date: str = "",
               require_published_date: bool = False) -> list:
    """Search only through an explicitly chosen provider policy.

    Tavily is not a universal fallback: callers must opt in with one of the
    current-information scenarios above.
    """
    query = english_search_query(query, scenario)
    out, seen = [], set()
    domains = tuple(str(value).strip().casefold() for value in (include_domains or []) if str(value).strip())
    tavily_enabled = bool(allow_tavily and _tavily_allowed(scenario))
    providers = ((tavily_search, firecrawl_search) if search_priority == "tavily" and tavily_enabled
                 else ((firecrawl_search, tavily_search) if tavily_enabled else (firecrawl_search,)))
    for provider in providers:
        rows = (provider(query, max_results=max_results, include_domains=domains,
                         scenario=scenario, topic=topic, time_range=time_range,
                         start_date=start_date, end_date=end_date)
                if provider is tavily_search else provider(
                    query, max_results=max_results, topic=topic, time_range=time_range,
                ))
        for item in rows:
            url = (item.get("url") or "").strip()
            if require_published_date and not (
                item.get("published_date") or item.get("publishedAt") or item.get("date")
            ):
                continue
            if domains:
                try:
                    from urllib.parse import urlparse
                    host = (urlparse(url).hostname or "").casefold()
                except Exception:
                    host = ""
                if not any(host == domain or host.endswith("." + domain) for domain in domains):
                    continue
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(item)
            if len(out) >= max_results:
                return out
        if out:
            return out
    return out
