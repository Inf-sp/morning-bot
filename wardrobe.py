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
from util import esc
import verify
import secure
import memory
import research
import settings as _settings
from ui import wardrobe as wardrobe_ui

_log = logging.getLogger(__name__)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def closet_kb():
    return _kb([
        [("✏️ Добавить вещь", "w_add"), ("❌ Удалить вещи", "w_del")],
        [("◀️ Назад", "m_wardrobe")],
    ])

def _look_result_kb():
    return _kb([
        [("😍 Надел", "w_fb_worn"), ("🫪 Не моё", "w_fb_nostyle")],
        [("◀️ Назад", "m_wardrobe")],
    ])

def _back_kb():
    return _kb([[("◀️ Назад", "m_wardrobe")]])

def _today_label():
    now = datetime.now(config.TZ)
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return f"• {weekdays[now.weekday()]}, {now.day} {months[now.month - 1]}"

def _day_key():
    return datetime.now(config.TZ).date().isoformat()

def _build_look_message(items, intro="", add_text=""):
    msg = wardrobe_ui.look_message(items, intro=intro, add_text=add_text)
    return msg.text, msg.entities


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _build_entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    msg = wardrobe_ui.entity_card(title, summary, quote, bullets, final, bullet_label)
    return msg.text, msg.entities

def _get_cached_look(cid):
    cached = store.get_wardrobe_daylook(cid)
    if not isinstance(cached, dict):
        return None
    if cached.get("date") != _day_key():
        return None
    items = cached.get("items") or []
    if not items:
        return None
    return cached

def _save_cached_look(cid, items, intro="", add=""):
    text, _ = _build_look_message(items, intro=intro, add_text=add)
    store.set_wardrobe_daylook(cid, {
        "date": _day_key(),
        "items": list(items or []),
        "intro": intro or "",
        "add": add or "",
        "text": text,
    })


# ---------- главный экран раздела (панель состояния) ----------
def _wardrobe_home_kb():
    return _kb([
        [("✨ Образ на сегодня", "w_look")],
        [("🧥 Разбор гардероба", "w_improve")],
        [("🔎 Проверка покупки", "w_check")],
        [("🎚️ Настройки гардероба", "set_wardrobe_g")],
    ])


async def send_home(bot, cid, q=None):
    """Динамическая панель состояния раздела «Гардероб».

    Статистика пересчитывается на лету из store.load_wardrobe, поэтому всегда
    актуальна после любых изменений шкафа.
    """
    w = store.load_wardrobe(cid)
    total, counts = wardrobe_stats(w)
    params_filled = _params_filled(cid)
    missing = []
    if total <= 0:
        missing.append("👕 Шкаф")
    if not params_filled:
        missing.append("👤 Мои параметры")
    msg = wardrobe_ui.home_screen(total, counts, ZONE_ORDER, ZONE_EMOJI, params_filled, missing)
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


def _build_weather_rules(cid, w, flags):
    """Формирует блок погодных правил для промпта и фиксирует пробелы гардероба.

    Возвращает (rules_text, gap_note). gap_note — честная фраза для ответа, если
    под погоду нужной одежды нет; иначе пустая строка.
    """
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
    if flags["sunny"]:
        rules.append(
            "СОЛНЦЕ/ЖАРА: можно порекомендовать кепку, солнцезащитные очки, лёгкие натуральные ткани — "
            "ТОЛЬКО если они реально есть в гардеробе."
        )
    if not rules:
        return "", ""
    return _PRIORITY_BLOCK + "\n" + "\n".join(rules), gap_note


# ---------- генерация лука по погоде ----------
async def send_looks(bot, cid):
    cached = _get_cached_look(cid)
    if cached:
        store.last_source[str(cid)] = "Гардероб · Образ"
        store.last_answer[str(cid)] = cached.get("text", "")
        store.last_look[str(cid)] = ", ".join(str(it) for it in cached.get("items", []))[:120]
        text, entities = _build_look_message(cached.get("items", []), intro=cached.get("intro", ""), add_text=cached.get("add", ""))
        await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=_look_result_kb())
        return
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_closet"),
        ], [
            InlineKeyboardButton("◀️ Назад", callback_data="m_wardrobe"),
        ]])
        await bot.send_message(
            chat_id=cid,
            text=(
                "👔 <b>Шкаф пуст</b>\n\n"
                "Чтобы собрать образ из твоих вещей, сначала добавь их в шкаф."
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    s = store.get_settings(cid)
    # Персональный профиль из настроек пользователя
    user_profile = _settings.get(cid, "wardrobe_profile", "")
    user_style = _settings.get(cid, "style", "")
    user_body = _settings.get(cid, "body", "")
    priority_line = _settings.priority_context(cid)
    profile_line = f"Профиль пользователя: {user_profile}." if user_profile else ""
    style_line = f"Стиль пользователя: {user_style}." if user_style and not user_profile else ""
    body_line = f"Параметры тела: {user_body}." if user_body and not user_profile else ""
    style_block = "\n".join(x for x in [priority_line, profile_line, style_line, body_line] if x)
    tmax = None
    flags = None
    has_rain = False
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
        wparts.append(f"ветер до {flags['wind_ms']} м/с" + (" (сильный)" if flags["strong_wind"] else ""))
        if has_rain:
            mm_txt = f", {flags['rain_mm']} мм" if flags.get("rain_mm") else ""
            wparts.append(f"дождь вероятностью {flags['rain_prob']}%{mm_txt}"
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
                     "Без тяжёлых слоёв и ветровок.")
    else:
        temp_rule = (f"tmax={tmax}°C, ПРОХЛАДНО{' / дождь' if has_rain else ''} — "
                     "слои уместны, можно ветровку или флис, закрытая обувь.")
    weather_rules, gap_note = _build_weather_rules(cid, w, flags)
    recent = store.recent_looks.get(str(cid), [])
    avoid = ("\nНе повторяй образы за последние 3 дня: " + "; ".join(recent)) if recent else ""
    hints = memory.wardrobe_hints(cid)
    fb_line = ("\nУчитывай прошлый фидбек (НЕ показывай его дословно, просто учти): "
               + secure.wrap_untrusted(hints, "фидбек гардероба")) if hints else ""
    pref_hints = memory.profile_hints(cid)
    pref_line = ("\n" + secure.wrap_untrusted(pref_hints, "предпочтения")) if pref_hints else ""
    profile_block = (f"\n{style_block}" if style_block else "")
    weather_block = (f"\n{weather_rules}" if weather_rules else "")
    prompt = f"""Ты опытный стилист. Собери ОДИН образ из гардероба на сегодня.{profile_block}
Погода: {wctx}
ТЕМПЕРАТУРНОЕ ПРАВИЛО (строго, не нарушать): {temp_rule}{weather_block}{fb_line}{pref_line}
Гардероб пользователя (ТОЛЬКО эти вещи, другие не добавлять):
{wardrobe_text}
Правила: 1 верх + 1 низ + обувь (+ опц. аксессуар-совет). Сочетание по цвету и стилю.
Каждую вещь пиши ПОЛНЫМ названием из списка выше (напр. «Белая футболка Uniqlo», не «Верх: белая»).{avoid}
JSON (без markdown):
{{"intro":"1 строка про погоду и логику образа","items":["вещь 1 полным названием","вещь 2","вещь 3"],"add":"1 совет что добавить (аксессуар) и почему"}}"""
    try:
        d = await ai.allm_json(prompt, 700, module="wardrobe")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    items = d.get("items", [])
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось собрать образ. Попробуй ещё раз.", reply_markup=_look_result_kb())
        return
    rl = store.recent_looks.get(str(cid), [])
    rl.append(", ".join(items)[:80])
    store.recent_looks[str(cid)] = rl[-3:]
    store.last_look[str(cid)] = ", ".join(str(it) for it in items)[:120]   # для фидбека
    intro = d.get("intro", "")
    if gap_note:
        # Честно сообщаем о пробеле под дождь прямо в образе.
        intro = (intro + " " + gap_note).strip() if intro else gap_note
    text, entities = _build_look_message(items, intro=intro, add_text=d.get("add", ""))
    _save_cached_look(cid, items, intro=intro, add=d.get("add", ""))
    store.last_source[str(cid)] = "Гардероб · Образ"
    store.last_answer[str(cid)] = text
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=_look_result_kb())


# ---------- фидбек по образу ----------
_FB_ACK = {
    "worn": "😍 Отметил: надел. Буду чаще предлагать похожее.",
}

async def look_feedback(bot, cid, verdict):
    look = store.last_look.get(str(cid), "")
    memory.add_wardrobe_feedback(cid, look, verdict)
    if verdict == "nostyle":
        store.clear_wardrobe_daylook(cid)
        await send_looks(bot, cid)
    else:
        await bot.send_message(chat_id=cid, text=_FB_ACK.get(verdict, "Запомнил — учту в следующих образах."))


# ---------- шкаф ----------
# Порядок важен: «Верхняя одежда» проверяется раньше «Верх», иначе «куртка»/«ветровка»
# по подстроке «верх» ушли бы в «Верх».
ZONES = [
    ("Верхняя одежда", ["верхняя одежд", "верхн", "куртк", "ветровк", "пиджак", "пальто",
                        "плащ", "дождевик", "парк", "пуховик", "тренч", "анорак", "бомбер",
                        "жилет"]),
    ("Верх", ["верх", "футбол", "рубаш", "свит", "толстов", "худи", "лонгслив", "поло", "майк", "кофт"]),
    ("Низ", ["низ", "джинс", "брюк", "штан", "шорт", "юбк"]),
    ("Обувь", ["обув", "кроссов", "ботин", "кед", "туфл", "сандал"]),
    ("Аксессуары", ["аксессуар", "часы", "кольц", "ремен", "шапк", "кепк", "очк", "шарф", "сумк", "цепоч", "носк", "украшен"]),
]

# Порядок зон для отображения статистики и шкафа.
ZONE_ORDER = ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары", "Другое"]
ZONE_EMOJI = {"Верх": "👕", "Низ": "👖", "Верхняя одежда": "🧥",
              "Обувь": "👟", "Аксессуары": "⌚", "Другое": "🎒"}

def _zone_of(category):
    c = category.lower()
    for zone, keys in ZONES:
        if any(k in c for k in keys):
            return zone
    return "Другое"


def _flat_wardrobe_items(w):
    items = []
    for cat, values in (w or {}).items():
        if cat == "_v" or not isinstance(values, list):
            continue
        for value in values:
            value = str(value).strip()
            if value:
                items.append((str(cat), value))
    return items

# ---------- статистика и готовность гардероба ----------
def wardrobe_stats(w):
    """Считает вещи по зонам. Возвращает (total, {zone: count}) с полным набором зон."""
    counts = {z: 0 for z in ZONE_ORDER}
    total = 0
    for cat, item in _flat_wardrobe_items(w):
        counts[_zone_of(cat)] += 1
        total += 1
    return total, counts


def _params_filled(cid):
    """Заполнены ли личные параметры для точных рекомендаций.

    Отдельных полей пол/рост/вес в модели нет — ориентируемся на свободный
    профиль или связку стиль+тело.
    """
    profile = _settings.get(cid, "wardrobe_profile", "")
    style = _settings.get(cid, "style", "")
    body = _settings.get(cid, "body", "")
    return bool(profile or (style and body))


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


async def send_show(bot, cid):
    w = store.load_wardrobe(cid)
    if not w:
        await bot.send_message(chat_id=cid, text="Шкаф пуст. Добавь вещи через «🏷 Добавить вещь».", reply_markup=closet_kb())
        return
    grouped = {}
    for cat, items in w.items():
        if cat == "_v" or not isinstance(items, list):
            continue
        z = _zone_of(cat)
        grouped.setdefault(z, []).extend(items)
    lines = ["🗄 <b>Мой шкаф</b>", ""]
    for z in ZONE_ORDER:
        if grouped.get(z):
            lines.append(f"{ZONE_EMOJI.get(z,'•')} <b>{z}</b>")
            lines += [f"   - {esc(it)}" for it in grouped[z]]
            lines.append("")
    await bot.send_message(chat_id=cid, text="\n".join(lines).strip(), parse_mode="HTML", reply_markup=closet_kb())

async def _parse_and_add(bot, cid, text):
    w = store.load_wardrobe(cid)
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, джинсы, обувь, аксессуары"
    parsed = await ai.allm_json(
        f"Разбери вещи по категориям. Категории: {cats} (можно создать новую).\n"
        f"Вещи:\n{secure.wrap_untrusted(text, 'список вещей')}\n"
        "Каждую вещь пиши ПОЛНЫМ названием в порядке: тип + цвет + детали/бренд "
        "(напр. «Футболка белая Uniqlo плотная», «Шорты серые тонкие»). Сохраняй бренд если указан.\n"
        'JSON: {"категория": ["полное название вещи"]}.', 700, tier="cheap", module="wardrobe")
    return store.merge_wardrobe(parsed, cid)

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

async def send_del(bot, cid):
    w = store.load_wardrobe(cid)
    flat = []
    for cat, items in w.items():
        if cat == "_v" or not isinstance(items, list):
            continue
        for it in items:
            flat.append((cat, it))
    if not flat:
        await bot.send_message(chat_id=cid, text="Шкаф пуст.", reply_markup=closet_kb()); return
    store.del_index[str(cid)] = flat
    rows = [[InlineKeyboardButton(f"❌ {it}", callback_data=f"w_delitem_{i}")] for i, (cat, it) in enumerate(flat[:40])]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="w_closet")])
    await bot.send_message(chat_id=cid, text="Что удалить?", reply_markup=InlineKeyboardMarkup(rows))

async def del_item(bot, cid, i):
    flat = store.del_index.get(str(cid), [])
    if i >= len(flat):
        await bot.send_message(chat_id=cid, text="Уже удалено."); return
    cat, it = flat[i]
    w = store.load_wardrobe(cid)
    if cat in w and it in w[cat]:
        w[cat].remove(it)
        if not w[cat]:
            del w[cat]
        store.save_wardrobe(w, cid)
    await bot.send_message(chat_id=cid, text="Удалено. Шкаф стал легче.")
    await send_del(bot, cid)


# ---------- улучшить гардероб ----------
def _fallback_improve_data(w):
    """Резервный разбор по зонам (без ИИ) в новой схеме карточки-стилиста."""
    items = _flat_wardrobe_items(w)
    zones = {}
    for cat, item in items:
        zones.setdefault(_zone_of(cat), []).append(item)

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
    for zone, emoji in (("Верх", "👔"), ("Низ", "👖"), ("Обувь", "👟"), ("Аксессуары", "🧢")):
        if zones.get(zone):
            look_items.append(f"{emoji} {zones[zone][0]}")

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
            InlineKeyboardButton("✏️ Добавить вещи в шкаф", callback_data="set_closet"),
        ], [
            InlineKeyboardButton("◀️ Назад", callback_data="m_wardrobe"),
        ]])
        await bot.send_message(
            chat_id=cid,
            text="🧥 <b>Шкаф пуст</b>\n\nДобавь вещи в шкаф — тогда разберу гардероб и дам советы.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    prompt = _improve_prompt(cid, wardrobe_text)
    try:
        d = await ai.allm_json(prompt, 2000, module="wardrobe",
                               route="claude", claude_model=config.WARDROBE_MODEL)
    except Exception as e:
        _log.warning("wardrobe improve AI failed, using fallback: %r", e, exc_info=True)
        d = _fallback_improve_data(w)
    d = _merge_priority_gaps(cid, d)
    msg = wardrobe_ui.improve_card(d)
    store.last_source[str(cid)] = "Гардероб · Улучшение"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=_kb([[("◀️ Назад", "m_wardrobe")]]))


def _improve_prompt(cid, wardrobe_text):
    """Промпт персонального стилиста: аудит гардероба, а не технический лог."""
    user_style = _settings.get(cid, "style", "")
    user_body = _settings.get(cid, "body", "")
    user_profile = _settings.get(cid, "wardrobe_profile", "")
    priority = _settings.priority_context(cid)
    ctx = []
    if user_profile:
        ctx.append(f"Профиль: {user_profile}")
    if user_style:
        ctx.append(f"Любимый стиль: {user_style}")
    if user_body:
        ctx.append(f"Параметры/телосложение: {user_body}")
    if priority:
        ctx.append(priority)
    ctx_block = ("Данные о пользователе (учитывай в анализе):\n" + "\n".join(ctx) + "\n\n") if ctx else ""
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
"best_look": {{"items":["👔 вещь","👖 вещь","👟 вещь","🧢 акцент"], "why":"почему образ работает"}},
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
    user_profile = _settings.get(cid, "wardrobe_profile", "")
    user_style = _settings.get(cid, "style", "")
    user_body = _settings.get(cid, "body", "")
    priority_ctx = (_settings.priority_context(cid) + " ") if _settings.priority_context(cid) else ""
    profile_ctx = f"Профиль пользователя: {user_profile}. " if user_profile else ""
    style_ctx = f"Стиль: {user_style}. " if user_style and not user_profile else ""
    body_ctx = f"Параметры тела: {user_body}. " if user_body and not user_profile else ""
    prompt = f"""Ты честный стилист-аналитик. Пользователь думает купить: {text}
{priority_ctx}{profile_ctx}{style_ctx}{body_ctx}
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
        reply_markup=_kb([[("◀️ Назад", "m_wardrobe")]]))


# ---------- добавление файлом (старый режим, оставлен) ----------
async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


# ---------- роутер кнопок ----------
async def handle_callback(bot, cid, q, data):
    if data == "w_look":
        await util.ack_loading(q); await send_looks(bot, cid); await util.clear_loading(q); return
    if data == "w_fb_nostyle":
        await util.ack_loading(q)
        await look_feedback(bot, cid, "nostyle"); await util.clear_loading(q); return
    if data == "w_fb_worn":
        await look_feedback(bot, cid, "worn"); return
    if data == "w_closet":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "kast")
        return
    if data == "w_show":
        await send_show(bot, cid); return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="🏷 Напиши вещь в формате: тип + цвет + детали/бренд.\n"
                               "Напр.: «Футболка белая Uniqlo плотная» или «Шорты серые тонкие». Можно списком.",
                               reply_markup=_back_kb()); return
    if data == "w_del":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "kast"); return
    if data.startswith("w_delitem_"):
        await del_item(bot, cid, int(data.split("_")[-1])); return
    if data == "w_improve":
        await util.ack_loading(q); await send_improve(bot, cid); await util.clear_loading(q); return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="Пришли ссылку или название вещи - оценю, брать или нет.",
                               reply_markup=_back_kb()); return
