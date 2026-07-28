"""Музыкальные рекомендации и управление любимыми артистами."""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import music_releases
import recommendation_stoplist
import store
from ui import leisure as leisure_ui
from ui.constants import save_toggle_label, ui_label
from ui.navigation import back_menu_keyboard

_log = logging.getLogger(__name__)

_MUSIC_GENRES = [
    ("indie", "🌿 Инди", "инди-поп или инди-рок"),
    ("pop", "✨ Поп", "современный поп"),
    ("electronic", "⚡ Электроника", "электронная музыка"),
    ("rnb", "🪩 R&B", "R&B или соул"),
    ("rock", "🎸 Рок", "рок"),
    ("hiphop", "🎤 Хип-хоп", "хип-хоп"),
]
_RECENT_ARTISTS_LIMIT = 40

# Последний резерв, когда все AI-провайдеры временно недоступны. Это реальные,
# достаточно известные артисты с существующими треками; выбор всё равно исключает
# уже знакомых, сохранённых, отклонённых и недавно показанных.
_LOCAL_ARTIST_FALLBACKS = {
    "indie": [
        {"artist": "Big Thief", "desc": "Живой инди-рок с хрупким вокалом и неожиданными поворотами.",
         "why": ["Тёплая, детальная гитарная музыка без лишнего шума.", "Песни звучат свободнее и острее обычного инди-попа."],
         "tracks": ["Not", "Simulation Swarm", "Vampire Empire"],
         "fact": "Американская инди-группа из Бруклина."},
    ],
    "pop": [
        {"artist": "Caroline Polachek", "desc": "Артистичный поп с воздушным вокалом и точной электроникой.",
         "why": ["Мелодии остаются лёгкими, но аранжировки не банальные.", "Это поп с более странным и кинематографичным настроением."],
         "tracks": ["Bunny Is a Rider", "Welcome to My Island", "So Hot You're Hurting My Feelings"],
         "fact": "Американская певица и продюсер."},
    ],
    "electronic": [
        {"artist": "Fred again..", "desc": "Электроника, собранная из живых голосов, дневниковых фраз и мягких битов.",
         "why": ["Танцевальная музыка здесь остаётся очень личной и мелодичной.", "Подойдёт, если хочется движения без холодного клубного звучания."],
         "tracks": ["Delilah (pull me out of this)", "Danielle (smile on my face)", "adore u"],
         "fact": "Британский электронный музыкант и продюсер."},
    ],
    "rnb": [
        {"artist": "Kelela", "desc": "Гладкий альтернативный R&B на стыке клубной электроники и мягкого соула.",
         "why": ["Вокал остаётся близким и спокойным, даже когда биты становятся резче.", "Звучание смелее привычного современного R&B."],
         "tracks": ["Rewind", "Washed Away", "On the Run"],
         "fact": "Американская певица и автор песен."},
    ],
    "rock": [
        {"artist": "Fontaines D.C.", "desc": "Нервный, мелодичный рок с тёмным городским настроением.",
         "why": ["Есть напор и гитары, но песни не превращаются в шум.", "Подойдёт для более собранного и драматичного настроения."],
         "tracks": ["I Love You", "Starburster", "Favourite"],
         "fact": "Ирландская рок-группа, основанная в Дублине."},
    ],
    "hiphop": [
        {"artist": "Little Simz", "desc": "Точный хип-хоп с сильным голосом, джазовыми деталями и личными историями.",
         "why": ["Ритм и тексты держат внимание без показной агрессии.", "В музыке много масштаба, но она остаётся очень личной."],
         "tracks": ["Introvert", "Gorilla", "Woman"],
         "fact": "Британская рэперша из Лондона."},
    ],
    "default": [
        {"artist": "FKA twigs", "desc": "Хрупкий арт-поп, где R&B, электроника и камерный вокал постоянно меняют форму.",
         "why": ["Много деталей, воздуха и эмоционального напряжения.", "Звучание смелее обычного попа, но остаётся очень мелодичным."],
         "tracks": ["Two Weeks", "cellophane", "oh my love"],
         "fact": "Британская певица, автор песен и танцовщица."},
        {"artist": "Khruangbin", "desc": "Мягкая гитарная психоделика с фанком, соулом и долгими тёплыми грувами.",
         "why": ["Подходит для спокойного фона, но в музыке много мелких деталей.", "Здесь больше солнечного грува, чем в привычном инди-роке."],
         "tracks": ["Time (You and I)", "Friday Morning", "Texas Sun"],
         "fact": "Американское трио из Хьюстона."},
        {"artist": "Little Simz", "desc": "Точный хип-хоп с сильным голосом, джазовыми деталями и личными историями.",
         "why": ["Ритм и тексты держат внимание без показной агрессии.", "В музыке много масштаба, но она остаётся очень личной."],
         "tracks": ["Introvert", "Gorilla", "Woman"],
         "fact": "Британская рэперша из Лондона."},
    ],
}


def _local_artist_fallback(known, category=None):
    """Возвращает нового артиста без сетевого запроса, если AI-цепочка недоступна."""
    key = category.get("value") if isinstance(category, dict) else "default"
    candidates = [*_LOCAL_ARTIST_FALLBACKS.get(key, []), *_LOCAL_ARTIST_FALLBACKS["default"]]
    known = {str(value or "").casefold() for value in known}
    for item in candidates:
        if item["artist"].casefold() not in known:
            return dict(item)
    return None


def _cached_artist(cid):
    entry = (store._load(config.MUSIC_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    item = entry.get("item")
    today = datetime.now(config.TZ).date().isoformat()
    return dict(item) if entry.get("date") == today and isinstance(item, dict) else None


def _cache_artist(cid, item):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {"date": datetime.now(config.TZ).date().isoformat(), "item": dict(item or {})}
        return data, None
    store.mutate_kv(config.MUSIC_RECO_CACHE_KEY, mutate)


def _invalidate_artist(cid):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data.pop(str(cid), None)
        return data, None
    store.mutate_kv(config.MUSIC_RECO_CACHE_KEY, mutate)


def _recent_artists(cid):
    profile = store.get_profile(cid)
    values = profile.get("music_recent_artists", []) if isinstance(profile, dict) else []
    recent = []
    for value in values if isinstance(values, list) else []:
        artist = str(value or "").strip()
        if artist and artist.casefold() not in {item.casefold() for item in recent}:
            recent.append(artist)
    return recent[-_RECENT_ARTISTS_LIMIT:]


def _remember_artist(cid, artist):
    artist = str(artist or "").strip()
    if not artist:
        return
    profile = store.get_profile(cid)
    profile = dict(profile) if isinstance(profile, dict) else {}
    recent = _recent_artists(cid)
    recent = [item for item in recent if item.casefold() != artist.casefold()]
    profile["music_recent_artists"] = [*recent, artist][-_RECENT_ARTISTS_LIMIT:]
    store.set_profile(cid, profile)


def _add_unique(key, cid, value):
    items = store.get_list(key, cid)
    if value and value.lower() not in {_item_text(item).lower() for item in items}:
        store.set_list(key, cid, [*items, value])


async def _ask_collect(bot, cid, kind):
    import leisure_collection
    return await leisure_collection._ask_collect(bot, cid, kind)


def content_recommend(kind, cid):
    import leisure_collection
    return leisure_collection.content_recommend(kind, cid)


def _kick_off_new_artist_concert_check(cid, artist_names):
    """При добавлении нового артиста запускает внешний поиск концертов сразу
    (Tavily/Firecrawl/AI), не дожидаясь недельного цикла — фоновой задачей."""
    # Сводная подборка хранится неделю. Сбрасываем её сразу, иначе новый артист
    # не попадёт в «Концерты» до планового воскресного обновления.
    import leisure_concerts
    leisure_concerts.invalidate_user_concerts_cache(cid)
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"

    async def _run():
        for name in artist_names:
            try:
                await leisure_concerts.refresh_artist_external_events(name, cc, cname)
            except Exception as e:
                _log.warning("new artist concert check failed for %r: %r", name, e)

    asyncio.create_task(_run())


async def listen_love(bot, cid, q=None):
    """Добавляет артиста в любимые без дублей и отражает состояние на карточке."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        _add_unique(config.FAVORITE_ARTISTS_KEY, cid, artist)
        _invalidate_artist(cid)
        _kick_off_new_artist_concert_check(cid, [artist])
        if q is not None:
            import saved_items
            await q.message.edit_reply_markup(
                reply_markup=_listen_kb(saved_items.is_note_saved(cid, artist), favorite=True))

def _listen_kb(saved=False, favorite=False):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другой артист", callback_data="a_listen_no")],
        [InlineKeyboardButton("🎫 Концерты", callback_data="a_artist_concerts")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="music_genre_menu"),
         InlineKeyboardButton(save_toggle_label(saved, "Сохранить"), callback_data="listen_0")],
        [InlineKeyboardButton("💾 Сохранения", callback_data="artist_saved"),
         InlineKeyboardButton("🎚️ Предпочтения", callback_data="music_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def music_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать музыку", callback_data="music_reco")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="music_genre_menu"),
         InlineKeyboardButton("💾 Сохранения", callback_data="artist_saved")],
        [InlineKeyboardButton("🎫 Концерты", callback_data="a_concerts_find"),
         InlineKeyboardButton("🎚️ Предпочтения", callback_data="music_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_music_home(bot, cid, q=None):
    concerts, albums = await asyncio.gather(
        _weekly_concerts(cid),
        asyncio.to_thread(
            music_releases.weekly_new_albums,
            str(store.get_settings(cid).get("cc") or "NL"),
        ),
        return_exceptions=True,
    )
    if isinstance(concerts, Exception):
        _log.warning("music home concerts failed cid=%s: %r", cid, concerts)
        concerts = []
    if isinstance(albums, Exception):
        _log.warning("music home releases failed cid=%s: %r", cid, albums)
        albums = []
    msg = leisure_ui.music_week_screen(concerts or [], albums or [])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=music_home_keyboard(),
    )


async def _weekly_concerts(cid):
    """Ближайшие подтверждённые концерты любимых артистов без web/AI fallback."""
    import leisure_concerts
    from util import _MONTHS

    settings_data = store.get_settings(cid)
    cc = str(settings_data.get("cc") or "NL").upper()
    artists = leisure_concerts._ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return []
    events = leisure_concerts._concerts_cache_get(cid, cc)
    if events is None:
        now = datetime.now(config.TZ)
        events = await leisure_concerts._ticketmaster_events_many(
            artists, cc,
            start_dt=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            size=3, limit=20,
        )
        leisure_concerts._concerts_cache_set(cid, cc, events)

    today = datetime.now(config.TZ).date().isoformat()
    rows, seen = [], set()
    for event in events:
        artist = str(event.get("_artist") or "").strip()
        event_date = str(((event.get("dates") or {}).get("start") or {}).get("localDate") or "")
        venue = (((event.get("_embedded") or {}).get("venues") or [{}])[0])
        city = str(((venue.get("city") or {}).get("name") or "")).strip()
        key = (artist.casefold(), event_date, city.casefold())
        if not artist or (event_date and event_date < today) or key in seen:
            continue
        seen.add(key)
        try:
            year, month, day = event_date.split("-")
            date_label = f"{int(day)} {_MONTHS[int(month) - 1]}"
            if int(year) != datetime.now(config.TZ).year:
                date_label += f" {year}"
        except (ValueError, IndexError):
            date_label = event_date
        rows.append({"artist": artist, "date": date_label, "place": city})
        if len(rows) >= 3:
            break
    return rows


def _music_genre(key):
    for genre_key, label, prompt_name in _MUSIC_GENRES:
        if genre_key == key:
            return label, prompt_name
    return "", ""


def _music_genre_menu_kb():
    buttons = [InlineKeyboardButton(label, callback_data=f"music_g_{key}")
               for key, label, _prompt_name in _MUSIC_GENRES]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_music_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу нового артиста в этом звучании."
    kb = _music_genre_menu_kb()
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def send_music_by_genre(bot, cid, genre_key, *, status=None):
    label, prompt_name = _music_genre(genre_key)
    if not prompt_name:
        await send_music_genre_menu(bot, cid)
        return
    await send_listen(
        bot, cid,
        category={"kind": "genre", "value": genre_key, "label": label, "prompt_name": prompt_name},
        force=True,
        status=status,
    )


def _music_preferences_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Мои артисты", callback_data="artist_favorites")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_music_preferences(bot, cid, q=None):
    text = "🎚️ Предпочтения музыки\n\nДобавь любимых артистов — так рекомендации будут точнее."
    kb = _music_preferences_kb()
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

async def listen_dislike(bot, cid, *, status=None):
    """Скрывает текущего артиста и заменяет его карточку следующим."""
    rec = store.last_recos.get(str(cid))
    category = rec.get("category") if isinstance(rec, dict) else None
    if rec and rec.get("kind") == "listen" and rec["items"]:
        recommendation_stoplist.add(cid, "artist", rec["items"][0], "hidden")
    _invalidate_artist(cid)
    await send_listen(bot, cid, category=category, force=bool(category), status=status)

def _item_text(item):
    """Текст элемента списка: элемент может быть строкой или {"id":..., "value": строка}
    (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _ensure_artists(cid):
    """Единая нормализация списка артистов для музыкальных рекомендаций."""
    return [_item_text(item) for item in store.get_list(config.FAVORITE_ARTISTS_KEY, cid)
            if _item_text(item)]


async def send_listen(bot, cid, *, preview=False, category=None, force=False, status=None):
    import saved_items
    _log.info("send_listen: start cid=%s", cid)
    category = category if isinstance(category, dict) and category.get("kind") == "genre" else None
    cached = None if category or force else _cached_artist(cid)
    if cached:
        artist = str(cached.get("artist") or "")
        if artist:
            if preview:
                return cached
            store.last_recos[str(cid)] = {"kind": "listen", "items": [artist]}
            store.last_source[str(cid)] = "Музыка"
            msg = leisure_ui.artist_card(cached)
            await _deliver_artist_card(
                bot, cid, msg, _listen_kb(saved=False), status=status)
            return
    arts_raw = _ensure_artists(cid)
    arts = [_item_text(a) for a in arts_raw if _item_text(a)]
    anchors = ", ".join(arts[:25])
    genre_context = (
        f"Подбор строго в жанре «{category.get('prompt_name') or category.get('label') or ''}». "
        "Не предлагай артиста из другого основного жанра."
        if category else ""
    )
    blocked = recommendation_stoplist.values(cid, "artist")
    recent = _recent_artists(cid)
    notes = store.get_list(config.CONTENT_RECORDS_KEY, cid)
    booked = [n.get("text", "") for n in notes
              if isinstance(n, dict) and "музык" in str(n.get("source", "")).lower()]
    known = (set(a.lower() for a in arts) | set(b.lower() for b in booked)
             | set(value.lower() for value in blocked) | set(value.lower() for value in recent))
    avoid_all = ", ".join(list(arts) + booked + blocked + recent)[:600]
    data = None
    rejected = []
    for attempt in range(3):
        avoid_this_try = avoid_all
        if rejected:
            avoid_this_try = f"{avoid_all}, {', '.join(rejected)}"[:600]
        try:
            cand = await ai.allm_json(
                "Ты — музыкальный эксперт-минималист. Пиши коротко, емко, без воды и лишних вводных слов "
                '(никаких "стоит отметить", "однако"). Используй контрастную структуру.\n'
                "Правила подбора ориентиров:\n"
                "1. Сравнивай только с релевантными группами из вкуса пользователя.\n"
                "2. Не смешивай полярные жанры: никакого симфо-метала, чистого клубного хауса "
                "и других дальних жанров в сравнениях, если их нет во вкусе пользователя.\n\n"
                f"Любимые исполнители пользователя (его вкус): {anchors}.\n"
                f"{genre_context}\n"
                f"НЕ предлагай никого из этого списка (уже в закладках/любимых/отклонены): {avoid_this_try}.\n"
                "Предложи РОВНО ОДНОГО НОВОГО исполнителя, максимально близкого по вкусу "
                "пользователя. Предпочитай современных активных артистов с выразительной, мелодичной, "
                "качественно спродюсированной музыкой. Исполнитель должен быть заметным, популярным или "
                "признанным в своей сцене — не выбирай чрезмерно малоизвестного артиста без сильного совпадения.\n"
                "Треки указывай ТОЛЬКО реально существующие — без выдуманных названий.\n"
                "В why дай 2 коротких контрастных пункта: сначала точное сходство, затем отличие/зацепку.\n"
                f"Попытка генерации: {attempt + 1}. Если сомневаешься, выбирай менее очевидный вариант.\n"
                "Верни строго такой JSON:\n"
                '{"artist": "имя исполнителя", '
                '"desc": "1-2 строки образно о звучании", '
                '"why": ["пункт 1 - на кого из его любимых похоже и чем", "пункт 2"], '
                '"tracks": ["трек 1 - короткая пометка", "трек 2", "трек 3"], '
                '"fact": "1 интересный факт об исполнителе"}',
                1000, tier="leisure", route="gemini", module="leisure")
        except Exception as e:
            _log.warning("send_listen: allm_json attempt=%s failed cid=%s: %r", attempt, cid, e, exc_info=True)
            # Cooldown или таймаут цепочки не исправится мгновенным повтором.
            # Сразу переходим к локальному резерву, чтобы не показывать ошибку.
            data = _local_artist_fallback(known, category)
            break
        cand_artist = str(cand.get("artist") or "").strip() if isinstance(cand, dict) else ""
        _log.info("send_listen: attempt=%s cid=%s cand_type=%s cand_artist=%r",
                  attempt, cid, type(cand).__name__, cand_artist)
        if cand_artist and cand_artist.lower() not in known:
            data = cand
            break
        if cand_artist:
            rejected.append(cand_artist)
        data = cand
    if not data or not data.get("artist"):
        data = _local_artist_fallback(known, category)
    if not data or not data.get("artist"):
        _log.info("send_listen: no data after retries cid=%s data=%r", cid, data)
        if preview:
            return None
        text = "Не удалось подобрать. Попробуй ещё раз."
        kb = back_menu_keyboard("m_music")
        if status is not None:
            await status.replace(text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return
    artist = data.get("artist", "")
    _remember_artist(cid, artist)
    _cache_artist(cid, data)
    if preview:
        return data
    rec = {"kind": "listen", "items": [artist]}
    if category:
        rec["category"] = category
    store.last_recos[str(cid)] = rec
    store.last_source[str(cid)] = "Музыка"
    try:
        msg = leisure_ui.artist_card(data)
    except Exception as e:
        _log.error("send_listen: artist_card render failed cid=%s data=%r: %r", cid, data, e, exc_info=True)
        raise
    store.last_answer[str(cid)] = leisure_ui.plain_from_html(msg.text)
    _log.info("send_listen: sending card cid=%s artist=%r", cid, artist)
    await _deliver_artist_card(
        bot, cid, msg, _listen_kb(saved_items.is_note_saved(cid, artist)), status=status)


async def _deliver_artist_card(bot, cid, msg, reply_markup, *, status=None):
    """Передаёт карточку через статусный сценарий либо отправляет новый экран."""
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=reply_markup)
        return
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=reply_markup)

async def add_listen(bot, cid, i, q=None):
    import saved_items
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        title = rec["items"][0]
        saved = saved_items.toggle_note(cid, title, source="Музыка")
        await saved_items.update_save_button(q, "listen_0", saved)
        if saved:
            _invalidate_artist(cid)
            await send_listen(bot, cid, category=rec.get("category"), force=bool(rec.get("category")))
