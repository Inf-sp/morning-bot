import asyncio
import copy
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
import weather
import util
import verify
import secure
import research
import settings as _settings
from ui import wardrobe as wardrobe_ui
from ui.constants import ui_label

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6
WARDROBE_OUTERWEAR_MAX_TEMP = 20  # °C — выше этой tmax верхняя одежда не предлагается (без дождя/ветра)

# Нейтральные цвета не создают конфликт с любым другим цветом в наборе (§ score_outfit).
NEUTRAL_COLORS = ("бел", "чёрн", "черн", "сер", "беж", "сини", "деним", "джинс")

# zone -> с какими зонами сочетается (простое правило по ZONE_ORDER, без похода в AI).
_ZONE_COMPAT = {
    "Верх": ["Низ", "Обувь", "Верхняя одежда", "Аксессуары"],
    "Низ": ["Верх", "Обувь", "Верхняя одежда", "Аксессуары"],
    "Верхняя одежда": ["Верх", "Низ", "Обувь", "Аксессуары"],
    "Обувь": ["Верх", "Низ", "Верхняя одежда", "Аксессуары"],
    "Аксессуары": ["Верх", "Низ", "Верхняя одежда", "Обувь"],
    "Другое": [],
}

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("✏️ Добавить вещь", "w_add"), ("❌ Удалить вещи", "w_del")],
        [("⬅️ Назад", "m_wardrobe")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_wardrobe")]])

def _day_key():
    return datetime.now(config.TZ).date().isoformat()

def _weather_emoji(has_rain, flags):
    """Эмодзи для строки погоды в карточке образа — по тому же приоритету, что и погодные правила
    (дождь → ветер → солнце)."""
    if has_rain:
        return "🌧️"
    if flags and flags.get("strong_wind"):
        return "💨"
    if flags and flags.get("sunny"):
        return "☀️"
    return "☁️"


def _wind_label(wind_ms, strong_wind):
    """Ветер — важный для одежды фактор, показываем его всегда, не только когда заметный."""
    if strong_wind:
        return "сильный ветер"
    if wind_ms >= WARDROBE_WIND_LAYER_MS:
        return "умеренный ветер"
    return "слабый ветер"


def _short_weather_line(weather_ctx):
    """Погодная строка новой карточки: '☁️ +18…+23°C · сухо · слабый ветер'."""
    if not weather_ctx or weather_ctx.get("tmax") is None:
        return ""
    emoji = _weather_emoji(weather_ctx["has_rain"], weather_ctx)
    tmin, tmax = weather_ctx.get("tmin"), weather_ctx["tmax"]
    temp = f"{tmin:+d}…{tmax:+d}°C" if tmin is not None else f"до {tmax:+d}°C"
    wind_label = _wind_label(weather_ctx.get("wind_ms") or 0, weather_ctx.get("strong_wind"))
    parts = [temp, "дождь" if weather_ctx["has_rain"] else "сухо", wind_label]
    return f"{emoji} " + " · ".join(parts)


def build_weather_context(wdata, day_str, tmax, tmin, wind_ms, rain_prob_day, rain_mm_day, weathercode):
    """Сжимает сырой прогноз в то немногое, что реально нужно для строки погоды и
    подбора образа (см. select_outfit_candidates/score_outfit) — пользователю не
    показываем промежуточные метео-поля, только tags и готовую строку."""
    flags = weather.daytime_outfit_weather(wdata, day_str, tmax, wind_ms, rain_prob_day, rain_mm_day, weathercode)
    has_rain = flags["rain_daytime"]
    hot = tmax is not None and tmax >= 24
    warm = tmax is not None and 17 <= tmax < 24
    tags = []
    if has_rain:
        tags.append("rain")
    if flags["strong_wind"]:
        tags.append("strong_wind")
    if hot:
        tags.append("hot")
    elif warm:
        tags.append("warm")
    else:
        tags.append("cool")
    if flags["sunny"]:
        tags.append("sunny")
    return {
        "tmin": tmin, "tmax": tmax, "has_rain": has_rain,
        "wind_ms": flags["wind_ms"], "strong_wind": flags["strong_wind"],
        "sunny": flags["sunny"], "hot": hot, "warm": warm, "tags": tags,
    }


def _build_look_message(look_data):
    msg = wardrobe_ui.render_wardrobe_message(look_data)
    return msg.text, msg.entities


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _build_entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    msg = wardrobe_ui.entity_card(title, summary, quote, bullets, final, bullet_label)
    return msg.text, msg.entities

def _get_cached_look(cid):
    cached = store.get_valid_wardrobe_daylook(cid)   # ссылочная целостность (version+id)
    if not cached or cached.get("date") != _day_key():   # день — бизнес-правило «раз в день»
        return None
    return cached

def _item_name(it):
    return it.get("name") if isinstance(it, dict) else it

def _save_cached_look(cid, item_ids, look_data):
    text, _ = _build_look_message(look_data)
    w = store.load_wardrobe(cid)
    store.set_wardrobe_daylook(cid, {
        "date": _day_key(),
        "version": w.get("_v", 0),
        "item_ids": list(item_ids or []),
        "look_data": look_data,
        "text": text,
    })


# ---------- главный экран раздела (панель состояния) ----------
def build_wardrobe_keyboard():
    return _kb([
        [("✨ Обновить образ на сегодня", "w_look")],
        [("✂️ Разбор гардероба", "w_improve"), ("🔍 Проверка покупки", "w_check")],
        [("🎚️ Настройки гардероба", "set_wardrobe_g")],
        [("⬅️ Назад", "m_menu")],
    ])


_wardrobe_home_kb = build_wardrobe_keyboard  # старое имя — обратная совместимость вызовов ниже


async def _restore_home_kb(q):
    if q is None or getattr(q, "message", None) is None:
        return
    try:
        await q.message.edit_reply_markup(reply_markup=_wardrobe_home_kb())
    except Exception:
        pass


async def send_home(bot, cid, q=None):
    """Главный экран раздела «Гардероб» — сразу образ на сегодня."""
    status = await util.StatusManager.start(bot, cid=cid, message=q.message if q else None)
    await send_looks(bot, cid, status=status, kb=_wardrobe_home_kb())


_PRIORITY_BLOCK = (
    "ПОРЯДОК ВАЖНОСТИ рекомендаций (сверху вниз, при конфликте — компромисс, "
    "не ориентируйся только на температуру):\n"
    "1. Защита от дождя\n2. Комфорт по температуре\n3. Защита от ветра\n"
    "4. Соответствие стилю пользователя\n5. Не повторять недавние образы\n"
    "Порядок анализа погоды: осадки → температура → ветер → солнце/облачность.\n"
    "Практичность важнее красоты: не предлагай промокнуть ради образа.\n"
    "Примеры компромисса: +23 и дождь → футболка + лёгкая ветровка/дождевик; "
    "+18 и дождь → кофта + дождевик; +28 и дождь → футболка + дождевик (не толстовка); "
    "+12 и ветер → слои + ветровка/куртка."
)


def _resync_wardrobe_gaps(cid, w):
    """Снимает персистентные пробелы, которые уже закрыты вещами в шкафу (симметрично
    add_wardrobe_gap, который их только добавляет). Вызывается при каждой генерации
    образа — «пробел снова появляется при следующей проверке», если вещь удалена."""
    if not _has_rain_outerwear(w):
        return
    gaps = get_wardrobe_gaps(cid)
    kept = [g for g in gaps if g.get("item", "").lower() != "непромокаемая верхняя одежда"]
    if len(kept) != len(gaps):
        store.set_list(config.WARDROBE_GAPS_KEY, cid, kept)


def _build_weather_rules(cid, w, flags):
    """Формирует блок погодных правил для промпта и фиксирует пробелы гардероба.

    Возвращает (rules_text, gap_note). gap_note — честная фраза для ответа, если
    под погоду нужной одежды нет; иначе пустая строка.
    """
    _resync_wardrobe_gaps(cid, w)
    if not flags:
        return "", ""
    rules = []
    gap_note = ""
    has_rain_outer = _has_rain_outerwear(w)
    if flags["rain_daytime"]:
        if has_rain_outer:
            rules.append(
                "ДОЖДЬ: приоритет верхней одежды — дождевик > лёгкая непромокаемая ветровка > "
                "непромокаемая куртка (в прохладу) > обычная ветровка. Бери защиту от дождя из гардероба."
            )
        else:
            rules.append(
                "ДОЖДЬ ожидается, но в гардеробе НЕТ дождевика/ветровки/непромокаемой верхней одежды. "
                "Не выдумывай такие вещи — честно напиши, что подходящей защиты от дождя в шкафу нет."
            )
            gap_note = ("Сегодня пригодились бы дождевик или лёгкая ветровка. "
                        "В гардеробе таких вещей пока нет.")
            add_wardrobe_gap(cid, "непромокаемая верхняя одежда", "дождливая погода", priority=True)
    if flags["heavy_rain"]:
        rules.append(
            "ЛИВЕНЬ: предпочти непромокаемую обувь и кроссовки вместо замши, куртку с капюшоном/дождевик. "
            "Если таких вещей нет — предупреди пользователя."
        )
    if flags["strong_wind"]:
        rules.append(
            "СИЛЬНЫЙ ВЕТЕР: избегай лёгких льняных рубашек как верхнего слоя, очень свободных вещей и "
            "открытой обуви в прохладу; ветровка получает приоритет."
        )
    elif flags.get("wind_ms") is not None and flags["wind_ms"] >= WARDROBE_WIND_LAYER_MS:
        rules.append(
            "ВЕТЕР ОТ 6 М/С: если в гардеробе есть лёгкая ветровка, особенно чёрная, добавь её как "
            "практичный слой. Не называй ветер сильным, просто учти, что без лёгкой верхней одежды "
            "может быть некомфортно."
        )
    if flags["sunny"]:
        rules.append(
            "СОЛНЦЕ/ЖАРА: можно порекомендовать кепку, солнцезащитные очки, лёгкие натуральные ткани — "
            "ТОЛЬКО если они реально есть в гардеробе."
        )
    if not rules:
        return "", ""
    return _PRIORITY_BLOCK + "\n" + "\n".join(rules), gap_note


# ---------- ленивая миграция атрибутов вещи (colors/season/temp_range/...) ----------
_ATTR_DEFAULTS = {
    "last_used": None, "use_count": 0, "accepted_count": 0, "rejected_count": 0,
    "season": [], "temp_range": None, "compatible_categories": [],
    "colors": [], "formality": None, "rain_ok": False, "wind_ok": False,
}


def _ensure_attr_defaults(item):
    """Проставляет недостающие ключи атрибутов дефолтом (не трогает colors, если
    его нет вовсе — это маркер немигрированной вещи, см. _migrate_item_attrs)."""
    for k, v in _ATTR_DEFAULTS.items():
        item.setdefault(k, v if not isinstance(v, (list, dict)) else list(v))


def _needs_migration(item):
    """Маркер «вещь не мигрирована» — именно ОТСУТСТВИЕ ключа colors, не пустой
    список (пустой список — валидный результат AI для вещи без чёткого цвета)."""
    return "colors" not in item


async def _migrate_item_attrs(cid, w):
    """Батч-миграция атрибутов немигрированных вещей одним AI-запросом (экономия
    токенов — не по одной вещи). При недоступности AI — тихий fallback: вещи
    остаются без атрибутов и участвуют в подборе без цветового/сезонного скоринга."""
    flat = _flat_wardrobe_items(w)
    todo = [(zone, subcat, item) for zone, subcat, item in flat if _needs_migration(item)]
    if not todo:
        return w
    listing = "\n".join(f"{i}: {item['name']} ({zone}/{subcat})"
                        for i, (zone, subcat, item) in enumerate(todo))
    prompt = f"""Определи атрибуты вещей одежды по названию, зоне и подкатегории. Для КАЖДОЙ верни:
colors (список 1-2 основных цветов на русском), season (список из "лето"/"деми"/"зима"),
temp_range ([min,max] комфортных °C), formality ("casual"/"smart_casual"/"formal"/"sport"),
rain_ok (true если куртка/обувь непромокаемая или подходит для дождя), wind_ok (true если защищает от ветра — верхняя одежда).
Вещи:
{listing}
Верни строго валидный JSON (без markdown): {{"items":[{{"i":0,"colors":[],"season":[],"temp_range":[min,max],"formality":"","rain_ok":false,"wind_ok":false}}, "..."]}}"""
    try:
        d = await ai.allm_json(prompt, 1400, tier="cheap", module="wardrobe")
        by_idx = {int(it["i"]): it for it in (d.get("items") or []) if "i" in it}
    except Exception as e:
        _log.warning("wardrobe attr migration AI failed, item stays unmigrated for retry: %r", e, exc_info=True)
        # Не пишем в store — вещи остаются без ключа "colors" и попробуют мигрировать
        # заново при следующем подборе. Для ТЕКУЩЕГО подбора отдаём дефолты локально,
        # не мутируя гардероб, чтобы не потерять шанс на повторную миграцию.
        w_local = copy.deepcopy(w)
        for _zone, _subcat, item in _flat_wardrobe_items(w_local):
            if _needs_migration(item):
                _ensure_attr_defaults(item)
        return w_local

    def _mut(w2):
        flat2 = _flat_wardrobe_items(w2)
        todo2 = [(zone, subcat, item) for zone, subcat, item in flat2 if _needs_migration(item)]
        for i, (_zone, _subcat, item) in enumerate(todo2):
            got = by_idx.get(i, {})
            item["colors"] = [str(c).strip() for c in (got.get("colors") or []) if str(c).strip()]
            item["season"] = [str(s).strip() for s in (got.get("season") or []) if str(s).strip()]
            tr = got.get("temp_range")
            item["temp_range"] = [int(tr[0]), int(tr[1])] if isinstance(tr, list) and len(tr) == 2 else None
            item["formality"] = str(got.get("formality") or "").strip() or None
            item["rain_ok"] = bool(got.get("rain_ok"))
            item["wind_ok"] = bool(got.get("wind_ok"))
            item["compatible_categories"] = _ZONE_COMPAT.get(item.get("zone"), [])
            _ensure_attr_defaults(item)

    return store.mutate_wardrobe(cid, _mut)


# ---------- локальный подбор образа (без AI) ----------
_TEMP_CONFLICT_MARGIN = 10  # °C — насколько диапазон temp_range должен разойтись с погодой, чтобы вещь исключалась


def _temp_conflicts(item, weather_ctx):
    tr = item.get("temp_range")
    tmax = weather_ctx.get("tmax")
    if not tr or tmax is None:
        return False
    lo, hi = tr
    return tmax > hi + _TEMP_CONFLICT_MARGIN or tmax < lo - _TEMP_CONFLICT_MARGIN


def select_outfit_candidates(w, weather_ctx):
    """Жёсткая фильтрация кандидатов по зонам (не скоринг). Возвращает
    {zone: [item, ...]} — зона «Верхняя одежда» опциональна по погоде и может
    вернуть пустой список кандидатов, даже если в шкафу есть куртки."""
    candidates = {}
    for zone in store.ZONE_ORDER:
        if zone == "Другое":
            continue
        items = [it for _s, items in (w.get("zones", {}).get(zone, {}) or {}).items() for it in items]
        items = [it for it in items if not _temp_conflicts(it, weather_ctx)]
        if zone == "Верхняя одежда":
            too_warm_for_outer = (weather_ctx.get("tmax") or 0) > WARDROBE_OUTERWEAR_MAX_TEMP
            outerwear_needed = weather_ctx.get("has_rain") or weather_ctx.get("strong_wind") or not too_warm_for_outer
            if not outerwear_needed:
                candidates[zone] = []
                continue
            if weather_ctx.get("has_rain"):
                items = sorted(items, key=lambda it: not it.get("rain_ok"))
        candidates[zone] = items
    return candidates


def _is_neutral_color(color):
    c = str(color or "").lower()
    return any(p in c for p in NEUTRAL_COLORS)


def _color_penalty(items):
    """Штраф за 2+ ярких (не-нейтральных) цвета одновременно в наборе."""
    bright = []
    for it in items:
        for c in (it.get("colors") or []):
            if not _is_neutral_color(c):
                bright.append(c.lower())
    if len(bright) <= 1:
        return 0
    return -10 * (len(bright) - 1)


def score_outfit(items, weather_ctx, wardrobe_history, prefs_text):
    """Скоринг одной комбинации вещей (одна вещь на зону, максимум 5 вещей).
    Возвращает float — выше лучше."""
    score = 0.0
    tmax = weather_ctx.get("tmax")
    for it in items:
        tr = it.get("temp_range")
        if tr and tmax is not None and tr[0] <= tmax <= tr[1]:
            score += 5
        if it.get("zone") == "Аксессуары":
            # У аксессуаров обычно нет temp_range (не привязаны к погоде) — без
            # небольшого бонуса они никогда не выигрывают у варианта "без аксессуара"
            # при равном score и не попадают в образ вовсе.
            score += 2
    score += _color_penalty(items)
    item_ids = {it.get("id") for it in items}
    cutoff_3d = (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_3d:
            continue
        if item_ids & set(entry.get("item_ids") or []):
            score -= 3
    cutoff_7d = (datetime.now(config.TZ) - timedelta(days=7)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_7d:
            continue
        if item_ids and item_ids == set(entry.get("item_ids") or []):
            score -= 100
    if prefs_text:
        avoid_raw = str(prefs_text)
        for it in items:
            for c in (it.get("colors") or []):
                if c and c.lower() in avoid_raw.lower() and "нежелательные цвета" in avoid_raw.lower():
                    score -= 4
    return score


def _top_candidates(items, limit=3):
    """Топ-N кандидатов зоны по частоте использования (use_count), иначе как есть —
    ограничивает размер перебора комбинаций до разумных ~100 вариантов."""
    return sorted(items, key=lambda it: it.get("use_count", 0))[:limit]


def pick_best_outfit(w, weather_ctx, wardrobe_history, prefs_text):
    """Собирает кандидатов, перебирает ограниченные комбинации (топ-3 на зону),
    возвращает лучший набор вещей (list[item]) или None, если нет кандидатов хотя
    бы на одну обязательную зону (Верх/Низ/Обувь)."""
    candidates = select_outfit_candidates(w, weather_ctx)
    required = ["Верх", "Низ", "Обувь"]
    if any(not candidates.get(z) for z in required):
        return None

    def _combos():
        import itertools
        pools = [_top_candidates(candidates[z]) for z in required]
        optional_zones = [z for z in ("Верхняя одежда", "Аксессуары") if candidates.get(z)]
        for zone in optional_zones:
            pools.append([None] + _top_candidates(candidates[zone], limit=2))
        for combo in itertools.product(*pools):
            yield [it for it in combo if it is not None]

    scored = [(score_outfit(combo, weather_ctx, wardrobe_history, prefs_text), combo) for combo in _combos()]
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_combo = scored[0]
    if best_score <= -50:
        # Похоже, единственный приемлемый вариант — это тот самый 7-дневный повтор
        # (guard). Пересчитываем без 7-дневного штрафа — маленький гардероб важнее антиповтора.
        rescored = [(score_outfit(combo, weather_ctx, [
            e for e in wardrobe_history
            if e.get("date", "") >= (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
        ], prefs_text), combo) for _s, combo in scored]
        rescored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_combo = rescored[0]
    return best_combo


# ---------- текст образа: локальный fallback ----------
def build_outfit_reasons(items, weather_ctx, score_details=None):
    """До 3 строк, каждая — про конкретную вещь/погоду, не шаблонная общая фраза.

    Все шаблоны — конструкция "вещь — свойство" (тире, без глагольного
    согласования рода/числа с названием вещи): названия вещей — свободный текст
    от AI, их род/число не гарантированы ("Дождевик" муж., "Шорты" мн.ч.)."""
    reasons = []
    colors = [c for it in items for c in (it.get("colors") or [])]
    bright = [c for c in colors if not _is_neutral_color(c)]
    neutral_anchor = next((it for it in items if any(_is_neutral_color(c) for c in (it.get("colors") or []))), None)
    if neutral_anchor and len(set(bright)) >= 2:
        reasons.append(
            f"{neutral_anchor.get('name')} — нейтральная база, держит {' и '.join(sorted(set(bright))[:2])} в одной палитре."
        )
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("has_rain") and outer.get("rain_ok"):
        reasons.append(f"{outer.get('name')} — защита от дождя.")
    elif weather_ctx.get("has_rain") and not (outer and outer.get("rain_ok")):
        reasons.append("Дождевика или непромокаемой куртки в шкафу нет — сегодня без защиты от дождя.")
    elif outer and weather_ctx.get("warm"):
        reasons.append(f"{outer.get('name')} — пригодится утром, после обеда можно убрать.")
    low = next((it for it in items if it.get("zone") == "Низ"), None)
    if low and weather_ctx.get("hot"):
        reasons.append(f"{low.get('name')} — без перегрева в жару.")
    if len(reasons) < 3:
        unused = next((it for it in items if it.get("use_count", 0) == 0), None)
        if unused:
            reasons.append(f"{unused.get('name')} — ещё не было в образах, стоит попробовать.")
    return reasons[:3]


_LONG_SLEEVE_MARKERS = ("рубаш", "свитер", "худи")


def build_style_tip(items, weather_ctx=None):
    """Один совет по носке, использующий только вещи из items. Пустая строка,
    если нет подходящего шаблона — не выдумываем совет ради совета."""
    weather_ctx = weather_ctx or {}
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("warm"):
        return f"Днём {outer.get('name')} можно снять или расстегнуть."
    sleeved = next((it for it in items
                    if it.get("zone") == "Верх" and any(m in str(it.get("name", "")).lower() for m in _LONG_SLEEVE_MARKERS)),
                   None)
    if sleeved:
        return f"Подверни рукава {sleeved.get('name')} — образ станет легче."
    return ""


def build_wardrobe_insight(cid, items, wardrobe_history):
    """Один инсайт по фиксированному приоритету правил, первое совпавшее — оно и
    возвращается. None, если ничего не подошло."""
    item_ids = {it.get("id") for it in items}
    if wardrobe_history:
        last = wardrobe_history[-1]
        if item_ids and item_ids == set(last.get("item_ids") or []):
            return "Этот образ был и вчера."
    today = datetime.now(config.TZ).date()
    for it in items:
        last_used = it.get("last_used")
        if last_used:
            try:
                days = (today - datetime.fromisoformat(last_used).date()).days
            except ValueError:
                days = None
            if days is not None and days > 14:
                return f"{it.get('name')} — впервые за {days} дней."
    if len(wardrobe_history) >= 3:
        last3 = wardrobe_history[-3:]
        for it in items:
            if all(it.get("id") in (e.get("item_ids") or []) for e in last3):
                return f"{it.get('name')} — в последних образах подряд."
    return None


def save_outfit_feedback(cid, item_ids, weather_tags):
    """Вызывается только при НОВОЙ генерации образа (не при показе кэша дня):
    use_count/last_used вещей + запись в персистентную историю образов."""
    today = _day_key()
    id_set = set(item_ids)

    def _mut(w):
        for _zone, _subcat, item in _flat_wardrobe_items(w):
            if item.get("id") in id_set:
                item["use_count"] = int(item.get("use_count", 0)) + 1
                item["last_used"] = today

    store.mutate_wardrobe(cid, _mut)
    store.add_wardrobe_history_entry(cid, today, weather_tags, item_ids)


# ---------- генерация лука по погоде ----------
def _empty_wardrobe_screen():
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_ward_add"),
    ], [
        InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"),
    ]])
    text = (
        f"<b>{ui_label('empty_wardrobe', 'Шкаф пуст')}</b>\n\n"
        "Чтобы собрать образ из твоих вещей, сначала добавь их в шкаф."
    )
    return text, kb


def _no_outfit_screen(result_kb):
    text = (
        f"<b>{ui_label('no_outfit', 'Не нашлось подходящего образа')}</b>\n\n"
        "В шкафу не хватает вещей на сегодняшнюю погоду. Добавь ещё немного одежды."
    )
    return text, result_kb


async def _ai_reframe_look(cid, items, weather_ctx, reasons, tip, short_date, city):
    """Опциональный тонкий AI-рефрейз локального текста — тот же образ, живее
    формулировки. Тихий fallback на локальные reasons/tip при любой ошибке."""
    names = ", ".join(it.get("name", "") for it in items)
    prompt = f"""Ты личный стилист. Переформулируй живее и короче, не меняя сути и фактов.
Образ: {names}
Причины (локальные заметки, переформулируй естественно): {"; ".join(reasons) if reasons else "нет"}
Совет по носке: {tip or "нет"}
Обращайся на «ты», без имени, без приветствий. Не выдумывай факты, которых нет выше.
Верни строго валидный JSON (без markdown): {{"reasons": ["до 3 строк"], "tip": "1 строка или пусто"}}"""
    try:
        d = await ai.allm_json(prompt, 400, tier="cheap", module="wardrobe")
        new_reasons = [str(r).strip() for r in (d.get("reasons") or []) if str(r).strip()]
        new_tip = str(d.get("tip") or "").strip()
        return (new_reasons or reasons)[:3], new_tip or tip
    except Exception as e:
        _log.warning("wardrobe AI reframe failed, using local text: %r", e, exc_info=True)
        return reasons, tip


async def send_looks(bot, cid, status=None, kb=None):
    result_kb = kb or _wardrobe_home_kb()
    cached = _get_cached_look(cid)
    if cached:
        cached_names = [_item_name(it) for it in (cached.get("look_data") or {}).get("items", [])]
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached_names)[:120]
        text, entities = _build_look_message(cached.get("look_data", {}))
        if status is not None:
            await status.replace(text, entities=entities, reply_markup=result_kb)
        else:
            await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=result_kb)
        return
    w = store.load_wardrobe(cid)
    if not store.wardrobe_to_text(w).strip():
        empty_text, empty_kb = _empty_wardrobe_screen()
        if status is not None:
            await status.replace(empty_text, parse_mode="HTML", reply_markup=empty_kb)
        else:
            await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=empty_kb)
        return
    s = store.get_settings(cid)
    status = status or await util.StatusManager.start(bot, cid)
    tmax = tmin = None
    flags = None
    try:
        wdata = await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 2)
        wd = wdata["daily"]
        day_str = (wd.get("time") or [None])[0] or _day_key()
        tmax = round(wd["temperature_2m_max"][0])
        tmin = round(wd["temperature_2m_min"][0])
        wind_ms = round(wd["windspeed_10m_max"][0])
        rain_prob_day = wd["precipitation_probability_max"][0] or 0
        rain_mm_day = (wd.get("precipitation_sum") or [None])[0]
        weathercode = (wd.get("weathercode") or [None])[0]
        flags = weather.daytime_outfit_weather(
            wdata, day_str, tmax, wind_ms, rain_prob_day, rain_mm_day, weathercode)
        weather_ctx = build_weather_context(wdata, day_str, tmax, tmin, wind_ms, rain_prob_day, rain_mm_day, weathercode)
    except Exception:
        weather_ctx = {"tmin": None, "tmax": None, "has_rain": False, "wind_ms": None,
                       "strong_wind": False, "sunny": False, "hot": False, "warm": False, "tags": []}
    _build_weather_rules(cid, w, flags)  # side effect: фиксирует/снимает пробелы гардероба (дождевик и т.п.)

    w = await _migrate_item_attrs(cid, w)
    style_block = _settings.wardrobe_prefs_context(cid)
    wardrobe_history = store.get_wardrobe_history(cid)
    best = pick_best_outfit(w, weather_ctx, wardrobe_history, style_block)
    if not best:
        no_text, no_kb = _no_outfit_screen(result_kb)
        if status is not None:
            await status.replace(no_text, parse_mode="HTML", reply_markup=no_kb)
        else:
            await bot.send_message(chat_id=cid, text=no_text, parse_mode="HTML", reply_markup=no_kb)
        return

    order = {"Верх": 0, "Низ": 1, "Обувь": 2, "Верхняя одежда": 3, "Аксессуары": 4}
    best_sorted = sorted(best, key=lambda it: order.get(it.get("zone"), 9))
    reasons = build_outfit_reasons(best_sorted, weather_ctx)
    tip = build_style_tip(best_sorted, weather_ctx)
    insight = build_wardrobe_insight(cid, best_sorted, wardrobe_history)

    now_dt = datetime.now(config.TZ)
    short_date = f"{util._WEEKDAY_SHORT[now_dt.weekday()]}, {now_dt.day} {util._MONTHS[now_dt.month - 1]}"
    city = s.get("city", "")
    reasons, tip = await _ai_reframe_look(cid, best_sorted, weather_ctx, reasons, tip, short_date, city)

    item_ids = [it.get("id") for it in best_sorted]
    look_data = {
        "short_date": short_date,
        "city": city,
        "weather_line": _short_weather_line(weather_ctx),
        "items": [{"name": it.get("name", "")} for it in best_sorted],
        "reasons": reasons,
        "style_tip": tip,
        "insight": insight,
    }
    text, entities = _build_look_message(look_data)
    # Порядок важен: save_outfit_feedback мутирует гардероб (use_count/last_used) и
    # бампает версию через mutate_wardrobe — кэш дня должен сохраняться ПОСЛЕ, иначе
    # он окажется привязан к устаревшей версии и станет невалидным сразу же.
    save_outfit_feedback(cid, item_ids, weather_ctx.get("tags", []))
    _save_cached_look(cid, item_ids, look_data=look_data)
    store.recent_looks[str(cid)] = (store.recent_looks.get(str(cid), []) + [", ".join(it.get("name", "") for it in best_sorted)[:80]])[-3:]
    store.last_look[str(cid)] = ", ".join(it.get("name", "") for it in best_sorted)[:120]
    store.last_source[str(cid)] = "Гардероб · Образ"
    store.last_answer[str(cid)] = text
    await status.replace(text, entities=entities, reply_markup=result_kb)


# ---------- шкаф ----------
# Порядок важен: «Верхняя одежда» проверяется раньше «Верх», иначе «куртка»/«ветровка»
# по подстроке «верх» ушли бы в «Верх».
# _zone_of используется только для миграции старых записей и как fallback в
# _guess_subcategory — новые вещи получают zone/subcategory явно от LLM.
ZONES = [
    ("Верхняя одежда", ["верхняя одежд", "верхн", "куртк", "ветровк", "пиджак", "пальто",
                        "плащ", "дождевик", "парк", "пуховик", "тренч", "анорак", "бомбер",
                        "жилет"]),
    ("Верх", ["верх", "футбол", "рубаш", "свит", "толстов", "худи", "лонгслив", "поло", "майк", "кофт"]),
    ("Низ", ["низ", "джинс", "брюк", "штан", "шорт", "юбк"]),
    ("Обувь", ["обув", "кроссов", "ботин", "кед", "туфл", "сандал"]),
    ("Аксессуары", ["аксессуар", "часы", "кольц", "ремен", "шапк", "кепк", "очк", "шарф", "сумк", "цепоч", "носк", "украшен"]),
]

# Порядок зон для отображения статистики и шкафа (владелец — store.py, здесь алиас
# для обратной совместимости импортов ui/тестов).
ZONE_ORDER = store.ZONE_ORDER
ZONE_EMOJI = {}

def _zone_of(category):
    c = category.lower()
    for zone, keys in ZONES:
        if any(k in c for k in keys):
            return zone
    return "Другое"


_SUBCAT_KEYWORDS = {
    "Футболки": ["футболк", "майк"], "Поло": ["поло"], "Рубашки": ["рубаш"],
    "Лонгсливы": ["лонгслив"], "Свитеры": ["свитер", "свитш", "джемпер"],
    "Кардиганы": ["кардиган"], "Худи": ["худи", "толстовк"], "Пиджаки": ["пиджак"],
    "Ветровки": ["ветровк"], "Куртки": ["куртк", "бомбер", "анорак", "жилет"], "Пальто": ["пальто"],
    "Пуховики": ["пуховик"], "Плащи": ["плащ", "тренч", "дождевик"],
    "Джинсы": ["джинс"], "Брюки": ["брюк", "штан"], "Чиносы": ["чино"],
    "Шорты": ["шорт"], "Спортивные брюки": ["спортивн"],
    "Кеды": ["кед"], "Кроссовки": ["кроссов"], "Лоферы": ["лофер"],
    "Ботинки": ["ботин"], "Сандалии": ["сандал"], "Тапочки": ["тапоч"],
    "Кепки": ["кепк"], "Шапки": ["шапк"], "Ремни": ["ремен", "ремн"], "Часы": ["час"],
    "Очки": ["очк"], "Украшения": ["украшен", "цепоч", "кольц"], "Шарфы": ["шарф"],
    "Перчатки": ["перчат"], "Сумки": ["сумк"], "Рюкзаки": ["рюкзак"], "Носки": ["носк"],
}


def _guess_subcategory(zone, name, fallback_text=""):
    """Fallback-эвристика (без LLM): по ключевым словам в названии вещи внутри
    зоны. Если по названию ничего не нашлось — вторая попытка по fallback_text
    (например, исходная строка-категория при миграции старых записей). Возвращает
    валидную подкатегорию из store.ZONE_SUBCATS[zone] или «Другое»."""
    valid = set(store.ZONE_SUBCATS.get(zone, ["Другое"]))
    for text in (str(name).lower(), str(fallback_text).lower()):
        if not text:
            continue
        for subcat, keys in _SUBCAT_KEYWORDS.items():
            if subcat in valid and any(k in text for k in keys):
                return subcat
    return "Другое"


def normalize_parsed_item(raw):
    """Валидирует/нормализует один сырой объект от LLM (добавление вещи) в готовый
    для store.add_wardrobe_items item без id. None, если нет названия."""
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None
    name = str(raw["name"]).strip()
    zone = raw.get("zone") if raw.get("zone") in store.ZONE_SUBCATS else _zone_of(name)
    subcat = raw.get("subcategory")
    if subcat not in store.ZONE_SUBCATS.get(zone, []):
        subcat = _guess_subcategory(zone, name)
    return {
        "zone": zone, "subcategory": subcat, "name": name,
        "color": str(raw.get("color") or "").strip(),
        "color_secondary": (str(raw["color_secondary"]).strip() or None) if raw.get("color_secondary") else None,
        "material": (str(raw["material"]).strip() or None) if raw.get("material") else None,
        "style": (str(raw.get("style") or "").strip() or None),
        "season": None,
    }


def _flat_wardrobe_items(w):
    """[(zone, subcategory, item_dict), ...] по всему гардеробу."""
    items = []
    for zone, subs in (w or {}).get("zones", {}).items():
        for subcat, values in subs.items():
            for item in values:
                items.append((zone, subcat, item))
    return items

# ---------- статистика и готовность гардероба ----------
def wardrobe_stats(w):
    """Считает вещи по зонам. Возвращает (total, {zone: count}) с полным набором зон."""
    counts = {z: 0 for z in ZONE_ORDER}
    total = 0
    for zone, _subcat, _item in _flat_wardrobe_items(w):
        counts[zone if zone in counts else "Другое"] += 1
        total += 1
    return total, counts


# --- слабые места гардероба (персистентный список пробелов) ---
_RAIN_OUTER_MARKERS = ("дождевик", "ветровк", "непромокаем", "мембран", "raincoat",
                       "waterproof", "плащ", "тренч", "анорак")


def _has_rain_outerwear(w):
    """Есть ли в гардеробе верх для дождя (по ключевым словам)."""
    text = store.wardrobe_to_text(w).lower()
    return any(m in text for m in _RAIN_OUTER_MARKERS)


def get_wardrobe_gaps(cid):
    return store.get_list(config.WARDROBE_GAPS_KEY, cid)


def add_wardrobe_gap(cid, item, reason, priority=True):
    """Добавляет пробел гардероба без дублей (по item, case-insensitive)."""
    gaps = store.get_list(config.WARDROBE_GAPS_KEY, cid)
    if any(g.get("item", "").lower() == item.lower() for g in gaps):
        return False
    gaps.append({"item": item, "reason": reason, "priority": bool(priority)})
    store.set_list(config.WARDROBE_GAPS_KEY, cid, gaps)
    return True


_ZONES_DESC = "; ".join(f"{z}: {', '.join(subs)}" for z, subs in store.ZONE_SUBCATS.items())

async def _parse_and_add(bot, cid, text):
    parsed = await ai.allm_json(
        f"Разбери вещи по атрибутам. Зоны и подкатегории (используй ТОЛЬКО эти значения, "
        f"если не подходит ни одна — subcategory=\"Другое\"): {_ZONES_DESC}\n"
        f"Вещи:\n{secure.wrap_untrusted(text, 'список вещей')}\n"
        "Для каждой вещи верни: zone (одна из зон выше, если не ясно — \"Другое\"), "
        "subcategory (строго из списка для этой зоны), name (полное название: тип + цвет + бренд/детали), "
        "color (основной цвет), color_secondary (доп. цвет или пусто), material (материал или пусто), "
        "style (Casual/Formal/Sport/Streetwear и т.п. или пусто). Сохраняй бренд если указан.\n"
        'JSON: {"items": [{"zone":"","subcategory":"","name":"","color":"","color_secondary":"",'
        '"material":"","style":""}]}', 900, tier="cheap", module="wardrobe")
    norm = [normalize_parsed_item(it) for it in (parsed.get("items") or [])]
    norm = [it for it in norm if it]
    store.add_wardrobe_items(cid, norm)
    return len(norm)

async def add_item(bot, cid, text):
    try:
        added = await _parse_and_add(bot, cid, text)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    await bot.send_message(chat_id=cid, text=f"Добавлено в шкаф ({added}).", reply_markup=closet_kb())

async def add_item_settings(bot, cid, text):
    try:
        added = await _parse_and_add(bot, cid, text)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    await bot.send_message(chat_id=cid, text=f"Добавлено в шкаф ({added}).")


async def handle_wardrobe_search(bot, cid, query):
    """Ищет по подстроке названия вещи (без учёта регистра), показывает
    первое совпадение с кнопкой удаления. По образцу поиска в словаре."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(chat_id=cid, text="Пришли название вещи или часть названия.")
        return
    w = store.load_wardrobe(cid)
    match = None
    for _zone, _subcat, item in _flat_wardrobe_items(w):
        if query_norm in str(item.get("name", "")).casefold():
            match = item
            break
    if not match:
        await bot.send_message(
            chat_id=cid, text="Не нашла такую вещь. Попробуй другое название или посмотри весь список.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎚️ Настройки гардероба", callback_data="w_del_g")]]),
        )
        return
    lines = [match.get("name", "")]
    if match.get("color"):
        lines.append(f"Цвет: {match['color']}")
    if match.get("material"):
        lines.append(f"Материал: {match['material']}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"w_searchdel_{match.get('id')}")],
        [InlineKeyboardButton("🔍 Искать ещё", callback_data="w_search")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="w_del_g")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=kb)

# ---------- удаление: навигация Зона → Подкатегория → мультивыбор (cleanup.py) ----------
# origin-слаг вместо полного callback «назад» — чтобы не протаскивать "_" сквозь разбор data.split("_").
ZONE_SLUG = {"Верх": "top", "Низ": "bot", "Верхняя одежда": "out",
             "Обувь": "shoe", "Аксессуары": "acc", "Другое": "oth"}
ZONE_BY_SLUG = {slug: zone for zone, slug in ZONE_SLUG.items()}
_ORIGIN_BACK = {"m": "m_wardrobe", "g": "m_wardrobe"}


async def send_del_zones(bot, cid, q=None, origin="m"):
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    if not total:
        await bot.send_message(chat_id=cid, text="Шкаф пуст.", reply_markup=closet_kb()); return
    rows = [[InlineKeyboardButton(f"{z} ({counts.get(z,0)})",
                                  callback_data=f"w_delz_{ZONE_SLUG[z]}_{origin}")]
            for z in ZONE_ORDER if counts.get(z, 0) > 0]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=_ORIGIN_BACK.get(origin, "m_wardrobe"))])
    msg = wardrobe_ui.zone_picker_screen()
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_wardrobe_zones(bot, cid, q=None):
    """Кнопка «Настройки гардероба»: сразу список зон с количеством вещей, без
    промежуточного экрана «Добавить/Удалить». Переиспользует навигацию
    зона → подкатегория → список вещей (cleanup.py), origin="g"."""
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    rows = [[InlineKeyboardButton(f"{z} ({counts.get(z,0)})", callback_data=f"w_delz_{ZONE_SLUG[z]}_g")]
            for z in ZONE_ORDER if counts.get(z, 0) > 0]
    rows.append([InlineKeyboardButton("✏️ Добавить вещь", callback_data="w_add")])
    if total:
        rows.append([InlineKeyboardButton("🔍 Найти вещь", callback_data="w_search")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe")])
    msg = wardrobe_ui.wardrobe_home_screen(total)
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_del_subcats(bot, cid, zone_slug, origin="m", q=None):
    zone = ZONE_BY_SLUG.get(zone_slug)
    w = store.load_wardrobe(cid)
    subs = w.get("zones", {}).get(zone, {}) if zone else {}
    rows = [[InlineKeyboardButton(f"{sc} ({len(items)})",
                                  callback_data=f"w_delsc_{zone_slug}_{i}_{origin}")]
            for i, sc in enumerate(store.ZONE_SUBCATS.get(zone, [])) if (items := subs.get(sc, []))]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"w_del_{origin}")])
    msg = wardrobe_ui.subcat_picker_screen(zone or "Другое")
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ---------- улучшить гардероб ----------
# Одна тема на показ — экран не пытается разобрать всё гардероб сразу (см. docs/wardrobe.md).
_ANALYSIS_TOPICS = [
    ("balance", "баланс категорий (чего в шкафу много, а чего не хватает по зонам)"),
    ("duplicates", "повторяющиеся, почти одинаковые вещи"),
    ("colors", "слабые или отсутствующие цветовые сочетания"),
    ("layers", "нехватка слоёв (верхних слоёв, фактур, что надеть поверх базы)"),
    ("seasonal", "готовность гардероба к текущему сезону"),
    ("hard_to_combine", "вещи, которые сложно сочетать с остальным гардеробом"),
    ("best_buy", "одна покупка, которая даст максимальный эффект на все будущие образы"),
]
_ANALYSIS_TTL_DAYS = 14


def _wardrobe_content_hash(w):
    """Хэш состава шкафа (какие вещи есть, не порядок) — меняется при добавлении/
    удалении вещи, не меняется от простого перечитывания. Определяет, устарел ли
    кэшированный разбор."""
    import hashlib
    ids = sorted(it.get("id", "") for _z, _s, it in _flat_wardrobe_items(w))
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:16]


def _fallback_improve_data(w):
    """Резервный разбор по зонам (без ИИ), всегда в теме «баланс категорий» —
    остальные темы без ИИ содержательно не собрать."""
    items = _flat_wardrobe_items(w)
    zones = {}
    for zone, _subcat, item in items:
        zones.setdefault(zone, []).append(item["name"])

    covered = [f"{zone.lower()}" for zone in ("Верх", "Низ", "Обувь", "Аксессуары") if zones.get(zone)]

    missing_zone = next((z for z in ("Верх", "Низ", "Обувь") if not zones.get(z)), None)
    if missing_zone == "Верх":
        missing = "Базового верха — без него не собрать даже повседневный образ."
        buy_item, buy_why = ("Плотная однотонная футболка или рубашка спокойного цвета",
                              "Станет основой верха и свяжет низ с обувью.")
    elif missing_zone == "Низ":
        missing = "Базового низа — силуэт держится без опоры, образы выглядят незавершённо."
        buy_item, buy_why = ("Прямые джинсы или лёгкие брюки нейтрального цвета",
                              "Дадут универсальный низ под весь имеющийся верх.")
    elif missing_zone == "Обувь":
        missing = "Базовой обуви — без неё любой образ выглядит недоделанным."
        buy_item, buy_why = ("Нейтральные кеды или кроссовки", "Завершат большинство повседневных образов.")
    else:
        missing = "Явных пустых категорий нет — дальше есть смысл смотреть на сочетаемость цветов и слоёв."
        buy_item, buy_why = ("Один лёгкий верхний слой нейтрального цвета",
                              "Добавит многослойность без ухода от уже сложившегося стиля.")

    return {
        "headline": "Базовый разбор по категориям",
        "summary": "Разбор по зонам без ИИ. Начни с баланса верха, низа и обуви — это даст больше всего новых сочетаний.",
        "imbalance_title": "Главный перекос",
        "imbalance": missing,
        "covered": covered,
        "missing_title": "Чего реально не хватает",
        "missing": missing,
        "next_buy_title": "Следующая разумная покупка",
        "next_buy_item": buy_item,
        "next_buy_why": buy_why,
    }


async def send_improve(bot, cid, force=False):
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_ward_add"),
        ], [
            InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"),
        ]])
        await bot.send_message(
            chat_id=cid,
            text=f"<b>{ui_label('empty_wardrobe', 'Шкаф пуст')}</b>\n\n"
                 "Добавь вещи в шкаф — тогда разберу гардероб и дам советы.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    content_hash = _wardrobe_content_hash(w)
    cached = w.get("_analysis") or {}
    is_fresh = (
        not force
        and cached.get("wardrobe_hash") == content_hash
        and cached.get("generated_at")
        and (datetime.now(config.TZ) - datetime.fromisoformat(cached["generated_at"])) < timedelta(days=_ANALYSIS_TTL_DAYS)
    )
    if is_fresh:
        d = cached["data"]
    else:
        topic_idx = (int(cached.get("topic_idx", -1)) + 1) % len(_ANALYSIS_TOPICS)
        _topic_key, topic_desc = _ANALYSIS_TOPICS[topic_idx]
        prompt = _improve_prompt(cid, wardrobe_text, topic_desc)
        try:
            d = await ai.allm_json(prompt, 1200, module="wardrobe", route="gemini")
            w["_analysis"] = {
                "data": d,
                "wardrobe_hash": content_hash,
                "topic_idx": topic_idx,
                "generated_at": datetime.now(config.TZ).isoformat(),
            }
            store.save_wardrobe(w, cid)
        except Exception as e:
            _log.warning("wardrobe improve AI failed, using fallback: %r", e, exc_info=True)
            d = _fallback_improve_data(w)

    d = _merge_priority_gaps(cid, d)
    msg = wardrobe_ui.improve_card(d)
    store.last_source[str(cid)] = "Гардероб · Разбор"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe")]]))


def _improve_prompt(cid, wardrobe_text, topic_desc):
    """Промпт персонального стилиста — один фокус за раз (see _ANALYSIS_TOPICS),
    а не полный аудит на экран текста."""
    prefs = _settings.wardrobe_prefs_context(cid)
    ctx_block = (f"Данные о пользователе (учитывай в анализе):\n{prefs}\n\n") if prefs else ""
    return f"""Ты — персональный стилист уровня Thread, Whering и мужской стилист GQ.
Твоя задача — не перечислить вещи, а провести короткий точечный разбор одной темы гардероба так, чтобы пользователь подумал: «Это разбирал живой стилист».

Опирайся на знания мужского стиля, цветовых сочетаний, пропорций силуэта, капсульного гардероба, минимализма, smart casual, streetwear, old money, японского минимализма и современной европейской моды.

{ctx_block}Гардероб пользователя:
{wardrobe_text}

Сегодняшняя тема разбора: {topic_desc}. Разбирай только её — не пытайся охватить весь гардероб сразу.

ПРАВИЛА:
- Обращайся на «ты», без имени.
- Никаких общих фраз («гардероб выглядит рабочим», «докупайте точечно»).
- Рекомендация покупки объясняет ПОЧЕМУ и какой эффект даёт (с чем свяжет, сколько новых сочетаний).
- Никакой воды, повторов и шаблонов. Короткие ёмкие предложения. Telegram-формат.
- Не упоминай сегодняшнюю погоду и не описывай готовый образ на сегодня — это отдельный экран.

Верни строго валидный JSON (без markdown):
{{"headline": "заголовок в 3-5 слов про сегодняшнюю тему (например «Гардероб собран, но немного однообразен»)",
"summary": "1 предложение — суть темы для этого гардероба",
"imbalance_title": "короткий заголовок находки по теме (например «Главный перекос»)",
"imbalance": "1-2 предложения — конкретная находка по теме на основе реального гардероба",
"covered": ["категория, которая уже хорошо закрыта", "... максимум 4, короткие фразы"],
"missing_title": "короткий заголовок (например «Чего реально не хватает»)",
"missing": "1-2 предложения — чего конкретно не хватает по теме",
"next_buy_title": "короткий заголовок (например «Следующая разумная покупка»)",
"next_buy_item": "одна конкретная вещь (тип, цвет, посадка)",
"next_buy_why": "1-2 предложения — почему именно она и какой эффект даст"}}"""


def _merge_priority_gaps(cid, d):
    """Персистентный пробел гардероба (например, дождевик под сегодняшнюю погоду)
    важнее темы дня — если он есть, заменяет собой next_buy разбора."""
    gaps = [g for g in get_wardrobe_gaps(cid) if g.get("priority")]
    if not gaps:
        return d
    gap = gaps[0]
    d = dict(d)
    d["next_buy_title"] = "Срочная покупка"
    d["next_buy_item"] = str(gap.get("item", "")).capitalize()
    d["next_buy_why"] = str(gap.get("reason", "")) or "Закрывает реальный пробел под текущую погоду."
    return d


async def check_purchase(bot, cid, text):
    w = store.load_wardrobe(cid)
    web_block = ""
    web_data = await asyncio.to_thread(
        research.tavily_snippet,
        f"{text} отзывы обзор стоит ли покупать",
        900,
    )
    if web_data:
        web_block = (
            "\nАктуальная информация о товаре из сети (используй как дополнительный контекст):\n"
            + secure.wrap_untrusted(web_data, "web") + "\n"
        )
    prefs = _settings.wardrobe_prefs_context(cid)
    prefs_ctx = f"{prefs}\n" if prefs else ""
    prompt = f"""Ты честный стилист-аналитик. Пользователь думает купить: {text}
{prefs_ctx}
Гардероб пользователя:
{store.wardrobe_to_text(w)}
{web_block}
Задача — конкретный анализ, не комплименты. Ответь на вопросы:
1. С какими конкретными вещами из гардероба это сочетается (назови их)?
2. Каких вещей не хватает, чтобы это носить?
3. Дублирует ли это что-то уже имеющееся?
4. Насколько вещь соответствует стилю и повседневным задачам?

Верни JSON (без markdown):
{{"verdict":"БРАТЬ или НЕ БРАТЬ","why":["2-3 конкретные причины на основе реального гардероба, на ты, без имени"],"outro":"1 строка — честный итог с характером, на ты, без имени"}}

Если гардероб пустой — честно скажи что оценка приблизительная."""
    try:
        d = await ai.allm_json(prompt, 600, tier="smart", module="wardrobe")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    verdict = d.get("verdict", "")
    why = list(d.get("why") or [])
    total, _ = wardrobe_stats(w)
    if total <= 0:
        why.insert(0, (
            "Сейчас рекомендация основана в основном на характеристиках вещи и отзывах из "
            "интернета. После заполнения гардероба я смогу оценивать совместимость покупки с "
            "твоими вещами и выявлять дубликаты."
        ))
    text_out, entities = _build_entity_card(
        "Проверка покупки",
        _clean_text(text),
        f"Вердикт: {verdict}" if verdict else "",
        why,
        d.get("outro") or "Покупай только если вещь закрывает реальный пробел в гардеробе.",
        bullet_label="Почему:",
    )
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = text_out
    await bot.send_message(chat_id=cid, text=text_out, entities=entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe")]]))


# ---------- добавление файлом (старый режим, оставлен) ----------
async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


# ---------- роутер кнопок ----------
async def handle_callback(bot, cid, q, data):
    if data == "w_look":
        store.clear_wardrobe_daylook(cid)
        try:
            await send_home(bot, cid, q=q)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="Напиши вещь в формате: тип + цвет + детали/бренд.\n"
                               "Напр.: «Футболка белая Uniqlo плотная» или «Шорты серые тонкие». Можно списком.",
                               reply_markup=_back_kb()); return
    if data == "w_search":
        store.pending_input[str(cid)] = "wardrobe_search"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="w_del_g")]])
        await bot.send_message(chat_id=cid, text="🔍 Напиши название вещи или часть названия.",
                               reply_markup=kb); return
    if data.startswith("w_searchdel_"):
        item_id = data[len("w_searchdel_"):]
        n = store.remove_wardrobe_items(cid, [item_id])
        text = "Удалено." if n else "Вещь уже удалена."
        await bot.send_message(chat_id=cid, text=text)
        await send_wardrobe_zones(bot, cid); return
    if data == "w_del_g":
        await send_wardrobe_zones(bot, cid, q=q); return
    if data.startswith("w_del_"):
        await send_del_zones(bot, cid, q=q, origin=data[len("w_del_"):]); return
    if data == "w_del":
        await send_del_zones(bot, cid, q=q, origin="m"); return
    if data.startswith("w_delz_"):
        _, zone_slug, origin = data.split("_")[1:]
        await send_del_subcats(bot, cid, zone_slug, origin=origin, q=q); return
    if data.startswith("w_delsc_"):
        _, zone_slug, idx, origin = data.split("_")[1:]
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"kast_{zone_slug}_{idx}_{origin}"); return
    if data == "w_improve":
        status = await util.StatusManager.start_inline(q, bot=bot, cid=cid, stages=util.StatusManager.TOPIC_STAGES["wardrobe"])
        try:
            await send_improve(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await status.stop(delete=False)
            await _restore_home_kb(q)
        return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="Пришли ссылку или название вещи - оценю, брать или нет.",
                               reply_markup=_back_kb()); return
