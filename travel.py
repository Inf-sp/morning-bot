"""Раздел «Путешествия»: подбор новой страны/направления под вкус пользователя
(LLM + проверенные факты через research.py) и сборка подробного плана поездки.

Вынесен из leisure.py в самостоятельный раздел главного меню (был подпунктом Досуга).
"""
import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import ui_label

import ai
import config
import research
import store
import util
import verify
from ui import travel as travel_ui
from util import country_flag

_log = logging.getLogger(__name__)


def _home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Подобрать страну", callback_data="a_trav_go")],
        [InlineKeyboardButton("🎚️ Настройки стран", callback_data="as_love_countries")],
    ])


async def send_home(bot, cid, q=None):
    """Приветственный экран раздела «Путешествия» (тот же паттерн, что у Гардероба/Кино):
    сколько стран посещено/в любимых/в планах, снизу — вход в подбор новой страны."""
    visited_count = len(store.get_list(config.COUNTRIES_KEY, cid))
    fav_count = len(store.get_list(config.FAVCOUNTRIES_KEY, cid))
    plan_count = len(_plan_countries(cid))
    msg = travel_ui.home_screen(visited_count, fav_count, plan_count)
    kb = _home_kb()
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _plan_countries(cid):
    """Страны из уже сохранённых планов поездок (вкладка «Планы»)."""
    notes = store.get_list(config.NOTES_KEY, cid)
    return [n.get("country", "") for n in notes
            if isinstance(n, dict) and n.get("bucket") == "plan" and n.get("country")]


def travel_suggest_one(cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    plans = _plan_countries(cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked] + plans)
    prompt = f"""Уже был / в закладках / не интересно (СТРОГО НЕ предлагай ничего из этого списка): {skip}.
Профиль: любит интеллектуальную атмосферу, города с характером, природу; путешествия важнее вещей.
Предложи РОВНО 1 НОВУЮ страну, которой ТОЧНО НЕТ в списке выше. Перепроверь, что её нет в списке. Компактно. Верни JSON:
{{"flag":"эмодзи флага","country":"страна",
 "about":"1-2 строки образно о стране",
 "for_what":"ради чего ехать, 1 строка",
 "langs":"язык(и) + говорят ли на английском",
 "note":"главный нюанс/предупреждение, 1 строка"}}"""
    return ai.llm_json(prompt, 700, tier="leisure")


def _country_card(d):
    return travel_ui.country_card(d)


def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ui_label("routes", "Собрать маршрут"), callback_data="a_trav_plan")],
        [InlineKeyboardButton("❤️ В любимые", callback_data="a_trav_fav"),
         InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton("🎚️ Настройки стран", callback_data="as_love_countries")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel")],
    ])


async def send_go(bot, cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    plans = _plan_countries(cid)
    skip_set = {str(x).strip().lower() for x in (list(visited) + fav_names + list(disliked) + plans) if str(x).strip()}
    d = None
    try:
        for _ in range(3):
            cand = travel_suggest_one(cid)
            cname = (cand.get("country") or "").strip().lower()
            if cname and cname not in skip_set:
                d = cand
                break
        if d is None:
            d = cand
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    facts = await asyncio.to_thread(research.country_facts, d.get("country", ""))
    if facts.get("cc"):
        d["flag"] = util.flag_from_cc(facts["cc"]) or d.get("flag", "")
    if facts.get("languages"):
        d["langs"] = ", ".join(facts["languages"][:3])
    rfact = await asyncio.to_thread(research.wiki_fact, d.get("country", ""))
    if rfact:
        d["fact"] = rfact
    if not research.grounded(facts):
        print("[research] travel: no grounding for", d.get("country", ""))
    country_msg = _country_card(d)
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", country_msg.text)
    store.last_source[str(cid)] = "Путешествия"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d
    await bot.send_message(chat_id=cid, text=country_msg.text, entities=country_msg.entities, reply_markup=_travel_kb())


async def travel_dislike(bot, cid):
    c = store.suggested_countries.get(str(cid))
    existing = {str(x).strip().lower() for x in store.get_list(config.TRAVEL_DISLIKE_KEY, cid)}
    if c and c.strip().lower() not in existing:
        store.add_to_list(config.TRAVEL_DISLIKE_KEY, cid, c)
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
        await bot.send_message(chat_id=cid, text=f"❤️ В любимых (Мои страны): {flag} {c}")
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
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked] + [country])
    facts = await asyncio.to_thread(research.country_facts, country)
    fblock = research.facts_block(facts)
    rfact = await asyncio.to_thread(research.wiki_fact, country)
    if not research.grounded(facts):
        _log.warning("[research] travel-plan: no grounding for %s", country)
    ground_line = (f"Проверенные факты (ИСТОЧНИК ИСТИНЫ для столицы/языка/региона/валюты, "
                   f"не противоречь им): {fblock}.\n" if fblock else "")
    web_data = await asyncio.to_thread(
        research.tavily_snippet,
        f"{country} туризм путешествие достопримечательности 2025",
        900,
    )
    web_line = (f"Актуальная туристическая информация из сети (используй как дополнение к фактам):\n{web_data}\n"
                if web_data else "")
    prompt = f"""Подробный план поездки в страну/направление: {country}. Вылет из: {home}.
{ground_line}{web_line}Профиль: ценит атмосферу, природу, города с характером; путешествия важнее вещей.
Бюджет и сроки — это ОРИЕНТИР/оценка (так и помечай), фактические данные бери из проверенных фактов, не выдумывай.
Дай JSON (компактно, по делу, на русском):
{{"flag":"эмодзи","title":"страна/регион","about":"1-2 строки",
 "why":["3 пункта почему подойдёт"],
 "best_time":"лучшее время + темп. диапазон (ориентир), 1-2 строки",
 "budget":["перелёт туда-обратно ориентир из {home}","эконом в день","комфорт в день"],
 "spots":["3 места не пропустить с короткой пометкой"],
 "lgbt":"1 строка про дружелюбность/безопасность",
 "fact":"1 интересный местный факт"}}"""
    try:
        p = await ai.allm_json(prompt, 1100, tier="leisure", module="travel")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    if facts.get("cc"):
        p["flag"] = util.flag_from_cc(facts["cc"]) or p.get("flag", "")
    if rfact:
        p["fact"] = rfact
    msg = travel_ui.travel_plan(p, country)
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Путешествия · План"
    store.last_recipe[str(cid)] = {
        **(store.last_recipe.get(str(cid)) or {}),
        "plan_text": msg.text, "plan_entities": util.entities_to_json(msg.entities),
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton(ui_label("save", "Сохранить маршрут"), callback_data="a_trav_save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def save_plan(bot, cid):
    from datetime import datetime
    d = store.last_recipe.get(str(cid)) or {}
    plan = d.get("plan_text", "")
    country = d.get("country") or store.suggested_countries.get(str(cid), "план")
    if not plan:
        await bot.send_message(chat_id=cid, text="Сначала собери план поездки."); return
    store.add_to_list(config.NOTES_KEY, cid, {
        "date": datetime.now(config.TZ).strftime("%d.%m"),
        "text": plan, "entities": d.get("plan_entities", []),
        "source": "План поездки", "bucket": "plan", "country": country,
    })
    await bot.send_message(chat_id=cid, text=f"{ui_label('save', 'Маршрут')} ({country}) сохранён в «Мои данные» → «Путешествия».")
    await send_go(bot, cid)
