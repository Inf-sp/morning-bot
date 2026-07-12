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
import memory
import research
import settings as _settings
from ui import wardrobe as wardrobe_ui
from ui.constants import ui_label

_log = logging.getLogger(__name__)

WARDROBE_WIND_LAYER_MS = 6

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("✏️ Добавить вещь", "w_add"), ("❌ Удалить вещи", "w_del")],
        [("⬅️ Назад", "m_wardrobe")],
    ])

def _look_result_kb():
    return _kb([
        [("😍 Надел", "w_fb_worn"), ("🫥 Не моё", "w_fb_nostyle")],
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


def _short_weather_line(tmax, cond, has_rain, flags):
    """Короткая погодная строка для карточки образа, без LLM: '☀️ Сегодня: солнечно · до +25°C.'"""
    if tmax is None:
        return ""
    emoji = _weather_emoji(has_rain, flags)
    if has_rain:
        word = "дождь"
    elif flags and flags.get("sunny"):
        word = "солнечно"
    else:
        word = str(cond or "").lower() or "облачно"
    return f"{emoji} Сегодня: {word} · до {tmax:+d}°C."


def _build_look_message(look_data):
    msg = wardrobe_ui.look_message(look_data)
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

def _resolve_item_ids(w, names):
    """Сопоставляет имена вещей (как их вернула LLM) с их id в текущем гардеробе.
    Регистронезависимое точное совпадение; вещь без совпадения не попадает в
    результат — защита от того, что LLM вернула вещь не из списка."""
    by_name = {it["name"].lower(): it["id"]
               for zone in (w or {}).get("zones", {}).values()
               for items in zone.values() for it in items}
    return [by_name[n.lower()] for n in names if n.lower() in by_name]

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
def _wardrobe_home_kb():
    return _kb([
        [("✨ Обновить образ на сегодня", "w_look")],
        [("👕 Разбор гардероба", "w_improve")],
        [("🔍 Проверка покупки", "w_check")],
        [("👔 Мой гардероб", "set_wardrobe_g")],
        [("⬅️ Назад", "m_menu")],
    ])


async def _restore_home_kb(q):
    if q is None or getattr(q, "message", None) is None:
        return
    try:
        await q.message.edit_reply_markup(reply_markup=_wardrobe_home_kb())
    except Exception:
        pass


async def send_home(bot, cid, q=None):
    """Динамическая панель состояния раздела «Гардероб».

    Статистика пересчитывается на лету из store.load_wardrobe, поэтому всегда
    актуальна после любых изменений шкафа.
    """
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    msg = wardrobe_ui.home_screen(total, counts, ZONE_ORDER)
    kb = _wardrobe_home_kb()
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


_PRIORITY_BLOCK = (
    "ПОРЯДОК ВАЖНОСТИ рекомендаций (сверху вниз, при конфликте — компромисс, "
    "не ориентируйся только на температуру):\n"
    "1. Защита от дождя\n2. Комфорт по температуре\n3. Защита от ветра\n"
    "4. Соответствие стилю пользователя\n5. Не повторять недавние образы\n"
    "6. Прошлые оценки «Надел»/«Не моё»\n"
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
async def send_looks(bot, cid, status=None):
    cached = _get_cached_look(cid)
    if cached:
        cached_names = [_item_name(it) for it in (cached.get("look_data") or {}).get("items", [])]
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached_names)[:120]
        text, entities = _build_look_message(cached.get("look_data", {}))
        if status is not None:
            await status.replace(text, entities=entities, reply_markup=_look_result_kb())
        else:
            await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=_look_result_kb())
        return
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_ward_add"),
        ], [
            InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"),
        ]])
        empty_text = (
            f"<b>{ui_label('empty_wardrobe', 'Шкаф пуст')}</b>\n\n"
            "Чтобы собрать образ из твоих вещей, сначала добавь их в шкаф."
        )
        if status is not None:
            await status.replace(empty_text, parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(chat_id=cid, text=empty_text, parse_mode="HTML", reply_markup=kb)
        return
    s = store.get_settings(cid)
    status = status or await util.StatusManager.start(bot, cid)
    # Персональный профиль из настроек пользователя (Персонализация → Гардероб)
    style_block = _settings.wardrobe_prefs_context(cid)
    tmax = None
    flags = None
    has_rain = False
    cond = ""
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
        has_rain = flags["rain_daytime"]
        cond = weather.DESC.get(weathercode, "")
        wparts = [f"днём до +{tmax}°C (ночью +{tmin}°C)"]
        if cond:
            wparts.append(cond)
        apparent = (wdata.get("current") or {}).get("apparent_temperature")
        if apparent is not None:
            apparent = round(apparent)
            if abs(apparent - tmax) >= 2:
                wparts.append(f"ощущается как +{apparent}°C")
        wparts.append(f"ветер до {flags['wind_ms']} м/с" + (" (сильный)" if flags["strong_wind"] else ""))
        if has_rain:
            mm_txt = f", {flags['rain_mm']} мм" if flags.get("rain_mm") else ""
            rain_periods = weather._periods(wdata, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
            when_txt = f" ({'/'.join(rain_periods)})" if rain_periods else ""
            wparts.append(f"дождь вероятностью {flags['rain_prob']}%{mm_txt}{when_txt}"
                          + (", возможен ливень" if flags["heavy_rain"] else ""))
        elif flags["sunny"]:
            wparts.append("солнечно")
        wctx = "Сегодня: " + ", ".join(wparts)
    except Exception:
        wctx = "нет данных"
        flags = None
        has_rain = False
    if tmax is not None and tmax >= 24 and not has_rain:
        temp_rule = (f"tmax={tmax}°C, ЖАРКО — ЗАПРЕЩЕНО: ветровки, флис, куртки, толстовки, слои. "
                     "Только лёгкий верх (футболка/рубашка) + шорты или лёгкие брюки.")
    elif tmax is not None and tmax >= 17:
        temp_rule = (f"tmax={tmax}°C, ТЕПЛО — лёгкие брюки/джинсы + футболка или рубашка. "
                     "Без тяжёлых слоёв; ветровка допустима только при дожде или ветре от 6 м/с.")
    else:
        temp_rule = (f"tmax={tmax}°C, ПРОХЛАДНО{' / дождь' if has_rain else ''} — "
                     "слои уместны, можно ветровку или флис, закрытая обувь.")
    weather_rules, _gap_note = _build_weather_rules(cid, w, flags)
    recent = store.recent_looks.get(str(cid), [])
    avoid = ("\nНе повторяй образы за последние 3 дня: " + "; ".join(recent)) if recent else ""
    hints = memory.wardrobe_hints(cid)
    fb_line = ("\nУчитывай прошлый фидбек (НЕ показывай его дословно, просто учти): "
               + secure.wrap_untrusted(hints, "фидбек гардероба")) if hints else ""
    pref_hints = memory.profile_hints(cid)
    pref_line = ("\n" + secure.wrap_untrusted(pref_hints, "предпочтения")) if pref_hints else ""
    profile_block = (f"\n{style_block}" if style_block else "")
    weather_block = (f"\n{weather_rules}" if weather_rules else "")
    now_dt = datetime.now(config.TZ)
    short_date = f"{util._WEEKDAY_SHORT[now_dt.weekday()]}, {now_dt.day} {util._MONTHS[now_dt.month - 1]}"
    city = s.get("city", "")
    prompt = f"""Ты — личный стилист и ассистент по гардеробу. Составь один готовый образ на сегодня только из вещей пользователя.
Дата: {short_date}. Город: {city}.{profile_block}
Погода: {wctx}
ТЕМПЕРАТУРНОЕ ПРАВИЛО (строго, не нарушать): {temp_rule}{weather_block}{fb_line}{pref_line}
Гардероб пользователя (ТОЛЬКО эти вещи, другие не добавлять):
{wardrobe_text}
Задача:
1. Выбери полноценный и практичный образ под погоду и стиль пользователя.
2. Используй только вещи из списка выше.
3. Учитывай сочетание цветов, посадку, силуэт, материалы и удобство.
4. Не повторяй без необходимости недавние вещи.{avoid}
5. Включай все нужные детали: верх, низ, обувь (+ опц. аксессуар или верхняя одежда, если нужна по погоде).
6. Не добавляй вещь только ради заполнения списка — например, не указывай куртку, если она не нужна.
7. Можно один практичный совет по носке: закатать рукава рубашки, оставить рубашку навыпуск, расстегнуть верхнюю пуговицу. Пользователь НИКОГДА не заправляет рубашку в штаны — не советуй это.
Поле name — вещь ПОЛНЫМ названием из списка выше, точь-в-точь как там написано (для сверки со шкафом).
Поле short_name — то же название без бренда (напр. «Белая футболка Uniqlo» → «Белая футболка»), в остальном не меняй формулировку.
Обращайся на «ты», без имени. Не пиши «вот образ», «хорошего дня», «шкаф заполнен хорошо», «образ идеально подходит», «стильно и комфортно». Не повторяй погоду в объяснении. Не называй стиль отдельным словом — он должен чувствоваться в подборе, а не быть тегом. Не добавляй рекомендаций докупить что-либо.

Верни строго валидный JSON (без markdown):
{{"items":[{{"name":"вещь полным названием из списка","short_name":"вещь без бренда"}}, "... 3-4 вещи: верх, низ, обувь, опц. аксессуар"],
"explanation":"одно естественное предложение, максимум 18 слов, почему сочетание работает именно сегодня"}}"""
    try:
        d = await ai.allm_json(prompt, 500, module="wardrobe")
    except Exception as e:
        await status.stop(delete=True)
        await verify.safe_error(bot, cid, e); return
    raw_items = d.get("items", [])
    items = [it.get("name", "") if isinstance(it, dict) else str(it) for it in raw_items]
    items = [it for it in items if it.strip()]
    if not items:
        await status.replace("Не удалось собрать образ. Попробуй ещё раз.", reply_markup=_look_result_kb())
        return
    rl = store.recent_looks.get(str(cid), [])
    rl.append(", ".join(items)[:80])
    store.recent_looks[str(cid)] = rl[-3:]
    store.last_look[str(cid)] = ", ".join(str(it) for it in items)[:120]   # для фидбека
    wardrobe_total, _ = wardrobe_stats(w)
    look_data = {
        "short_date": short_date,
        "city": city,
        "weather_line": _short_weather_line(tmax, cond, has_rain, flags),
        "items": raw_items,
        "explanation": d.get("explanation", ""),
        "wardrobe_total": wardrobe_total,
    }
    text, entities = _build_look_message(look_data)
    item_ids = _resolve_item_ids(w, items)
    _save_cached_look(cid, item_ids, look_data=look_data)
    store.last_source[str(cid)] = "Гардероб · Образ"
    store.last_answer[str(cid)] = text
    await status.replace(text, entities=entities, reply_markup=_look_result_kb())


# ---------- фидбек по образу ----------
_FB_ACK = {
    "worn": "Отметил: надел. Буду чаще предлагать похожее.",
}

async def look_feedback(bot, cid, verdict, status=None):
    look = store.last_look.get(str(cid), "")
    memory.add_wardrobe_feedback(cid, look, verdict)
    if verdict == "nostyle":
        store.clear_wardrobe_daylook(cid)
        await send_looks(bot, cid, status=status)
    else:
        await bot.send_message(chat_id=cid, text=_FB_ACK.get(verdict, "Запомнил — учту в следующих образах."))


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
    "Перчатки": ["перчат"], "Сумки": ["сумк"],
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👔 Мой гардероб", callback_data="w_del_g")]]),
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
def _fallback_improve_data(w):
    """Резервный разбор по зонам (без ИИ) в новой схеме карточки-стилиста."""
    items = _flat_wardrobe_items(w)
    zones = {}
    for zone, _subcat, item in items:
        zones.setdefault(zone, []).append(item["name"])

    strengths = []
    if zones.get("Верх"):
        strengths.append(f"{zones['Верх'][0]} — рабочая база для верхнего слоя, сочетается с большинством низа.")
    if zones.get("Низ"):
        strengths.append(f"{zones['Низ'][0]} — держит силуэт и подходит под разный верх.")
    if zones.get("Обувь"):
        strengths.append(f"{zones['Обувь'][0]} — закрывает повседневные сценарии.")

    weaknesses = []
    buy = []
    if not zones.get("Верх"):
        weaknesses.append({"title": "Нет базового верха",
                           "text": "Без него сложно собрать даже повседневный образ."})
        buy.append({"item": "Плотная однотонная футболка или рубашка спокойного цвета",
                    "why": "Станет основой верха и свяжет низ с обувью — десятки новых сочетаний."})
    if not zones.get("Низ"):
        weaknesses.append({"title": "Нет базового низа",
                           "text": "Силуэт держится без опоры, образы выглядят незавершённо."})
        buy.append({"item": "Прямые джинсы или лёгкие брюки нейтрального цвета",
                    "why": "Дадут универсальный низ под весь имеющийся верх."})
    if not zones.get("Обувь"):
        weaknesses.append({"title": "Нет базовой обуви",
                           "text": "Без неё любой образ выглядит недоделанным."})
        buy.append({"item": "Нейтральные кеды или кроссовки",
                    "why": "Завершат большинство повседневных образов."})
    if not zones.get("Аксессуары"):
        buy.append({"item": "Один спокойный аксессуар (часы или ремень)",
                    "why": "Меняет характер образа без покупки новой одежды."})

    if not weaknesses:
        weaknesses.append({"title": "База выглядит рабочей",
                           "text": "Точные слабые места видно после примерки сочетаний."})

    look_items = []
    for zone in ("Верх", "Низ", "Обувь", "Аксессуары"):
        if zones.get(zone):
            look_items.append(f"{zone}: {zones[zone][0]}")

    total = len(items)
    score = max(40, min(90, 40 + total * 4))
    return {
        "score": score,
        "summary": "Разбор по категориям (базовый режим). Начни с баланса верха, низа и обуви — это даст больше всего новых сочетаний.",
        "strengths": strengths,
        "weaknesses": weaknesses[:5],
        "buy": buy[:5],
        "avoid": [],
        "best_look": {"items": look_items,
                      "why": "Простое сочетание базовых вещей с понятными пропорциями."} if look_items else {},
        "potential": "Гардероб собирается вокруг базы. Следующий шаг — закрыть пустые категории и добавить один цветовой акцент, чтобы образы стали разнообразнее.",
    }


async def send_improve(bot, cid):
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
    prompt = _improve_prompt(cid, wardrobe_text)
    try:
        d = await ai.allm_json(prompt, 2000, module="wardrobe", route="gemini")
    except Exception as e:
        _log.warning("wardrobe improve AI failed, using fallback: %r", e, exc_info=True)
        d = _fallback_improve_data(w)
    d = _merge_priority_gaps(cid, d)
    msg = wardrobe_ui.improve_card(d)
    store.last_source[str(cid)] = "Гардероб · Улучшение"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe")]]))


def _improve_prompt(cid, wardrobe_text):
    """Промпт персонального стилиста: аудит гардероба, а не технический лог."""
    prefs = _settings.wardrobe_prefs_context(cid)
    ctx_block = (f"Данные о пользователе (учитывай в анализе):\n{prefs}\n\n") if prefs else ""
    return f"""Ты — персональный стилист уровня Thread, Whering и мужской стилист GQ.
Твоя задача — не перечислить вещи, а провести профессиональный аудит гардероба так, чтобы пользователь подумал: «Это разбирал живой стилист».

Опирайся на знания мужского стиля, цветовых сочетаний, пропорций силуэта, капсульного гардероба, минимализма, smart casual, streetwear, old money, японского минимализма и современной европейской моды.

{ctx_block}Гардероб пользователя:
{wardrobe_text}

Оцени: баланс категорий, универсальность вещей, лёгкость сборки образов, сочетаемость цветов и силуэтов, качество базы, слабые места, дубли, редко используемые вещи, отсутствующие категории.

ПРАВИЛА:
- Обращайся на «ты», без имени.
- Никаких общих фраз («гардероб выглядит рабочим», «докупайте точечно»).
- Каждая рекомендация объясняет ПОЧЕМУ и какой эффект даёт (сколько новых сочетаний, что с чем свяжет).
- Никакой воды, повторов и шаблонов. Короткие ёмкие предложения. Telegram-формат.

Верни строго валидный JSON (без markdown):
{{"score": число 0-100,
"summary": "2-3 предложения: общая оценка гардероба и главный вывод",
"strengths": ["сильная сторона с объяснением ценности", "..."],
"weaknesses": [{{"title":"кратко проблема","text":"последствие для образов"}}, "... максимум 5, по важности"],
"buy": [{{"item":"конкретная вещь","why":"зачем, сколько новых сочетаний, с чем работает"}}, "... максимум 5, по влиянию"],
"avoid": ["лишняя покупка или дубль с объяснением", "... если есть"],
"best_look": {{"items":["Верх: вещь","Низ: вещь","Обувь: вещь","Аксессуары: акцент"], "why":"почему образ работает"}},
"potential": "1 абзац: универсальность, лёгкость сборки, какой стиль просматривается, следующий логичный шаг"}}"""


def _merge_priority_gaps(cid, d):
    """Персистентные пробелы гардероба (например, дождевик) — первыми в списке покупок."""
    gaps = get_wardrobe_gaps(cid)
    priority_gaps = [g for g in gaps if g.get("priority")]
    if not priority_gaps:
        return d
    buy = list(d.get("buy") or [])
    existing = {(b.get("item") if isinstance(b, dict) else str(b)).lower() for b in buy}
    prepend = []
    for g in priority_gaps[:2]:
        item = g.get("item", "")
        if item.lower() in existing:
            continue
        prepend.append({"item": item.capitalize(),
                        "why": f"Приоритетная покупка: {g.get('reason', '')}."})
    d = dict(d)
    d["buy"] = (prepend + buy)[:5]
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
        status = await util.StatusManager.start(bot, cid=cid, message=q.message if q else None)
        try:
            await send_looks(bot, cid, status=status)
        except Exception as e:
            await status.stop(delete=False)
            await verify.safe_error(bot, cid, e)
        return
    if data == "w_fb_nostyle":
        status = await util.StatusManager.start(bot, cid=cid, message=q.message if q else None)
        try:
            await look_feedback(bot, cid, "nostyle", status=status)
        except Exception as e:
            await status.stop(delete=False)
            await verify.safe_error(bot, cid, e)
        return
    if data == "w_fb_worn":
        await look_feedback(bot, cid, "worn"); return
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
        status = await util.StatusManager.start_inline(q, bot=bot, cid=cid)
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
