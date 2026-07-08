import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import api_usage
import access
import ai
import config
import store

_log = logging.getLogger(__name__)

NEWS_CACHE_KEY = "personal_news_cache.json"
NEWS_STATS_KEY = "personal_news_stats.json"
NEWS_MONTHLY_CREDIT_BUDGET = 1000
TAVILY_MONTHLY_CREDIT_LIMIT = 1000
NEWS_DAILY_CREDIT_BUDGET = 50
NEWS_HARD_MONTHLY_LIMIT = 1000
NEWS_HISTORY_KEY = "personal_news_history.json"
NEWS_MAX_ITEMS = 5
NEWS_MIN_RELEVANCE_SCORE = 70
NEWS_HISTORY_DAYS = 14
REFRESH_COOLDOWN_SEC = 6 * 3600

_CATEGORY_LABELS = {
    "city": "📍 Алкмар",
    "north_holland": "📍 Noord-Holland",
    "netherlands": "🇳🇱 Нидерланды",
    "transport": "🚆 Транспорт",
    "housing_money": "🏠 Жильё и деньги",
    "documents_study": "🧾 Документы и учёба",
    "health": "🩺 Медицина",
    "tech": "🤖 AI / технологии",
    "leisure": "🎬 Досуг",
    "food": "🍽 Еда",
    "wardrobe_weather": "👕 Гардероб и погода",
    "travel": "✈️ Путешествия",
    "language": "🇳🇱 Язык",
}

_ACTION_LABELS = {
    "read": "Подробнее",
    "watch": "Что известно",
    "listen": "Послушать",
    "visit": "Посмотреть",
    "prepare": "Разобраться",
    "none": "Подробнее",
}

_OFFICIAL_DOMAINS = {
    "city": ("gemeentealkmaar.nl", "alkmaarsdagblad.nl", "nhnieuws.nl", "streekstadcentraal.nl",
             "noordhollandsdagblad.nl", "indebuurt.nl", "alkmaarcentraal.nl"),
    "north_holland": ("nhnieuws.nl", "noordhollandsdagblad.nl", "streekstadcentraal.nl"),
    "netherlands": ("nos.nl", "nu.nl", "rtlnieuws.nl", "ad.nl", "telegraaf.nl",
                    "rijksoverheid.nl", "government.nl"),
    "transport": ("ns.nl", "9292.nl", "rijksoverheid.nl", "nos.nl"),
    "housing_money": ("rijksoverheid.nl", "belastingdienst.nl", "independer.nl", "nos.nl", "nu.nl"),
    "documents_study": ("duo.nl", "rijksoverheid.nl", "government.nl", "gemeentealkmaar.nl"),
    "health": ("rijksoverheid.nl", "rivm.nl", "ggd.nl", "thuisarts.nl", "apotheek.nl",
               "zorgwijzer.nl", "independer.nl"),
    "tech": ("openai.com", "apple.com", "telegram.org", "cloudflare.com", "ai.googleblog.com",
             "theverge.com", "techcrunch.com", "arstechnica.com", "railway.com"),
    "leisure": ("pathe.nl", "filmvandaag.nl", "ticketmaster.nl", "songkick.com",
                "bandsintown.com", "indebuurt.nl", "alkmaarcentraal.nl"),
    "food": ("ah.nl", "jumbo.com", "nvwa.nl", "indebuurt.nl", "alkmaarcentraal.nl"),
    "wardrobe_weather": ("knmi.nl", "weeronline.nl", "nos.nl", "nhnieuws.nl"),
    "travel": ("schiphol.nl", "nsinternational.com", "ns.nl", "rijksoverheid.nl"),
    "language": ("duo.nl", "inburgeren.nl", "rijksoverheid.nl", "gemeentealkmaar.nl"),
}

_QUERY_DEFINITIONS = [
    ("city", "nl", "Alkmaar nieuws vandaag", "local"),
    ("city", "nl", "gemeente Alkmaar nieuws wijziging", "local"),
    ("city", "nl", "Alkmaar evenementen dit weekend nieuw restaurant", "local_event"),
    ("north_holland", "nl", "Noord-Holland nieuws vandaag Alkmaar", "local"),
    ("netherlands", "nl", "Nederland nieuws vandaag regels wijziging inwoners", "country"),
    ("transport", "nl", "NS wijziging Nederland storing staking dienstregeling", "country"),
    ("housing_money", "nl", "huurwet Nederland wijziging huurtoeslag belasting", "country"),
    ("documents_study", "nl", "DUO wijziging Nederland inburgering gemeente", "country"),
    ("health", "nl", "zorgverzekering Nederland wijziging huisarts apotheek", "country"),
    ("tech", "en", "OpenAI Telegram Apple Cloudflare Railway API update outage pricing", "tech"),
    ("leisure", "nl", "bioscoop releases Nederland concerten Noord-Holland Alkmaar", "local_event"),
    ("food", "nl", "nieuw restaurant Alkmaar AH Jumbo product recall Nederland", "local_event"),
    ("wardrobe_weather", "nl", "KNMI Alkmaar Noord-Holland code geel regen wind UV", "urgent"),
    ("travel", "nl", "Schiphol NS International staking vertraging wijziging", "country"),
    ("language", "nl", "inburgering examen Nederlands cursus Alkmaar wijziging", "local_event"),
]

_CATEGORY_GROUPS = {
    "city": "local",
    "north_holland": "local",
    "netherlands": "netherlands",
    "transport": "netherlands",
    "housing_money": "netherlands",
    "documents_study": "netherlands",
    "health": "netherlands",
    "tech": "tech",
    "leisure": "leisure",
    "food": "leisure",
    "wardrobe_weather": "weather",
    "travel": "travel",
    "language": "language",
}

_CATEGORY_PRIORITY = [
    "city", "north_holland", "netherlands", "transport", "documents_study", "health",
    "housing_money", "tech", "leisure", "food", "wardrobe_weather", "travel", "language",
]

_SOURCE_NAMES = {
    "openai.com": "OpenAI",
    "telegram.org": "Telegram",
    "apple.com": "Apple",
    "cloudflare.com": "Cloudflare",
    "railway.com": "Railway",
    "gemeentealkmaar.nl": "Gemeente Alkmaar",
    "alkmaarsdagblad.nl": "Alkmaars Dagblad",
    "nhnieuws.nl": "NH Nieuws",
    "streekstadcentraal.nl": "Streekstad Centraal",
    "noordhollandsdagblad.nl": "Noordhollands Dagblad",
    "alkmaarcentraal.nl": "Alkmaar Centraal",
    "rijksoverheid.nl": "Rijksoverheid",
    "belastingdienst.nl": "Belastingdienst",
    "duo.nl": "DUO",
    "ns.nl": "NS",
    "nos.nl": "NOS",
    "nu.nl": "NU.nl",
    "rtlnieuws.nl": "RTL Nieuws",
    "ad.nl": "AD",
    "telegraaf.nl": "De Telegraaf",
    "zorgwijzer.nl": "Zorgwijzer",
    "independer.nl": "Independer",
    "knmi.nl": "KNMI",
    "schiphol.nl": "Schiphol",
}


@dataclass
class NewsItem:
    title: str
    summary: str
    url: str
    source: str
    published_at: str
    category: str
    language: str
    relevance_score: int
    why_important: str
    action_hint: str
    hash: str


def _now():
    return datetime.now(config.TZ)


def _iso_week_key(dt):
    year, week, _ = dt.isocalendar()
    return f"{year}-{week:02d}"


def cache_key(cid, period, now=None):
    now = now or _now()
    suffix = now.strftime("%Y-%m-%d") if period == "today" else _iso_week_key(now)
    return f"personal_news:{cid}:{period}:{suffix}"


def _period_max_age_days(period):
    return 7


def _period_cache_ttl(period):
    return 24 * 3600


def _canonical_url(url):
    try:
        p = urlparse(url)
        netloc = p.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+$", "", p.path or "")
        return urlunparse((p.scheme.lower() or "https", netloc, path, "", "", ""))
    except Exception:
        return (url or "").strip()


def _host(url):
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _title_key(title):
    text = re.sub(r"[^\w\s]", " ", (title or "").lower(), flags=re.U)
    stop = {"het", "een", "the", "and", "voor", "van", "met", "naar", "news", "nieuws"}
    return " ".join(w for w in text.split() if len(w) > 2 and w not in stop)[:140]


def _title_similarity(a, b):
    ta = set(_title_key(a).split())
    tb = set(_title_key(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, config.TZ)
        except Exception:
            return None
    value = str(value).replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            dt = datetime.fromisoformat(value) if fmt is None else datetime.strptime(value[:10], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=config.TZ)
        except Exception:
            continue
    return None


def _published_value(item):
    for key in ("published_at", "published_date", "publishedDate", "date", "datetime"):
        if item.get(key):
            return item.get(key)
    return None


def _category_max_age_days(category, urgency=None):
    if urgency == "urgent" or category == "wardrobe_weather":
        return 1
    if urgency == "local_event" or category in {"city", "north_holland", "leisure", "food", "language"}:
        return 7
    return 3


def _item_age_days(item, now=None):
    dt = _parse_dt(_published_value(item))
    if not dt:
        return None
    now = now or _now()
    return max(0, (now - dt).total_seconds() / 86400)


def _is_official_url(url):
    host = _host(url)
    return any(
        host.endswith(domain)
        for domains in _OFFICIAL_DOMAINS.values()
        for domain in domains
    )


def _is_fresh(item, period, now=None):
    dt = _parse_dt(_published_value(item))
    if not dt:
        return False
    now = now or _now()
    category = item.get("_category_hint") or item.get("category")
    urgency = item.get("_urgency")
    return dt >= now - timedelta(days=_category_max_age_days(category, urgency))


def _has_concrete_change(item):
    text = f"{item.get('title', '')} {item.get('content', '')}".lower()
    markers = (
        "new", "nieuw", "nieuwe", "wijzig", "change", "changed", "update", "price", "pricing",
        "limit", "outage", "premiere", "trailer", "season", "cancelled", "release", "tour",
        "recall", "waarschuwing", "tekort", "opened", "opening", "datum", "api",
        "verandering", "aangepast", "storing", "uitval", "seizoen", "prijs", "tarief",
        "vandaag", "weekend", "evenement", "concert", "staking", "vertraging", "dienstregeling",
        "werkzaamheden", "wegwerkzaamheden", "afsluiting", "gemeente", "subsidie", "toeslag",
        "verzekering", "premie", "huur", "belasting", "schiphol", "code geel", "wind", "regen",
        "hitte", "uv", "inburgering", "examen", "cursus", "restaurant", "cafe", "terugroepactie",
        "нов", "измен", "обнов", "цена", "тариф", "лимит", "сбой", "премьера", "сезон",
        "отмен", "релиз", "тур", "открыл", "открыт", "предупрежд", "дефицит",
    )
    return any(m in text for m in markers)


def strict_filter(items, period="today", now=None):
    out, seen_urls, seen_titles = [], set(), set()
    for raw in items or []:
        item = dict(raw)
        url = _canonical_url(item.get("url", ""))
        title = (item.get("title") or "").strip()
        if not url or not title or not _is_fresh(item, period, now):
            continue
        if not _has_concrete_change(item):
            continue
        tkey = _title_key(title)
        if url in seen_urls or tkey in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(tkey)
        item["url"] = url
        item["_date_missing"] = _parse_dt(_published_value(item)) is None
        out.append(item)
    return out


def _dedupe_semantic(items):
    groups = {}
    for item in items:
        key = _title_key(item.get("title", ""))
        tokens = set(key.split())
        matched = None
        for existing_key in groups:
            existing = set(existing_key.split())
            if tokens and existing and len(tokens & existing) / max(len(tokens | existing), 1) >= 0.72:
                matched = existing_key
                break
        groups[matched or key] = _prefer_item(groups.get(matched or key), item)
    return list(groups.values())


def _prefer_item(a, b):
    if not a:
        return b
    ah, bh = _host(a.get("url")), _host(b.get("url"))
    official = {d for domains in _OFFICIAL_DOMAINS.values() for d in domains}
    a_off = any(ah.endswith(d) for d in official)
    b_off = any(bh.endswith(d) for d in official)
    if b_off and not a_off:
        return b
    return a


def _stat_mutate(mutator):
    return store.mutate_kv(NEWS_STATS_KEY, mutator)


def _today_key(now=None):
    return (now or _now()).strftime("%Y-%m-%d")


def _month_key(now=None):
    return (now or _now()).strftime("%Y-%m")


def _inc_stat(name, amount=1, now=None):
    day = _today_key(now)
    month = _month_key(now)

    def mut(data):
        data.setdefault("days", {}).setdefault(day, {})
        data.setdefault("months", {}).setdefault(month, {})
        data["days"][day][name] = int(data["days"][day].get(name, 0)) + amount
        data["months"][month][name] = int(data["months"][month].get(name, 0)) + amount
        data["last_build_ts"] = int(time.time())
        return data, data["days"][day][name]

    return _stat_mutate(mut)


def _reserve_credits(credits, now=None):
    day = _today_key(now)
    month = _month_key(now)

    def mut(data):
        days = data.setdefault("days", {})
        months = data.setdefault("months", {})
        d = days.setdefault(day, {})
        m = months.setdefault(month, {})
        used_day = int(d.get("credits", 0))
        used_month = int(m.get("credits", 0))
        if used_day + credits > NEWS_DAILY_CREDIT_BUDGET:
            return data, False
        if used_month + credits > NEWS_MONTHLY_CREDIT_BUDGET or used_month + credits > NEWS_HARD_MONTHLY_LIMIT:
            return data, False
        d["credits"] = used_day + credits
        m["credits"] = used_month + credits
        d["tavily_calls"] = int(d.get("tavily_calls", 0)) + 1
        m["tavily_calls"] = int(m.get("tavily_calls", 0)) + 1
        return data, True

    return _stat_mutate(mut)


def budget_snapshot(now=None):
    data = store._load(NEWS_STATS_KEY)
    day = data.get("days", {}).get(_today_key(now), {})
    month = data.get("months", {}).get(_month_key(now), {})
    return {
        "today_credits": int(day.get("credits", 0)),
        "month_credits": int(month.get("credits", 0)),
        "tavily_calls": int(day.get("tavily_calls", 0)),
        "cache_hits": int(day.get("cache_hits", 0)),
        "filtered": int(day.get("filtered", 0)),
        "shown": int(day.get("shown", 0)),
        "errors": int(day.get("errors", 0)),
        "avg_card_size": int(day.get("avg_card_size", 0)),
        "last_build_ts": int(data.get("last_build_ts", 0)),
    }


def _cache_get(cid, period, allow_stale=False, now=None):
    key = cache_key(cid, period, now)
    entry = store._load(NEWS_CACHE_KEY).get(key)
    if not entry:
        return None
    age = time.time() - int(entry.get("ts", 0))
    if allow_stale or age <= _period_cache_ttl(period):
        return entry
    return None


def _cache_set(cid, period, entry, now=None):
    key = cache_key(cid, period, now)
    data = store._load(NEWS_CACHE_KEY)
    data[key] = entry
    store._save(NEWS_CACHE_KEY, data)


def _last_refresh(cid):
    return int((store.get_profile(cid).get("personal_news") or {}).get("last_refresh_ts", 0))


def _set_last_refresh(cid):
    prof = store.get_profile(cid)
    pn = dict(prof.get("personal_news") or {})
    pn["last_refresh_ts"] = int(time.time())
    prof["personal_news"] = pn
    store.set_profile(cid, prof)


def _queries_for(cid):
    return _queries_for_profile(_profile_context(cid))


def _queries_for_profile(profile):
    city = profile.get("city") or "Alkmaar"
    if str(city).lower() in {"алкмар", "alkmar"}:
        city = "Alkmaar"
    queries = []
    for category, language, query, urgency in _QUERY_DEFINITIONS:
        q = query.replace("Alkmaar", city)
        queries.append({
            "category": category,
            "query": q,
            "language": language,
            "domains": _OFFICIAL_DOMAINS.get(category),
            "urgency": urgency,
        })
    movies = " ".join(map(str, (profile.get("movies") or [])[:3])).strip()
    if movies:
        queries.append({
            "category": "leisure",
            "query": f"{movies[:120]} release Nederland streaming bioscoop",
            "language": "nl",
            "domains": _OFFICIAL_DOMAINS.get("leisure"),
            "urgency": "local_event",
        })
    artists = " ".join(map(str, (profile.get("artists") or [])[:3])).strip()
    if artists:
        queries.append({
            "category": "leisure",
            "query": f"{artists[:120]} concert Nederland Noord-Holland",
            "language": "nl",
            "domains": _OFFICIAL_DOMAINS.get("leisure"),
            "urgency": "local_event",
        })
    return queries


def _tavily_search(query, max_results=5, domains=None, time_range="week"):
    if not config.TAVILY_API_KEY:
        return []
    payload = {
        "api_key": config.TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "topic": "news",
    }
    if domains:
        payload["include_domains"] = list(domains)
    if time_range:
        payload["time_range"] = time_range
    try:
        r = requests.post("https://api.tavily.com/search", json=payload, timeout=18)
        r.raise_for_status()
        api_usage.record_request("tavily", ok=True, units={"credits": 1}, headers=r.headers)
        return r.json().get("results", [])
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        api_usage.record_request("tavily", ok=False, units={"credits": 1},
                                 status_code=status, error=type(e).__name__)
        raise


def _search_all(cid):
    return _search_all_for_profile(_profile_context(cid))


def _search_all_for_profile(profile):
    rows = []
    for spec in _queries_for_profile(profile):
        if not _reserve_credits(1):
            break
        try:
            max_results = 5
            time_range = "day" if spec["urgency"] == "urgent" else "week"
            for item in _call_tavily(
                spec["query"],
                max_results=max_results,
                domains=spec.get("domains"),
                time_range=time_range,
            ):
                item = dict(item)
                item["_category_hint"] = spec["category"]
                item["_query_language"] = spec["language"]
                item["_urgency"] = spec["urgency"]
                rows.append(item)
        except Exception as e:
            _inc_stat("errors")
            _log.warning("personal_news Tavily failed: %s", str(e)[:120])
    return rows


def _call_tavily(query, max_results=5, domains=None, time_range="week"):
    try:
        return _tavily_search(query, max_results=max_results, domains=domains, time_range=time_range)
    except TypeError:
        return _tavily_search(query, max_results=max_results, domains=domains)


def _profile_context(cid):
    s = store.get_settings(cid)
    return {
        "city": s.get("city") or "Алкмар",
        "country": s.get("country") or "Нидерланды",
        "cc": s.get("cc") or "NL",
        "movies": store.get_list(config.WATCHLIST_KEY, cid)[:20],
        "artists": store.get_list(config.ARTISTS_KEY, cid)[:20],
        "services": ["OpenAI", "Gemini", "Groq", "Cloudflare", "Railway", "Telegram",
                     "OpenWeather", "Pexels", "Unsplash", "Apple", "Mac", "VS Code"],
    }


def _history_for(cid, now=None):
    now = now or _now()
    cutoff = now - timedelta(days=NEWS_HISTORY_DAYS)
    data = store._load(NEWS_HISTORY_KEY)
    rows = []
    changed = False
    for row in data.get(str(cid), []) or []:
        sent_at = _parse_dt(row.get("date_sent"))
        if sent_at and sent_at >= cutoff:
            rows.append(row)
        else:
            changed = True
    if changed:
        data[str(cid)] = rows
        store._save(NEWS_HISTORY_KEY, data)
    return rows


def _history_has_match(history, item):
    url = _canonical_url(item.get("url", ""))
    title = item.get("title") or item.get("title_ru") or ""
    h = stable_hash({"url": url, "title": title})
    for row in history or []:
        if url and url == row.get("url"):
            return True
        if h and h == row.get("hash"):
            return True
        old_title = row.get("title") or row.get("normalized_title") or ""
        if _title_similarity(title, old_title) >= 0.62:
            return True
    return False


def _save_history(cid, items, now=None):
    now = now or _now()
    if not items:
        return

    def mut(data):
        rows = []
        cutoff = now - timedelta(days=NEWS_HISTORY_DAYS)
        for row in data.get(str(cid), []) or []:
            sent_at = _parse_dt(row.get("date_sent"))
            if sent_at and sent_at >= cutoff:
                rows.append(row)
        for item in items:
            title = item.get("title") or item.get("title_ru") or ""
            url = _canonical_url(item.get("url") or item.get("source_url") or "")
            rows.append({
                "url": url,
                "title": title,
                "normalized_title": _title_key(title),
                "hash": item.get("hash") or stable_hash({"url": url, "title": title}),
                "source": item.get("source") or item.get("source_name") or _source_name(url),
                "category": item.get("category") or "netherlands",
                "date_sent": now.isoformat(),
            })
        data[str(cid)] = rows[-200:]
        return data, True

    store.mutate_kv(NEWS_HISTORY_KEY, mut)


def _plain_text(item):
    return f"{item.get('title', '')} {item.get('content', '')}".lower()


def _category_from_item(item):
    cat = item.get("_category_hint") or item.get("category")
    if cat:
        return cat
    text = _plain_text(item)
    host = _host(item.get("url"))
    if "alkmaar" in text or any(host.endswith(d) for d in _OFFICIAL_DOMAINS["city"]):
        return "city"
    if any(w in text for w in ("ns ", "dienstregeling", "staking", "vertraging")):
        return "transport"
    if any(w in text for w in ("duo", "inburgering", "examen")):
        return "documents_study"
    if any(w in text for w in ("zorg", "huisarts", "apotheek", "verzekering")):
        return "health"
    if any(w in text for w in ("huur", "belasting", "toeslag", "hypotheek")):
        return "housing_money"
    if any(w in text for w in ("openai", "telegram", "apple", "cloudflare", "railway", "api")):
        return "tech"
    return "netherlands"


def _score_candidate(item, profile, now=None):
    now = now or _now()
    text = _plain_text(item)
    host = _host(item.get("url"))
    category = _category_from_item(item)
    age = _item_age_days(item, now)
    score = 0
    reasons = []

    local_hit = "alkmaar" in text or "noord-holland" in text or any(
        host.endswith(d) for d in _OFFICIAL_DOMAINS.get("city", ())
    )
    practical = category in {
        "city", "transport", "housing_money", "documents_study", "health",
        "food", "wardrobe_weather", "travel", "language",
    } or any(w in text for w in (
        "geld", "belasting", "huur", "zorg", "huisarts", "apotheek", "duo",
        "ns", "ov", "schiphol", "storing", "staking", "wijzig", "verzekering",
        "gemeente", "toeslag", "api", "pricing", "outage", "recall",
    ))
    interests = [str(x).lower() for x in (profile.get("movies") or []) + (profile.get("artists") or [])
                 + (profile.get("services") or [])]
    interest_hit = any(x and x in text for x in interests)
    if category == "tech" and any(w in text for w in ("openai", "telegram", "apple", "cloudflare", "railway", "api")):
        interest_hit = True
    action_hit = any(w in text for w in (
        "vanaf", "per ", "deadline", "aanvragen", "check", "wijzig", "storing",
        "staking", "afsluiting", "waarschuwing", "terugroepactie", "pricing",
        "limit", "outage", "release", "ticket", "premiere",
    ))

    if local_hit:
        score += 30
        reasons.append("это рядом с Алкмаром")
    if practical:
        score += 25
        reasons.append("может повлиять на планы, деньги или документы")
    if interest_hit:
        score += 20
        reasons.append("связано с твоими интересами")
    if age is not None and age <= 3:
        score += 15
    if action_hit:
        score += 10

    if age is None:
        score -= 50
    elif age > _category_max_age_days(category, item.get("_urgency")):
        score -= 30
    if not _has_concrete_change(item):
        score -= 25
    if not (local_hit or practical or interest_hit):
        score -= 20

    return max(0, min(100, score)), category, reasons


def _short_summary(item):
    content = re.sub(r"\s+", " ", (item.get("content") or "")).strip()
    title = re.sub(r"\s+", " ", (item.get("title") or "")).strip()
    base = content or title
    if not base:
        return "Есть свежее изменение по этой теме."
    sentence = re.split(r"(?<=[.!?])\s+", base)[0].strip()
    if len(sentence) > 150:
        sentence = sentence[:147].rstrip(" ,.;:") + "..."
    return sentence


def _why_important(category, reasons):
    if reasons:
        return reasons[0] + "."
    defaults = {
        "city": "это может повлиять на планы рядом с домом.",
        "transport": "это может изменить поездки и время в пути.",
        "housing_money": "это может повлиять на расходы или правила.",
        "documents_study": "это важно для документов или обучения.",
        "health": "это может повлиять на zorg и доступ к услугам.",
        "tech": "это полезно для работы бота и сервисов.",
        "leisure": "это помогает выбрать планы на ближайшие дни.",
        "food": "это может быть полезно для покупок или еды рядом.",
        "wardrobe_weather": "это влияет на одежду и поездки на велосипеде.",
        "travel": "это может повлиять на дорогу и вылеты.",
        "language": "это полезно для изучения нидерландского.",
    }
    return defaults.get(category, "это практичное изменение для ближайших дней.")


def _action_hint(category):
    return "Проверь детали" if category in {"transport", "travel", "documents_study"} else "Подробнее"


def _to_news_item(item, profile, now=None):
    score, category, reasons = _score_candidate(item, profile, now)
    url = _canonical_url(item.get("url", ""))
    title = (item.get("title") or "").strip()
    published = _parse_dt(_published_value(item))
    source = item.get("source") or item.get("source_name") or _source_name(url)
    return NewsItem(
        title=title,
        summary=_short_summary(item),
        url=url,
        source=source,
        published_at=published.isoformat() if published else "",
        category=category,
        language=item.get("_query_language") or ("en" if category == "tech" else "nl"),
        relevance_score=score,
        why_important=_why_important(category, reasons),
        action_hint=_action_hint(category),
        hash=stable_hash({"url": url, "title": title}),
    )


def _select_diverse(items):
    selected = []
    used_categories = set()
    group_counts = {}
    priority = {cat: idx for idx, cat in enumerate(_CATEGORY_PRIORITY)}
    ordered = sorted(
        items,
        key=lambda x: (-x.relevance_score, priority.get(x.category, 99), x.published_at),
    )
    for item in ordered:
        if item.category in used_categories:
            continue
        group = _CATEGORY_GROUPS.get(item.category, item.category)
        if group_counts.get(group, 0) >= 2:
            continue
        selected.append(item)
        used_categories.add(item.category)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) >= NEWS_MAX_ITEMS:
            break
    return selected


def collect_personal_news(user_profile, sources=None, now=None, search_fn=None):
    now = now or _now()
    profile = dict(user_profile or {})
    cid = profile.get("cid") or profile.get("chat_id") or "default"
    raw_sources = sources if sources is not None else (search_fn or _search_all_for_profile)(profile)
    filtered = strict_filter(raw_sources, "today", now)
    filtered = _dedupe_semantic(filtered)
    history = _history_for(cid, now)

    candidates = []
    seen_titles = []
    for item in filtered:
        if _history_has_match(history, item):
            continue
        if any(_title_similarity(item.get("title"), title) >= 0.62 for title in seen_titles):
            continue
        news = _to_news_item(item, profile, now)
        if news.relevance_score >= NEWS_MIN_RELEVANCE_SCORE:
            candidates.append(news)
            seen_titles.append(news.title)

    return _select_diverse(candidates)


def _score_items(cid, candidates):
    if not candidates:
        return []
    profile = _profile_context(cid)
    compact = [
        {
            "title": x.get("title"),
            "url": x.get("url"),
            "content": (x.get("content") or "")[:700],
            "published_at": _published_value(x),
            "date_missing": bool(x.get("_date_missing")),
            "category_hint": x.get("_category_hint"),
        }
        for x in candidates[:24]
    ]
    prompt = (
        "Оцени новости для персонального раздела Telegram-бота. "
        "Показывай только практичные свежие изменения для конкретного пользователя. "
        "Запрещены криминал, общая политика, кликбейт, слухи, SEO-статьи, Reddit, реклама, медицинские страшилки. "
        "Для здоровья используй спокойный тон и только официальные источники. "
        "Не добавляй факты, которых нет в источниках.\n\n"
        f"Профиль пользователя: {json.dumps(profile, ensure_ascii=False)}\n"
        f"Кандидаты: {json.dumps(compact, ensure_ascii=False)}\n\n"
        "Верни JSON: {\"items\": [{\"is_relevant\": true, \"importance\": 1, "
        "\"category\": \"city|netherlands|screen|music|tech|health|food\", "
        "\"title_ru\": \"...\", \"summary_ru\": \"...\", \"why_it_matters_ru\": \"...\", "
        "\"source_name\": \"...\", \"source_url\": \"https://...\", "
        "\"published_at\": \"ISO datetime\", \"action_type\": \"read|watch|listen|visit|prepare|none\"}]}"
    )
    try:
        data = ai.llm_json(prompt, 2200, tier="leisure", route="gemini", module="personal_news")
    except Exception as e:
        _inc_stat("errors")
        _log.warning("personal_news Gemini scoring failed: %s", str(e)[:120])
        return []
    items = data.get("items") if isinstance(data, dict) else []
    good = []
    for item in items or []:
        try:
            if item.get("is_relevant") is True and int(item.get("importance", 0)) >= 3:
                good.append(item)
        except Exception:
            continue
    return good[:NEWS_MAX_ITEMS]


def _source_name(url):
    host = _host(url)
    for domain, name in _SOURCE_NAMES.items():
        if host.endswith(domain):
            return name
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].title()
    return host.title() or "Источник"


def _fallback_items(candidates):
    items = []
    for raw in candidates[:NEWS_MAX_ITEMS]:
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        content = re.sub(r"\s+", " ", (raw.get("content") or "")).strip()
        summary = content[:180].rstrip()
        if len(content) > 180:
            summary = summary.rstrip(".,;:") + "..."
        items.append({
            "is_relevant": True,
            "importance": 3,
            "category": raw.get("_category_hint") or "netherlands",
            "title_ru": title,
            "summary_ru": summary,
            "why_it_matters_ru": "",
            "source_name": _source_name(raw.get("url")),
            "source_url": raw.get("url"),
            "published_at": _published_value(raw) or "",
            "action_type": "read",
        })
    return items


def _build_card(items, updated_ts=None, stale=False):
    if not items:
        text = (
            "📰 Новости для тебя\n\n"
            "Сегодня нет достаточно важных новостей для тебя.\n\n"
            "Проверил:\n"
            "• Алкмар\n"
            "• Нидерланды\n"
            "• NS / DUO\n"
            "• AI / технологии\n"
            "• досуг"
        )
        return text, []
    lines = ["📰 Новости для тебя"]
    buttons = []
    now = _now()
    for item in items[:NEWS_MAX_ITEMS]:
        cat = item.get("category") or "city"
        label = _CATEGORY_LABELS.get(cat, "🇳🇱 Нидерланды")
        title = (item.get("title") or item.get("title_ru") or "").strip()
        summary = (item.get("summary") or item.get("summary_ru") or "").strip()
        why = (item.get("why_important") or item.get("why_it_matters_ru") or "").strip()
        url = item.get("url") or item.get("source_url") or ""
        source = item.get("source") or item.get("source_name") or _source_name(url)
        published = _parse_dt(item.get("published_at"))
        day_word = _relative_day(published, now)
        lines.extend(["", label, title])
        if summary:
            lines.append(f"Коротко: {summary}")
        if why:
            lines.append(f"💡 Почему важно: {why}")
        lines.append(f"Источник: {source} · {day_word}")
        if url:
            label_btn = item.get("action_hint") or _ACTION_LABELS.get(item.get("action_type") or "read", "Подробнее")
            buttons.append([InlineKeyboardButton(label_btn, url=url)])
    return "\n".join(lines).strip(), buttons[:NEWS_MAX_ITEMS]


def _relative_day(published, now=None):
    if not published:
        return "сегодня"
    now = now or _now()
    days = (now.date() - published.astimezone(config.TZ).date()).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def build_from_sources(cid, period, sources, now=None):
    profile = _profile_context(cid)
    profile["cid"] = str(cid)
    filtered = strict_filter(sources, period, now)
    _inc_stat("filtered", max(0, len(sources or []) - len(filtered)), now)
    news_items = collect_personal_news(profile, sources=filtered, now=now)
    items = [asdict(item) for item in news_items]
    _save_history(cid, items, now)
    text, url_buttons = _build_card(items)
    entry = {"ts": int(time.time()), "period": period, "items": items, "sources": filtered, "text": text}
    _cache_set(cid, period, entry, now)
    _inc_stat("shown", len(items), now)
    _update_avg_card_size(len(text), now)
    return entry, url_buttons


def _update_avg_card_size(size, now=None):
    day = _today_key(now)

    def mut(data):
        d = data.setdefault("days", {}).setdefault(day, {})
        n = int(d.get("cards", 0)) + 1
        prev = int(d.get("avg_card_size", 0))
        d["cards"] = n
        d["avg_card_size"] = round((prev * (n - 1) + size) / n)
        return data, d["avg_card_size"]

    return _stat_mutate(mut)


def _is_admin(cid):
    return bool(cid) and (access.is_owner(cid) or str(cid) == str(config.ADMIN_CHAT_ID or ""))


def _default_keyboard(period, url_buttons=None, cid=None):
    rows = list(url_buttons or [])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")])
    return InlineKeyboardMarkup(rows)


def _loading_text():
    return ""


async def send_home(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Сегодня", callback_data="a_news_today")],
        [InlineKeyboardButton("📅 За неделю", callback_data="a_news_week")],
        [InlineKeyboardButton("⚙️ Настроить темы", callback_data="a_news_topics")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
    ])
    s = store.get_settings(cid)
    city = s.get("city") or "твой город"
    country = s.get("country") or "Нидерланды"
    text = (
        "📰 Новости для тебя\n\n"
        f"Здесь собраны свежие новости по твоему городу, стране и личным темам: сервисы, поездки, здоровье, еда, кино и музыка.\n\n"
        f"Если по {city} нет достаточно свежего, покажу важное по стране: {country}."
    )
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def send_period(bot, cid, period="today", force=False):
    cached = None if force else _cache_get(cid, period)
    if cached:
        _inc_stat("cache_hits")
        text, buttons = _build_card(cached.get("items", []), cached.get("ts"))
        await bot.send_message(chat_id=cid, text=text, reply_markup=_default_keyboard(period, buttons, cid),
                               disable_web_page_preview=True)
        return
    sources = await __import__("asyncio").to_thread(_search_all, cid)
    try:
        entry, buttons = await __import__("asyncio").to_thread(build_from_sources, cid, period, sources)
    except Exception:
        stale = _cache_get(cid, period, allow_stale=True)
        if stale:
            text, buttons = _build_card(stale.get("items", []), stale.get("ts"), stale=True)
            await bot.send_message(chat_id=cid, text=text, reply_markup=_default_keyboard(period, buttons, cid),
                                   disable_web_page_preview=True)
            return
        raise
    await bot.send_message(chat_id=cid, text=entry["text"], reply_markup=_default_keyboard(period, buttons, cid),
                           disable_web_page_preview=True)


async def refresh(bot, cid, period="today"):
    last = _last_refresh(cid)
    if last and time.time() - last < REFRESH_COOLDOWN_SEC and not _is_admin(cid):
        last_dt = datetime.fromtimestamp(last, config.TZ)
        next_dt = datetime.fromtimestamp(last + REFRESH_COOLDOWN_SEC, config.TZ)
        await bot.send_message(
            chat_id=cid,
            text=f"Последняя проверка была в {last_dt.strftime('%H:%M')}.\n"
                 f"Новые источники проверю после {next_dt.strftime('%H:%M')}, чтобы не тратить лимит зря.",
            reply_markup=_default_keyboard(period, cid=cid),
        )
        return
    _set_last_refresh(cid)
    await send_period(bot, cid, period, force=True)


async def send_topics(bot, cid):
    s = store.get_settings(cid)
    city = s.get("city") or "Алкмар"
    rows = [
        [InlineKeyboardButton("Изменить любимые фильмы", callback_data="as_love_movies")],
        [InlineKeyboardButton("Изменить любимых артистов", callback_data="as_love_artists")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif")],
        [InlineKeyboardButton("◀️ Назад", callback_data="a_news_home")],
    ]
    text = (
        "📰 Мои темы\n\n"
        f"🏙 {city} и Нидерланды      ✅\n"
        "🚆 NS, DUO и gemeente         ✅\n"
        "🎬 Фильмы и сериалы          ✅\n"
        "🎵 Музыка и концерты         ✅\n"
        "💻 Apple, AI и сервисы       ✅\n"
        "🩺 Здоровье и медицина       ✅\n"
        "🍽 Новые места и еда         ✅"
    )
    await bot.send_message(chat_id=cid, text=text, reply_markup=InlineKeyboardMarkup(rows))


async def send_scheduled(bot, cid):
    await send_period(bot, cid, "today", force=False)


def admin_stats_text():
    snap = budget_snapshot()
    last = datetime.fromtimestamp(snap["last_build_ts"], config.TZ).strftime("%H:%M") if snap["last_build_ts"] else "—"
    return (
        "📰 Personal News · Tavily\n\n"
        f"Сегодня: {snap['today_credits']} / {NEWS_DAILY_CREDIT_BUDGET} credits\n"
        f"Месяц: {snap['month_credits']} / {TAVILY_MONTHLY_CREDIT_LIMIT} credits\n"
        f"Кэш-попадания: {snap['cache_hits']}\n"
        f"Последняя сборка: {last}\n\n"
        f"Tavily calls: {snap['tavily_calls']}\n"
        f"Отфильтровано как нерелевантное: {snap['filtered']}\n"
        f"Показано пользователю: {snap['shown']}\n"
        f"Ошибки Tavily/Gemini: {snap['errors']}\n"
        f"Средний объём карточки: {snap['avg_card_size']} знаков"
    )


def stable_hash(item):
    raw = f"{_canonical_url(item.get('url', ''))}|{_title_key(item.get('title', ''))}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
