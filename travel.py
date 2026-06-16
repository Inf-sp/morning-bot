from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import send_long, country_flag

def travel_suggest_data():
    prompt = f"""Дмитрий уже был: {config.VISITED}.
Любит интеллектуальную атмосферу, города с характером, природу, путешествия важнее вещей.
Предложи 4-5 НОВЫХ направлений (где не был).
JSON: {{"items": [{{"flag": "эмодзи флага", "country": "страна или город", "why": "1 строка почему ему зайдёт"}}]}}"""
    return ai.llm_json(prompt, 900)

def country_facts(country):
    return ai.llm(f"10 интересных фактов про {country}. Коротко, по пунктам, без markdown. Заголовок: 📍 {country}.", 900, 0.7)

async def send_go(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю направления...")
    data = travel_suggest_data()
    items = data.get("items", [])
    store.suggested_countries[str(cid)] = [it.get("country", "") for it in items]
    lines = ["🗺 Куда поехать", ""]
    for it in items:
        lines.append(f"{it.get('flag','')} {it.get('country','')} - {it.get('why','')}")
    rows = [[InlineKeyboardButton(f"📍 10 фактов: {it.get('country','')}", callback_data=f"facts_{i}")]
            for i, it in enumerate(items)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_travel")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def send_facts(bot, cid, i):
    countries = store.suggested_countries.get(str(cid), [])
    if i < len(countries):
        await bot.send_message(chat_id=cid, text="Собираю факты...")
        await send_long(bot, cid, country_facts(countries[i]))

async def send_my(bot, cid):
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    out = ["🏳 Любимые страны:"]
    rows = []
    if favs:
        for i, c in enumerate(favs):
            out.append(f"{c.get('flag','🏳')} {c.get('name','')}")
            rows.append([InlineKeyboardButton(f"❌ {c.get('name','')}", callback_data=f"delcountry_{i}")])
    else:
        out.append("пусто")
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_travel")])
    store.pending_input[str(cid)] = "favcountry"
    await bot.send_message(chat_id=cid, text="\n".join(out) + "\n\n➕ Напиши страну - добавлю.",
                           reply_markup=InlineKeyboardMarkup(rows))

async def del_country(bot, cid, i):
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    if i < len(favs):
        removed = favs.pop(i)
        store.set_list(config.FAVCOUNTRIES_KEY, cid, favs)
        await bot.send_message(chat_id=cid, text=f"Удалил: {removed.get('name','')}")

async def add_country(bot, cid, text):
    flag = country_flag(text.strip())
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    favs.append({"name": text.strip(), "flag": flag})
    store.set_list(config.FAVCOUNTRIES_KEY, cid, favs)
    await bot.send_message(chat_id=cid, text=f"Добавил: {flag} {text.strip()}")
