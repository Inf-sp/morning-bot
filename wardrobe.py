import asyncio
import logging
from datetime import datetime
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
from ui.constants import choose_label, delete_label, ui_label
from wardrobe_model import (
    ZONE_ORDER,
    flat_items as _flat_wardrobe_items,
    has_rain_outerwear as _has_rain_outerwear,
    normalize_parsed_item,
    public_zone_name,
    public_item_name,
    wardrobe_stats,
)
from wardrobe_outfit import (
    build_outfit_reasons,
    build_style_tip,
    choose_outfit_style,
    pick_best_outfit,
    save_outfit_feedback,
    validate_outfit_copy,
)
from wardrobe_copy import ai_reframe_look
from wardrobe_migration import migrate_item_attrs

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6
COPY_VALIDATOR_VERSION = 5

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("🆕 Добавить вещь", "w_add")],
        [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Меню", "m_menu")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_wardrobe"), ("#️⃣ Меню", "m_menu")]])

def _day_key():
    return datetime.now(config.TZ).date().isoformat()


def _weather_decision(weather_ctx):
    """Коротко называет только условия, которые меняют выбор одежды."""
    if not weather_ctx or weather_ctx.get("tmax") is None:
        return ""
    has_rain = weather_ctx.get("has_rain")
    strong_wind = weather_ctx.get("strong_wind")
    hot = weather_ctx.get("hot")
    warm = weather_ctx.get("warm")

    if has_rain and hot:
        return "Тепло, возможен дождь — нужен лёгкий защищённый слой."
    if has_rain and strong_wind:
        return "Прохладно, ветрено и возможен дождь."
    if has_rain:
        return "Возможен дождь — лучше выбрать закрытую обувь."
    if strong_wind and hot:
        return "Тепло, но ветрено — пригодится лёгкий слой."
    if strong_wind:
        return "Прохладно и ветрено — нужен дополнительный слой."
    if hot:
        return "Жарко и сухо — выбирай лёгкие ткани."
    if warm:
        return "Тепло и сухо — достаточно лёгких слоёв."
    return "Прохладно — нужен дополнительный слой."


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
    if cached.get("copy_validator_version") != COPY_VALIDATOR_VERSION:
        return None
    return cached


def get_cached_outfit_items(cid):
    """Названия вещей из актуального образа дня для других пользовательских карточек."""
    cached = _get_cached_look(cid)
    if not cached:
        return []
    return [
        _clean_text(_item_name(item))
        for item in (cached.get("look_data") or {}).get("items", [])
        if _clean_text(_item_name(item))
    ]

def _item_name(it):
    return it.get("name") if isinstance(it, dict) else it

def _save_cached_look(cid, item_ids, look_data):
    text, _ = _build_look_message(look_data)
    w = store.load_wardrobe(cid)
    store.set_wardrobe_daylook(cid, {
        "date": _day_key(),
        "version": w.get("_v", 0),
        "copy_validator_version": COPY_VALIDATOR_VERSION,
        "item_ids": list(item_ids or []),
        "look_data": look_data,
        "text": text,
    })
    try:
        import myday
        myday.reset_day_cache(cid)
    except Exception as e:
        _log.warning("wardrobe: myday cache reset failed: %s", e)


# ---------- главный экран раздела (панель состояния) ----------
def build_wardrobe_keyboard():
    return _kb([
        [("✨ Другой образ", "w_look")],
        [("🧐 Покупка", "w_check"), ("✂️ Мой шкаф", "w_closet")],
        [(choose_label("Выбрать стили"), "set_wardrobe_style")],
        [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
    ])


_wardrobe_home_kb = build_wardrobe_keyboard


def _cancel_wardrobe_input(cid):
    cid = str(cid)
    if str(store.pending_input.get(cid, "")).startswith("wardrobe_"):
        store.pending_input.pop(cid, None)
    store.wardrobe_add_queue.pop(cid, None)
    store.wardrobe_edit_item.pop(cid, None)


async def send_home(bot, cid, q=None):
    """Главный экран раздела «Гардероб» — сразу образ на сегодня."""
    _cancel_wardrobe_input(cid)
    await send_looks(bot, cid, kb=_wardrobe_home_kb(), q=q)


class _WarmCacheStatus:
    """Минимальный status для фоновой сборки без Telegram-сообщений."""
    async def replace(self, _text, **_kwargs):
        return True


async def warm_home_cache(cid):
    """Собирает образ дня в кэш без отправки пользователю."""
    if _get_cached_look(cid):
        return True
    await send_looks(
        None,
        cid,
        status=_WarmCacheStatus(),
        kb=_wardrobe_home_kb(),
    )
    # Для пустого шкафа постоянная карточка не нужна; сам прогрев всё равно успешен.
    return bool(_get_cached_look(cid) or not store.wardrobe_to_text(store.load_wardrobe(cid)).strip())


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


# ---------- генерация лука по погоде ----------
def _empty_wardrobe_screen():
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🆕 Добавить вещь", callback_data="w_add"),
    ], [
        InlineKeyboardButton("✂️ Мой шкаф", callback_data="w_closet"),
    ], [
        InlineKeyboardButton(choose_label("Выбрать стили"), callback_data="set_wardrobe_style"),
    ], [
        InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ]])
    text = (
        "<b>👕 Образ на сегодня</b>\n\n"
        "Чтобы собрать образ из твоих вещей, сначала добавь их в шкаф."
    )
    return text, kb


def _no_outfit_screen(result_kb, alternative=False):
    if alternative:
        return (
            "Другого полноценного комплекта для этих условий сейчас нет.",
            result_kb,
        )
    text = (
        f"<b>{ui_label('no_outfit', 'Не нашлось подходящего образа')}</b>\n\n"
        "В шкафу не хватает вещей на сегодняшнюю погоду. Добавь ещё немного одежды."
    )
    return text, result_kb


async def send_looks(bot, cid, status=None, kb=None, previous_item_ids=None, q=None):
    result_kb = kb or _wardrobe_home_kb()
    cached = None if previous_item_ids else _get_cached_look(cid)
    if cached:
        cached_names = [_item_name(it) for it in (cached.get("look_data") or {}).get("items", [])]
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached_names)[:120]
        text, entities = _build_look_message(cached.get("look_data", {}))
        if q is not None:
            try:
                await q.message.edit_text(text, entities=entities, reply_markup=result_kb)
            except Exception:
                await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=result_kb)
        elif status is not None:
            await status.replace(text, entities=entities, reply_markup=result_kb)
        else:
            await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=result_kb)
        return
    w = store.load_wardrobe(cid)
    if not store.wardrobe_to_text(w).strip():
        empty_text, empty_kb = _empty_wardrobe_screen()
        if q is not None:
            try:
                await q.message.edit_text(empty_text, parse_mode="HTML", reply_markup=empty_kb)
            except Exception:
                await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=empty_kb)
        elif status is not None:
            await status.replace(empty_text, parse_mode="HTML", reply_markup=empty_kb)
        else:
            await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=empty_kb)
        return
    s = store.get_settings(cid)
    status = status or await util.StatusManager.start(bot, cid, message=q.message if q else None, stages=util.StatusManager.TOPIC_STAGES["wardrobe"])
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
    _rules, gap_note = _build_weather_rules(cid, w, flags)

    w = await migrate_item_attrs(cid, w)
    style_block = _settings.wardrobe_prefs_context(cid)
    selected_styles = _settings.wardrobe_styles(cid)
    wardrobe_history = store.get_wardrobe_history(cid)
    best = pick_best_outfit(
        w, weather_ctx, wardrobe_history, style_block,
        previous_item_ids=previous_item_ids,
        selected_styles=selected_styles,
    )
    if not best:
        no_text, no_kb = _no_outfit_screen(result_kb, alternative=bool(previous_item_ids))
        if status is not None:
            await status.replace(no_text, parse_mode="HTML", reply_markup=no_kb)
        else:
            await bot.send_message(chat_id=cid, text=no_text, parse_mode="HTML", reply_markup=no_kb)
        return

    order = {"Верх": 0, "Низ": 1, "Обувь": 2, "Верхняя одежда": 3, "Аксессуары": 4}
    best_sorted = sorted(best, key=lambda it: order.get(it.get("zone"), 9))
    reasons = build_outfit_reasons(best_sorted, weather_ctx)
    tip = build_style_tip(best_sorted, weather_ctx)
    reasons, tip = await ai_reframe_look(best_sorted, reasons, tip)

    item_ids = [it.get("id") for it in best_sorted]
    final_heading = "На случай дождя" if gap_note else "Образ готов"
    validated_copy = validate_outfit_copy(
        best_sorted,
        w,
        weather_ctx,
        reasons,
        tip,
        final_heading,
        gap_note or "ничего добавлять не нужно",
    )
    look_data = {
        "primary_style": choose_outfit_style(best_sorted, selected_styles),
        "weather_intro": _weather_decision(weather_ctx),
        "items": [{"name": public_item_name(it)} for it in validated_copy["items"]],
        "style_tip": (
            "Возьми зонт или лёгкий дождевик, чтобы выбранные вещи не промокли."
            if gap_note else validated_copy["style_tip"]
        ),
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


async def _parse_items(text):
    parsed = await ai.allm_json(
        f"Разбери вещи по атрибутам. Зоны и подкатегории (используй ТОЛЬКО эти значения, "
        f"если не подходит ни одна — subcategory=\"Другое\"): {_ZONES_DESC}\n"
        f"Вещи:\n{secure.wrap_untrusted(text, 'список вещей')}\n"
        "Для каждой вещи верни: zone (одна из зон выше, если не ясно — \"Другое\"), "
        "subcategory (строго из списка для этой зоны), name (естественное русское название: цвет перед "
        "типом вещи, затем детали; пример: «Тёмно-оливковые брюки с карманами», но БЕЗ слов "
        "лёгкая/тонкая/тёплая/толстая/плотная/летняя/зимняя — это отдельные поля), "
        "color (основной цвет), color_secondary (доп. цвет или пусто), material (материал или пусто), "
        "length (длина или пусто), warmth (СТРОГО лёгкие/обычные/тёплые; толстая/плотная/утеплённая = тёплые), "
        "fit (свободная/прямая/приталенная или пусто), season (массив сезонов), rain_ok, wind_ok, "
        "occasions (массив подходящих случаев), style (Casual/Formal/Sport/Streetwear и т.п. или пусто). "
        "Сохраняй бренд, если он указан.\n"
        'JSON: {"items": [{"zone":"","subcategory":"","name":"","color":"","color_secondary":"",'
        '"material":"","length":"","warmth":"обычные","fit":"","season":[],"rain_ok":false,"wind_ok":false,'
        '"occasions":[],"style":""}]}',
        1100, tier="cheap", module="wardrobe")
    raw_items = parsed.get("items") or []
    source_text = text if len(raw_items) == 1 else ""
    norm = [normalize_parsed_item({**item, "_source_text": source_text}) for item in raw_items]
    return [it for it in norm if it]


async def _show_added_items(bot, cid, items):
    if not items:
        await bot.send_message(chat_id=cid, text="Такая вещь уже есть в шкафу.", reply_markup=closet_kb())
        return
    msg = wardrobe_ui.add_preview(items[0]) if len(items) == 1 else wardrobe_ui.add_batch_preview(items)
    if len(items) == 1:
        rows = [[(delete_label("Удалить"), f"w_delete_{items[0]['id']}")]]
    else:
        rows = [[(delete_label(f"Удалить: {public_item_name(item)[:28]}"), f"w_delete_{item['id']}")]
                for item in items]
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Меню", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_kb(rows))

async def add_item(bot, cid, text):
    try:
        items = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось распознать вещь. Опиши её одним сообщением.", reply_markup=_back_kb())
        return
    saved = store.add_wardrobe_items(cid, items)
    await _show_added_items(bot, cid, saved)

async def add_item_settings(bot, cid, text):
    await add_item(bot, cid, text)


async def add_item_photo(bot, cid, image_bytes, mime_type="image/jpeg", caption=""):
    try:
        parsed = await ai.allm_image_json(
            image_bytes,
            mime_type,
            f"""Распознай только предметы одежды и аксессуары на фото. Подпись пользователя: {secure.wrap_untrusted(caption, 'подпись')}
Зоны и подкатегории: {_ZONES_DESC}
Для каждого отчётливо видимого предмета верни zone, subcategory, name, color, color_secondary,
material, length, warmth (строго лёгкие/обычные/тёплые), fit, season, rain_ok, wind_ok,
occasions и style. Физические свойства храни полями, не добавляй их в name.
Не выдумывай бренд и невидимые физические свойства.
JSON: {{"items":[{{"zone":"","subcategory":"","name":"","color":"","color_secondary":"","material":"","length":"","warmth":"обычные","fit":"","season":[],"rain_ok":false,"wind_ok":false,"occasions":[],"style":""}}]}}""",
            max_tokens=1100,
        )
        raw_items = parsed.get("items") or []
        source_text = caption if len(raw_items) == 1 else ""
        items = [normalize_parsed_item({**item, "_source_text": source_text}) for item in raw_items]
        items = [item for item in items if item]
    except Exception as e:
        store.pending_input[str(cid)] = "wardrobe_add"
        await verify.safe_error(bot, cid, e, back="m_wardrobe")
        return
    if not items:
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="Не удалось уверенно распознать вещь. Опиши её одним сообщением.", reply_markup=_back_kb())
        return
    saved = store.add_wardrobe_items(cid, items)
    await _show_added_items(bot, cid, saved)


def _find_item(cid, item_id):
    for zone, subcat, item in _flat_wardrobe_items(store.load_wardrobe(cid)):
        if item.get("id") == item_id:
            return zone, subcat, item
    return None, None, None


def _replace_item(cid, item_id, replacement):
    changed = {"ok": False}

    def _mut(w):
        for zone, subcats in w.get("zones", {}).items():
            for subcat, items in subcats.items():
                for index, item in enumerate(list(items)):
                    if item.get("id") != item_id:
                        continue
                    items.pop(index)
                    new_item = dict(replacement)
                    new_item["id"] = item_id
                    target = w.setdefault("zones", {}).setdefault(new_item["zone"], {}).setdefault(new_item["subcategory"], [])
                    target.append(new_item)
                    changed["ok"] = True
                    return

    store.mutate_wardrobe(cid, _mut)
    return changed["ok"]


async def edit_item_text(bot, cid, text):
    item_id = store.wardrobe_edit_item.pop(str(cid), None)
    if not item_id:
        await send_wardrobe_zones(bot, cid)
        return
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed or not _replace_item(cid, item_id, parsed[0]):
        await bot.send_message(
            chat_id=cid,
            text="Не удалось изменить вещь. Открой карточку и попробуй ещё раз.",
            reply_markup=_back_kb(),
        )
        return
    await send_item_card(bot, cid, item_id)


async def edit_add_preview(bot, cid, text):
    store.wardrobe_add_queue.pop(str(cid), None)
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed:
        await bot.send_message(
            chat_id=cid, text="Не удалось распознать исправление.",
            reply_markup=_back_kb())
        return
    saved = store.add_wardrobe_items(cid, parsed)
    await _show_added_items(bot, cid, saved)


async def handle_wardrobe_search(bot, cid, query):
    """Ищет обычным текстом по названию, бренду, цвету, категории и сезону."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(chat_id=cid, text="Пришли название вещи или часть названия.")
        return
    w = store.load_wardrobe(cid)
    aliases = {"летняя": "лето", "летний": "лето", "зимняя": "зима", "зимний": "зима"}
    terms = [aliases.get(term, term) for term in query_norm.split()]
    matches = []
    for zone, subcat, item in _flat_wardrobe_items(w):
        values = [item.get("name"), zone, subcat, item.get("color"), item.get("material"), item.get("style")]
        values.extend(item.get("season") or [])
        haystack = " ".join(str(value or "") for value in values).casefold()
        if all(term in haystack for term in terms):
            matches.append(item)
    if not matches:
        await bot.send_message(
            chat_id=cid, text="Ничего не нашлось. Попробуй цвет, бренд или категорию.",
            reply_markup=_kb([[("⬅️ Назад", "w_closet"), ("#️⃣ Меню", "m_menu")]]),
        )
        return
    msg = wardrobe_ui.search_results(query, matches)
    rows = [[(str(item.get("name") or "Вещь")[:48], f"w_item_{item.get('id')}")] for item in matches[:10]]
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Меню", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_kb(rows))

# ---------- шкаф, категории и карточки вещей ----------
ZONE_SLUG = {"Верх": "top", "Низ": "bot", "Верхняя одежда": "out",
             "Обувь": "shoe", "Аксессуары": "acc", "Другое": "oth"}
ZONE_BY_SLUG = {slug: zone for zone, slug in ZONE_SLUG.items()}


async def send_wardrobe_zones(bot, cid, q=None):
    """«Мой шкаф»: действия и непустые категории на одном экране."""
    _cancel_wardrobe_input(cid)
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    rows = [
        [InlineKeyboardButton("🆕 Добавить вещь", callback_data="w_add")],
    ]
    for zone in (z for z in ZONE_ORDER if counts.get(z, 0) > 0):
        rows.append([InlineKeyboardButton(
            f"{public_zone_name(zone)} · {counts[zone]}",
            callback_data=f"w_cat_{ZONE_SLUG[zone]}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = wardrobe_ui.wardrobe_home_screen(total)
    kb = InlineKeyboardMarkup(rows)
    # Экран шкафа служебный. Отправляем его отдельно, чтобы карточка образа,
    # из которой пользователь пришёл, осталась в истории как полезный результат.
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


async def send_category(bot, cid, zone_slug, q=None):
    zone = ZONE_BY_SLUG.get(zone_slug)
    if not zone:
        await send_wardrobe_zones(bot, cid, q=q)
        return
    items = [item for item_zone, _subcat, item in _flat_wardrobe_items(store.load_wardrobe(cid))
             if item_zone == zone]
    msg = wardrobe_ui.category_screen(public_zone_name(zone), items)
    rows = [[InlineKeyboardButton(str(item.get("name") or "Вещь")[:48], callback_data=f"w_item_{item.get('id')}")]
            for item in items]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="w_closet"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_item_card(bot, cid, item_id, q=None):
    _cancel_wardrobe_input(cid)
    zone, _subcat, item = _find_item(cid, item_id)
    if not item:
        await bot.send_message(chat_id=cid, text="Этой вещи уже нет в шкафу.", reply_markup=closet_kb())
        return
    msg = wardrobe_ui.item_card(item)
    zone_slug = ZONE_SLUG.get(zone, "oth")
    kb = _kb([
        [(delete_label("Удалить"), f"w_delete_{item_id}")],
        [("⬅️ Назад", f"w_cat_{zone_slug}"), ("#️⃣ Меню", "m_menu")],
    ])
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_delete_confirmation(bot, cid, item_id, q=None):
    zone, _subcat, item = _find_item(cid, item_id)
    if not item:
        await send_wardrobe_zones(bot, cid, q=q)
        return
    msg = wardrobe_ui.delete_confirmation(item)
    kb = _kb([
        [(delete_label("Удалить"), f"w_deleteok_{item_id}"), ("Отмена", f"w_item_{item_id}")],
        [("⬅️ Назад", f"w_item_{item_id}"), ("#️⃣ Меню", "m_menu")],
    ])
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


_PURCHASE_VERDICTS = {
    "брать": "брать",
    "брать только со скидкой": "брать только со скидкой",
    "только со скидкой": "брать только со скидкой",
    "не брать": "не брать",
    "недостаточно данных": "недостаточно данных",
}
_PURCHASE_FLAGS = {"да", "нет", "недостаточно данных"}
_PURCHASE_REJECT_REASONS = {
    "duplicate", "fit", "forbidden_color", "low_compatibility",
    "material_or_season", "price_vs_utility", "poor_condition",
}


def _normalize_purchase_check(data, wardrobe=None):
    """Не пропускает неподдерживаемый вердикт и беспричинное «не брать»."""
    data = data if isinstance(data, dict) else {}
    verdict_key = _clean_text(data.get("verdict")).casefold().rstrip(".!?")
    verdict = _PURCHASE_VERDICTS.get(verdict_key, "недостаточно данных")

    flag_values = {}
    for key in ("duplicates", "closes_gap"):
        value = _clean_text(data.get(key)).casefold().rstrip(".!?")
        flag_values[key] = value if value in _PURCHASE_FLAGS else "недостаточно данных"

    try:
        if isinstance(data.get("fits_count"), bool):
            raise ValueError
        fits_count = int(data.get("fits_count"))
        if fits_count < 0:
            raise ValueError
    except (TypeError, ValueError):
        fits_count = "недостаточно данных"
    if wardrobe is not None and isinstance(fits_count, int):
        total, _counts = wardrobe_stats(wardrobe)
        fits_count = min(fits_count, total)

    why = data.get("why")
    if isinstance(why, list):
        why = why[0] if why else ""
    why = _clean_text(why)
    reject_reason = _clean_text(data.get("not_buy_reason")).casefold()
    if verdict == "не брать" and (reject_reason not in _PURCHASE_REJECT_REASONS or not why):
        verdict = "недостаточно данных"
        why = "Нет подтверждённой конкретной причины отказываться от покупки. Нужны дополнительные данные о вещи."
    elif verdict == "недостаточно данных" and not why:
        why = "Не хватает подтверждённых свойств вещи, которые влияют на решение."

    wear_with = data.get("wear_with")
    if not isinstance(wear_with, list):
        wear_with = []
    wear_with = [_clean_text(value) for value in wear_with if _clean_text(value)][:2]

    return {
        "verdict": verdict,
        "fits_count": fits_count,
        "duplicates": flag_values["duplicates"],
        "closes_gap": flag_values["closes_gap"],
        "why": why,
        "wear_with": wear_with,
    }


async def check_purchase(bot, cid, text):
    w = store.load_wardrobe(cid)
    web_block = ""
    web_data = await asyncio.to_thread(
        research.web_snippet,
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
Ответь на один вопрос: есть ли смысл добавлять эту вещь в гардероб пользователя?

Правила:
1. Вердикт — строго один из четырёх: «брать», «брать только со скидкой», «не брать», «недостаточно данных».
2. Если из описания нельзя подтвердить важные для решения свойства (например длину, крой, материал, сезонность, состояние или цену), выбери «недостаточно данных». Не додумывай их.
3. «Не брать» разрешено только при одной конкретной подтверждённой причине: почти полный дубль; неподходящая посадка; цвет прямо указан в запретах пользователя; сочетаемость лишь с одной-двумя позициями; неподходящие материал или сезонность; завышенная цена относительно пользы; плохое состояние.
4. Нельзя писать «не соответствует стилю» без конкретного объяснения из фактов выше. Общего несовпадения со стилем недостаточно для вердикта «не брать».
5. Посчитай, со сколькими конкретными вещами из шкафа покупка сочетается. Не считай саму покупку и не выдумывай отсутствующие вещи.
6. Дублирование и закрытие пробела обозначь только как «да», «нет» или «недостаточно данных».
7. В why дай одно конкретное компактное объяснение, максимум два предложения. Для «недостаточно данных» назови недостающие свойства. Для «не брать» объясни подтверждённую причину.
8. В wear_with дай максимум два готовых сочетания только с реальными вещами из шкафа. При нехватке данных можно дать условное сочетание, но явно назвать условие. Если честного сочетания нет — верни пустой список.

Верни JSON (без markdown):
{{"verdict":"брать / брать только со скидкой / не брать / недостаточно данных","fits_count":0,"duplicates":"да / нет / недостаточно данных","closes_gap":"да / нет / недостаточно данных","not_buy_reason":"duplicate / fit / forbidden_color / low_compatibility / material_or_season / price_vs_utility / poor_condition / пустая строка","why":"одно конкретное объяснение","wear_with":["до двух готовых сочетаний"]}}

Если гардероб пустой, fits_count должен быть 0, а вывод не должен притворяться точным."""
    try:
        d = await ai.allm_json(prompt, 600, tier="smart", module="wardrobe")
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    text_out, entities = _build_purchase_message(_normalize_purchase_check(d, wardrobe=w))
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = text_out
    await bot.send_message(chat_id=cid, text=text_out, entities=entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe"), ("#️⃣ Меню", "m_menu")]]))


# ---------- добавление файлом (старый режим, оставлен) ----------
async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


# ---------- роутер кнопок ----------
async def handle_callback(bot, cid, q, data):
    if data == "w_look":
        previous = _get_cached_look(cid) or {}
        store.clear_wardrobe_daylook(cid)
        status = await util.StatusManager.start(
            bot, cid=cid, message=q.message, stages=util.StatusManager.TOPIC_STAGES["wardrobe"])
        try:
            await send_looks(
                bot, cid, status=status, kb=_wardrobe_home_kb(),
                previous_item_ids=previous.get("item_ids") or [],
            )
        except Exception as e:
            await verify.safe_error(bot, cid, e, back="m_wardrobe")
        finally:
            await status.stop(delete=False)
        return
    if data in ("w_closet", "w_del_g"):
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="Опиши её одним сообщением или отправь вещи списком через запятую.\n\n"
                               "Пример: Голубая свободная рубашка Uniqlo.",
                               reply_markup=_back_kb()); return
    if data == "w_add_ok":
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_add_all":
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_add_edit":
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_search":
        # Совместимость со старыми сообщениями: поиск убран из актуального шкафа.
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data.startswith("w_searchdel_"):
        item_id = data[len("w_searchdel_"):]
        await send_delete_confirmation(bot, cid, item_id, q=q); return
    if data.startswith("w_cat_"):
        await send_category(bot, cid, data[len("w_cat_"):], q=q); return
    if data.startswith("w_item_"):
        await send_item_card(bot, cid, data[len("w_item_"):], q=q); return
    if data.startswith("w_edit_"):
        item_id = data[len("w_edit_"):]
        await send_item_card(bot, cid, item_id, q=q); return
    if data.startswith("w_deleteok_"):
        item_id = data[len("w_deleteok_"):]
        store.remove_wardrobe_items(cid, [item_id])
        await send_wardrobe_zones(bot, cid, q=q); return
    if data.startswith("w_delete_"):
        await send_delete_confirmation(bot, cid, data[len("w_delete_"):], q=q); return
    if data == "w_del" or data.startswith(("w_del_", "w_delz_", "w_delsc_")):
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_improve":
        await send_home(bot, cid, q=q); return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="Опиши покупку: тип вещи, цвет, длину, крой, материал, состояние и цену — всё, что известно.",
                               reply_markup=_back_kb()); return
