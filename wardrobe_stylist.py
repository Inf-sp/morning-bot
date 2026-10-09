"""ИИ-стилист: выбирает один из готовых комплектов кода и пишет главный акцент.

Код уже отсеял всё, что не подходит по погоде, и отсортировал комплекты по
сочетаемости. ИИ выбирает между проверенными вариантами с учётом стиля,
предпочтений и трендов недели. Ответ проверяется: номер только из списка,
носки не синие, акцент говорит о вещах образа. Иначе — None, и карточку
собирают правила кода.
"""
import logging
from datetime import date, timedelta

import ai
import config
import research
import store
from wardrobe_model import public_item_name
from wardrobe_outfit import _claims_are_grounded, outfit_accessories

_log = logging.getLogger(__name__)

TRENDS_TTL_DAYS = 7
TRENDS_RETRY_DAYS = 1      # поиск не удался — база, повторная попытка завтра
_TRENDS_VERSION = 1
_TREND_MAX_CHARS = 140
_ACCENT_MAX_CHARS = 220

# База на случай, если поиск или ИИ недоступны: устойчивые тенденции 2026.
BASE_TRENDS = (
    "Прямые и свободные брюки и джинсы вместо узких",
    "Многослойность: футболка, расстёгнутая рубашка или овершот, лёгкая куртка",
    "Землистая палитра — олива, коричневый, бежевый — в паре с тёмно-синим",
    "Тон в тон: верх и низ одного цвета разной фактуры",
    "Утилитарные детали: накладные карманы, ветровки, технические ткани",
    "Чистые кожаные кеды и лоферы вместо массивных кроссовок",
    "Один цветной акцент на нейтральной базе — носки, шапка или аксессуар",
)
_STYLE_QUERY = {
    "Минимализм": "minimalist", "Скандинавский": "scandinavian", "Повседневный": "casual",
    "Городской": "urban streetwear", "Классический": "smart classic", "Спортивный": "athleisure",
}
_SEASONS = ("winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "fall", "fall", "fall", "winter")
_BLUE = ("син", "голуб", "navy", "blue", "индиго")


# ---------- тренды недели ----------
def _trends_query(today, styles):
    words = " ".join(dict.fromkeys(_STYLE_QUERY[s] for s in styles if s in _STYLE_QUERY))
    return f"men's {words} street style trends {_SEASONS[today.month - 1]} {today.year}".replace("  ", " ")


def _clean_trends(data):
    rows = (data or {}).get("trends") if isinstance(data, dict) else None
    trends = [str(row).strip().rstrip(".") for row in rows or [] if isinstance(row, str) and row.strip()]
    trends = [row for row in trends if len(row) <= _TREND_MAX_CHARS]
    return trends[:7] if len(trends) >= 3 else []


def _fetch_trends(today, styles):
    results = research.web_search(_trends_query(today, styles), max_results=5)
    snippets = "\n---\n".join(
        str(row.get("content") or row.get("description") or "")[:1200] for row in results
    ).strip()
    if not snippets:
        return []
    prompt = f"""Ниже выдержки о мужской уличной моде этого сезона.
Сожми их в 5–7 практичных пунктов на русском: силуэты, цвета сезона, сочетания.
Только то, что можно применить к обычному гардеробу; без брендов, цен и подиумной экзотики.
Каждый пункт — до 120 символов, без точки в конце.

{snippets}

JSON без Markdown: {{"trends":["..."]}}"""
    return _clean_trends(ai.llm_json(
        prompt, 600, tier="smart", module="wardrobe", fallback_allowed=True,
        privacy_level="public", budget_seconds=25,
    ))


def weekly_trends(styles=(), today=None):
    """5–7 трендов сезона для стилей пользователя; кэш на неделю, без поиска — база."""
    today = today or date.today()
    key = ",".join(sorted(styles)) or "-"
    cache = store._load(config.WARDROBE_TRENDS_CACHE_KEY)
    cache = cache if isinstance(cache, dict) and cache.get("version") == _TRENDS_VERSION else {}
    entry = (cache.get("styles") or {}).get(key) or {}
    if str(entry.get("expires") or "") > today.isoformat() and entry.get("items"):
        return list(entry["items"])
    try:
        trends = _fetch_trends(today, styles)
    except Exception as exc:
        _log.warning("Wardrobe trends search failed: %s", exc)
        trends = []
    days = TRENDS_TTL_DAYS if trends else TRENDS_RETRY_DAYS
    trends = trends or list(BASE_TRENDS)

    def mutate(data):
        data = data if isinstance(data, dict) and data.get("version") == _TRENDS_VERSION else {}
        styles_map = {**(data.get("styles") or {}),
                      key: {"expires": (today + timedelta(days=days)).isoformat(), "items": trends}}
        return {"version": _TRENDS_VERSION, "styles": styles_map}, None

    store.mutate_kv(config.WARDROBE_TRENDS_CACHE_KEY, mutate)
    return trends


# ---------- выбор комплекта ----------
def _item_line(item):
    facts = [str(item.get(key) or "") for key in ("material", "fit")]
    colors = ", ".join(str(c) for c in item.get("colors") or [] if c)
    details = "; ".join(part for part in (colors, *facts) if part)
    return f"{item.get('zone')}: {public_item_name(item)}" + (f" ({details})" if details else "")


def _weather_line(ctx):
    if ctx.get("feels_min") is None and ctx.get("tmax") is None:
        return "нет данных"
    parts = []
    if ctx.get("window"):
        parts.append(f"с {ctx['window'][0]:02d}:00 до {ctx['window'][1]:02d}:00")
    if ctx.get("feels_min") is not None:
        parts.append(f"ощущается от {ctx['feels_min']:+d}° до {ctx['feels_max']:+d}°")
    else:
        parts.append(f"до {ctx['tmax']:+d}°")
    if ctx.get("has_rain"):
        parts.append(f"дождь с {ctx['rain_from']:02d}:00" if ctx.get("rain_from") is not None else "дождь")
    if ctx.get("gust_max"):
        parts.append(f"порывы до {ctx['gust_max']} м/с")
    return ", ".join(parts)


def _prompt(outfits, ctx, styles, prefs_text, trends):
    looks = "\n".join(
        f"{index}. " + "; ".join(_item_line(item) for item in outfit)
        for index, outfit in enumerate(outfits, 1)
    )
    trend_lines = "\n".join(f"- {trend}" for trend in trends)
    return f"""Ты персональный стилист. Все комплекты ниже уже подходят по погоде.
Выбери один — самый стильный и современный по трендам сезона, с лучшим сочетанием цветов.

Погода: {_weather_line(ctx)}
Стиль: {", ".join(styles) or "любой"}
Предпочтения: {prefs_text or "нет"}
Тренды сезона:
{trend_lines}

Комплекты:
{looks}

socks — цвет носков одним прилагательным во множественном числе («бордовые»): в тон брюкам или обуви
либо осознанный акцент к образу; не синие и не голубые.
accent — одно предложение до 180 символов: главный акцент выбранного образа и почему он работает.
Называй только вещи этого комплекта и их цвета, ничего не выдумывай.
JSON без Markdown: {{"choice":1,"socks":"","accent":""}}"""


def _valid_socks(value):
    text = str(value or "").strip().casefold().removesuffix(" носки").strip()
    if not text or len(text.split()) > 2 or any(marker in text for marker in _BLUE):
        return ""
    return f"{text[:1].upper()}{text[1:]} носки"


def _names_look(text, items):
    """Акцент говорит хотя бы об одной вещи образа (по основе слова названия)."""
    words = text.casefold().split()
    for item in items:
        for word in public_item_name(item).casefold().split():
            if len(word) >= 4 and any(w.startswith(word[:max(3, min(5, len(word) - 1))]) for w in words):
                return True
    return False


def _valid_accent(value, items):
    text = " ".join(str(value or "").split())
    if not text or len(text) > _ACCENT_MAX_CHARS or not _claims_are_grounded(text, items):
        return ""
    # Аксессуары видны только в акценте, поэтому он обязан их назвать.
    focus = outfit_accessories(items) or items
    return text if _names_look(text, focus) else ""


def choose(outfits, ctx, styles, prefs_text, trends):
    """{index, socks, accent} или None — тогда карточку собирают правила кода."""
    if len(outfits) < 2:
        return None
    try:
        data = ai.llm_json(
            _prompt(outfits, ctx, styles, prefs_text, trends), 500, tier="smart", module="wardrobe",
            budget_seconds=20,
            cache_context={
                "scenario": "wardrobe_stylist", "outfits": [[it.get("id") for it in o] for o in outfits],
                "weather": _weather_line(ctx), "styles": list(styles), "preferences": prefs_text,
                "trends": list(trends), "language": "ru", "schema_version": 1,
            },
        )
    except Exception as exc:
        _log.warning("Wardrobe stylist failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    try:
        index = int(data.get("choice")) - 1
    except (TypeError, ValueError):
        return None
    if not 0 <= index < len(outfits):
        return None
    accent = _valid_accent(data.get("accent"), outfits[index])
    socks = _valid_socks(data.get("socks"))
    if not accent or not socks:
        return None
    return {"index": index, "socks": socks, "accent": accent}
