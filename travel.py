"""Поездки: актуальная идея маршрута, рекомендации и посещённые страны."""
import asyncio
import logging
import re
import threading
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import memory
import recommendation_stoplist
import research
import settings
import store
import travel_photos
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
        [InlineKeyboardButton("🧳 Мои страны", callback_data="a_trav_countries_0")],
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


def _save_country_photo(code, country_name, photo):
    if not code or not photo:
        return photo
    def change(data):
        current = data.get(code) if isinstance(data.get(code), dict) else {}
        data[code] = {**current, "country_code": code,
                      "country_name": current.get("country_name") or country_name,
                      "photo": photo}
        return data, photo
    return store.mutate_kv(config.TRAVEL_COUNTRY_CARDS_KEY, change)


def _recommendation_photo(country, facts=None):
    facts = facts or {}
    code = str(facts.get("cc") or util.cc_of(country) or "").upper()
    cached = (_card_cache().get(code) or {}).get("photo") if code else None
    if isinstance(cached, dict) and cached.get("url"):
        return cached
    lookup = research.restcountries_lookup(country)
    if lookup:
        code = str(lookup.get("iso") or code).upper()
        cached = (_card_cache().get(code) or {}).get("photo") if code else None
        if isinstance(cached, dict) and cached.get("url"):
            return cached
    search_name = (lookup or {}).get("name_en") or country
    photo = travel_photos.country_cover(search_name)
    return _save_country_photo(code, country, photo) if photo else None


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
            "photo": old.get("photo"),
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
    prompt = f"""Не предлагай: {skip}. Предложи ровно одну новую страну для путешествия.
Предпочтительный транспорт: {_transport_context(cid)} — учитывай доступность этим способом.
Это короткое превью, а не статья. Пиши естественно, конкретно и без рекламы.
Верни только JSON: {{"flag":"флаг","country":"страна по-русски",
"about":"ровно 1 короткое предложение о характере путешествия",
"for_what":"1 короткая причина выбрать страну",
"note":"1 действительно важный практический нюанс поездки"}}.
Не добавляй языки, бюджет, LGBTQ+, факты, статистику и список мест.
Не повторяй одну мысль в разных полях. Не пиши «главный нюанс» внутри note."""
    return ai.llm_json(prompt, 700, tier="leisure", module="travel")


def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺️ Показать подробности", callback_data="a_trav_plan")],
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
    d = {
        "flag": d.get("flag", ""),
        "country": str(d.get("country") or "").strip(),
        "about": _short_text(d.get("about"), 220),
        "for_what": _short_text(d.get("for_what"), 150),
        "note": _short_text(d.get("note"), 170),
    }
    photo = await asyncio.to_thread(_recommendation_photo, d.get("country", ""), facts)
    if photo:
        d["photo"] = photo
    msg = travel_ui.country_card(d)
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", msg.text)
    store.last_source[str(cid)] = "Поездки"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d
    if photo:
        try:
            await bot.send_photo(
                chat_id=cid, photo=photo["url"], caption=msg.text,
                caption_entities=msg.entities, reply_markup=_travel_kb(),
            )
            return
        except Exception as exc:
            _log.warning("travel country photo delivery failed: %s", type(exc).__name__)
            text_data = {**d, "photo": None}
            msg = travel_ui.country_card(text_data)
            store.last_answer[str(cid)] = msg.text
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


def _short_text(value, limit):
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip(" ,;:")


def _short_list(values, count, limit=130):
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values or []:
        text = _short_text(value, limit)
        if text and text.casefold() not in {item.casefold() for item in result}:
            result.append(text)
        if len(result) == count:
            break
    return result


async def send_plan(bot, cid):
    import saved_items
    data = store.last_recipe.get(str(cid)) or {}
    country = data.get("country") or store.suggested_countries.get(str(cid), "")
    if not country:
        await bot.send_message(chat_id=cid, text="Сначала выбери страну в Поездках."); return
    home = store.get_settings(cid).get("city", "дом")
    facts = await asyncio.to_thread(research.country_facts, country)
    fact_block = research.facts_block(facts)
    wiki_sources = await asyncio.to_thread(research.wiki_sentences, country)
    web_data = await asyncio.to_thread(
        research.web_snippet,
        f"{country} official travel advice regional climate LGBTQ travel attractions 2026",
        1600,
    )
    profile = memory.profile_hints(cid) or "Предпочтения пользователя пока не сохранены."
    preview = {
        key: data.get(key) for key in ("about", "for_what", "note") if data.get(key)
    }
    prompt = f"""Собери подробную практическую карточку путешествия в {country} из {home}.
Предпочтительный транспорт: {_transport_context(cid)}.
Персональный контекст: {profile}
Проверенные стабильные данные: {fact_block or 'нет структурированных данных'}.
Свежие поисковые фрагменты: {web_data or 'нет свежих фрагментов'}.
Фрагменты Wikipedia для географического или культурного факта: {' '.join(wiki_sources[:3]) or 'нет'}.
В превью уже было: {preview}. Не повторяй эти формулировки и мысли.

Верни только JSON на русском:
{{"flag":"эмодзи","title":"название страны","about":"1-2 коротких предложения о характере поездки и соответствии пользователю",
"why":["ровно 2 конкретные персональные причины"],
"spots":["ровно 3 главных места или региона"],
"best_time":"короткая практическая рекомендация с различиями по регионам, если климат неоднороден",
"languages":["основные языки по-русски"],
"lgbt":"1 нейтральная конкретная практическая строка для путешественника",
"fact":"1 связанный с путешествием факт о географии, культуре или природе"}}.

Не пиши бюджет: он будет добавлен отдельно без выдуманных сумм.
Не используй рекламный стиль, случайную статистику, политику или религию.
Не пиши шаблон «соблюдайте местные обычаи». Изменяемые сведения бери только из свежих фрагментов.
Каждый блок должен быть коротким, конкретным и без тавтологии."""
    try:
        plan = await ai.allm_json(prompt, 1100, tier="leisure", module="travel")
    except Exception as exc:
        await verify.safe_error(bot, cid, exc, back="m_travel"); return
    if facts.get("cc"):
        plan["flag"] = util.flag_from_cc(facts["cc"]) or plan.get("flag", "")
    plan = {
        "flag": plan.get("flag") or data.get("flag", ""),
        "title": country,
        "about": _short_text(plan.get("about"), 140),
        "why": _short_list(plan.get("why"), 2, 65),
        "spots": _short_list(plan.get("spots"), 3, 45),
        "best_time": _short_text(plan.get("best_time"), 100),
        "budget": f"Стоимость зависит от дат, длительности поездки, дороги из {home} и маршрута внутри страны.",
        "languages": _normalize_languages(facts.get("languages")) or _short_list(plan.get("languages"), 3, 30),
        "lgbt": _short_text(plan.get("lgbt"), 90) if web_data else "Актуальные данные не найдены — проверь рекомендации перед поездкой.",
        "fact": _short_text(plan.get("fact"), 90),
        "photo": data.get("photo"),
    }
    msg = travel_ui.travel_plan(plan, country)
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Поездки · Страна"
    store.last_recipe[str(cid)] = {
        **data, "plan_text": msg.text,
        "plan_entities": util.entities_to_json(msg.entities), "details": plan,
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton(save_toggle_label(saved_items.is_note_saved(cid, msg.text, "plan")), callback_data="a_trav_save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    photo = plan.get("photo") or {}
    if photo.get("url"):
        try:
            await bot.send_photo(
                chat_id=cid, photo=photo["url"], caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception as exc:
            _log.warning("travel details photo delivery failed: %s", type(exc).__name__)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def save_plan(bot, cid, q=None):
    import saved_items
    data = store.last_recipe.get(str(cid)) or {}
    plan = data.get("plan_text", "")
    if not plan:
        await bot.send_message(chat_id=cid, text="Сначала открой подробности страны."); return
    saved = saved_items.toggle_note(cid, plan, source="Карточка страны", bucket="plan",
                                    entities=data.get("plan_entities", []),
                                    extra={"country": data.get("country", "")})
    await saved_items.update_save_button(q, "a_trav_save", saved)
