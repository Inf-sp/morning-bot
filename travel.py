"""Поездки: актуальная идея маршрута, рекомендации и посещённые страны."""
import asyncio
import logging
import re
import threading
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import recommendation_stoplist
import research
import settings
import store
import util
import verify
from ui import travel as travel_ui
from ui.constants import choose_label, delete_label, save_toggle_label

_log = logging.getLogger(__name__)
_COUNTRIES_PER_PAGE = 10
_CARD_CONTENT_VERSION = 1
_CARD_LOCKS = {}
_CARD_LOCKS_GUARD = threading.Lock()
_IDEA_LOCKS = {}
_IDEA_LOCKS_GUARD = threading.Lock()

_TRANSPORTS = (
    ("bike", "🚴🏻‍♂️", "Велосипед", "На велосипеде"),
    ("bus", "🚌", "Автобус", "На автобусе"),
    ("train", "🚆", "Поезд", "На поезде"),
    ("plane", "✈️", "Самолёт", "На самолёте"),
    ("ferry", "⛴️", "Паром", "На пароме"),
)
_TRANSPORT_BY_KEY = {row[0]: row for row in _TRANSPORTS}
_LANG_RU = {
    "Dutch": "нидерландский", "English": "английский", "German": "немецкий",
    "French": "французский", "Italian": "итальянский", "Romansh": "ретороманский",
    "Spanish": "испанский", "Portuguese": "португальский", "Polish": "польский",
    "Danish": "датский", "Swedish": "шведский", "Japanese": "японский",
}
_CURRENCY_RU = {
    "EUR": "евро · EUR", "CHF": "швейцарский франк · CHF", "GBP": "фунт стерлингов · GBP",
    "USD": "доллар США · USD", "CAD": "канадский доллар · CAD", "JPY": "японская иена · JPY",
    "PLN": "польский злотый · PLN", "DKK": "датская крона · DKK", "SEK": "шведская крона · SEK",
}


def selected_transports(cid):
    raw = settings.get(cid, "travel_transports", ["bike", "train"])
    if not isinstance(raw, list):
        raw = []
    valid = [key for key in raw if key in _TRANSPORT_BY_KEY]
    return valid or ["train"]


def _transport_context(cid):
    return ", ".join(_TRANSPORT_BY_KEY[key][2] for key in selected_transports(cid))


def _fallback_idea(cid):
    city = store.get_settings(cid).get("city") or "Алкмар"
    key = selected_transports(cid)[0]
    emoji, _label, title = _TRANSPORT_BY_KEY[key][1:]
    targets = {
        "bike": ("Берген", ["Велосипед · около 40 минут в одну сторону", "Старый центр и дюны", "Обратно до вечера"]),
        "bus": ("Харлем", ["Автобус · без долгих пересадок", "Центр и прогулка у каналов", "Возвращение вечером"]),
        "train": ("Лейден", ["Поезд · удобный дневной маршрут", "Старый центр и каналы", "Возвращение до вечера"]),
        "plane": ("Копенгаген", ["Самолёт · основной транспорт", "Прогулка по центру", "Заложи время на аэропорт"]),
        "ferry": ("Тексел", ["Паром · короткая переправа", "Дюны и побережье", "Проверь последний рейс обратно"]),
    }
    target, route = targets[key]
    return {"emoji": emoji, "transport": key, "transport_title": title, "from": city, "to": target,
            "intro": "Недалеко, красиво и без перегруженного плана.", "route": route,
            "tip": "проверь расписание перед выходом и оставь запас на обратную дорогу."}


def _generate_home_idea(cid):
    city = store.get_settings(cid).get("city") or "Алкмар"
    modes = selected_transports(cid)
    previous_entry = (store._load(config.TRAVEL_IDEA_KEY) or {}).get(str(cid), {})
    previous = previous_entry.get("idea", previous_entry) if isinstance(previous_entry, dict) else {}
    prompt = f"""Предложи одну реалистичную поездку на сегодня из города {city}.
Разрешённый транспорт: {_transport_context(cid)}. Используй его; иной транспорт только как необходимый резерв.
Можно предложить ближайший город, деревню, природный маршрут или близкую зарубежную поездку.
Не повторяй прошлое направление: {previous.get('to', '')}.
Верни короткий JSON: {{"transport":"одно из {modes}","to":"место","intro":"1 предложение",
"route":["ровно 3 практичных пункта"],"tip":"короткий полезный совет"}}.
Не используй знак =, только стрелку → там, где нужна связь."""
    try:
        raw = ai.llm_json(prompt, 650, tier="leisure", module="travel")
    except Exception as exc:
        _log.warning("travel home idea failed: %r", exc)
        return _fallback_idea(cid)
    key = raw.get("transport") if isinstance(raw, dict) else ""
    if key not in modes:
        key = modes[0]
    emoji, _label, title = _TRANSPORT_BY_KEY[key][1:]
    route = [str(x).replace(" = ", " → ") for x in (raw.get("route") or [])[:3]]
    if len(route) < 3 or not raw.get("to"):
        return _fallback_idea(cid)
    return {"emoji": emoji, "transport": key, "transport_title": title, "from": city,
            "to": str(raw["to"]), "intro": str(raw.get("intro") or "Подходит для короткой поездки на день."),
            "route": route,
            "tip": str(raw.get("tip") or "проверь расписание перед выходом.")}


def _idea_lock(cid):
    key = str(cid)
    with _IDEA_LOCKS_GUARD:
        return _IDEA_LOCKS.setdefault(key, threading.Lock())


def _home_idea(cid):
    """Одна идея на локальные сутки; город или транспорт меняют входные данные кэша."""
    key = str(cid)
    today = datetime.now(config.TZ).date().isoformat()
    city = store.get_settings(cid).get("city") or "Алкмар"
    transports = selected_transports(cid)
    with _idea_lock(cid):
        state = store._load(config.TRAVEL_IDEA_KEY) or {}
        cached = state.get(key) or {}
        if (cached.get("date") == today and cached.get("city") == city
                and cached.get("transports") == transports and cached.get("idea")):
            return cached["idea"]
        idea = _generate_home_idea(cid)

        def change(data):
            data[key] = {"date": today, "city": city, "transports": transports, "idea": idea}
            return data, None

        store.mutate_kv(config.TRAVEL_IDEA_KEY, change)
        return idea


def _home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Подобрать страну", callback_data="a_trav_go")],
        [InlineKeyboardButton("🗺️ Мои страны", callback_data="a_trav_countries_0")],
        [InlineKeyboardButton(choose_label("Выбрать транспорт"), callback_data="a_trav_transport")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


async def send_home(bot, cid, q=None):
    idea = await asyncio.to_thread(_home_idea, cid)
    visited_count = len(_visited_codes(cid))
    msg = travel_ui.home_screen(idea, visited_count)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=_home_kb()); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_home_kb())


async def warm_home_cache(cid):
    """Создаёт дневную идею заранее, если актуального кэша ещё нет."""
    await asyncio.to_thread(_home_idea, cid)
    return True


def _plan_countries(cid):
    return [n.get("country", "") for n in store.get_list(config.NOTES_KEY, cid)
            if isinstance(n, dict) and n.get("bucket") == "plan" and n.get("country")]


def _card_cache():
    data = store._load(config.TRAVEL_COUNTRY_CARDS_KEY)
    return data if isinstance(data, dict) else {}


def _save_cached_card(code, card, *, replace=True):
    def change(data):
        if replace or code not in data:
            data[code] = card
        return data, data.get(code)
    return store.mutate_kv(config.TRAVEL_COUNTRY_CARDS_KEY, change)


def _stub_card(code, name, flag=""):
    return {"country_code": code, "country_name": name, "flag": flag or util.flag_from_cc(code),
            "content_version": 0}


def _valid_country_name(name):
    value = str(name or "").strip()
    if len(value) < 3 or len(value) > 80 or not re.search(r"[A-Za-zА-Яа-яЁё]", value):
        return False
    return not re.fullmatch(r"#?(?:X)?[0-9A-F]{5,8}", value, re.I)


def _visited_codes(cid):
    """Ленивая миграция обоих старых списков в единственную связь user -> country_code."""
    primary = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    migration_done = bool(settings.get(cid, "travel_country_codes_migrated", False))
    legacy = [] if migration_done else store.get_list(config.COUNTRIES_KEY, cid)
    raw = list(primary) + list(legacy)
    cache = _card_cache()
    codes, changed = [], False
    for item in raw:
        if isinstance(item, dict):
            name, code, flag = str(item.get("name") or "").strip(), str(item.get("code") or "").upper(), item.get("flag", "")
        else:
            text = str(item).strip()
            code = text.upper() if len(text) == 2 and text.isalpha() else ""
            name, flag = (cache.get(code) or {}).get("country_name", text), ""
        code = code or util.cc_of(name).upper()
        if len(code) != 2 or not code.isalpha():
            changed = True
            continue
        cached_name = (cache.get(code) or {}).get("country_name", "")
        country_name = cached_name if _valid_country_name(cached_name) else name
        if not _valid_country_name(country_name) or country_name.upper() == code:
            country_name = util.country_name_from_cc(code)
        if not _valid_country_name(country_name):
            changed = True
            continue
        if code not in codes:
            codes.append(code)
        if code not in cache or cache[code].get("country_name") != country_name:
            cache[code] = {**(cache.get(code) or {}), **_stub_card(code, country_name, flag)}
            _save_cached_card(code, cache[code])
        if item != code:
            changed = True
    if changed or legacy:
        store.set_list(config.FAVCOUNTRIES_KEY, cid, codes)
    if not migration_done:
        settings.set_(cid, "travel_country_codes_migrated", True)
    return codes


def _country_name(code):
    name = (_card_cache().get(code) or {}).get("country_name") or util.country_name_from_cc(code)
    return name if _valid_country_name(name) else ""


def _sorted_countries(cid):
    valid = [code for code in _visited_codes(cid) if _country_name(code)]
    return sorted(valid, key=lambda code: _country_name(code).casefold())


def _countries_kb(cid, page):
    codes = _sorted_countries(cid)
    pages = max(1, (len(codes) + _COUNTRIES_PER_PAGE - 1) // _COUNTRIES_PER_PAGE)
    page = min(max(0, page), pages - 1)
    shown = codes[page * _COUNTRIES_PER_PAGE:(page + 1) * _COUNTRIES_PER_PAGE]
    buttons = [InlineKeyboardButton(f"{util.flag_from_cc(code)} {_country_name(code)}".strip(),
                                    callback_data=f"a_trav_country_{code}_{page}") for code in shown]
    rows = [[InlineKeyboardButton("🆕 Добавить страну", callback_data="a_trav_country_add")]]
    rows.extend(buttons[i:i + 2] for i in range(0, len(buttons), 2))
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("‹", callback_data=f"a_trav_countries_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1} / {pages}", callback_data="noop"))
    if page < pages - 1: nav.append(InlineKeyboardButton("›", callback_data=f"a_trav_countries_{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows), page, pages


async def send_countries(bot, cid, page=0, q=None):
    kb, page, pages = _countries_kb(cid, page)
    msg = travel_ui.countries_screen(len(_sorted_countries(cid)), page, pages)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _normalize_languages(values):
    return [_LANG_RU.get(str(value), str(value).lower()) for value in (values or [])]


def _country_card_lock(code):
    with _CARD_LOCKS_GUARD:
        return _CARD_LOCKS.setdefault(code, threading.Lock())


def _build_country_card(code):
    with _country_card_lock(code):
        return _build_country_card_unlocked(code)


def _build_country_card_unlocked(code):
    relation_code = code
    cache = _card_cache()
    old = cache.get(code) or {}
    if old.get("content_version") == _CARD_CONTENT_VERSION:
        return old
    name = old.get("country_name") or code
    lookup = research.restcountries_lookup(name)
    if lookup and lookup.get("iso"):
        code = lookup["iso"]
        name = lookup.get("name_ru") or name
    facts = research.country_facts(name)
    langs = _normalize_languages(facts.get("languages"))
    currency = _CURRENCY_RU.get(facts.get("currency"), facts.get("currency", ""))
    prompt = f"""Создай компактную карточку уже посещённой страны {name} на русском.
Проверенные языки: {', '.join(langs)}. Проверенная валюта: {currency}.
Если проверенное поле заполнено, не противоречь ему. Названия языков пиши только по-русски.
Верни JSON: {{"description":"1 предложение","highlight":"1 строка — чем запоминается",
"languages":["языки по-русски"],"currency":"название валюты · код",
"main_nuance":"1 практичный нюанс","fact":"1 проверяемый исторический или общественный факт"}}."""
    try:
        generated = ai.llm_json(prompt, 650, tier="cheap", module="travel")
    except Exception as exc:
        _log.warning("country card generation failed for %s: %r", code, exc)
        generated = {}
    wiki = research.wiki_fact(name)
    generated_languages = generated.get("languages", [])
    if isinstance(generated_languages, str):
        generated_languages = [x.strip() for x in generated_languages.split(",") if x.strip()]
    if not isinstance(generated_languages, list):
        generated_languages = []
    now = datetime.now(timezone.utc).isoformat()
    card = {"country_code": code, "country_name": name, "flag": util.flag_from_cc(code),
            "description": generated.get("description") or f"{name} — страна со своим характером, историей и повседневным ритмом.",
            "highlight": generated.get("highlight") or "местные города, пейзажи и культура",
            "languages": langs or [str(x).lower() for x in generated_languages],
            "currency": currency or generated.get("currency", ""),
            "main_nuance": generated.get("main_nuance") or "условия поездки зависят от сезона и региона.",
            "fact": wiki or generated.get("fact") or "У страны есть несколько исторически сложившихся регионов.",
            "content_version": _CARD_CONTENT_VERSION, "generated_at": old.get("generated_at") or now, "updated_at": now}
    return _save_cached_card(relation_code, card)


async def send_country_card(bot, cid, code, page=0, q=None):
    if code not in _visited_codes(cid):
        await send_countries(bot, cid, page, q); return
    card = await asyncio.to_thread(_build_country_card, code)
    msg = travel_ui.visited_country_card(card)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить страну"), callback_data=f"a_trav_country_del_{code}_{page}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_trav_countries_{page}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def _confirm_country_delete(bot, cid, code, page, q):
    text = f"Удалить {_country_name(code)} из посещённых стран?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_trav_country_yes_{code}_{page}"),
         InlineKeyboardButton("Отмена", callback_data=f"a_trav_country_{code}_{page}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_trav_country_{code}_{page}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    await q.message.edit_text(text, reply_markup=kb)


async def handle_country_callback(bot, cid, q, act):
    if act == "trav_country_add":
        await send_country_add_prompt(bot, cid)
        return
    if act.startswith("trav_countries_"):
        await send_countries(bot, cid, int(act.rsplit("_", 1)[1]), q); return
    match = re.fullmatch(r"trav_country_(del|yes)_([A-Z0-9]+)_(\d+)", act)
    if match:
        action, code, page = match.group(1), match.group(2), int(match.group(3))
        if action == "del":
            await _confirm_country_delete(bot, cid, code, page, q); return
        store.set_list(config.FAVCOUNTRIES_KEY, cid, [x for x in _visited_codes(cid) if x != code])
        await send_countries(bot, cid, page, q); return
    match = re.fullmatch(r"trav_country_([A-Z0-9]+)_(\d+)", act)
    if match:
        await send_country_card(bot, cid, match.group(1), int(match.group(2)), q)


async def add_visited_country(bot, cid, text):
    name = str(text or "").strip()
    code = util.cc_of(name).upper()
    lookup = None
    if not code:
        lookup = await asyncio.to_thread(research.restcountries_lookup, name)
        code = str((lookup or {}).get("iso") or "").upper()
    if not code:
        store.pending_input[str(cid)] = "trav_country_add"
        await bot.send_message(chat_id=cid, text="Не нашёл такую страну. Проверь название и попробуй ещё раз.")
        return
    country_name = (lookup or {}).get("name_ru") or name
    codes = _visited_codes(cid)
    if code not in codes:
        codes.append(code)
        store.set_list(config.FAVCOUNTRIES_KEY, cid, codes)
    _save_cached_card(code, _stub_card(code, country_name, util.flag_from_cc(code)), replace=False)
    await send_countries(bot, cid, 0)


async def send_country_add_prompt(bot, cid):
    store.pending_input[str(cid)] = "trav_country_add"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="a_trav_countries_0"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ]])
    await bot.send_message(chat_id=cid, text="Напиши название страны, в которой уже был.", reply_markup=kb)


async def send_transport_settings(bot, cid, q=None):
    selected = set(selected_transports(cid))
    rows = [[InlineKeyboardButton(("✅ " if key in selected else "") + f"{emoji} {label}",
                                  callback_data=f"a_trav_mode_{key}")]
            for key, emoji, label, _ in _TRANSPORTS]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = travel_ui.transport_screen(", ".join(_TRANSPORT_BY_KEY[k][2] for k in selected_transports(cid)))
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def toggle_transport(bot, cid, key, q=None):
    if key not in _TRANSPORT_BY_KEY:
        await send_transport_settings(bot, cid, q); return
    selected = selected_transports(cid)
    if key in selected and len(selected) > 1:
        selected.remove(key)
    elif key not in selected:
        selected.append(key)
    settings.set_(cid, "travel_transports", selected)
    await send_transport_settings(bot, cid, q)


def travel_suggest_one(cid):
    visited = [_country_name(code) for code in _visited_codes(cid)]
    blocked = recommendation_stoplist.values(cid, "country")
    skip = ", ".join(visited + blocked + _plan_countries(cid))
    prompt = f"""Не предлагай: {skip}. Предложи ровно одну новую страну.
Предпочтительный транспорт: {_transport_context(cid)} — учитывай доступность этим способом.
Верни JSON: {{"flag":"флаг","country":"страна","about":"1-2 строки","for_what":"1 строка",
"langs":"языки по-русски","note":"главный нюанс"}}."""
    return ai.llm_json(prompt, 700, tier="leisure", module="travel")


def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺 Собрать маршрут", callback_data="a_trav_plan")],
        [InlineKeyboardButton("❤️ В любимые", callback_data="a_trav_fav"), InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


async def send_go(bot, cid):
    skip_set = {name.strip().casefold() for name in (
        [_country_name(code) for code in _visited_codes(cid)]
        + recommendation_stoplist.values(cid, "country") + _plan_countries(cid)
    ) if name.strip()}
    data = None
    try:
        for _ in range(3):
            candidate = await asyncio.to_thread(travel_suggest_one, cid)
            if str(candidate.get("country") or "").strip().casefold() not in skip_set:
                data = candidate
                break
        d = data or candidate
    except Exception as exc:
        await verify.safe_error(bot, cid, exc, back="m_travel"); return
    facts = await asyncio.to_thread(research.country_facts, d.get("country", ""))
    if facts.get("cc"): d["flag"] = util.flag_from_cc(facts["cc"]) or d.get("flag", "")
    if facts.get("languages"): d["langs"] = ", ".join(_normalize_languages(facts["languages"]))
    fact = await asyncio.to_thread(research.wiki_fact, d.get("country", ""))
    if fact: d["fact"] = fact
    msg = travel_ui.country_card(d)
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", msg.text)
    store.last_source[str(cid)] = "Поездки"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_travel_kb())


async def travel_dislike(bot, cid):
    country = store.suggested_countries.get(str(cid))
    if country: recommendation_stoplist.add(cid, "country", country, "hidden")
    await send_go(bot, cid)


async def travel_fav(bot, cid):
    country = store.suggested_countries.get(str(cid))
    if country:
        code = util.cc_of(country).upper()
        if not code:
            lookup = await asyncio.to_thread(research.restcountries_lookup, country)
            code = (lookup or {}).get("iso", "")
        if code:
            codes = _visited_codes(cid)
            if code not in codes: codes.append(code)
            store.set_list(config.FAVCOUNTRIES_KEY, cid, codes)
            _save_cached_card(code, _stub_card(code, country, util.flag_from_cc(code)), replace=False)
            await bot.send_message(chat_id=cid, text=f"✅ {country} добавлена в «Мои страны».")
    await send_go(bot, cid)


async def send_plan(bot, cid):
    import saved_items
    data = store.last_recipe.get(str(cid)) or {}
    country = data.get("country") or store.suggested_countries.get(str(cid), "")
    if not country:
        await bot.send_message(chat_id=cid, text="Сначала выбери страну в Поездках."); return
    home = store.get_settings(cid).get("city", "дом")
    facts = await asyncio.to_thread(research.country_facts, country)
    fact_block = research.facts_block(facts)
    wiki_fact = await asyncio.to_thread(research.wiki_fact, country)
    web_data = await asyncio.to_thread(
        research.web_snippet, f"{country} туризм путешествие достопримечательности", 900,
    )
    prompt = f"""Подробный план поездки в {country} из {home}.
Основной транспорт: {_transport_context(cid)}. Не предлагай другой, если маршрут нормально строится выбранным.
Проверенные факты: {fact_block}. Свежая туристическая информация: {web_data}.
Бюджет и сроки помечай как ориентир. Верни компактный JSON на русском:
{{"flag":"эмодзи","title":"страна/регион","about":"1-2 строки","why":["3 пункта"],
"best_time":"1-2 строки","budget":["3 ориентира"],"spots":["3 места"],
"lgbt":"1 строка о дружелюбности и безопасности","fact":"1 факт"}}."""
    try:
        plan = await ai.allm_json(prompt, 1100, tier="leisure", module="travel")
    except Exception as exc:
        await verify.safe_error(bot, cid, exc, back="m_travel"); return
    if facts.get("cc"):
        plan["flag"] = util.flag_from_cc(facts["cc"]) or plan.get("flag", "")
    if wiki_fact:
        plan["fact"] = wiki_fact
    msg = travel_ui.travel_plan(plan, country)
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Поездки · План"
    store.last_recipe[str(cid)] = {**data, "plan_text": msg.text, "plan_entities": util.entities_to_json(msg.entities)}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton(save_toggle_label(saved_items.is_note_saved(cid, msg.text, "plan")), callback_data="a_trav_save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def save_plan(bot, cid, q=None):
    import saved_items
    data = store.last_recipe.get(str(cid)) or {}
    plan = data.get("plan_text", "")
    if not plan:
        await bot.send_message(chat_id=cid, text="Сначала собери план поездки."); return
    saved = saved_items.toggle_note(cid, plan, source="План поездки", bucket="plan",
                                    entities=data.get("plan_entities", []),
                                    extra={"country": data.get("country", "")})
    await saved_items.update_save_button(q, "a_trav_save", saved)
