import asyncio
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import weather
import util
import settings as _settings
import wardrobe_rules
import wardrobe_stylist
import wardrobe_weather
from ui import wardrobe as wardrobe_ui
from ui.constants import delete_label, ui_label
from wardrobe_model import (
    CLOTHING_ZONES,
    ZONE_SUBCATS,
    flat_items as _flat_wardrobe_items,
    has_rain_outerwear as _has_rain_outerwear,
    public_zone_name,
    public_item_name,
    wardrobe_stats,
)
from wardrobe_outfit import (
    build_how_to_wear,
    build_style_tip,
    choose_outfit_style,
    fresh_outfits,
    outfit_display_order,
    save_outfit_feedback,
    top_outfits,
)
from wardrobe_migration import migrate_item_attrs

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6
COPY_VALIDATOR_VERSION = 14  # 14 — логика комплекта (outfit_conflicts): сегодняшний образ пересобирается
WARDROBE_CATEGORY_PAGE_SIZE = 8
CLOSET_ZONE_ORDER = ("Верх", "Кофты", "Низ", "Верхняя одежда", "Обувь")
def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("✅ Добавить вещь", "w_add")],
        [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")]])

def _day_key():
    return datetime.now(config.TZ).date().isoformat()


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


_NO_WEATHER = {"tmin": None, "tmax": None, "has_rain": False, "wind_ms": None,
               "strong_wind": False, "sunny": False, "hot": False, "warm": False, "tags": []}


def _wear_weather(settings_data):
    """(weather_ctx, flags) на часы, когда образ носят; без погоды — (_NO_WEATHER, None)."""
    try:
        wdata = weather.fetch_weather(settings_data["lat"], settings_data["lon"], 2)
        wd = wdata["daily"]
        day_str = (wd.get("time") or [None])[0] or _day_key()
        daytime_min, daytime_max = weather._daytime_temperature_range(
            wdata, day_str, wd["temperature_2m_min"][0], wd["temperature_2m_max"][0],
        )
        tmax, tmin = round(daytime_max), round(daytime_min)
        wind_ms = round(wd["windspeed_10m_max"][0])
        rain_prob_day = wd["precipitation_probability_max"][0] or 0
        rain_mm_day = (wd.get("precipitation_sum") or [None])[0]
        weathercode = (wd.get("weathercode") or [None])[0]
        flags = weather.daytime_outfit_weather(
            wdata, day_str, tmax, wind_ms, rain_prob_day, rain_mm_day, weathercode)
        weather_ctx = build_weather_context(wdata, day_str, tmax, tmin, wind_ms, rain_prob_day, rain_mm_day, weathercode)
        # Точная погода на часы, когда образ будут носить: от сейчас до 22:00.
        window = wardrobe_weather.wear_window(
            wdata, datetime.now(config.TZ).replace(tzinfo=None), sunny=flags["sunny"])
        if window:
            weather_ctx = {**weather_ctx, **window}
        return weather_ctx, flags
    except Exception as error:
        _log.warning("wardrobe: weather unavailable, look built without weather: %r", error)
        return dict(_NO_WEATHER), None


def weather_signature(weather_ctx, flags):
    """Под какую погоду собран образ: дождь, сильный ветер, холод, лето; None — погоды не было."""
    if flags is None:
        return None
    low = wardrobe_rules.feels_low(weather_ctx)
    return {
        "rain": bool(weather_ctx.get("has_rain")),
        "wind": bool(weather_ctx.get("strong_wind")),
        "cold": low is not None and low < wardrobe_rules.SWEATER_FEELS_MAX,
        "summer": wardrobe_rules.summer_weather(weather_ctx),
    }


_ADJECTIVE_ENDINGS = ("ый", "ий", "ой")


def _closing_line(items, primary_style, style_tip):
    """Финальная фраза карточки: по кругу сочетание цветов, силуэт и стиль.

    Тип выбирается по дню и составу образа, поэтому «Новый образ» и новый день дают другую
    фразу; тип без данных (все вещи одного цвета, нет стиля) пропускается.
    """
    import hashlib
    colors = []
    for item in items:
        color = str((item.get("colors") or [""])[0] or "").strip().casefold()
        if color.endswith(_ADJECTIVE_ENDINGS) and color not in colors and item.get("zone") != "Обувь":
            colors.append(color)
    seed = int(hashlib.md5("|".join(str(it.get("id") or "") for it in items).encode()).hexdigest(), 16)
    seed += datetime.now(config.TZ).date().toordinal()
    options = [
        wardrobe_ui.color_pair_line(colors[0], colors[1], seed) if len(colors) >= 2 else "",
        str(style_tip or "").strip(),
        wardrobe_ui.style_about(primary_style),
    ]
    options = [option for option in options if option]
    return options[seed % len(options)] if options else ""


def wardrobe_counts(w) -> dict:
    """«Твой шкаф · 37 вещей · 528 образов» — для нижней строки карточки образа."""
    from wardrobe_outfit import count_outfits
    return {"items": wardrobe_stats(w)[0], "outfits": count_outfits(w)}


def _build_look_message(look_data):
    msg = wardrobe_ui.render_wardrobe_message(look_data)
    return msg.text, msg.entities


def _cached_outfit_items(w, look_data):
    names = {
        _clean_text(item.get("name") if isinstance(item, dict) else item).casefold()
        for item in (look_data.get("items") or [])
    }
    return [
        item for _zone, _subcategory, item in _flat_wardrobe_items(w)
        if _clean_text(public_item_name(item)).casefold() in names
        or _clean_text(item.get("name")).casefold() in names
    ]


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _get_cached_look(cid):
    cached = store.get_valid_wardrobe_daylook(cid)   # ссылочная целостность (version+id)
    if not cached or cached.get("date") != _day_key():   # день — бизнес-правило «раз в день»
        return None
    if cached.get("copy_validator_version") != COPY_VALIDATOR_VERSION:
        return None
    look_data = cached.get("look_data") or {}
    return cached


def _with_trip_flag(cid, look_data):
    """В отпуске заголовок образа — «🇿🇦 Надень сегодня в отпуске»: флаг страны поездки; дома — без него."""
    import util
    import vacation
    flag = (util.flag_from_cc(vacation.trip_cc(cid)) or "✈️") if vacation.active(cid) else ""
    look_data = {key: value for key, value in look_data.items() if key != "trip_flag"}  # вернулся домой — без флага
    return {**look_data, "trip_flag": flag} if flag else look_data


def get_cached_outfit_summary(cid):
    """Эмодзи стиля и точный видимый список текущего образа для «Моего дня»; в отпуске с чемоданом — образ на
    сегодня из плана поездки (тот же, что в Гардеробе)."""
    import wardrobe_suitcase
    planned = wardrobe_suitcase.today_look(cid)
    if planned:
        return {"emoji": "", "items": [wardrobe_ui._lower_first(_clean_text(name)) for name in planned]}
    cached = _get_cached_look(cid)
    if not cached:
        return {"emoji": "", "items": []}
    look_data = cached.get("look_data") or {}
    return {
        "emoji": wardrobe_ui.outfit_emoji(wardrobe_ui.outfit_group(look_data)),
        "items": wardrobe_ui.outfit_item_names(look_data),
    }

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
def build_wardrobe_keyboard(cid=None):
    import vacation
    if cid is not None and vacation.active(cid):
        # В отпуске: «Ещё вариант» — новый образ дня из вещей чемодана по погоде поездки; без стилей и покупок.
        # «Мой чемодан» — синяя (telegram_runtime._BLUE_RE): чемодан поездки уже есть или соберётся под неё.
        return _kb([
            [("Ещё вариант", "w_look")],
            [("🧳 Мой чемодан", "w_pack")],
            [("🎚️ Настроить шкаф", "w_closet"), ("#️⃣ Главная", "m_menu")],
        ])
    import wardrobe_suitcase
    # Чемодан уже собран — «Чемодан в отпуск» (откроет его); нет — «Собрать чемодан» (спросит, куда и когда едешь).
    packed = cid is not None and bool(wardrobe_suitcase.saved_suitcase(cid))
    rows = [
        # Новый образ сразу в выбранной группе стиля; предпочтения не меняются. Стандартного цвета.
        [("Повседневный", "w_lookst_casual"), ("Классический", "w_lookst_classic")],
        [("🧳 Чемодан в отпуск" if packed else "🧳 Собрать чемодан", "w_pack")],
        [("🎚️ Настроить шкаф", "w_closet"), ("#️⃣ Главная", "m_menu")],
    ]
    return _kb(rows)


_wardrobe_home_kb = build_wardrobe_keyboard


def style_picker_kb():
    """Старая кнопка «Новый образ» из истории чата: те же две группы стиля."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Повседневный", callback_data="w_lookst_casual"),
         InlineKeyboardButton("Классический", callback_data="w_lookst_classic")],
        nav_row("w_card"),
    ])


def _cancel_wardrobe_input(cid):
    cid = str(cid)
    if str(store.pending_input.get(cid, "")).startswith("wardrobe_"):
        store.pending_input.pop(cid, None)
    store.wardrobe_add_queue.pop(cid, None)
    store.wardrobe_edit_item.pop(cid, None)


async def send_home(bot, cid, q=None, status=None):
    """Главный экран раздела «Гардероб» — сразу образ на сегодня. В отпуске с сохранённым чемоданом этой страны —
    образ на сегодня из плана поездки с листанием дней (wardrobe_suitcase.show_day)."""
    _cancel_wardrobe_input(cid)
    import wardrobe_suitcase
    if wardrobe_suitcase.trip_suitcase(cid):
        await wardrobe_suitcase.show_day(bot, cid, q=q, trip=True, status=status)
        return
    await send_looks(bot, cid, status=status, q=q)


class _WarmCacheStatus:
    """Минимальный status для фоновой сборки без Telegram-сообщений."""
    async def replace(self, _text, **_kwargs):
        return True


async def warm_home_cache(cid):
    """Собирает образ дня в кэш без отправки пользователю; в отпуске с чемоданом — план образов поездки."""
    import wardrobe_suitcase
    if wardrobe_suitcase.trip_suitcase(cid):
        return bool(await asyncio.to_thread(wardrobe_suitcase.plan, cid))
    if not _get_cached_look(cid):
        await send_looks(None, cid, status=_WarmCacheStatus())
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
    kb = _kb([
        [("✅ Добавить вещи", "w_fill")],
        [("#️⃣ Главная", "m_menu")],
    ])
    return wardrobe_ui.empty_wardrobe().text, kb


def has_wardrobe_items(cid) -> bool:
    return bool(store.wardrobe_to_text(store.load_wardrobe(cid)).strip())


def _no_outfit_screen(result_kb, alternative=False, anchored=False):
    if anchored:
        return (
            "С этой вещью полный образ пока не собрать: в шкафу не хватает подходящих к ней верха, низа или обуви.",
            result_kb,
        )
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


async def send_looks(bot, cid, status=None, kb=None, previous_item_ids=None,
                     previous_style_tip=None, previous_weather_intro=None,
                     previous_style=None, q=None, silent=False,
                     style=None, anchor_id=None):
    result_kb = kb or build_wardrobe_keyboard(cid)
    cached = None if previous_item_ids or anchor_id else _get_cached_look(cid)
    weather_now = (None, None)
    if cached:
        # Погода поменялась (дождь, ветер, холод) или образ собран без неё — пересобираем.
        weather_now = await asyncio.to_thread(_wear_weather, store.get_settings(cid))
        current = weather_signature(*weather_now)
        saved = (cached.get("look_data") or {}).get("weather")
        if current is not None and current != saved:
            cached = None
    if cached:
        if silent:
            return
        cached_names = [_item_name(it) for it in (cached.get("look_data") or {}).get("items", [])]
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached_names)[:120]
        original_look_data = cached.get("look_data", {})
        look_data = dict(original_look_data)
        if not look_data.get("stats"):  # образ, сохранённый до строки статистики
            look_data = {**look_data, "stats": wardrobe_counts(store.load_wardrobe(cid))}
        if not look_data.get("weather_reason") and weather_now[1] is not None:
            # Образ собран раньше, чем строка о погоде стала обязательной: дописываем её сверху.
            reason = wardrobe_rules.weather_reason(weather_now[0], look_data.get("items") or [])
            look_data = {**look_data, "weather_reason": reason} if reason else look_data
        look_data = _with_trip_flag(cid, look_data)
        text, entities = _build_look_message(look_data)
        store.last_answer[str(cid)] = text
        if look_data != original_look_data:
            cached["look_data"] = look_data
            cached["text"] = text
            store.set_wardrobe_daylook(cid, cached)
        if kb is None:
            result_kb = build_wardrobe_keyboard(cid)
        if status is not None:
            await status.replace(
                text, entities=entities, reply_markup=result_kb,
                disable_web_page_preview=True,
            )
        elif q is not None:
            try:
                await q.message.edit_text(
                    text, entities=entities, reply_markup=result_kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                await bot.send_message(
                    chat_id=cid, text=text, entities=entities, reply_markup=result_kb,
                    disable_web_page_preview=True,
                )
        else:
            await bot.send_message(
                chat_id=cid, text=text, entities=entities, reply_markup=result_kb,
                disable_web_page_preview=True,
            )
        return
    w = store.load_wardrobe(cid)
    if not store.wardrobe_to_text(w).strip():
        if silent:
            return
        empty_text, empty_kb = _empty_wardrobe_screen()
        if status is not None:
            await status.replace(empty_text, parse_mode="HTML", reply_markup=empty_kb)
        elif q is not None:
            try:
                await q.message.edit_text(empty_text, parse_mode="HTML", reply_markup=empty_kb)
            except Exception:
                await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=empty_kb)
        else:
            await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=empty_kb)
        return
    s = store.get_settings(cid)
    if status is None and not silent:
        if q is not None:
            status = await util.StatusManager.start_inline(
                q,
                bot=bot,
                cid=cid,
                stages=util.StatusManager.TOPIC_STAGES["wardrobe"],
                preserve_message=True,
            )
        else:
            status = await util.StatusManager.start(
                bot, cid, message=None, stages=util.StatusManager.TOPIC_STAGES["wardrobe"])
    weather_ctx, flags = await asyncio.to_thread(_wear_weather, s)
    _rules, gap_note = _build_weather_rules(cid, w, flags)

    w = await migrate_item_attrs(cid, w)
    import wardrobe_suitcase
    w = wardrobe_suitcase.packed_wardrobe(cid, w)  # в отпуске — только вещи из чемодана, если он собран
    style_block = _settings.wardrobe_prefs_context(cid)
    # style — разовый выбор группы («Повседневный / Классический») или старого стиля; предпочтения не меняются.
    selected_styles = (list(style) if isinstance(style, (list, tuple)) else [style]) if style \
        else _settings.wardrobe_styles(cid)
    if not style and previous_item_ids and len(selected_styles) > 1 and previous_style in selected_styles:
        next_index = (selected_styles.index(previous_style) + 1) % len(selected_styles)
        selected_styles = [selected_styles[next_index]]
    wardrobe_history = store.get_wardrobe_history(cid)
    outfits = top_outfits(
        w, weather_ctx, wardrobe_history, style_block,
        previous_item_ids=previous_item_ids,
        selected_styles=selected_styles,
        anchor_id=anchor_id,
    )
    if not outfits:
        if silent:
            return
        no_text, no_kb = _no_outfit_screen(
            result_kb, alternative=bool(previous_item_ids), anchored=bool(anchor_id))
        if status is not None:
            await status.replace(no_text, parse_mode="HTML", reply_markup=no_kb)
        else:
            await bot.send_message(chat_id=cid, text=no_text, parse_mode="HTML", reply_markup=no_kb)
        return

    # ИИ-стилист выбирает из уже проверенных кодом комплектов; без ИИ — лучший по коду.
    if not anchor_id:
        outfits = fresh_outfits(outfits, wardrobe_history)
    trends = await asyncio.to_thread(wardrobe_stylist.weekly_trends, tuple(selected_styles))
    pick = await asyncio.to_thread(
        wardrobe_stylist.choose, outfits, weather_ctx, selected_styles, style_block, trends)
    best = outfits[pick["index"]] if pick else outfits[0]
    best_sorted = sorted(best, key=outfit_display_order)
    item_ids = [it.get("id") for it in best_sorted]
    fallback_tip = build_style_tip(
        best_sorted,
        weather_ctx,
        avoid_tips={previous_style_tip} if previous_style_tip else None,
    )
    look_data = {
        "primary_style": choose_outfit_style(best_sorted, selected_styles),
        "items": [
            {"name": public_item_name(it), "zone": it.get("zone")}
            for it in best_sorted
        ],
        "style_tip": fallback_tip,
        "how_to_wear": build_how_to_wear(best_sorted, fallback_tip),
        "weather_reason": wardrobe_rules.weather_reason(weather_ctx, best_sorted),
        "weather": weather_signature(weather_ctx, flags),
    }
    # Заголовок — группа стиля: нажатая кнопка, иначе стиль самого образа.
    groups = _settings.style_groups_of(selected_styles)
    look_data["style_group"] = (_settings.style_group_labels(groups)[0] if len(groups) == 1
                                else wardrobe_ui.outfit_group(look_data))
    look_data["closing"] = _closing_line(best_sorted, look_data["primary_style"], fallback_tip)
    look_data["stats"] = wardrobe_counts(w)  # нижняя строка: шкаф помнит, сколько в нём вещей и образов
    look_data = _with_trip_flag(cid, look_data)
    if kb is None:
        result_kb = build_wardrobe_keyboard(cid)
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
    if silent:
        return
    if status:
        await status.replace(
            text, entities=entities, reply_markup=result_kb,
            disable_web_page_preview=True,
        )
    else:
        await bot.send_message(
            chat_id=cid, text=text, entities=entities, reply_markup=result_kb,
            disable_web_page_preview=True,
        )
# Extracted to wardrobe_management.py: def get_wardrobe_gaps.

_ZONES_DESC = "; ".join(f"{z}: {', '.join(subs)}" for z, subs in ZONE_SUBCATS.items()
                        if z in CLOTHING_ZONES)


# Extracted to wardrobe_management.py: def _local_text_item.

ZONE_SLUG = {"Верх": "top", "Кофты": "sw", "Низ": "bot", "Верхняя одежда": "out",
             "Обувь": "shoe", "Аксессуары": "acc", "Другое": "oth"}
ZONE_BY_SLUG = {slug: zone for zone, slug in ZONE_SLUG.items()}


async def send_wardrobe_zones(bot, cid, q=None):
    """«Мой шкаф»: заголовок с числом вещей и кнопки категорий."""
    _cancel_wardrobe_input(cid)
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    rows = [[InlineKeyboardButton("✅ Добавить вещь", callback_data="w_add")]]
    for zone in CLOSET_ZONE_ORDER:
        count = counts.get(zone, 0)
        rows.append([InlineKeyboardButton(
            f"{public_zone_name(zone)} · {count}",
            callback_data=f"w_cat_{ZONE_SLUG[zone]}",
        )])
    rows.append(nav_row("m_wardrobe"))
    msg = wardrobe_ui.wardrobe_home_screen(total)
    kb = InlineKeyboardMarkup(rows)
    # Экран шкафа служебный. Отправляем его отдельно, чтобы карточка образа,
    # из которой пользователь пришёл, осталась в истории как полезный результат.
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


async def send_category(bot, cid, zone_slug, page=0, q=None):
    zone = ZONE_BY_SLUG.get(zone_slug)
    if not zone:
        await send_wardrobe_zones(bot, cid, q=q)
        return
    items = [item for item_zone, _subcat, item in _flat_wardrobe_items(store.load_wardrobe(cid))
             if item_zone == zone]
    total = len(items)
    pages = max(1, (total + WARDROBE_CATEGORY_PAGE_SIZE - 1) // WARDROBE_CATEGORY_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    chunk = items[
        page * WARDROBE_CATEGORY_PAGE_SIZE:(page + 1) * WARDROBE_CATEGORY_PAGE_SIZE
    ]
    msg = wardrobe_ui.category_screen(public_zone_name(zone), chunk, total=total)
    rows = [[InlineKeyboardButton("✅ Добавить вещь", callback_data="w_add")]]
    rows.extend([[InlineKeyboardButton(str(item.get("name") or "Вещь")[:48], callback_data=f"w_item_{item.get('id')}")]
                 for item in chunk])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"w_cat_{zone_slug}_{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"w_cat_{zone_slug}_{(page + 1) % pages}"),
        ])
    rows.append(nav_row("w_closet"))
    kb = InlineKeyboardMarkup(rows)
    await rich_delivery.show(bot, cid, msg, reply_markup=kb, query=q)


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
        [("Собрать образ", f"w_with_{item_id}")],
        [("⬅️ Назад", f"w_cat_{zone_slug}"), ("#️⃣ Главная", "m_menu")],
    ])
    await rich_delivery.show(bot, cid, msg, reply_markup=kb, query=q)


from wardrobe_management import (  # noqa: E402 — wardrobe_management uses names defined above
    get_wardrobe_gaps,
    add_wardrobe_gap,
    _local_text_item,
    _parse_items,
    _show_added_items,
    add_item,
    add_item_settings,
    add_item_photo,
    _find_item,
    _replace_item,
    edit_item_text,
    edit_add_preview,
    handle_wardrobe_search,
)
from wardrobe_router import (  # noqa: E402 — wardrobe_router uses names defined above
    ingest,
    handle_callback,
)
import rich_delivery
from ui.navigation import nav_row
