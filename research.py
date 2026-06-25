"""Research-first: слой доверенных данных (Wikipedia, REST Countries) для фактических ответов.

Принцип: сначала получить факты из источника, затем дать их LLM как источник истины -
вместо «уверенной фантазии». Источники бесплатные, без ключей. TTL-кеш по образцу
weather._WX_CACHE.
"""
import logging
import re
import time
import random
import requests

_log = logging.getLogger(__name__)
import util

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
    s = re.sub(r"\(\s*(?:нид|англ|МФА|лат|нем|фр)\.?[^)]*\)", "", s)
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

def wiki_sentences(name):
    """Список кандидатов-предложений для факта о месте (без дефинитивного первого)."""
    if not name:
        return []
    ru_title = name if re.search(r"[А-Яа-яЁё]", name) else _wiki_ru_title(name)
    extract = (wiki_summary(ru_title, "ru") if ru_title else "") or wiki_summary(name, "en")
    if not extract:
        return []
    extract = _clean_wiki(extract)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", extract) if len(s.strip()) > 40]
    # Убираем дефинитивные предложения ("X — это город...") вне зависимости от их количества
    sents = [s for s in sents if not re.match(r"^.{0,60}[—–-]", s)]
    sents = [s for s in sents if not _is_dubious_record(s)]
    return sents[:6]

def wiki_fact(name):
    """Реальный факт о месте/стране из Википедии. Источник правды - Wikipedia, без LLM."""
    sents = wiki_sentences(name)
    if not sents:
        return ""
    return random.choice(sents)


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
