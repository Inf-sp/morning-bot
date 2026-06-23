from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
import weather
from util import esc

HOME_TEXT = (
    "👕 <b>Гардероб</b>\n\n"
    "Одежда без хаоса.\n"
    "Соберу тебе актуальный Образ, разберу шкаф и честно скажу, что с ним не так.\n\n"
    "Выбирай 👇"
)

SCENARIOS = {
    "work": ("👔 Официальная", "официальный выход, деловая встреча"),
    "party": ("🪩 Вечеринка", "вечеринка, выход вечером"),
}


def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    return _kb([
        [("✨ Сгенерировать образ", "w_look")],
        [("💡 Улучшить гардероб", "w_improve")],
        [("🛒 Проверка покупки", "w_check")],
    ])

def closet_kb():
    return _kb([
        [("👁 Показать всё", "w_show")],
        [("🏷 Добавить вещь", "w_add")],
        [("🧹 Удалить вещь", "w_del")],
        [("⬅️ В меню", "w_home")],
    ])

def _look_result_kb():
    return _kb([
        [("✨ Другой образ", "w_look")],
        [("👔 Официальная", "w_scen_work")],
        [("🪩 Вечеринка", "w_scen_party")],
        [("⬅️ Назад", "w_home")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "w_home")]])


async def send_home(bot, cid):
    await bot.send_message(chat_id=cid, text=HOME_TEXT, parse_mode="HTML", reply_markup=home_kb())


# ---------- генерация лука по погоде ----------
async def send_looks(bot, cid, scenario=None):
    w = store.load_wardrobe()
    s = store.get_settings(cid)
    try:
        wblock = weather.weather_block(weather.fetch_weather(s["lat"], s["lon"], 2), 0, s["city"])
    except Exception:
        wblock = "нет данных"
    recent = store.recent_looks.get(str(cid), [])
    avoid = ("\nНе повторяй образы за последние 3 дня: " + "; ".join(recent)) if recent else ""
    scen_line = ""
    if scenario and scenario in SCENARIOS:
        scen_line = f"\nСценарий: {SCENARIOS[scenario][1]}. Подбери образ под этот случай."
    await bot.send_message(chat_id=cid, text="Собираю образ под погоду...")
    prompt = f"""Ты опытный стилист. Собери ОДИН образ из гардероба на сегодня.
{config.STYLE_PROFILE}
Погода сегодня: {wblock}{scen_line}
Гардероб (только эти вещи, ПОЛНЫЕ точные названия с брендом и цветом):
{store.wardrobe_to_text(w)}
Правила: 1 верх + 1 низ + обувь (+ опц. аксессуар-совет). Минимализм, сочетание по цвету.
Жёстко по температуре: от +24°C без дождя - ШОРТЫ + футболка; +17..+23 - лёгкие брюки/джинсы + футболка/рубашка; ниже +16 или дождь/ветер - слои, ветровка/флис, закрытая обувь.
Каждую вещь пиши ПОЛНЫМ названием (напр. «Белая футболка Uniqlo», не «Верх: белая»).{avoid}
JSON (без markdown):
{{"intro":"1 строка про погоду и логику образа","items":["вещь 1 полным названием","вещь 2","вещь 3"],"add":"1 совет что добавить (аксессуар) и почему"}}"""
    try:
        d = ai.llm_json(prompt, 700)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    items = d.get("items", [])
    rl = store.recent_looks.get(str(cid), [])
    rl.append(", ".join(items)[:80])
    store.recent_looks[str(cid)] = rl[-3:]
    L = ["✨ <b>Новый образ</b>", ""]
    L += [f"• {esc(str(it))}" for it in items]
    if d.get("add"):
        L += ["", "⚡ <b>Можно добавить:</b>", esc(d["add"])]
    store.last_source[str(cid)] = "Гардероб · Образ"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_look_result_kb())


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
    w = store.load_wardrobe()
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

async def add_item(bot, cid, text):
    w = store.load_wardrobe()
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, джинсы, обувь, аксессуары"
    try:
        parsed = ai.llm_json(
            f"Разбери вещи по категориям. Категории: {cats} (можно создать новую).\nВещи:\n{text}\n"
            "Каждую вещь пиши ПОЛНЫМ названием в порядке: тип + цвет + детали/бренд "
            "(напр. «Футболка белая Uniqlo плотная», «Шорты серые тонкие»). Сохраняй бренд если указан.\n"
            'JSON: {"категория": ["полное название вещи"]}.', 700)
        added = store.merge_wardrobe(parsed)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    await bot.send_message(chat_id=cid, text=f"Добавлено в шкаф ({added}).", reply_markup=closet_kb())

async def send_del(bot, cid):
    w = store.load_wardrobe()
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
    w = store.load_wardrobe()
    if cat in w and it in w[cat]:
        w[cat].remove(it)
        if not w[cat]:
            del w[cat]
        store.save_wardrobe(w)
    await bot.send_message(chat_id=cid, text="Удалено. Шкаф стал легче.")
    await send_del(bot, cid)


# ---------- улучшить гардероб ----------
async def send_improve(bot, cid):
    w = store.load_wardrobe()
    await bot.send_message(chat_id=cid, text="Разбираю шкаф...")
    prompt = f"""Ты стилист с прямым тоном. {config.STYLE_PROFILE}
Разбери ТВОЙ гардероб пользователя (обращайся на "ты", НЕ используй имя):
{store.wardrobe_to_text(w)}
Сжато, для СДВГ, без воды. Верни JSON (без markdown):
{{"style":"1 строка: какой стиль","verdict":"1 строка вердикт по базе и силуэтам",
"remove":["что убрать, ломает стиль, 1-3 пункта"],
"replace":["на что заменить, 1-3 пункта"],
"texture":"1-2 строки: как через фактуру/ткани сделать интереснее без ярких принтов",
"accessory":"1-2 строки: какие строгие аксессуары и многослойность добавить"}}"""
    try:
        d = ai.llm_json(prompt, 800)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    L = ["💡 <b>Разбор гардероба</b>", ""]
    if d.get("style"):
        L.append(f"<b>Стиль:</b> {esc(d['style'])}")
    if d.get("verdict"):
        L.append(f"<b>Вердикт:</b> {esc(d['verdict'])}")
    if d.get("remove"):
        L += ["", "❌ <b>Что убрать:</b>"] + [esc(str(x)) for x in d["remove"]]
    if d.get("replace"):
        L += ["", "✅ <b>На что заменить:</b>"] + [esc(str(x)) for x in d["replace"]]
    if d.get("texture"):
        L += ["", "<b>Фактура вместо принтов:</b>", esc(d["texture"])]
    if d.get("accessory"):
        L += ["", "<b>Аксессуары:</b>", esc(d["accessory"])]
    store.last_source[str(cid)] = "Гардероб · Улучшение"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
        reply_markup=_kb([[("⭐ В закладки", "as_fav")], [("⬅️ Назад", "w_home")]]))


async def check_purchase(bot, cid, text):
    w = store.load_wardrobe()
    await bot.send_message(chat_id=cid, text="Оцениваю...")
    prompt = f"""Ты стилист. Пользователь думает купить: {text}
{config.STYLE_PROFILE}
Оцени по ЕГО гардеробу (обращайся на "ты", НЕ используй имя):
{store.wardrobe_to_text(w)}
Верни JSON (без markdown):
{{"verdict":"БРАТЬ или НЕ БРАТЬ","why":["2-3 причины, на ты, без имени"],"outro":"1 строка итог, на ты, без имени"}}"""
    try:
        d = ai.llm_json(prompt, 500)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    verdict = d.get("verdict", "")
    emoji = "✅" if "НЕ" not in verdict.upper() else "⚠️"
    L = ["🛒 <b>Модный приговор</b>", "", f"{emoji} <b>Вердикт: {esc(verdict)}</b>"]
    if d.get("why"):
        L += ["", "<b>Почему:</b>"] + [f"• {esc(str(x))}" for x in d["why"]]
    if d.get("outro"):
        L += ["", "<b>Вывод:</b>", esc(d["outro"])]
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
        reply_markup=_kb([[("⭐ В закладки", "as_fav")], [("⬅️ Назад", "w_home")]]))


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
        await send_looks(bot, cid); return
    if data.startswith("w_scen_"):
        await send_looks(bot, cid, data[len("w_scen_"):]); return
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
        await send_del(bot, cid); return
    if data.startswith("w_delitem_"):
        await del_item(bot, cid, int(data.split("_")[-1])); return
    if data == "w_improve":
        await send_improve(bot, cid); return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="Пришли ссылку или название вещи - оценю, брать или нет.",
                               reply_markup=_back_kb()); return