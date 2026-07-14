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
from wardrobe_model import (
    ZONE_ORDER,
    flat_items as _flat_wardrobe_items,
    guess_subcategory as _guess_subcategory,
    has_rain_outerwear as _has_rain_outerwear,
    normalize_parsed_item,
    wardrobe_stats,
    zone_of as _zone_of,
)
from wardrobe_outfit import (
    build_outfit_reasons,
    build_style_tip,
    build_wardrobe_insight,
    pick_best_outfit,
    save_outfit_feedback,
    score_outfit,
    select_outfit_candidates,
)

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6

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
        [("⬅️ Назад", "m_wardrobe"), ("🏠 Меню", "m_menu")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_wardrobe"), ("🏠 Меню", "m_menu")]])

def _day_key():
    return datetime.now(config.TZ).date().isoformat()


def _weather_decision(weather_ctx):
    """Одно предложение-решение про одежду вместо цифр погоды — пользователь
    открывает Гардероб, чтобы не думать над прогнозом самому."""
    if not weather_ctx or weather_ctx.get("tmax") is None:
        return ""
    has_rain = weather_ctx.get("has_rain")
    strong_wind = weather_ctx.get("strong_wind")
    hot = weather_ctx.get("hot")
    warm = weather_ctx.get("warm")

    if has_rain and hot:
        return "Сегодня дождь, но тепло — возьми дождевик, тяжёлую куртку надевать не стоит."
    if has_rain and strong_wind:
        return "Дождь и ветер — нужна непромокаемая куртка и закрытая обувь."
    if has_rain:
        return "Сегодня дождь — возьми дождевик или непромокаемую куртку."
    if strong_wind and hot:
        return "Тепло, но ветрено — лёгкая ветровка не помешает."
    if strong_wind:
        return "Сильный ветер — нужен верхний слой."
    if hot:
        return "Сегодня жарко — одевайся легко."
    if warm:
        return "Сегодня лучше одеться легко, но оставить один слой на прохладное утро."
    return "Сегодня прохладно — нужен тёплый верхний слой."


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

def _build_purchase_message(data):
    msg = wardrobe_ui.purchase_check_card(data)
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
        [("✂️ Разбор шкафа", "w_improve"), ("🔍 Оценка", "w_check")],
        [("🎚️ Настройки гардероба", "set_wardrobe_settings")],
        [("⬅️ Назад", "m_menu"), ("🏠 Меню", "m_menu")],
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


# ---------- генерация лука по погоде ----------
def _empty_wardrobe_screen():
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_ward_add"),
    ], [
        InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"),
        InlineKeyboardButton("🏠 Меню", callback_data="m_menu"),
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


async def _ai_reframe_look(cid, items, weather_ctx, reasons, tip):
    """Опциональный тонкий AI-рефрейз локального текста — тот же образ, живее
    формулировки. Тихий fallback на локальные reasons/tip при любой ошибке."""
    names = ", ".join(it.get("name", "") for it in items)
    prompt = f"""Ты личный стилист. Переформулируй живее и короче, не меняя сути и фактов.
Образ: {names}
Причины (локальные заметки, переформулируй естественно): {"; ".join(reasons) if reasons else "нет"}
Совет по носке: {tip or "нет"}
Обращайся на «ты», без имени, без приветствий. Не выдумывай факты, которых нет выше.
Никогда не пиши, что вещь "давно не носили"/"ещё не пробовали"/"пора попробовать" — это не факт, а
предположение, даже если звучит правдоподобно.
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

    reasons, tip = await _ai_reframe_look(cid, best_sorted, weather_ctx, reasons, tip)

    item_ids = [it.get("id") for it in best_sorted]
    look_data = {
        "weather_decision": _weather_decision(weather_ctx),
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👕 Мой гардероб", callback_data="w_del_g")]]),
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="w_del_g"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=kb)

# ---------- удаление: навигация Зона → Подкатегория → мультивыбор (cleanup.py) ----------
# origin-слаг вместо полного callback «назад» — чтобы не протаскивать "_" сквозь разбор data.split("_").
ZONE_SLUG = {"Верх": "top", "Низ": "bot", "Верхняя одежда": "out",
             "Обувь": "shoe", "Аксессуары": "acc", "Другое": "oth"}
ZONE_BY_SLUG = {slug: zone for zone, slug in ZONE_SLUG.items()}
_ORIGIN_BACK = {"m": "m_wardrobe", "g": "w_del_g"}


async def send_del_zones(bot, cid, q=None, origin="m"):
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    if not total:
        await bot.send_message(chat_id=cid, text="Шкаф пуст.", reply_markup=closet_kb()); return
    rows = [[InlineKeyboardButton(f"{z} ({counts.get(z,0)})",
                                  callback_data=f"w_delz_{ZONE_SLUG[z]}_{origin}")]
            for z in ZONE_ORDER if counts.get(z, 0) > 0]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=_ORIGIN_BACK.get(origin, "m_wardrobe")), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")])
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
    """Кнопка «Мой гардероб»: сразу список зон с количеством вещей, без
    промежуточного экрана «Добавить/Удалить». Переиспользует навигацию
    зона → подкатегория → список вещей (cleanup.py), origin="g"."""
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    rows = [[InlineKeyboardButton(f"{z} ({counts.get(z,0)})", callback_data=f"w_delz_{ZONE_SLUG[z]}_g")]
            for z in ZONE_ORDER if counts.get(z, 0) > 0]
    rows.append([InlineKeyboardButton("✏️ Добавить вещь", callback_data="w_add")])
    if total:
        rows.append([InlineKeyboardButton("🔍 Найти вещь", callback_data="w_search")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe_settings"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")])
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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"w_del_{origin}"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")])
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
# Капсульный аудит всего гардероба сразу (см. docs/wardrobe.md) — не тема по кругу.
_ANALYSIS_TTL_DAYS = 14


def _wardrobe_content_hash(w):
    """Хэш состава шкафа (какие вещи есть, не порядок) — меняется при добавлении/
    удалении вещи, не меняется от простого перечитывания. Определяет, устарел ли
    кэшированный разбор."""
    import hashlib
    ids = sorted(it.get("id", "") for _z, _s, it in _flat_wardrobe_items(w))
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:16]


def _fallback_improve_data(w):
    """Резервный разбор по зонам (без ИИ) — простой баланс категорий, капсульный
    аудит по стилю и сочетаниям без ИИ содержательно не собрать."""
    items = _flat_wardrobe_items(w)
    zones = {}
    for zone, _subcat, item in items:
        zones.setdefault(zone, []).append(item["name"])

    works = [f"{zone.lower()} — {len(names)} шт." for zone, names in zones.items() if zone in ("Верх", "Низ", "Обувь")]

    missing_zone = next((z for z in ("Верх", "Низ", "Обувь") if not zones.get(z)), None)
    if missing_zone == "Верх":
        fix_first = ["Добавь плотную однотонную футболку или рубашку спокойного цвета — свяжет низ с обувью."]
    elif missing_zone == "Низ":
        fix_first = ["Добавь прямые джинсы или лёгкие брюки нейтрального цвета — универсальный низ под весь верх."]
    elif missing_zone == "Обувь":
        fix_first = ["Добавь нейтральные кеды или кроссовки — завершат большинство повседневных образов."]
    else:
        fix_first = ["Явных пустых категорий нет — дальше стоит смотреть на сочетаемость цветов и слоёв."]

    return {
        "headline": "Базовый разбор по категориям без ИИ.",
        "works": works,
        "clashes": [],
        "fix_first": fix_first,
        "skip_buying": "",
        "next_capsule": "",
    }


async def send_improve(bot, cid, force=False):
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_ward_add"),
        ], [
            InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"),
            InlineKeyboardButton("🏠 Меню", callback_data="m_menu"),
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
        prompt = _improve_prompt(cid, wardrobe_text)
        try:
            d = await ai.allm_json(prompt, 1800, module="wardrobe", route="gemini")
            w["_analysis"] = {
                "data": d,
                "wardrobe_hash": content_hash,
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
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe"), ("🏠 Меню", "m_menu")]]))


def _improve_prompt(cid, wardrobe_text):
    """Промпт персонального стилиста — капсульный аудит всего гардероба сразу:
    что уже работает, что выбивается, что менять первым."""
    prefs = _settings.wardrobe_prefs_context(cid)
    ctx_block = (f"Данные о пользователе (учитывай в анализе):\n{prefs}\n\n") if prefs else ""
    return f"""Ты — персональный стилист уровня Thread, Whering и мужской стилист GQ.
Твоя задача — короткий капсульный аудит гардероба так, чтобы пользователь подумал: «Это разбирал живой стилист».

Опирайся на знания мужского стиля, цветовых сочетаний, пропорций силуэта, капсульного гардероба, минимализма, smart casual, streetwear, old money, японского минимализма и современной европейской моды.

{ctx_block}Гардероб пользователя:
{wardrobe_text}

Задача — оценить гардероб как единую капсулу: что уже держит образы вместе (основной силуэт, база), что выбивается из общей палитры/посадки/стиля, и какие 2-3 замены дадут максимальный эффект на все будущие образы.

ПРАВИЛА:
- Обращайся на «ты», без имени.
- Никаких общих фраз («гардероб выглядит рабочим», «докупайте точечно»).
- Называй конкретные вещи из гардероба пользователя, не абстрактные категории.
- fix_first — это ЗАМЕНЫ существующих слабых вещей на конкретный тип/цвет/посадку, не любые новые покупки.
- skip_buying — вещи, которые пользователь может захотеть купить, но они дублируют то, что уже есть.
- Никакой воды, повторов и шаблонов. Короткие ёмкие предложения. Telegram-формат.
- Не упоминай сегодняшнюю погоду и не описывай готовый образ на сегодня — это отдельный экран.
- Заполни ВСЕ поля JSON содержательно (headline, works, clashes, fix_first, skip_buying, next_capsule) — пустых или отсутствующих полей быть не должно, гардероб пользователя для этого достаточно большой.

Верни строго валидный JSON (без markdown):
{{"headline": "1 предложение — главный вывод по гардеробу целиком",
"works": ["конкретная вещь или сочетание, которое уже держит образы — максимум 4, короткие фразы"],
"clashes": ["конкретная вещь, которая выбивается из капсулы, и почему коротко — максимум 4"],
"fix_first": ["конкретная замена вида «старое → новое» — максимум 3, в порядке приоритета"],
"skip_buying": "1 строка — что не стоит покупать сейчас, потому что дублирует уже имеющееся",
"next_capsule": "1 строка — вещи через « · », как будет выглядеть капсула после замен из fix_first"}}"""


def _merge_priority_gaps(cid, d):
    """Персистентный пробел гардероба (например, дождевик под сегодняшнюю погоду)
    важнее обычного аудита — если он есть, добавляется первым пунктом в «что менять первым»."""
    gaps = [g for g in get_wardrobe_gaps(cid) if g.get("priority")]
    if not gaps:
        return d
    gap = gaps[0]
    item = str(gap.get("item", "")).capitalize()
    reason = str(gap.get("reason", "")) or "Закрывает реальный пробел под текущую погоду."
    reason = reason[0].lower() + reason[1:] if reason else reason
    d = dict(d)
    d["fix_first"] = ([f"{item} — {reason}"] + list(d.get("fix_first") or []))[:3]
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
Задача — конкретный анализ на цифрах, не комплименты. Ответь на вопросы:
1. Сколько в гардеробе вещей той же категории (например «рубашки») и сколько из них похожи по назначению на эту покупку?
2. С какими конкретными вещами это сочетается — сколько новых сочетаний реально даст покупка?
3. Дублирует ли это что-то уже имеющееся?
4. Насколько вещь соответствует стилю пользователя?
5. Если вердикт скорее отрицательный — при каком условии (другая посадка, ткань, оттенок) решение могло бы измениться?

Верни JSON (без markdown):
{{"verdict":"скорее брать или скорее не брать, коротко","why":["2-3 конкретные причины на основе реального гардероба и цифр выше, на ты, без имени"],"have_category":"категория во мн.ч., напр. рубашек","have_count":число вещей этой категории в гардеробе или 0,"similar_count":число из них похожих по назначению или 0,"reconsider_if":"1 строка — условие, при котором вердикт мог бы стать положительным (посадка/ткань/оттенок), если вердикт скорее не брать, иначе пусто","alternative":"1 строка — что искать вместо этого, если вердикт скорее не брать, иначе пусто"}}

Если гардероб пустой — верни have_count 0 и в why честно скажи, что оценка приблизительная."""
    try:
        d = await ai.allm_json(prompt, 600, tier="smart", module="wardrobe")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    text_out, entities = _build_purchase_message({
        "item": text,
        "verdict": d.get("verdict", ""),
        "why": d.get("why") or [],
        "have_category": d.get("have_category", ""),
        "have_count": d.get("have_count"),
        "similar_count": d.get("similar_count"),
        "reconsider_if": d.get("reconsider_if", ""),
        "alternative": d.get("alternative", ""),
    })
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = text_out
    await bot.send_message(chat_id=cid, text=text_out, entities=entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe"), ("🏠 Меню", "m_menu")]]))


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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="w_del_g"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]])
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
