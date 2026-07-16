"""Раздел «Поездки»: подбор новой страны/направления под вкус пользователя
(LLM + проверенные факты через research.py) и сборка подробного плана поездки.

Вынесен из leisure.py в самостоятельный раздел главного меню (был подпунктом Досуга).
"""
import asyncio
import logging
import re
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import ui_label

import ai
import config
import research
import secure
import store
import util
import verify
from ui import travel as travel_ui
from util import country_flag

_log = logging.getLogger(__name__)
_FACTS_CACHE_TTL = 30 * 86400  # 30 дней, как договорено в спеке "10 фактов"


def _home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Подобрать страну", callback_data="a_trav_go")],
        [InlineKeyboardButton("🧭 Интересные факты", callback_data="a_trav_facts")],
        [InlineKeyboardButton("🎚️ Настройки поездок", callback_data="set_travel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _home_facts_countries_hash(names):
    import hashlib
    key = "|".join(sorted(n.lower() for n in names))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _home_facts_country_names(cid):
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    return [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs if f]


def _generate_home_facts(names):
    sample = ", ".join(names[:15])
    prompt = f"""Пользователь посетил эти страны: {sample}.
Дай 3 коротких интересных факта — по одному факту о разных странах из списка (выбери сам, какие интереснее).
Не банальности (не столица/валюта/флаг). Формат каждого факта: короткое яркое название + 1 предложение сути.
Верни JSON (без markdown): {{"facts": [{{"country": "страна", "title": "короткое название", "text": "1 предложение"}}]}}"""
    try:
        d = ai.llm_json(prompt, 700, tier="cheap", module="travel")
    except Exception as e:
        _log.warning("travel home facts AI failed: %r", e, exc_info=True)
        return []
    raw = d.get("facts") if isinstance(d, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for f in raw[:3]:
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        text = str(f.get("text") or "").strip()
        if title and text:
            out.append({"title": title, "text": text})
    return out


def _home_facts(cid):
    """3 факта о странах пользователя на главном экране — кэш на неделю, пересобирается
    только если список посещённых стран изменился (не на каждое открытие раздела)."""
    from datetime import datetime
    names = _home_facts_country_names(cid)
    if not names:
        return []
    cid = str(cid)
    countries_hash = _home_facts_countries_hash(names)
    week_key = datetime.now(config.TZ).isocalendar()[:2]
    week_str = f"{week_key[0]}-{week_key[1]:02d}"
    all_data = store._load(config.TRAVEL_HOME_FACTS_KEY) or {}
    entry = all_data.get(cid) or {}
    if entry.get("countries_hash") == countries_hash and entry.get("week") == week_str and entry.get("facts"):
        return entry["facts"]
    facts = _generate_home_facts(names)
    if facts:
        all_data[cid] = {"week": week_str, "countries_hash": countries_hash, "facts": facts}
        store._save(config.TRAVEL_HOME_FACTS_KEY, all_data)
        return facts
    return entry.get("facts") or []


async def send_home(bot, cid, q=None):
    """Приветственный экран раздела «Поездки»: сколько стран посещено/в планах,
    несколько фактов о посещённых странах, снизу — вход в подбор новой страны."""
    visited_count = len(store.get_list(config.FAVCOUNTRIES_KEY, cid))
    plan_count = len(_plan_countries(cid))
    facts = await asyncio.to_thread(_home_facts, cid)
    msg = travel_ui.home_screen(visited_count, plan_count, facts)
    kb = _home_kb()
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def warm_home_cache(cid):
    """Прогревает недельные факты главного экрана без отправки сообщения."""
    await asyncio.to_thread(_home_facts, cid)
    return True


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
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
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
    store.last_source[str(cid)] = "Поездки"
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
        await bot.send_message(chat_id=cid, text="Сначала выбери страну в Поездках."); return
    s = store.get_settings(cid)
    home = s.get("city", "дом")
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
    store.last_source[str(cid)] = "Поездки · План"
    store.last_recipe[str(cid)] = {
        **(store.last_recipe.get(str(cid)) or {}),
        "plan_text": msg.text, "plan_entities": util.entities_to_json(msg.entities),
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data="a_trav_no")],
        [InlineKeyboardButton(ui_label("save", "Сохранить маршрут"), callback_data="a_trav_save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
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
    await bot.send_message(chat_id=cid, text=f"{ui_label('save', 'Маршрут')} ({country}) сохранён в «Мои данные» → «Поездки».")
    await send_go(bot, cid)


# ================= 10 ФАКТОВ О СТРАНЕ =================
# Поток: кнопка -> запрос названия страны текстом -> REST Countries определяет
# ISO -> Tavily собирает сырые данные по нескольким темам -> Gemini/Groq отбирает
# и пишет 10 сильных фактов -> кэш подборки на 30 дней по ISO (общий для всех
# пользователей, факты о стране не зависят от того, кто спрашивает).
# "Ещё 10" использует те же данные, избегая уже показанных пользователю фактов.

_FACTS_SEARCH_QUERIES = (
    "daily life and social customs in {country}",
    "unique traditions and habits in {country}",
    "architecture transport technology in {country}",
    "unusual but verified facts about {country}",
    "how people live in {country}",
    "social etiquette in {country}",
)
_FACTS_PER_BATCH = 5
_FACTS_MIN_SOURCES_HINT = 20  # промпт просит собрать минимум столько кандидатов перед отбором


def _facts_cache_all():
    return store._load(config.TRAVEL_FACTS_CACHE_KEY) or {}


def _facts_cache_get(iso):
    data = _facts_cache_all().get(iso)
    if not data:
        return None
    if time.time() - int(data.get("cached_at") or 0) > _FACTS_CACHE_TTL:
        return None
    return data


def _facts_cache_set(iso, country_info, batches, sources_by_batch):
    """sources_by_batch — источники параллельно batches (тот же индекс), хранятся
    для аудита/логов, пользователю не показываются (см. ТЗ 'Кэширование')."""
    data = _facts_cache_all()
    data[iso] = {
        "cached_at": int(time.time()),
        "official": country_info.get("official", ""),
        "name_ru": country_info.get("name_ru", ""),
        "name_en": country_info.get("name_en", ""),
        "batches": batches,
        "sources_by_batch": sources_by_batch,
    }
    store._save(config.TRAVEL_FACTS_CACHE_KEY, data)


def _facts_seen_ids(cid, iso):
    seen = store.get_list(config.TRAVEL_FACTS_SEEN_KEY, cid)
    entry = next((s for s in seen if isinstance(s, dict) and s.get("iso") == iso), None)
    return set(entry.get("ids", [])) if entry else set()


def _facts_seen_add(cid, iso, ids):
    seen = store.get_list(config.TRAVEL_FACTS_SEEN_KEY, cid)
    entry = next((s for s in seen if isinstance(s, dict) and s.get("iso") == iso), None)
    if entry:
        entry["ids"] = list(dict.fromkeys(entry.get("ids", []) + list(ids)))
    else:
        seen.append({"iso": iso, "ids": list(ids)})
    store.set_list(config.TRAVEL_FACTS_SEEN_KEY, cid, seen)


def _facts_prompt(country_name, raw_context, avoid_titles):
    avoid_block = ""
    if avoid_titles:
        avoid_block = (
            "\nУже показаны пользователю (НЕ повторяй и НЕ перефразируй эти темы, ищи другие):\n"
            + "\n".join(f"- {t}" for t in avoid_titles[:30])
        )
    return f"""Ты пишешь для современного журнала о путешествиях. Страна: {country_name}.

Сырые данные из поиска (несколько независимых источников, используй как материал, не копируй дословно):
{secure.wrap_untrusted(raw_context, "материалы поиска")}
{avoid_block}

Собери минимум {_FACTS_MIN_SOURCES_HINT} возможных фактов о том, как устроена страна, повседневная жизнь
и особенности общества — НЕ достопримечательности. Подходящие темы: поведение и общение, повседневные
привычки, отношение к чистоте/времени/работе/обществу, необычная архитектура и жильё, транспорт, язык
и письменность, образование, технологии, городская жизнь, природа и климат, праздники, законы и нормы.
Еда — только если в ней есть по-настоящему необычный контекст.

НЕ используй банальности: столица, валюта, площадь, население, флаг, популярная еда без контекста,
стандартные туристические достопримечательности.

Каждый факт должен вызывать реакцию «я этого не знал» или «теперь я лучше понимаю эту страну» — раскрывать
устройство общества, повседневную жизнь или решение, возникшее из-за географии или истории.

Точность: каждый факт подтверждается материалами выше. Для спорных или необычных утверждений нужно
подтверждение из нескольких источников в материалах. Не превращай культурную тенденцию в правило для всех
жителей. Если факт эффектный, но его нельзя нормально подтвердить материалами — не включай его.
ЗАПРЕЩЕНЫ формулировки вида «все японцы», «японцы всегда», «в этой стране никто», категоричные обобщения
на весь народ. Используй точные формулировки: «часто», «во многих», «традиционно считается», «всё ещё
распространено», «в крупных городах часто встречается».

Отбери 5 самых сильных фактов из собранных: без банальностей и повторов, без факта без подтверждения,
без спорных обобщений. Факты должны относиться к разным темам. Не давай несколько почти
одинаковых фактов про транспорт, еду или традиции. Расположи самые сильные в начале.

Стиль: живо, просто, конкретно, без канцеляризмов, без рекламного тона, без обращения к пользователю,
без длинных вступлений, без фразы «интересный факт заключается в том что», без выводов в конце, без ссылок
в тексте. Название факта — короткое и яркое. Описание — 2 коротких предложения. Пиши на русском.

Верни JSON: {{"facts": [{{"title": "короткое яркое название", "text": "2 коротких предложения"}}, ...]}}"""


async def _collect_facts_raw_context(country_name):
    """Tavily по нескольким темам сразу — независимые источники, не Wikipedia/Wikidata."""
    queries = [q.format(country=country_name) for q in _FACTS_SEARCH_QUERIES]
    results = await asyncio.gather(
        *[asyncio.to_thread(research.tavily_search, q, 3) for q in queries],
        return_exceptions=True,
    )
    parts, sources = [], []
    for r in results:
        if isinstance(r, Exception) or not r:
            continue
        for item in r:
            content = (item.get("content") or "").strip()
            url = item.get("url") or ""
            if content:
                parts.append(content[:600])
            if url:
                sources.append(url)
    return "\n---\n".join(parts)[:6000], list(dict.fromkeys(sources))[:15]


async def _generate_facts_batch(country_name, avoid_titles):
    """Возвращает (facts|None, sources, reason). reason -> 'search_failed' (Tavily
    не дал результатов - не показываем 'не нашлось фактов' в этом случае, честно
    говорим что поиск недоступен) | 'no_facts' (поиск сработал, но подходящих
    фактов не нашлось - актуально для повторных 'Ещё 10') | '' при успехе."""
    raw_context, sources = await _collect_facts_raw_context(country_name)
    if not raw_context:
        return None, [], "search_failed"
    prompt = _facts_prompt(country_name, raw_context, avoid_titles)
    try:
        d = await ai.allm_json(prompt, 2200, module="travel_facts10", route=None)
    except Exception as e:
        _log.warning("travel facts10 AI failed for %s: %r", country_name, e, exc_info=True)
        return None, sources, "no_facts"
    raw_facts = d.get("facts") if isinstance(d, dict) else None
    if not isinstance(raw_facts, list):
        return None, sources, "no_facts"
    facts = []
    seen_titles = set()
    for f in raw_facts:
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        text = str(f.get("text") or "").strip()
        if not title or not text or title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        facts.append({"id": uuid.uuid4().hex[:8], "title": title, "text": text})
        if len(facts) >= _FACTS_PER_BATCH:
            break
    return (facts or None), sources, ("" if facts else "no_facts")


def _facts_kb(has_more_hint=True):
    rows = [
        [InlineKeyboardButton(f"✨ Ещё {_FACTS_PER_BATCH} фактов", callback_data="a_trav_facts_more")],
        [InlineKeyboardButton("🌍 Другая страна", callback_data="a_trav_facts_new")],
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


def _facts_exhausted_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Другая страна", callback_data="a_trav_facts_new")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _facts_retry_kb(action):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Повторить" if action == "retry" else "Попробовать снова", callback_data="a_trav_facts")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


async def facts_start(bot, cid):
    """Кнопка «🧭 10 фактов» — просит название страны текстом."""
    store.pending_input[str(cid)] = "trav_facts_country"
    msg = travel_ui.facts_prompt_screen()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)


async def facts_new_country(bot, cid):
    """«🌍 Другая страна» — то же приглашение, без промежуточного экрана."""
    store.trav_facts_state.pop(str(cid), None)
    await facts_start(bot, cid)


async def _send_facts_batch(bot, cid, country_info, facts):
    iso = country_info["iso"]
    store.trav_facts_state[str(cid)] = country_info
    _facts_seen_add(cid, iso, [f["id"] for f in facts])
    name = country_info.get("name_ru") or country_info.get("official", "")
    msg = travel_ui.facts_card(name, facts)
    chunks = util.chunk_text_with_entities(msg.text, msg.entities, limit=3800)
    for chunk_text, chunk_entities in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=chunk_text, entities=chunk_entities)
    last_text, last_entities = chunks[-1]
    await bot.send_message(chat_id=cid, text=last_text, entities=last_entities, reply_markup=_facts_kb())


async def handle_facts_country_input(bot, cid, text):
    """Обрабатывает ответ пользователя на «О какой стране рассказать?»."""
    try:
        await bot.send_chat_action(chat_id=cid, action="typing")
    except Exception:
        pass
    country_info = await asyncio.to_thread(research.restcountries_lookup, text)
    if not country_info:
        msg = travel_ui.facts_not_found_screen()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_facts_retry_kb("retry"))
        return
    await _run_facts_flow(bot, cid, country_info, more=False)


async def facts_more(bot, cid):
    """«✨ Ещё 10» — новая подборка по той же стране, без повторов. Страна уже
    определена раньше в этой сессии — переиспользуем сохранённый country_info,
    без повторного похода в REST Countries API."""
    country_info = store.trav_facts_state.get(str(cid))
    if not country_info:
        await facts_start(bot, cid); return
    await _run_facts_flow(bot, cid, country_info, more=True)


async def _run_facts_flow(bot, cid, country_info, more):
    iso = country_info["iso"]
    cached = _facts_cache_get(iso)
    batches = list(cached.get("batches", [])) if cached else []
    sources_by_batch = list(cached.get("sources_by_batch", [])) if cached else []
    seen_ids = _facts_seen_ids(cid, iso)

    unseen_batch = next((b for b in batches if not (set(f["id"] for f in b) & seen_ids)), None)
    if unseen_batch:
        await _send_facts_batch(bot, cid, country_info, unseen_batch)
        return

    avoid_titles = []
    for b in batches:
        avoid_titles.extend(f["title"] for f in b)
    name_for_search = country_info.get("official") or country_info.get("name_en") or country_info.get("name_ru", "")
    try:
        facts, sources, reason = await _generate_facts_batch(name_for_search, avoid_titles)
    except Exception as e:
        await verify.safe_error(bot, cid, e)
        return
    if not facts:
        if reason == "search_failed":
            msg = travel_ui.facts_search_unavailable_screen()
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_facts_retry_kb("retry_search"))
            return
        if not batches:
            # первый запрос по этой стране — пользователю ещё нечего "уже показать".
            msg = travel_ui.facts_not_found_for_country_screen()
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_facts_retry_kb("retry"))
            return
        msg = travel_ui.facts_exhausted_screen()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_facts_exhausted_kb())
        return

    batches.append(facts)
    sources_by_batch.append(sources)
    _facts_cache_set(iso, country_info, batches, sources_by_batch)
    await _send_facts_batch(bot, cid, country_info, facts)
