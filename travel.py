from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
from util import send_long, country_flag, esc

def travel_suggest_one(cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    disliked = store.get_list("travel_dislike.json", cid)
    skip = ", ".join([str(x) for x in visited] + [str(x) for x in disliked])
    prompt = f"""Уже был/не интересно: {skip}.
Любит интеллектуальную атмосферу, города с характером, природу; путешествия важнее вещей.
Предложи РОВНО 1 НОВУЮ страну (где не был). Верни JSON:
{{"flag":"эмодзи флага","country":"страна","about":"2 строки о стране",
 "capital":"столица","biggest":"крупнейший город","langs":"официальные языки",
 "known":["3-4 пункта чем известна"],"politics":"1-2 строки про политику/устройство",
 "facts":["3 факта"]}}"""
    return ai.llm_json(prompt, 900)

def _country_card(d):
    L = [f"{d.get('flag','')} <b>{esc(d.get('country',''))}</b>", ""]
    if d.get("about"):
        L += ["🏔 <b>О стране</b>", esc(d["about"]), ""]
    if d.get("capital") or d.get("biggest"):
        L += ["🏙 <b>Города</b>"]
        if d.get("capital"): L.append(f"Столица - {esc(d['capital'])}")
        if d.get("biggest"): L.append(f"Крупнейший город - {esc(d['biggest'])}")
        L.append("")
    if d.get("langs"):
        L += ["🗣 <b>Языки</b>", esc(d["langs"]), ""]
    if d.get("known"):
        L += ["⏱ <b>Чем известна</b>"] + [f"• {esc(k)}" for k in d["known"]] + [""]
    if d.get("politics"):
        L += ["🏛 <b>Политика</b>", esc(d["politics"]), ""]
    if d.get("facts"):
        L += ["📊 <b>Факты</b>"] + [f"• {esc(f)}" for f in d["facts"]]
    return "\n".join(L).strip()

async def send_go(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю страну...")
    try:
        d = travel_suggest_one(cid)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", _country_card(d))
    store.last_source[str(cid)] = "Путешествия"
    store.suggested_countries[str(cid)] = d.get("country", "")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data="trav_no")],
        [InlineKeyboardButton("⭐ Сохранить в избранное", callback_data="as_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel")],
    ])
    await bot.send_message(chat_id=cid, text=_country_card(d), parse_mode="HTML", reply_markup=kb)

async def travel_dislike(bot, cid):
    c = store.suggested_countries.get(str(cid))
    if c:
        store.add_to_list("travel_dislike.json", cid, c)
    await send_go(bot, cid)

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