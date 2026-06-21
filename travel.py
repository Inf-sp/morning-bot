from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
from util import send_long, country_flag, esc

def travel_suggest_one(cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)            # Мои страны (был/посещённые)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)           # закладки
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list("travel_dislike.json", cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked])
    prompt = f"""Уже был / в закладках / не интересно (НЕ предлагай это): {skip}.
Профиль: любит интеллектуальную атмосферу, города с характером, природу; путешествия важнее вещей.
Предложи РОВНО 1 НОВУЮ страну (не из списка выше). Компактно. Верни JSON:
{{"flag":"эмодзи флага","country":"страна",
 "about":"1-2 строки образно о стране",
 "for_what":"ради чего ехать, 1 строка",
 "langs":"язык(и) + говорят ли на английском",
 "note":"главный нюанс/предупреждение, 1 строка"}}"""
    return ai.llm_json(prompt, 700)

def _country_card(d):
    L = [f"{d.get('flag','')} <b>{esc(d.get('country',''))}</b>", ""]
    if d.get("about"):
        L += [esc(d["about"]), ""]
    if d.get("for_what"):
        L += [f"🎯 <b>Ради чего ехать:</b> {esc(d['for_what'])}", ""]
    if d.get("langs"):
        L += [f"🗣️ <b>Язык:</b> {esc(d['langs'])}", ""]
    if d.get("note"):
        L += [f"⚠️ <b>Главный нюанс:</b> {esc(d['note'])}"]
    return "\n".join(L).strip()

def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧳 Собрать план поездки", callback_data="trav_plan")],
        [InlineKeyboardButton("😕 Не нравится", callback_data="trav_no")],
        [InlineKeyboardButton("⭐ Добавить в закладки", callback_data="trav_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def send_go(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю страну...")
    try:
        d = travel_suggest_one(cid)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", _country_card(d))
    store.last_source[str(cid)] = "Путешествия"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d  # переиспользуем слот для данных страны (для плана поездки)
    await bot.send_message(chat_id=cid, text=_country_card(d), parse_mode="HTML", reply_markup=_travel_kb())

async def travel_dislike(bot, cid):
    c = store.suggested_countries.get(str(cid))
    if c:
        store.add_to_list("travel_dislike.json", cid, c)
    await send_go(bot, cid)

async def travel_fav(bot, cid):
    """Сохранить предложенную страну в закладки и сразу показать следующую."""
    c = store.suggested_countries.get(str(cid))
    if c:
        d = store.last_recipe.get(str(cid)) or {}
        flag = d.get("flag") or country_flag(c)
        favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
        favs.append({"name": c, "flag": flag})
        store.set_list(config.FAVCOUNTRIES_KEY, cid, favs)
        await bot.send_message(chat_id=cid, text=f"⭐ В закладках: {flag} {c}")
    await send_go(bot, cid)

async def send_plan(bot, cid):
    """Подробный план поездки по текущей предложенной стране."""
    d = store.last_recipe.get(str(cid)) or {}
    country = d.get("country") or store.suggested_countries.get(str(cid), "")
    if not country:
        await bot.send_message(chat_id=cid, text="Сначала выбери страну в Путешествиях."); return
    s = store.get_settings(cid)
    home = s.get("city", "дом")
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list("travel_dislike.json", cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked] + [country])
    await bot.send_message(chat_id=cid, text="Собираю план поездки...")
    prompt = f"""Подробный план поездки в страну/направление: {country}. Вылет из: {home}.
Профиль: ценит атмосферу, природу, города с характером; путешествия важнее вещей.
Дай JSON (компактно, по делу, на русском):
{{"flag":"эмодзи","title":"страна/регион","about":"1-2 строки",
 "why":["3 пункта почему подойдёт"],
 "best_time":"лучшее время + темп. диапазон, 1-2 строки",
 "budget":["перелёт туда-обратно ориентир из {home}","эконом в день","комфорт в день"],
 "spots":["3 места не пропустить с короткой пометкой"],
 "lgbt":"1 строка про дружелюбность/безопасность",
 "fact":"1 интересный местный факт",
 "next":["2 следующих направления (НЕ из: {skip}) с эмодзи флага"]}}"""
    try:
        p = ai.llm_json(prompt, 1100)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    L = [f"{p.get('flag','')} <b>{esc(p.get('title', country))}</b>"]
    if p.get("about"):
        L += ["", esc(p["about"])]
    if p.get("why"):
        L += ["", "🎯 <b>Почему тебе подойдёт</b>"] + [f"• {esc(str(w))}" for w in p["why"]]
    if p.get("best_time"):
        L += ["", "📅 <b>Лучшее время</b>", esc(p["best_time"])]
    if p.get("budget"):
        L += ["", "💰 <b>Бюджет</b>"] + [f"• {esc(str(b))}" for b in p["budget"]]
    if p.get("spots"):
        L += ["", "📸 <b>Не пропусти</b>"] + [f"• {esc(str(sp))}" for sp in p["spots"]]
    if p.get("lgbt"):
        L += ["", "🏳️‍🌈 <b>LGBTQ+</b>", esc(p["lgbt"])]
    if p.get("fact"):
        L += ["", "🍲 <b>Интересный факт</b>", esc(p["fact"])]
    if p.get("next"):
        nxt = " → ".join(esc(str(n)) for n in p["next"])
        L += ["", "🎯 <b>Что дальше?</b>", f"Твой следующий маршрут: {nxt}"]
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    store.last_source[str(cid)] = "Путешествия · План"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Добавить в закладки", callback_data="trav_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

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