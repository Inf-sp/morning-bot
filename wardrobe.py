from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
from util import send_long, esc

HOME_TEXT = (
    "👕 <b>Гардероб</b>\n\n"
    "Одежда без хаоса.\n"
    "Соберу лук, разберу шкаф и скажу честно - что с ним не так.\n\n"
    "Выбирай 👇"
)

SCENARIOS = {
    "walk": ("🚶 Прогулка", "прогулка по городу, кофе"),
    "work": ("💼 Работа / учёба", "работа или учёба"),
    "party": ("🎉 Вечеринка", "вечеринка, выход вечером"),
}


def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    return _kb([
        [("✨ Сгенерировать лук", "w_look")],
        [("💡 Улучшить гардероб", "w_improve")],
        [("🛒 Проверка перед покупкой", "w_check")],
        [("⬅️ Назад", "m_close")],
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
        [("🔄 Ещё 3 варианта", "w_look")],
        [("⭐ Добавить в избранное", "as_fav")],
        [("⬅️ Назад", "w_home")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "w_home")]])


async def send_home(bot, cid):
    await bot.send_message(chat_id=cid, text=HOME_TEXT, parse_mode="HTML", reply_markup=home_kb())


# ---------- используется в «Мой день» (НЕ удалять) ----------
def build_outfit_focus(weather_text, day_label):
    w = store.load_wardrobe()
    prompt = f"""Ты опытный стилист. Собери ОДИН целостный лук на сегодня.
{config.STYLE_PROFILE}
Погода ({day_label}):
{weather_text}
Гардероб (используй ТОЛЬКО эти вещи, точные названия):
{store.wardrobe_to_text(w)}
Температурные зоны:{config.TEMP_ZONES}
Жёсткие правила по температуре:
- от +24°C и без дождя: ШОРТЫ + футболка, лёгкая обувь. Никаких брюк, ветровок, флиса.
- +17…+23°C: лёгкие брюки/джинсы + футболка/рубашка, опц. лёгкий слой.
- ниже +16°C или дождь/сильный ветер: слои, ветровка/флис, закрытая обувь.
Обязательно 1 верх + 1 низ + обувь. Сочетание по цвету, минимализм. Без обращения по имени.
JSON:
{{"outfit": ["верх","низ","обувь","аксессуар"], "why": "1-2 предложения", "focus": "один короткий совет"}}"""
    return ai.llm_json(prompt, 800)


# ---------- генерация лука (3 варианта, по погоде) ----------
async def send_looks(bot, cid):
    w = store.load_wardrobe()
    s = store.get_settings(cid)
    try:
        wblock = weather.weather_block(weather.fetch_weather(s["lat"], s["lon"], 1), 0, s["city"])
    except Exception:
        wblock = "нет данных"
    recent = store.recent_looks.get(str(cid), [])
    avoid = ("\nНе повторяй недавние луки: " + "; ".join(recent)) if recent else ""
    await bot.send_message(chat_id=cid, text="Собираю 3 лука под погоду...")
    prompt = f"""Ты опытный стилист. Собери 3 РАЗНЫХ лука из гардероба на сегодня.
{config.STYLE_PROFILE}
Погода сегодня: {wblock}
Гардероб (только эти вещи, точные названия):
{store.wardrobe_to_text(w)}
Правила: каждый лук - 1 верх + 1 низ + обувь (+ опц. аксессуар). Стиль минимализм, сочетание по цвету.
Жёстко по температуре: от +24°C без дождя - ШОРТЫ + футболка; +17..+23 - лёгкие брюки/джинсы + футболка/рубашка; ниже +16 или дождь/ветер - слои, ветровка/флис, закрытая обувь. Без обращения по имени.{avoid}
Ответь СТРОГО, без markdown:
1) {{название}} — Верх: .. • Низ: .. • Обувь: .. • Акс: ..
2) {{название}} — Верх: .. • Низ: .. • Обувь: .. • Акс: ..
3) {{название}} — Верх: .. • Низ: .. • Обувь: .. • Акс: .."""
    try:
        out = ai.llm(prompt, 700, 0.9)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    rl = store.recent_looks.get(str(cid), [])
    rl.append(out.split("\n")[0])
    store.recent_looks[str(cid)] = rl[-3:]
    store.last_source[str(cid)] = "Гардероб · Лук"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"👕 <b>3 варианта лука</b>\n\n{esc(out)}",
                           parse_mode="HTML", reply_markup=_look_result_kb())


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
            'JSON: {"категория": ["вещь"]}. Названия короткие, нижний регистр.', 700)
        added = store.merge_wardrobe(parsed)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    await bot.send_message(chat_id=cid, text=f"Добавлено в шкаф ({added}).", reply_markup=closet_kb())

async def send_del(bot, cid):
    w = store.load_wardrobe()
    flat = []
    for cat, items in w.items():
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
    prompt = f"""Ты стилист с прямым, дерзким, но добрым тоном.
{config.STYLE_PROFILE}
Гардероб:
{store.wardrobe_to_text(w)}
Коротко (макс 6-8 строк): где перекос/дубли, какие зоны слабые, чего не хватает из баз.
Добавь 1-2 смешных вердикта про конкретные вещи (в духе «держится дольше некоторых отношений»). Без markdown."""
    try:
        out = ai.llm(prompt, 700, 0.9)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_source[str(cid)] = "Гардероб · Улучшение"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"💡 <b>Улучшить гардероб</b>\n\n{esc(out)}", parse_mode="HTML",
        reply_markup=_kb([[("⭐ Добавить в избранное", "as_fav")], [("⬅️ Назад", "w_home")]]))


# ---------- проверка перед покупкой ----------
async def check_purchase(bot, cid, text):
    w = store.load_wardrobe()
    await bot.send_message(chat_id=cid, text="Оцениваю...")
    prompt = f"""Ты стилист. Пользователь думает купить: {text}
{config.STYLE_PROFILE}
Его гардероб:
{store.wardrobe_to_text(w)}
Оцени КРАТКО, строго в формате (без markdown):
⚖️ Вердикт: БРАТЬ / НЕ БРАТЬ
💬 Причина: {{1 строка}}
⚠️ Конфликт: {{с чем не сочетается или «нет»}}
💡 С чем носить: {{1-2 вещи из гардероба}}"""
    try:
        out = ai.llm(prompt, 500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"🛒 <b>Проверка покупки</b>\n\n{esc(out)}", parse_mode="HTML",
        reply_markup=_kb([[("⭐ Добавить в избранное", "as_fav")], [("⬅️ Назад", "w_home")]]))


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
        await bot.send_message(chat_id=cid, text="🏷 Напиши вещь (можно с категорией), например: «бежевая футболка Uniqlo».",
                               reply_markup=_back_kb()); return
    if data == "w_del":
        await send_del(bot, cid); return
    if data.startswith("w_delitem_"):
        await del_item(bot, cid, int(data.split("_")[-1])); return
    if data == "w_improve":
        await send_improve(bot, cid); return
    if data == "w_check":
        store.pending_input[str(cid)] = "wardrobe_check"
        await bot.send_message(chat_id=cid, text="🛒 Пришли ссылку или название вещи - оценю, брать или нет.",
                               reply_markup=_back_kb()); return