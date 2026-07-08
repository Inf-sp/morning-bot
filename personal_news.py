import hashlib
import json
import logging
import re
import time
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
NEWS_MAX_ITEMS = 10
NEWS_MIN_RELEVANCE_SCORE = 0.65
REFRESH_COOLDOWN_SEC = 6 * 3600

_CATEGORY_LABELS = {
    "city": "🏙 Алкмар",
    "netherlands": "🇳🇱 Нидерланды",
    "screen": "🎬 Смотреть",
    "music": "🎵 Музыка",
    "tech": "💻 Тех",
    "health": "🩺 Здоровье",
    "food": "🍽 Еда",
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
    "city": ("gemeentealkmaar.nl", "ns.nl", "rijksoverheid.nl", "government.nl", "alkmaar.nl"),
    "netherlands": ("rijksoverheid.nl", "government.nl", "duo.nl", "ns.nl", "belastingdienst.nl"),
    "screen": ("themoviedb.org", "netflix.com", "disneyplus.com", "primevideo.com", "max.com", "hbo.com"),
    "music": ("ticketmaster.nl", "ticketmaster.com", "songkick.com", "bandsintown.com", "spotify.com"),
    "tech": ("openai.com", "ai.google.dev", "cloud.google.com", "groq.com", "cloudflare.com",
             "railway.com", "telegram.org", "openweathermap.org", "pexels.com", "unsplash.com",
             "apple.com", "code.visualstudio.com"),
    "health": ("rijksoverheid.nl", "rivm.nl", "ggd.nl", "thuisarts.nl", "apotheek.nl",
               "zorginstituutnederland.nl"),
    "food": ("ah.nl", "jumbo.com", "nvwa.nl", "gemeentealkmaar.nl"),
}

_QUERY_TEMPLATES = {
    "city": [
        "{city} new restaurant cafe exhibition cultural place",
        "{city} nieuws vandaag nieuwe opening evenement verkeer wonen",
        "{city} local news this month restaurant museum event service",
        "site:gemeentealkmaar.nl Alkmaar wijzigingen gemeente service",
        "site:ns.nl NS dienstregeling wijziging Nederland",
    ],
    "netherlands": [
        "Nederland nieuws vandaag wonen reizen zorg geld prijzen regels",
        "Netherlands news this month housing travel healthcare money services",
        "site:rijksoverheid.nl Nederland regels wijziging wonen service",
        "site:duo.nl wijziging zorg ondersteuning Nederland",
        "site:belastingdienst.nl Nederland wijziging toeslagen belasting",
    ],
    "screen": [
        "{movies} official trailer premiere season cancelled streaming",
        "Netflix Disney Prime Video HBO Max Netherlands new releases this month",
        "nieuwe films series streaming Nederland deze maand release trailer",
    ],
    "music": [
        "{artists} new album single tour Netherlands official",
        "concerten Nederland deze maand nieuwe tour album single",
        "Ticketmaster Songkick Bandsintown Netherlands concerts this month",
    ],
    "tech": [
        "OpenAI Gemini Groq Cloudflare Railway Telegram API pricing limits outage",
        "Apple Mac VS Code OpenWeather Pexels Unsplash API pricing limits changes",
        "AI developer tools API update outage pricing this month OpenAI Google Cloudflare",
        "Telegram Railway GitHub Apple developer news this month API service changes",
    ],
    "health": [
        "Nederland gezondheid zorgverzekering huisarts apotheek nieuws deze maand",
        "Netherlands healthcare pharmacy GP insurance changes this month",
        "site:rivm.nl Nederland vaccinatie advies wijziging",
        "site:rijksoverheid.nl huisarts apotheek zorgverzekering wijziging",
        "site:apotheek.nl medicijn tekort Nederland",
    ],
    "food": [
        "Nederland eten supermarkt product recall nieuw restaurant deze maand",
        "Netherlands food supermarket recall new restaurant this month",
        "site:nvwa.nl product recall waarschuwing Nederland",
        "site:ah.nl nieuwe producten Albert Heijn Nederland",
        "{city} nieuw restaurant bakkerij markt ontbijt",
    ],
}

_COUNTRY_FALLBACK_QUERIES = [
    "{country} Netherlands breaking news today practical changes services",
    "{country} Netherlands local news today wonen reizen zorg geld",
    "Nederland nieuws vandaag wonen reizen zorg prijzen diensten",
    "Netherlands news this month practical changes housing travel healthcare food tech",
]


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
    return 30


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
    return " ".join(w for w in text.split() if len(w) > 2)[:120]


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
    return dt >= now - timedelta(days=_period_max_age_days(period))


def _has_concrete_change(item):
    text = f"{item.get('title', '')} {item.get('content', '')}".lower()
    markers = (
        "new", "nieuw", "nieuwe", "wijzig", "change", "changed", "update", "price", "pricing",
        "limit", "outage", "premiere", "trailer", "season", "cancelled", "release", "tour",
        "recall", "waarschuwing", "tekort", "opened", "opening", "datum", "api",
        "verandering", "aangepast", "storing", "uitval", "seizoen", "prijs", "tarief",
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
    s = store.get_settings(cid)
    city = s.get("city") or "Алкмар"
    country = s.get("country") or "Нидерланды"
    movies = ", ".join(map(str, store.get_list(config.WATCHLIST_KEY, cid)[:8])) or "favorite movies series"
    artists = ", ".join(map(str, store.get_list(config.ARTISTS_KEY, cid)[:8])) or "favorite artists"
    result = []
    for category, templates in _QUERY_TEMPLATES.items():
        for tpl in templates:
            q = tpl.format(city=city, country=country, movies=movies[:180], artists=artists[:180])
            result.append((category, q))
    return result


def _country_fallback_queries(cid):
    s = store.get_settings(cid)
    country = s.get("country") or "Нидерланды"
    return [
        ("netherlands", tpl.format(country=country))
        for tpl in _COUNTRY_FALLBACK_QUERIES
    ]


def _tavily_search(query, max_results=5, domains=None):
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
    rows = []
    for category, query in _queries_for(cid) + _country_fallback_queries(cid):
        if not _reserve_credits(1):
            break
        try:
            domains = _OFFICIAL_DOMAINS.get(category) if query.startswith("site:") else None
            max_results = 10
            for item in _tavily_search(query, max_results=max_results, domains=domains):
                item = dict(item)
                item["_category_hint"] = category
                rows.append(item)
        except Exception as e:
            _inc_stat("errors")
            _log.warning("personal_news Tavily failed: %s", str(e)[:120])
    return rows


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
    updated_ts = updated_ts or int(time.time())
    dt = datetime.fromtimestamp(updated_ts, config.TZ)
    if not items:
        text = (
            "📰 Новости для тебя\n\n"
            "Новости пока не загрузились. Попробуй обновить раздел позже."
        )
        return text, []
    day_word = "вчера" if stale else "сегодня"
    lines = ["📰 Новости для тебя", "", f"Обновлено {day_word} в {dt.strftime('%H:%M')}"]
    buttons = []
    by_cat = {}
    for item in items[:NEWS_MAX_ITEMS]:
        by_cat.setdefault(item.get("category") or "city", []).append(item)
    for cat, label in _CATEGORY_LABELS.items():
        rows = by_cat.get(cat) or []
        if not rows:
            continue
        lines.extend(["", label])
        for item in rows:
            lines.append(f"• {item.get('title_ru', '').strip()}")
            if item.get("summary_ru"):
                lines.append(f"  {item['summary_ru'].strip()}")
            if item.get("why_it_matters_ru"):
                lines.append(f"  Почему тебе: {item['why_it_matters_ru'].strip()}")
            url = item.get("source_url") or ""
            if url:
                label_btn = _ACTION_LABELS.get(item.get("action_type") or "read", "Подробнее")
                buttons.append([InlineKeyboardButton(label_btn, url=url)])
    return "\n".join(lines).strip(), buttons[:NEWS_MAX_ITEMS]


def build_from_sources(cid, period, sources, now=None):
    filtered = strict_filter(sources, period, now)
    _inc_stat("filtered", max(0, len(sources or []) - len(filtered)), now)
    filtered = _dedupe_semantic(filtered)
    items = _score_items(cid, filtered) if filtered else []
    if not items and filtered:
        items = _fallback_items(filtered)
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
    refresh_label = "↻ Проверить свежие источники" if _is_admin(cid) else "↻ Проверить обновления"
    rows.append([InlineKeyboardButton(refresh_label, callback_data=f"a_news_refresh_{period}")])
    if period == "today":
        rows.append([InlineKeyboardButton("📅 За неделю", callback_data="a_news_week")])
    else:
        rows.append([InlineKeyboardButton("📰 Сегодня", callback_data="a_news_today")])
    rows.append([InlineKeyboardButton("⚙️ Темы", callback_data="a_news_topics")])
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
