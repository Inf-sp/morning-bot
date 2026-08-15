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
import settings as _settings
from ui import wardrobe as wardrobe_ui
from ui.constants import delete_label, ui_label
from wardrobe_model import (
    ZONE_ORDER,
    ZONE_SUBCATS,
    flat_items as _flat_wardrobe_items,
    has_rain_outerwear as _has_rain_outerwear,
    normalize_parsed_item,
    public_zone_name,
    public_item_name,
    wardrobe_stats,
)
from wardrobe_outfit import (
    build_style_tip,
    choose_outfit_style,
    is_urban_2026_base_top,
    outfit_display_order,
    pick_best_outfit,
    save_outfit_feedback,
)
from wardrobe_migration import migrate_item_attrs

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6
COPY_VALIDATOR_VERSION = 11
PURCHASE_RECOMMENDATION_VERSION = 2

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("🆕 Добавить вещь", "w_add")],
        [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")]])

def _day_key():
    return datetime.now(config.TZ).date().isoformat()


def _weather_decision(weather_ctx, variant=0):
    """Коротко называет условия, которые меняют выбор одежды.

    Вариант меняется между новыми образами, чтобы одинаковая погода не
    превращалась в один и тот же застывший текст.
    """
    if not weather_ctx or weather_ctx.get("tmax") is None:
        return ""
    try:
        variant = max(0, int(variant))
    except (TypeError, ValueError):
        variant = 0

    def choose(options):
        return options[variant % len(options)]

    has_rain = weather_ctx.get("has_rain")
    strong_wind = weather_ctx.get("strong_wind")
    hot = weather_ctx.get("hot")
    warm = weather_ctx.get("warm")

    if has_rain and hot:
        return choose((
            "Тепло, возможен дождь — пригодится лёгкая защита.",
            "Тёплый день с дождём — выбери закрытую обувь и защищённый слой.",
        ))
    if has_rain and strong_wind:
        return choose((
            "Прохладно, ветрено и возможен дождь — нужен защищённый слой.",
            "Сегодня лучше прикрыться от ветра и дождя.",
        ))
    if has_rain:
        return choose((
            "Возможен дождь — лучше выбрать закрытую обувь.",
            "Возьми вещь, которая не боится короткого дождя.",
        ))
    if strong_wind and hot:
        return choose((
            "Тепло, но ветрено — пригодится лёгкий слой.",
            "Жарко, но порывисто — оставь лёгкую защиту от ветра.",
        ))
    if strong_wind:
        return choose((
            "Прохладно и ветрено — нужен дополнительный слой.",
            "Ветер усилит прохладу — добавь лёгкий верхний слой.",
        ))
    if hot:
        return choose((
            "Жарко и сухо — выбирай лёгкие ткани.",
            "Солнечный тёплый день — пусть вещи дышат и не перегружают образ.",
        ))
    if warm:
        return choose((
            "Тепло и сухо — достаточно лёгких слоёв.",
            "Мягкая погода — выбирай дышащие вещи без лишнего утепления.",
            "Днём комфортно — лёгкого верха будет достаточно.",
        ))
    return choose((
        "Прохладно — нужен дополнительный слой.",
        "Свежо — собери образ с тёплым верхним слоем.",
    ))


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


def _purchase_recommendation_text(recommendation):
    recommendation = recommendation or {}
    item = _clean_text(recommendation.get("item"))
    reason = _clean_text(recommendation.get("reason"))
    return f"{item} — {reason}" if item and reason else item or reason


def _city_style_selected(selected_styles):
    return any(str(style or "").casefold() == "городской" for style in (selected_styles or []))


def _purchase_candidate(w, weather_ctx, selected_styles=None):
    """Возвращает только действительно полезный пробел, без генерации текста."""
    if weather_ctx.get("has_rain") and not _has_rain_outerwear(w):
        return {
            "version": PURCHASE_RECOMMENDATION_VERSION,
            "item": "Лёгкая непромокаемая ветровка",
            "reason": "закроет важный пробел в шкафу и защитит образ от дождя",
            "priority": 100,
        }

    items = [item for _zone, _subcat, item in _flat_wardrobe_items(w)]
    if _city_style_selected(selected_styles):
        tops = [item for item in items if item.get("zone") == "Верх"]
        if tops and not any(is_urban_2026_base_top(item) for item in tops):
            return {
                "version": PURCHASE_RECOMMENDATION_VERSION,
                "item": "Белая свободная футболка",
                "reason": "добавит современную городскую базу к брюкам и кедам",
                "priority": 60,
            }

    facts = " ".join(str(item.get("name") or "") for item in items).casefold()
    if "джинс" not in facts and "деним" not in facts:
        tops = [
            item for item in items
            if item.get("zone") == "Верх"
            and any(marker in str(item.get("name") or "").casefold()
                    for marker in ("рубаш", "футбол", "лонгслив", "топ"))
        ]
        if tops:
            return {
                "version": PURCHASE_RECOMMENDATION_VERSION,
                "item": "Серые широкие джинсы",
                "reason": "закроют пробел в шкафу и дадут больше сочетаний с твоими рубашками и футболками",
                "priority": 40,
            }
    return None


def _get_or_create_purchase_recommendation(cid, w, weather_ctx, fallback_tip="", selected_styles=None,
                                           refresh_wear_tip=False):
    current = store.get_wardrobe_purchase_recommendation(cid)
    if current and current.get("version") != PURCHASE_RECOMMENDATION_VERSION:
        current = {}
    candidate = _purchase_candidate(w, weather_ctx, selected_styles)
    if current:
        if not _purchase_recommendation_text(current):
            if candidate:
                store.set_wardrobe_purchase_recommendation(cid, candidate)
                return candidate
            if fallback_tip:
                fallback = {
                    "version": PURCHASE_RECOMMENDATION_VERSION,
                    "kind": "wear",
                    "reason": fallback_tip,
                    "priority": 0,
                }
                store.set_wardrobe_purchase_recommendation(cid, fallback)
                return fallback
            return {}
        current_priority = int(current.get("priority", 0) or 0)
        candidate_priority = int((candidate or {}).get("priority", 0) or 0)
        if candidate_priority > current_priority:
            store.set_wardrobe_purchase_recommendation(cid, candidate)
            return candidate
        if (refresh_wear_tip and current.get("kind") == "wear" and fallback_tip
                and _clean_text(current.get("reason")) != _clean_text(fallback_tip)):
            refreshed = {
                "version": PURCHASE_RECOMMENDATION_VERSION,
                "kind": "wear",
                "reason": fallback_tip,
                "priority": 0,
            }
            store.set_wardrobe_purchase_recommendation(cid, refreshed)
            return refreshed
        return current
    if candidate:
        store.set_wardrobe_purchase_recommendation(cid, candidate)
        return candidate
    if current:
        return current
    if fallback_tip:
        fallback = {
            "version": PURCHASE_RECOMMENDATION_VERSION,
            "kind": "wear",
            "reason": fallback_tip,
            "priority": 0,
        }
        store.set_wardrobe_purchase_recommendation(cid, fallback)
        return fallback
    return {}


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


def _repair_missing_purchase_recommendation(cid, look_data):
    """Восстанавливает полезный блок в старом дневном кэше образа."""
    look_data = dict(look_data or {})
    recommendation = look_data.get("purchase_recommendation") or {}
    if (_purchase_recommendation_text(recommendation)
            and recommendation.get("version") == PURCHASE_RECOMMENDATION_VERSION):
        return look_data
    wardrobe = store.load_wardrobe(cid)
    outfit_items = _cached_outfit_items(wardrobe, look_data)
    fallback_tip = build_style_tip(outfit_items, {})
    if not _clean_text(look_data.get("style_tip")) and fallback_tip:
        look_data["style_tip"] = fallback_tip
    recommendation = _get_or_create_purchase_recommendation(
        cid,
        wardrobe,
        {},
        fallback_tip=fallback_tip,
        selected_styles=[look_data.get("primary_style")],
    )
    if recommendation:
        look_data["purchase_recommendation"] = recommendation
    return look_data


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _build_entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    msg = wardrobe_ui.entity_card(title, summary, quote, bullets, final, bullet_label)
    return msg.text, msg.entities

def _build_purchase_message(data):
    msg = wardrobe_ui.purchase_check_card(data)
    return msg.text, msg.entities

def _build_purchase_suggestions_message(data):
    msg = wardrobe_ui.purchase_suggestions_card(data)
    return msg.text, msg.entities


def _get_cached_look(cid):
    cached = store.get_valid_wardrobe_daylook(cid)   # ссылочная целостность (version+id)
    if not cached or cached.get("date") != _day_key():   # день — бизнес-правило «раз в день»
        return None
    if cached.get("copy_validator_version") != COPY_VALIDATOR_VERSION:
        return None
    look_data = cached.get("look_data") or {}
    if "purchase_recommendation" not in look_data or look_data.get("purchase_recommendation") is None:
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
def build_wardrobe_keyboard(has_result=True):
    rows = [[("✨ Подобрать другой образ" if has_result else "✨ Подобрать образ", "w_look")]]
    rows.extend([
        [("💳 Что докупить", "w_buy")],
        [("🎚️ Мой шкаф", "w_closet")],
        [("#️⃣ Главная", "m_menu")],
    ])
    return _kb(rows)


_wardrobe_home_kb = build_wardrobe_keyboard


def _cancel_wardrobe_input(cid):
    cid = str(cid)
    if str(store.pending_input.get(cid, "")).startswith("wardrobe_"):
        store.pending_input.pop(cid, None)
    store.wardrobe_add_queue.pop(cid, None)
    store.wardrobe_edit_item.pop(cid, None)


async def send_home(bot, cid, q=None, status=None):
    """Главный экран раздела «Гардероб» — сразу образ на сегодня."""
    _cancel_wardrobe_input(cid)
    await send_looks(bot, cid, status=status, q=q)


class _WarmCacheStatus:
    """Минимальный status для фоновой сборки без Telegram-сообщений."""
    async def replace(self, _text, **_kwargs):
        return True


async def warm_home_cache(cid):
    """Собирает образ дня в кэш без отправки пользователю."""
    if _get_cached_look(cid):
        return True
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
        [("🆕 Заполнить шкаф", "w_fill")],
        [("#️⃣ Главная", "m_menu")],
    ])
    return wardrobe_ui.empty_wardrobe().text, kb


def has_wardrobe_items(cid) -> bool:
    return bool(store.wardrobe_to_text(store.load_wardrobe(cid)).strip())


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


async def send_looks(bot, cid, status=None, kb=None, previous_item_ids=None,
                     previous_style_tip=None, previous_weather_intro=None, q=None):
    result_kb = kb or _wardrobe_home_kb()
    cached = None if previous_item_ids else _get_cached_look(cid)
    if cached:
        cached_names = [_item_name(it) for it in (cached.get("look_data") or {}).get("items", [])]
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached_names)[:120]
        original_look_data = cached.get("look_data", {})
        look_data = _repair_missing_purchase_recommendation(cid, original_look_data)
        text, entities = _build_look_message(look_data)
        store.last_answer[str(cid)] = text
        if look_data != original_look_data:
            cached["look_data"] = look_data
            cached["text"] = text
            store.set_wardrobe_daylook(cid, cached)
        if kb is None:
            result_kb = build_wardrobe_keyboard(has_result=True)
        if status is not None:
            await status.replace(text, entities=entities, reply_markup=result_kb)
        elif q is not None:
            try:
                await q.message.edit_text(text, entities=entities, reply_markup=result_kb)
            except Exception:
                await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=result_kb)
        else:
            await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=result_kb)
        return
    w = store.load_wardrobe(cid)
    if not store.wardrobe_to_text(w).strip():
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
    if status is None:
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
    tmax = tmin = None
    flags = None
    try:
        wdata = await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 2)
        wd = wdata["daily"]
        day_str = (wd.get("time") or [None])[0] or _day_key()
        daytime_min, daytime_max = weather._daytime_temperature_range(
            wdata, day_str, wd["temperature_2m_min"][0], wd["temperature_2m_max"][0],
        )
        tmax = round(daytime_max)
        tmin = round(daytime_min)
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

    best_sorted = sorted(best, key=outfit_display_order)
    item_ids = [it.get("id") for it in best_sorted]
    fallback_tip = build_style_tip(
        best_sorted,
        weather_ctx,
        avoid_tips={previous_style_tip} if previous_style_tip else None,
    )
    purchase_recommendation = _get_or_create_purchase_recommendation(
        cid,
        w,
        weather_ctx,
        fallback_tip=fallback_tip,
        selected_styles=selected_styles,
        refresh_wear_tip=bool(previous_item_ids),
    )
    look_data = {
        "primary_style": choose_outfit_style(best_sorted, selected_styles),
        "items": [
            {"name": public_item_name(it), "zone": it.get("zone")}
            for it in best_sorted
        ],
        "style_tip": fallback_tip,
        "purchase_recommendation": purchase_recommendation,
    }
    if kb is None:
        result_kb = build_wardrobe_keyboard(has_result=True)
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
# Extracted to wardrobe_management.py: def get_wardrobe_gaps.

_ZONES_DESC = "; ".join(f"{z}: {', '.join(subs)}" for z, subs in ZONE_SUBCATS.items())


# Extracted to wardrobe_management.py: def _local_text_item.

ZONE_SLUG = {"Верх": "top", "Низ": "bot", "Верхняя одежда": "out",
             "Обувь": "shoe", "Аксессуары": "acc", "Другое": "oth"}
ZONE_BY_SLUG = {slug: zone for zone, slug in ZONE_SLUG.items()}


async def send_wardrobe_zones(bot, cid, q=None):
    """«Мой шкаф»: действия и непустые категории на одном экране."""
    _cancel_wardrobe_input(cid)
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    rows = []
    for zone in ZONE_ORDER:
        rows.append([InlineKeyboardButton(
            public_zone_name(zone),
            callback_data=f"w_cat_{ZONE_SLUG[zone]}",
        )])
    rows.append([InlineKeyboardButton("🆕 Добавить вещь", callback_data="w_add")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="w_closet"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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
        [("⬅️ Назад", f"w_cat_{zone_slug}"), ("#️⃣ Главная", "m_menu")],
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
        [("⬅️ Назад", f"w_item_{item_id}"), ("#️⃣ Главная", "m_menu")],
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


# Extracted to wardrobe_management.py: def _normalize_purchase_check.

async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


# ---------- роутер кнопок ----------
async def handle_callback(bot, cid, q, data, status=None):
    if data == "w_look":
        previous = _get_cached_look(cid) or {}
        previous_style_tip = (previous.get("look_data") or {}).get("style_tip") or None
        store.clear_wardrobe_daylook(cid)
        owns_status = status is None
        if owns_status:
            status = await util.StatusManager.start_inline(
                q,
                bot=bot,
                cid=cid,
                stages=util.StatusManager.TOPIC_STAGES["wardrobe"],
                preserve_message=True,
            )
        try:
            await send_looks(
                bot, cid, status=status,
                previous_item_ids=previous.get("item_ids") or [],
                previous_style_tip=previous_style_tip,
            )
        except Exception as e:
            await verify.safe_error(bot, cid, e, back="m_wardrobe")
        finally:
            if owns_status:
                await status.stop(delete=True)
        return
    if data in ("w_closet", "w_del_g"):
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="Опиши её одним сообщением или отправь вещи списком через запятую.\n\n"
                               "Пример: Голубая свободная рубашка Uniqlo.",
                               reply_markup=_back_kb()); return
    if data == "w_fill":
        store.pending_input[str(cid)] = "wardrobe_fill"
        await bot.send_message(
            chat_id=cid,
            text="Пришли список всей своей одежды одним сообщением — я сам разложу всё по шкафу.",
            reply_markup=_back_kb(),
        )
        return
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
    if data == "w_del":
        # Сначала показываем категории: редактирование идёт внутри выбранной категории.
        await send_wardrobe_zones(bot, cid, q=q); return
    if data.startswith(("w_del_", "w_delz_", "w_delsc_")):
        await send_wardrobe_zones(bot, cid, q=q); return
    if data == "w_improve":
        await send_home(bot, cid, q=q); return
    if data == "w_buy":
        await send_purchase_hub(bot, cid); return
    if data == "w_buy_pick":
        store.pending_input[str(cid)] = "wardrobe_buy"
        await bot.send_message(
            chat_id=cid,
            text="Что ищем? Например: «худи», «зелёная худи» или «ботинки на осень».",
            reply_markup=_kb([[("⬅️ Назад", "w_buy"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    if data == "w_buy_gap":
        await recommend_missing_purchase(bot, cid)
        return
    if data == "w_check":
        # Совместимость со старыми сообщениями: отдельная оценка покупки убрана.
        await send_purchase_hub(bot, cid)
        return


from module_binding import bind_functions as _bind_functions
import wardrobe_management as _wardrobe_management
_bind_functions(globals(), _wardrobe_management, ["get_wardrobe_gaps","add_wardrobe_gap","_local_text_item","_parse_items","_show_added_items","add_item","add_item_settings","add_item_photo","_find_item","_replace_item","edit_item_text","edit_add_preview","handle_wardrobe_search","_normalize_purchase_check","check_purchase","_purchase_hub_kb","_purchase_result_kb","send_purchase_hub","_missing_purchase_candidate","recommend_missing_purchase","_local_purchase_suggestions","_normalize_purchase_suggestions","recommend_purchase"])
