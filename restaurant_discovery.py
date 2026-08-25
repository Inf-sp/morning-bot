"""Проверяемая городская рекомендация для главного экрана Готовки."""

from datetime import datetime
from urllib.parse import quote_plus

import ai
import config
import research
import secure
import store


_CACHE_TTL_DAYS = 7


def _cache(cid):
    value = store.get_profile(cid).get("food_restaurant_recommendation") or {}
    return value if isinstance(value, dict) else {}


def _fresh(value, city):
    try:
        cached = datetime.fromisoformat(str(value.get("cached_at") or ""))
    except (TypeError, ValueError):
        return False
    return (
        str(value.get("city") or "").casefold() == str(city or "").casefold()
        and (datetime.now(config.TZ) - cached).days < _CACHE_TTL_DAYS
        and value.get("name") and value.get("map_url")
    )


def _save(cid, card):
    store.mutate_profile(cid, lambda profile: (
        {**profile, "food_restaurant_recommendation": dict(card)}, None,
    ))


def _source_text(rows):
    return "\n---\n".join(
        f"TITLE: {row.get('title', '')}\nURL: {row.get('url', '')}\nTEXT: {row.get('content', '')}"
        for row in rows
    )


def get_restaurant(cid, *, refresh=False):
    city = str(store.get_settings(cid).get("city") or "Alkmaar").strip()
    cached = _cache(cid)
    if not refresh and _fresh(cached, city):
        return cached
    previous = str(cached.get("name") or "") if refresh else ""
    rows = research.web_search(
        f"best restaurant in {city} official menu signature dish cuisine",
        max_results=6, scenario="restaurant_local", allow_tavily=True,
        search_priority="tavily",
    )
    if not rows:
        return cached if _fresh(cached, city) else {"city": city}
    sources = _source_text(rows)
    prompt = f"""Выбери ОДИН реально существующий ресторан в городе {city} по источникам.
Не используй место {previous or 'без исключений'}. Не придумывай цену, блюдо или факт:
каждое поле должно прямо следовать из источников. Описание — одна короткая строка
по-русски. price только €, €€ или €€€. signature_dish — конкретное блюдо.
fact — один проверяемый факт о месте или его кухне. source_url скопируй из источника.

{secure.wrap_untrusted(sources, "результаты поиска")}

Верни JSON: {{"name":"...","cuisine":"...","price":"€€",
"signature_dish":"...","description":"...","fact":"...","source_url":"https://..."}}
"""
    try:
        result = ai.llm_json(
            prompt, 900, module="food_restaurant", fallback_allowed=True,
            cache_context={"city": city.casefold(), "previous": previous.casefold(), "sources": sources},
        )
    except Exception:
        return cached if _fresh(cached, city) else {"city": city}
    if not isinstance(result, dict):
        return cached if _fresh(cached, city) else {"city": city}
    name = " ".join(str(result.get("name") or "").split()).strip()
    source_url = str(result.get("source_url") or "").strip()
    source_urls = {str(row.get("url") or "").strip() for row in rows}
    source_blob = sources.casefold()
    required = ("cuisine", "price", "signature_dish", "description", "fact")
    if (
        not name or name.casefold() not in source_blob or source_url not in source_urls
        or result.get("price") not in {"€", "€€", "€€€"}
        or not all(str(result.get(field) or "").strip() for field in required)
    ):
        return cached if _fresh(cached, city) else {"city": city}
    card = {
        "city": city, "name": name,
        "cuisine": " ".join(str(result["cuisine"]).split()),
        "price": result["price"],
        "signature_dish": " ".join(str(result["signature_dish"]).split()),
        "description": " ".join(str(result["description"]).split()),
        "fact": " ".join(str(result["fact"]).split()),
        "source_url": source_url,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {city}')}",
        "cached_at": datetime.now(config.TZ).isoformat(),
    }
    _save(cid, card)
    return card
