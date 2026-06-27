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

HOME_TEXT = (
    "👕 <b>Гардероб</b>\n\n"
    "Одежда без хаоса. Соберу тебе актуальный образ, разберу шкаф и честно скажу, что с ним не так.\n\n"
    "<b>Команды:</b>\n\n"
    "/setup — настройки\n"
    "/notes — сохранённые закладки\n\n"
    "Сохраняй полезное через ⭐ <b>В закладк</b> или ❤️ <b>В любимые</b>.\n\n"
    "<b>Выбери</b> 👇"
)



def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    return _kb([
        [("✨ Сгенерировать образ", "w_look")],
        [("🧥 Улучшить гардероб", "w_improve")],
        [("🔎 Проверка покупки", "w_check")],
    ])

def closet_kb():
    return _kb([
        [("🗄️ Показать всё", "w_show")],
        [("🏷 Добавить вещь", "w_add")],
        [("🧹 Удалить вещь", "w_del")],
        [(" В меню", "w_home")],
    ])

def _look_result_kb():
    return _kb([
        [("😍 Надел", "w_fb_worn")],
        [("🫪 Не нравится", "w_fb_nostyle")],
        [(" ", "w_home")],
    ])

def _back_kb():
    return _kb([[(" ", "w_home")]])


async def send_home(bot, cid):
    await bot.send_message(chat_id=cid, text=HOME_TEXT, parse_mode="HTML", reply_markup=home_kb())


# ---------- генерация лука по погоде ----------
async def send_looks(bot, cid):
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👔 Добавить вещи в шкаф", callback_data="set_closet"),
            InlineKeyboardButton("◀️ Назад", callback_data="m_wardrobe"),
        ]])
        await bot.send_message(
            chat_id=cid,
            text=(
                "👔 <b>Шкаф пуст</b>\n\n"
                "Чтобы собрать образ из твоих вещей, сначала добавь их в шкаф.\n\n"
                "<i>Можно написать список вещей прямо в чат или добавить через /setup → Гардероб → Мой шкаф.</i>"
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    s = store.get_settings(cid)
    # Персональный профиль из настроек пользователя
    user_style = _settings.get(cid, "style", "")
    user_body = _settings.get(cid, "body", "")
    style_line = f"Стиль пользователя: {user_style}." if user_style else ""
    body_line = f"Параметры тела: {user_body}." if user_body else ""
    style_block = "\n".join(x for x in [style_line, body_line] if x)
    tmax = None
    try:
        wdata = weather.fetch_weather(s["lat"], s["lon"], 2)
        wd = wdata["daily"]
        tmax = round(wd["temperature_2m_max"][0])
        tmin = round(wd["temperature_2m_min"][0])
        wind_ms = round(wd["windspeed_10m_max"][0])
        rain_prob = wd["precipitation_probability_max"][0] or 0
        rain_mm = (wd.get("precipitation_sum") or [None])[0]
        has_rain = weather._rain_real(rain_prob, rain_mm)
        wctx = (f"Сегодня: +{tmax}°C (ночью +{tmin}°C), ветер {wind_ms} м/с"
                + (", ожидается дождь" if has_rain else ""))
    except Exception:
        wctx = "нет данных"
        has_rain = False
    if tmax is not None and tmax >= 24 and not has_rain:
        temp_rule = (f"tmax={tmax}°C, ЖАРКО — ЗАПРЕЩЕНО: ветровки, флис, куртки, толстовки, слои. "
                     "Только лёгкий верх (футболка/рубашка) + шорты или лёгкие брюки.")
    elif tmax is not None and tmax >= 17:
        temp_rule = (f"tmax={tmax}°C, ТЕПЛО — лёгкие брюки/джинсы + футболка или рубашка. "
                     "Без тяжёлых слоёв и ветровок.")
    else:
        temp_rule = (f"tmax={tmax}°C, ПРОХЛАДНО{'/ дождь' if has_rain else ''} — "
                     "слои уместны, можно ветровку или флис, закрытая обувь.")
    recent = store.recent_looks.get(str(cid), [])
    avoid = ("\nНе повторяй образы за последние 3 дня: " + "; ".join(recent)) if recent else ""
    hints = memory.wardrobe_hints(cid)
    fb_line = ("\nУчитывай прошлый фидбек (НЕ показывай его дословно, просто учти): "
               + secure.wrap_untrusted(hints, "фидбек гардероба")) if hints else ""
    pref_hints = memory.profile_hints(cid)
    pref_line = ("\n" + secure.wrap_untrusted(pref_hints, "предпочтения")) if pref_hints else ""
    await bot.send_message(chat_id=cid, text="Собираю образ под погоду...")
    profile_block = (f"\n{style_block}" if style_block else "")
    prompt = f"""Ты опытный стилист. Собери ОДИН образ из гардероба на сегодня.{profile_block}
Погода: {wctx}
ТЕМПЕРАТУРНОЕ ПРАВИЛО (строго, не нарушать): {temp_rule}{fb_line}{pref_line}
Гардероб пользователя (ТОЛЬКО эти вещи, другие не добавлять):
{wardrobe_text}
Правила: 1 верх + 1 низ + обувь (+ опц. аксессуар-совет). Сочетание по цвету и стилю.
Каждую вещь пиши ПОЛНЫМ названием из списка выше (напр. «Белая футболка Uniqlo», не «Верх: белая»).{avoid}
JSON (без markdown):
{{"intro":"1 строка про погоду и логику образа","items":["вещь 1 полным названием","вещь 2","вещь 3"],"add":"1 совет что добавить (аксессуар) и почему"}}"""
    try:
        d = ai.llm_json(prompt, 700)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    items = d.get("items", [])
    rl = store.recent_looks.get(str(cid), [])
    rl.append(", ".join(items)[:80])
    store.recent_looks[str(cid)] = rl[-3:]
    store.last_look[str(cid)] = ", ".join(str(it) for it in items)[:120]   # для фидбека
    L = ["✨ <b>Новый образ</b>", ""]
    L += [f"• {esc(str(it))}" for it in items]
    if d.get("add"):
        L += ["", "<b>Можно добавить:</b>", esc(d["add"])]
    store.last_source[str(cid)] = "Гардероб · Образ"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_look_result_kb())


# ---------- фидбек по образу ----------
_FB_ACK = {
    "worn": "😍 Отметил: надел. Буду чаще предлагать похожее.",
}

async def look_feedback(bot, cid, verdict):
    look = store.last_look.get(str(cid), "")
    memory.add_wardrobe_feedback(cid, look, verdict)
    if verdict == "nostyle":
        await bot.send_message(chat_id=cid, text="🫪 Понял: не твой стиль. Подбираю другой...")
        await send_looks(bot, cid)
    else:
        await bot.send_message(chat_id=cid, text=_FB_ACK.get(verdict, "Запомнил — учту в следующих образах."))


# ---------- шкаф ----------
ZONES = [
    ("Верх", ["футбол", "рубаш", "свит", "толстов", "худи", "лонгслив", "поло", "верхн", "куртк", "ветровк", "пиджак"]),
    ("Низ", ["джинс", "брюк", "штан", "шорт", "юбк"]),
    ("Обувь", ["обув", "кроссов", "ботин", "кед", "туфл", "сандал"]),
    ("Аксессуары", ["аксессуар", "часы", "кольц", "ремен", "шапк", "кепк", "очк", "шарф", "сумк", "цепоч", "носк", "украшен"]),
]

def _zone_of(category):
    c = category.lower()
    for zone, keys in ZONES:
        if any(k in c for k in keys):
            return zone
    return "Другое"

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
    zone_emoji = {"Верх": "👕", "Низ": "👖", "Обувь": "👟", "Аксессуары": "⌚", "Другое": "🎒"}
    order = ["Верх", "Низ", "Обувь", "Аксессуары", "Другое"]
    lines = ["🗄 <b>Мой шкаф</b>", ""]
    for z in order:
        if grouped.get(z):
            lines.append(f"{zone_emoji.get(z,'•')} <b>{z}</b>")
            lines += [f"   - {esc(it)}" for it in grouped[z]]
            lines.append("")
    await bot.send_message(chat_id=cid, text="\n".join(lines).strip(), parse_mode="HTML", reply_markup=closet_kb())

async def _parse_and_add(bot, cid, text):
    w = store.load_wardrobe(cid)
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, джинсы, обувь, аксессуары"
    parsed = ai.llm_json(
        f"Разбери вещи по категориям. Категории: {cats} (можно создать новую).\n"
        f"Вещи:\n{secure.wrap_untrusted(text, 'список вещей')}\n"
        "Каждую вещь пиши ПОЛНЫМ названием в порядке: тип + цвет + детали/бренд "
        "(напр. «Футболка белая Uniqlo плотная», «Шорты серые тонкие»). Сохраняй бренд если указан.\n"
        'JSON: {"категория": ["полное название вещи"]}.', 700, tier="cheap")
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
    rows = [[InlineKeyboardButton(f"🗑 {it}", callback_data=f"w_delitem_{i}")] for i, (cat, it) in enumerate(flat[:40])]
    rows.append([InlineKeyboardButton("↩ Отмена", callback_data="w_closet")])
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
async def send_improve(bot, cid):
    w = store.load_wardrobe(cid)
    wardrobe_text = store.wardrobe_to_text(w)
    if not wardrobe_text.strip():
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👔 Добавить вещи в шкаф", callback_data="set_closet"),
            InlineKeyboardButton("◀️ Назад", callback_data="m_wardrobe"),
        ]])
        await bot.send_message(
            chat_id=cid,
            text="🧥 <b>Шкаф пуст</b>\n\nДобавь вещи в шкаф — тогда разберу гардероб и дам советы.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    user_style = _settings.get(cid, "style", "")
    style_ctx = f"Стиль пользователя: {user_style}." if user_style else "Стиль не указан — выведи его из гардероба."
    await bot.send_message(chat_id=cid, text="Разбираю шкаф...")
    prompt = f"""Ты стилист с прямым, живым тоном — как умный друг, который шарит в одежде. {style_ctx}
Разбери гардероб (обращайся на "ты", НЕ используй имя):
{wardrobe_text}
Без воды — каждый пункт с одной короткой причиной.
Верни строго валидный JSON (без markdown):
{{"style":"1 строка: стиль и его вайб",
"verdict":"1-2 предложения: честный разбор базы и силуэтов",
"works":["вещь — почему работает"],
"weak":["вещь — почему ломает стиль"],
"replace":["что заменить → на что и какой эффект"],
"accessories":"Casio, кольца, цепь... — аксессуары одной строкой с характером",
"outfit":"Готовый образ из рекомендаций: верх + низ + обувь + акцент"}}"""
    try:
        d = ai.llm_json(prompt, 1000)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    def _bullets(items):
        return [f"• {esc(str(x))}" for x in (items or []) if str(x).strip()]
    L = ["🧥 <b>Улучшить гардероб</b>"]
    if d.get("style"):
        L += ["", f"<b>Стиль:</b> {esc(d['style'])}"]
    if d.get("verdict"):
        L += [f"<b>Вердикт:</b> {esc(d['verdict'])}"]
    if d.get("works"):
        L += ["", "🟢 <b>Работает</b>"] + _bullets(d["works"])
    if d.get("weak"):
        L += ["", "❌ <b>Слабые элементы</b>"] + _bullets(d["weak"])
    if d.get("replace"):
        L += ["", "🛒 <b>Замены</b>"] + _bullets(d["replace"])
    if d.get("accessories"):
        L += ["", f"⌚ <b>Аксессуары</b>\n{esc(d['accessories'])}"]
    if d.get("outfit"):
        L += ["", f"✨ <b>Готовый образ</b>\n{esc(d['outfit'])}"]
    store.last_source[str(cid)] = "Гардероб · Улучшение"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
        reply_markup=_kb([[("⭐ В закладки", "as_fav")], [(" ", "w_home")]]))


async def check_purchase(bot, cid, text):
    w = store.load_wardrobe(cid)
    await bot.send_message(chat_id=cid, text="Оцениваю...")
    web_block = ""
    web_data = research.tavily_snippet(f"{text} отзывы обзор стоит ли покупать", max_chars=900)
    if web_data:
        web_block = (
            "\nАктуальная информация о товаре из сети (используй как дополнительный контекст):\n"
            + secure.wrap_untrusted(web_data, "web") + "\n"
        )
    user_style = _settings.get(cid, "style", "")
    user_body = _settings.get(cid, "body", "")
    style_ctx = f"Стиль: {user_style}. " if user_style else ""
    body_ctx = f"Параметры тела: {user_body}. " if user_body else ""
    prompt = f"""Ты стилист. Пользователь думает купить: {text}
{style_ctx}{body_ctx}{web_block}
Оцени по ЕГО гардеробу (обращайся на "ты", НЕ используй имя):
{store.wardrobe_to_text(w)}
Верни JSON (без markdown):
{{"verdict":"БРАТЬ или НЕ БРАТЬ","why":["2-3 причины, на ты, без имени"],"outro":"1 строка — дерзкий или остроумный итог, с характером, на ты, без имени"}}"""
    try:
        d = ai.llm_json(prompt, 500, tier="cheap")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    verdict = d.get("verdict", "")
    emoji = "✅" if "НЕ" not in verdict.upper() else "⚠️"
    L = [
        f"🔎 <b>Проверка покупки</b>",
        f"<i>{esc(text)}</i>",
        "",
        f"{emoji} <b>Вердикт: {esc(verdict)}</b>",
    ]
    if d.get("why"):
        L += ["", "<b>Почему:</b>"] + [f"• {esc(str(x))}" for x in d["why"]]
    if d.get("outro"):
        L += ["", "<b>Вывод:</b>", esc(d["outro"])]
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
        reply_markup=_kb([[("⭐ В закладки", "as_fav")], [(" ", "w_home")]]))


# ---------- добавление файлом (старый режим, оставлен) ----------
async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


# ---------- роутер кнопок ----------
async def handle_callback(bot, cid, q, data):
    if data == "w_home":
        try:
            await q.message.edit_text(HOME_TEXT, parse_mode="HTML", reply_markup=home_kb())
        except Exception:
            await bot.send_message(chat_id=cid, text=HOME_TEXT, parse_mode="HTML", reply_markup=home_kb())
        return
    if data == "w_look":
        await util.ack_loading(q); await send_looks(bot, cid); return
    if data.startswith("w_fb_"):
        await look_feedback(bot, cid, data[len("w_fb_"):]); return
    if data == "w_closet":
        try:
            await q.message.edit_text("🗄 <b>Мой шкаф</b> - база вещей.", parse_mode="HTML", reply_markup=closet_kb())
        except Exception:
            await bot.send_message(chat_id=cid, text="🗄 <b>Мой шкаф</b> - база вещей.", parse_mode="HTML", reply_markup=closet_kb())
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
        await util.ack_loading(q); await send_improve(bot, cid); return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="Пришли ссылку или название вещи - оценю, брать или нет.",
                               reply_markup=_back_kb()); return