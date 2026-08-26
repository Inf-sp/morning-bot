"""Проверяемая городская рекомендация для главного экрана Готовки."""

from datetime import datetime
from urllib.parse import quote_plus

import ai
import config
import research
import secure
import store


_CACHE_TTL_DAYS = 7

_CITY_FALLBACKS = {
    "alkmaar": (
        {
            "name": "De Eendracht in 't IJkgebouw",
            "cuisine": "Нидерландская / европейская", "price": "€€",
            "opening_hours": "с 09:00 до 00:00",
            "signature_dish": "Eendracht burger",
            "dish_emoji": "🍔", "dish_price": "€22,50",
            "description": "Ресторан на весь день с сезонными блюдами и продуктами местных поставщиков.",
            "fact": "Историческое здание IJkgebouw, локальные продукты и кухня с блюдами на весь день.",
            "source_url": "https://www.deeendracht-alkmaar.nl/",
        },
        {
            "name": "MADA",
            "cuisine": "грузинская", "price": "€€",
            "signature_dish": "аджарули хачапури",
            "description": "Грузинский ресторан с выпечкой из печи, хинкали и блюдами на углях.",
            "fact": "Винная часть меню опирается на грузинскую традицию выдержки вина в квеври.",
            "source_url": "https://restaurant-mada.nl/",
        },
        {
            "name": "Roest Alkmaar",
            "cuisine": "современная европейская", "price": "€€",
            "signature_dish": "Roest Smashburger",
            "description": "Неформальный ресторан и коктейль-бар в старом центре Alkmaar.",
            "fact": "Сам ресторан называет Roest Smashburger своим фирменным блюдом.",
            "source_url": "https://roestalkmaar.nl/",
        },
    ),
}


def _cache(cid):
    value = store.get_profile(cid).get("food_restaurant_recommendation") or {}
    return value if isinstance(value, dict) else {}


def _fresh(value, city, context_key):
    try:
        cached = datetime.fromisoformat(str(value.get("cached_at") or ""))
    except (TypeError, ValueError):
        return False
    return (
        str(value.get("city") or "").casefold() == str(city or "").casefold()
        and value.get("context_key") == context_key
        and (datetime.now(config.TZ) - cached).days < _CACHE_TTL_DAYS
        and value.get("name") and value.get("map_url")
    )


def _save(cid, card):
    store.mutate_profile(cid, lambda profile: (
        {**profile, "food_restaurant_recommendation": dict(card)}, None,
    ))


def _usable(value, city):
    return bool(
        isinstance(value, dict)
        and str(value.get("city") or "").casefold() == str(city or "").casefold()
        and value.get("name") and value.get("map_url") and value.get("description")
    )


def _fallback_card(city, previous="", context_key=""):
    items = _CITY_FALLBACKS.get(str(city or "").casefold()) or ()
    picked = next(
        (item for item in items
         if str(item.get("name") or "").casefold() != str(previous or "").casefold()),
        items[0] if items else None,
    )
    if not picked:
        return {"city": city}
    name = picked["name"]
    return {
        "city": city, **picked,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {city}')}",
        "context_key": context_key,
        "cached_at": datetime.now(config.TZ).isoformat(),
    }


def _reserve(cid, cached, city, previous="", context_key=""):
    if (_usable(cached, city) and cached.get("context_key") == context_key
            and str(cached.get("name") or "").casefold() != str(previous or "").casefold()):
        return cached
    card = _fallback_card(city, previous, context_key)
    if card.get("name"):
        _save(cid, card)
    return card


def _source_text(rows):
    return "\n---\n".join(
        f"TITLE: {row.get('title', '')}\nURL: {row.get('url', '')}\nTEXT: {row.get('content', '')}"
        for row in rows
    )


def _good_terrace_weather(settings):
    """Проверяет погоду через общий слой; любой сбой просто отключает фильтр террасы."""
    lat, lon = settings.get("lat"), settings.get("lon")
    if lat is None or lon is None:
        return False
    try:
        import weather
        data = weather.fetch_weather(lat, lon, 2)
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        temperature = current.get("temperature_2m")
        rain = (daily.get("precipitation_probability_max") or [100])[0]
        code = current.get("weather_code", current.get("weathercode", 99))
        return float(temperature) >= 15 and float(rain or 0) < 30 and int(code) in {0, 1, 2, 3}
    except Exception:
        return False


def _context(settings, now=None):
    now = now or datetime.now(config.TZ)
    if now.hour < 11:
        key, search = "morning", "coffee and breakfast"
    elif now.hour < 17:
        key, search = "lunch", "lunch"
    elif now.weekday() in {4, 5}:
        key, search = "weekend_dinner", "Friday or Saturday dinner"
    else:
        key, search = "dinner", "dinner restaurant"
    if _good_terrace_weather(settings):
        key, search = f"{key}_terrace", f"{search} with a terrace"
    return key, search


def get_restaurant(cid, *, refresh=False):
    settings = store.get_settings(cid)
    city = str(settings.get("city") or "Alkmaar").strip()
    context_key, search_context = _context(settings)
    cached = _cache(cid)
    if not refresh and _fresh(cached, city, context_key):
        return cached
    previous = str(cached.get("name") or "") if refresh else ""
    rows = research.web_search(
        f"best {search_context} in {city} official menu opening hours signature dish price",
        max_results=6, scenario="restaurant_local", allow_tavily=True,
        search_priority="tavily",
    )
    if not rows:
        return _reserve(cid, cached, city, previous, context_key)
    sources = _source_text(rows)
    prompt = f"""Выбери ОДИН реально существующий ресторан в городе {city} по источникам.
Подбери его для контекста: {search_context}. Не используй место {previous or 'без исключений'}.
Не придумывай цену, блюдо или факт:
каждое поле должно прямо следовать из источников. Описание — одна короткая строка
по-русски. price только €, €€ или €€€. signature_dish — конкретное блюдо.
fact — один проверяемый факт о месте или его кухне. opening_hours и dish_price
заполняй только при явном подтверждении источником, иначе оставь пустыми.
dish_emoji — один подходящий эмодзи блюда. source_url скопируй из источника.

{secure.wrap_untrusted(sources, "результаты поиска")}

Верни JSON: {{"name":"...","cuisine":"...","price":"€€",
"opening_hours":"...","signature_dish":"...","dish_emoji":"🍽️","dish_price":"...",
"description":"...","fact":"...","source_url":"https://..."}}
"""
    try:
        result = ai.llm_json(
            prompt, 900, module="food_restaurant", fallback_allowed=True,
            cache_context={"city": city.casefold(), "context": context_key,
                           "previous": previous.casefold(), "sources": sources},
        )
    except Exception:
        return _reserve(cid, cached, city, previous, context_key)
    if not isinstance(result, dict):
        return _reserve(cid, cached, city, previous, context_key)
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
        return _reserve(cid, cached, city, previous, context_key)
    card = {
        "city": city, "name": name,
        "cuisine": " ".join(str(result["cuisine"]).split()),
        "price": result["price"],
        "signature_dish": " ".join(str(result["signature_dish"]).split()),
        "dish_emoji": " ".join(str(result.get("dish_emoji") or "🍽️").split()),
        "dish_price": " ".join(str(result.get("dish_price") or "").split()),
        "opening_hours": " ".join(str(result.get("opening_hours") or "").split()),
        "description": " ".join(str(result["description"]).split()),
        "fact": " ".join(str(result["fact"]).split()),
        "source_url": source_url,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {city}')}",
        "context_key": context_key,
        "cached_at": datetime.now(config.TZ).isoformat(),
    }
    _save(cid, card)
    return card
