"""Проверяемая городская рекомендация для главного экрана Готовки."""

from datetime import datetime
from urllib.parse import quote_plus

import ai
import config
import research
import recommendation_rotation as rotation
import secure
import store


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
            "name": "Mada - Smaak van Georgië",
            "cuisine": "грузинская", "price": "€€",
            "signature_dish": "аджарули хачапури",
            "signature_dishes": ["Аджарули хачапури", "Хинкали"],
            "description": "Душевный грузинский ресторан в центре Алкмара, идеальный для уютного вечера с дровяной выпечкой и блюдами с огня. Выпечка из печи, сочные хинкали и аутентичные блюда на углях.",
            "fact": "Традиционные грузинские глиняные сосуды квеври, которые Mada использует для выдержки вина, закопаны глубоко под землю для поддержания постоянной температуры. Этот метод виноделия внесён в список нематериального культурного наследия ЮНЕСКО.",
            "source_url": "https://restaurant-mada.nl/",
        },
        {
            "name": "Roest Alkmaar",
            "address": "Hekelstraat 30", "format": "Cocktail bar · restaurant · café",
            "cuisine": "современная европейская", "price": "€€",
            "signature_dish": "Roest Smashburger",
            "dish_price": "€19",
            "dish_description": "Двойная говяжья котлета, BBQ-соус, маринованный огурец, томат, салат, brioche bun и фри.",
            "description": "Неформальный ресторан и коктейль-бар в старом центре Alkmaar.",
            "fact": "Smashburger — фирменное блюдо Roest и логичный выбор для первого визита; сюда стоит идти за неформальным ужином и коктейлями.",
            "source_url": "https://roestalkmaar.nl/",
        },
    ),
}
_HISTORY_LIMIT = 100


def _cache(cid):
    value = store.get_profile(cid).get("food_restaurant_recommendation") or {}
    return value if isinstance(value, dict) else {}


def _fresh(value, city):
    try:
        cached = datetime.fromisoformat(str(value.get("cached_at") or ""))
    except (TypeError, ValueError):
        return False
    now = datetime.now(config.TZ)
    if cached.tzinfo is None:
        cached = cached.replace(tzinfo=config.TZ)
    return (
        str(value.get("city") or "").casefold() == str(city or "").casefold()
        and cached.astimezone(config.TZ).date() == now.date()
        and value.get("name") and value.get("map_url")
    )


def _save(cid, card):
    store.mutate_profile(cid, lambda profile: (
        {**profile, "food_restaurant_recommendation": dict(card)}, None,
    ))
    try:
        import myday
        myday.reset_day_cache(cid)
    except Exception:
        pass


def cached_restaurant_preview(cid):
    """Готовые данные для «Моего дня» без нового поиска и AI."""
    city = str(store.get_settings(cid).get("city") or "").strip()
    card = _cache(cid)
    if not _fresh(card, city):
        return {}
    return {
        "name": str(card.get("name") or "").strip(),
        "url": str(card.get("map_url") or "").strip(),
        "details": " · ".join(
            str(card.get(field) or "").strip()
            for field in ("cuisine", "price")
            if str(card.get(field) or "").strip()
        ),
    }


def cached_restaurant_summary(cid):
    """Совместимая короткая строка из готовой сегодняшней карточки."""
    preview = cached_restaurant_preview(cid)
    return " · ".join(
        value for value in (preview.get("name", ""), preview.get("details", ""))
        if value
    )


def _usable(value, city):
    return bool(
        isinstance(value, dict)
        and str(value.get("city") or "").casefold() == str(city or "").casefold()
        and value.get("name") and value.get("map_url") and value.get("description")
    )


def _fallback_card(city, previous="", context_key="", history=None):
    items = _CITY_FALLBACKS.get(str(city or "").casefold()) or ()
    used = {_normal(value) for value in (history or []) if _normal(value)}
    picked = next(
        (item for item in items
         if _normal(item.get("name")) not in used
         and _normal(item.get("name")) != _normal(previous)),
        next((item for item in items
              if _normal(item.get("name")) != _normal(previous)), items[0] if items else None),
    )
    if not picked:
        return {"city": city}
    name = picked["name"]
    return {
        "city": city, **picked,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {city}')}",
        "context_key": context_key,
        "history": rotation.remember(history or [], name, limit=_HISTORY_LIMIT),
        "cached_at": datetime.now(config.TZ).isoformat(),
    }


def _reserve(cid, cached, city, previous="", context_key="", *, allow_cached=True):
    if allow_cached and _usable(cached, city):
        return cached
    card = _fallback_card(city, previous, context_key, cached.get("history") or [])
    if card.get("name"):
        _save(cid, card)
        return card
    # В городе без проверенного локального пула нельзя выдумывать новое место.
    # В этом единственном случае безопаснее оставить последнюю полную карточку.
    return cached if _usable(cached, city) else card


def _source_text(rows):
    return "\n---\n".join(
        f"ID: source:{index}\nTITLE: {row.get('title', '')}\n"
        f"URL: {row.get('url', '')}\nTEXT: {row.get('content', '')}"
        for index, row in enumerate(rows)
    )


def _normal(value):
    return " ".join(str(value or "").casefold().split()).strip()


def _validated_evidence(result, rows, fields):
    """Return the primary source URL only when every fact has a literal quote."""
    evidence = result.get("evidence") if isinstance(result, dict) else None
    if not isinstance(evidence, dict):
        return ""
    by_id = {f"source:{index}": row for index, row in enumerate(rows)}
    primary_url = ""
    for field in fields:
        proof = evidence.get(field)
        if not isinstance(proof, dict):
            return ""
        row = by_id.get(str(proof.get("source_id") or ""))
        quote = _normal(proof.get("quote"))
        if not row or len(quote) < 3:
            return ""
        source_text = _normal(f"{row.get('title', '')} {row.get('content', '')}")
        if quote not in source_text:
            return ""
        if field == "name":
            if _normal(result.get("name")) not in quote:
                return ""
            primary_url = str(row.get("url") or "").strip()
        if field in {"price", "dish_price"} and (
            str(result.get(field) or "") not in str(proof.get("quote") or "")
        ):
            return ""
    return primary_url if primary_url.startswith("https://") else ""


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
    if not refresh and _fresh(cached, city):
        return cached
    previous = str(cached.get("name") or "") if _usable(cached, city) else ""
    used_names = rotation.recent(
        [*(cached.get("history") or []), previous], limit=_HISTORY_LIMIT,
    )
    exclusions = rotation.search_exclusions(used_names, limit=10)
    search_query = " ".join(part for part in (
        f"best {search_context} in {city} official menu opening hours signature dish price",
        exclusions,
    ) if part)
    rows = research.web_search(
        search_query,
        max_results=8, scenario="restaurant_local", allow_tavily=True,
        search_priority="tavily",
    )
    if not rows:
        return _reserve(cid, cached, city, previous, context_key, allow_cached=not refresh)
    sources = _source_text(rows)
    prompt = f"""Выбери ОДИН реально существующий ресторан в городе {city} по источникам.
Подбери его для контекста: {search_context}. Не используй место {previous or 'без исключений'}.
Не повторяй уже показанные места: {', '.join(used_names) or 'нет'}.
Не придумывай цену, блюдо или факт:
каждое поле должно прямо следовать из источников. Описание — одна короткая строка
по-русски. price только €, €€ или €€€. address — подтверждённый адрес, format — до трёх форматов места через ·. signature_dish — конкретное блюдо.
fact — один проверяемый факт о месте или его кухне. opening_hours и dish_price
заполняй только при явном подтверждении источником, иначе оставь пустыми.
dish_description — одна короткая строка о составе блюда только по меню.
dish_emoji — один подходящий эмодзи блюда. Для каждого фактического поля верни
evidence с source_id и короткой ДОСЛОВНОЙ цитатой из TITLE или TEXT, которая его
подтверждает. Не переводи evidence.quote. Если подтверждения нет, не выбирай место.

{secure.wrap_untrusted(sources, "результаты поиска")}

Верни JSON: {{"name":"...","address":"...","format":"restaurant · café","cuisine":"...","price":"€€",
"opening_hours":"...","signature_dish":"...","dish_emoji":"🍽️","dish_price":"...",
"dish_description":"...","description":"...","fact":"...","evidence":{{
"name":{{"source_id":"source:0","quote":"..."}},
"address":{{"source_id":"source:0","quote":"..."}},
"format":{{"source_id":"source:0","quote":"..."}},
"cuisine":{{"source_id":"source:0","quote":"..."}},
"price":{{"source_id":"source:0","quote":"..."}},
"signature_dish":{{"source_id":"source:0","quote":"..."}},
"description":{{"source_id":"source:0","quote":"..."}},
"dish_description":{{"source_id":"source:0","quote":"..."}},
"fact":{{"source_id":"source:0","quote":"..."}},
"opening_hours":{{"source_id":"source:0","quote":"..."}},
"dish_price":{{"source_id":"source:0","quote":"..."}}}}}}
"""
    try:
        result = ai.llm_json(
            prompt, 900, module="food_restaurant", fallback_allowed=True,
            cache_context={"city": city.casefold(), "context": context_key,
                           "previous": previous.casefold(),
                           "history": rotation.cache_history(used_names, limit=_HISTORY_LIMIT),
                           "sources": sources},
        )
    except Exception:
        return _reserve(cid, cached, city, previous, context_key, allow_cached=not refresh)
    if not isinstance(result, dict):
        return _reserve(cid, cached, city, previous, context_key, allow_cached=not refresh)
    name = " ".join(str(result.get("name") or "").split()).strip()
    required = ("cuisine", "price", "signature_dish", "description", "fact")
    evidence_fields = ["name", *required]
    evidence_fields.extend(
        field for field in ("address", "format", "opening_hours", "dish_price", "dish_description")
        if str(result.get(field) or "").strip()
    )
    source_url = _validated_evidence(result, rows, evidence_fields)
    if (
        not name or _normal(name) in {_normal(value) for value in used_names} or not source_url
        or result.get("price") not in {"€", "€€", "€€€"}
        or not all(str(result.get(field) or "").strip() for field in required)
    ):
        has_local_rotation = bool(_CITY_FALLBACKS.get(city.casefold()))
        return _reserve(
            cid, cached, city, previous, context_key,
            allow_cached=not refresh and not has_local_rotation,
        )
    card = {
        "city": city, "name": name,
        "address": " ".join(str(result.get("address") or "").split()),
        "format": " ".join(str(result.get("format") or "").split()),
        "cuisine": " ".join(str(result["cuisine"]).split()),
        "price": result["price"],
        "signature_dish": " ".join(str(result["signature_dish"]).split()),
        "dish_emoji": " ".join(str(result.get("dish_emoji") or "🍽️").split()),
        "dish_price": " ".join(str(result.get("dish_price") or "").split()),
        "dish_description": " ".join(str(result.get("dish_description") or "").split()),
        "opening_hours": " ".join(str(result.get("opening_hours") or "").split()),
        "description": " ".join(str(result["description"]).split()),
        "fact": " ".join(str(result["fact"]).split()),
        "source_url": source_url,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(f'{name}, {city}')}",
        "context_key": context_key,
        "history": rotation.remember(cached.get("history") or [], name, limit=_HISTORY_LIMIT),
        "cached_at": datetime.now(config.TZ).isoformat(),
    }
    _save(cid, card)
    return card
