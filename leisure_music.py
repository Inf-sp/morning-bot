"""Музыкальные рекомендации и управление любимыми артистами."""

import asyncio
from copy import deepcopy
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import recommendation_stoplist
import recommendation_rotation as rotation
import secure
import settings
import store
import monthly_rebuses
import youtube_tracks
from ui import leisure as leisure_ui
from leisure_collection import plain_label

_log = logging.getLogger(__name__)
_MUSIC_DAILY_LOCK = threading.Lock()
_MUSIC_LEGEND_CACHE_VERSION = 2
_MUSIC_HOME_CACHE_VERSION = 1
_MUSIC_HOME_LOCKS = {}


def _music_home_only_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])

_MUSIC_GENRES = [
    ("indie", "Инди", "инди-поп или инди-рок"),
    ("pop", "Поп", "современный поп"),
    ("electronic", "Электроника", "электронная музыка"),
    ("rnb", "R&B", "R&B или соул"),
    ("rock", "Рок", "рок"),
    ("hiphop", "Хип-хоп", "хип-хоп"),
]
_MUSIC_STYLE_KEY = "music_styles"
_RECENT_ARTISTS_LIMIT = 40


def favorite_artist_genre(cid, artist):
    """Возвращает подтверждённый локальный жанр без выдуманного «Без жанра»."""
    normalized = str(artist or "").strip().casefold()
    cached = _cached_artist(cid)
    if normalized and str((cached or {}).get("artist") or "").strip().casefold() == normalized:
        key = str((cached or {}).get("genre") or "").strip().casefold()
        label = next((label for genre, label, _prompt in _MUSIC_GENRES if genre == key), "")
        if label:
            return label
    for key, candidates in _LOCAL_ARTIST_FALLBACKS.items():
        if any(str(item.get("artist") or "").strip().casefold() == normalized for item in candidates):
            return next((label for genre, label, _prompt in _MUSIC_GENRES if genre == key), "Другие артисты")
    return "Другие артисты"


def group_favorite_artist_items(cid, items):
    order = {label: index for index, (_key, label, _prompt) in enumerate(_MUSIC_GENRES)}
    order["Другие артисты"] = len(order)
    return sorted(
        list(items or []),
        key=lambda item: (
            order.get(favorite_artist_genre(cid, item[1]), len(order)),
            str(item[1] or "").casefold(),
        ),
    )

_MUSIC_REBUSES = monthly_rebuses.local_pool("music")
_MUSIC_LEGEND_FALLBACKS = {
    (8, 4): {
        "name": "Луи Армстронг",
        "birth": "1901-08-04",
        "detail": "трубач и певец, определивший язык сольного джаза",
    },
}
_MUSIC_TASKS = {
    "focus": (
        {"title": "Грузить мозг (фокус)", "track": "Introvert", "artist": "Little Simz",
         "tag": "Чёткий ритм и много деталей без лишней суеты.",
         "note": "Подойдёт для работы, где нужно держать мысль."},
        {"title": "Грузить мозг (фокус)", "track": "Simulation Swarm", "artist": "Big Thief",
         "tag": "Живой гитарный поток для длинной задачи.",
         "note": "Лучше включить, когда хочется сосредоточиться без жёсткого бита."},
    ),
    "workout": (
        {"title": "Тренировка", "track": "Delilah (pull me out of this)", "artist": "Fred again..",
         "tag": "Бит, который не даёт сбавить темп.", "note": "Для разминки или последнего подхода."},
        {"title": "Тренировка", "track": "Gorilla", "artist": "Little Simz",
         "tag": "Уверенный грув без лишнего пафоса.", "note": "Когда нужен темп, но не агрессия."},
    ),
    "commute": (
        {"title": "Дорога домой", "track": "Friday Morning", "artist": "Khruangbin",
         "tag": "Тёплый грув для медленного переключения.", "note": "Чтобы оставить рабочий день позади."},
        {"title": "Дорога домой", "track": "cellophane", "artist": "FKA twigs",
         "tag": "Тихая пауза без фонового шума.", "note": "Если хочется посмотреть в окно и никуда не спешить."},
    ),
    "conversation": (
        {"title": "Фон для разговора", "track": "Texas Sun", "artist": "Khruangbin & Leon Bridges",
         "tag": "Мягко поддерживает разговор, не перетягивая внимание.", "note": "Хорошо работает за ужином или в гостях."},
        {"title": "Фон для разговора", "track": "Two Weeks", "artist": "FKA twigs",
         "tag": "Воздушный поп с аккуратным ритмом.", "note": "Для тихой беседы без тишины между фразами."},
    ),
    "archive": (
        {"title": "Архивная находка", "track": "Heaven or Las Vegas", "artist": "Cocteau Twins",
         "tag": "Скрытый поп-шедевр 1990 года.", "note": "Мечтательная гитара и голос, который звучит как отдельный инструмент."},
        {"title": "Архивная находка", "track": "Tinseltown in the Rain", "artist": "The Blue Nile",
         "tag": "Ночной синт-поп из 80-х.", "note": "Редкий случай, когда городская меланхолия звучит очень тепло."},
    ),
}

# Последний резерв, когда все AI-провайдеры временно недоступны. Это реальные,
# достаточно известные артисты с существующими треками; выбор всё равно исключает
# уже знакомых, любимых, отклонённых и недавно показанных.
_LOCAL_ARTIST_FALLBACKS = {
    "indie": [
        {"artist": "Big Thief", "desc": "Живой инди-рок с хрупким вокалом и неожиданными поворотами.",
         "why": ["Тёплая, детальная гитарная музыка без лишнего шума.", "Песни звучат свободнее и острее обычного инди-попа."],
         "tracks": ["Not", "Simulation Swarm", "Vampire Empire"],
         "fact": "Американская инди-группа из Бруклина."},
        {"artist": "Alvvays", "desc": "Светлый гитарный инди-поп, в котором меланхолия звучит легко и очень точно.",
         "why": ["Мелодии сразу цепляют, но не становятся приторными.", "Вместо сырого инди-рока — больше поп-ясности и воздуха."],
         "tracks": ["Archie, Marry Me", "Dreams Tonite", "After the Earthquake"],
         "fact": "Канадская инди-поп-группа из Торонто."},
    ],
    "pop": [
        {"artist": "Caroline Polachek", "desc": "Артистичный поп с воздушным вокалом и точной электроникой.",
         "why": ["Мелодии остаются лёгкими, но аранжировки не банальные.", "Это поп с более странным и кинематографичным настроением."],
         "tracks": ["Bunny Is a Rider", "Welcome to My Island", "So Hot You're Hurting My Feelings"],
         "fact": "Американская певица и продюсер."},
        {"artist": "Rina Sawayama", "desc": "Поп, который свободно смешивает большие припевы, гитары и клубную электронику.",
         "why": ["Есть тот же масштабный поп-драйв, но с резкими неожиданными поворотами.", "Песни одновременно танцевальные и чуть более дерзкие."],
         "tracks": ["XS", "This Hell", "Comme des Garçons (Like the Boys)"],
         "fact": "Британско-японская певица и автор песен."},
    ],
    "electronic": [
        {"artist": "Fred again..", "desc": "Электроника, собранная из живых голосов, дневниковых фраз и мягких битов.",
         "why": ["Танцевальная музыка здесь остаётся очень личной и мелодичной.", "Подойдёт, если хочется движения без холодного клубного звучания."],
         "tracks": ["Delilah (pull me out of this)", "Danielle (smile on my face)", "adore u"],
         "fact": "Британский электронный музыкант и продюсер."},
        {"artist": "Bicep", "desc": "Тёплая клубная электроника с брейкбитом, ностальгическими синтами и большим эмоциональным размахом.",
         "why": ["Ритм держит темп, но мелодии не уходят в безликий фон.", "Больше рейвовой энергии и крупных синтезаторных моментов."],
         "tracks": ["Glue", "Apricots", "Atlas"],
         "fact": "Электронный дуэт из Белфаста."},
    ],
    "rnb": [
        {"artist": "Kelela", "desc": "Гладкий альтернативный R&B на стыке клубной электроники и мягкого соула.",
         "why": ["Вокал остаётся близким и спокойным, даже когда биты становятся резче.", "Звучание смелее привычного современного R&B."],
         "tracks": ["Rewind", "Washed Away", "On the Run"],
         "fact": "Американская певица и автор песен."},
        {"artist": "Jorja Smith", "desc": "Ночной R&B с мягким голосом, джазовыми оттенками и сдержанным грувом.",
         "why": ["Песни остаются интимными и мелодичными без лишней драматичности.", "Здесь больше живого соула и британской городской прохлады."],
         "tracks": ["Blue Lights", "Be Honest", "Little Things"],
         "fact": "Британская певица из Уолсолла."},
    ],
    "rock": [
        {"artist": "Fontaines D.C.", "desc": "Нервный, мелодичный рок с тёмным городским настроением.",
         "why": ["Есть напор и гитары, но песни не превращаются в шум.", "Подойдёт для более собранного и драматичного настроения."],
         "tracks": ["I Love You", "Starburster", "Favourite"],
         "fact": "Ирландская рок-группа, основанная в Дублине."},
        {"artist": "Wolf Alice", "desc": "Гитарный рок, который одинаково уверенно работает с шумом, мечтательностью и сильными припевами.",
         "why": ["Есть живая гитарная энергия, но мелодии остаются на первом плане.", "Диапазон шире: от тихой уязвимости до почти гранжевого напора."],
         "tracks": ["Don't Delete the Kisses", "Smile", "The Last Man on Earth"],
         "fact": "Британская рок-группа из Лондона."},
    ],
    "hiphop": [
        {"artist": "Little Simz", "desc": "Точный хип-хоп с сильным голосом, джазовыми деталями и личными историями.",
         "why": ["Ритм и тексты держат внимание без показной агрессии.", "В музыке много масштаба, но она остаётся очень личной."],
         "tracks": ["Introvert", "Gorilla", "Woman"],
         "fact": "Британская рэперша из Лондона."},
        {"artist": "Noname", "desc": "Спокойный, умный хип-хоп с джазовыми сэмплами, точным флоу и разговорной интонацией.",
         "why": ["Тексты и ритм требуют внимания, но музыка не давит тяжестью.", "Вместо большого стадионного жеста — камерная и очень живая подача."],
         "tracks": ["Diddy Bop", "Song 31", "Rainforest"],
         "fact": "Рэперша и поэтесса из Чикаго."},
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


def _local_artist_fallback(known, category=None, *, recent=None):
    """Возвращает артиста без сети; после полного круга начинает новый цикл."""
    key = category.get("value") if isinstance(category, dict) else "default"
    candidates = list(
        _LOCAL_ARTIST_FALLBACKS.get(key) or _LOCAL_ARTIST_FALLBACKS["default"]
    )
    blocked = {str(value or "").casefold() for value in known}
    candidates = [
        item for item in candidates
        if str(item.get("artist") or "").casefold() not in blocked
    ]
    recent = list(recent or [])
    available = rotation.candidates_for_cycle(
        candidates, recent,
        current=recent[-1] if recent else None,
        key=lambda item: str(
            item.get("artist") if isinstance(item, dict) else item or ""
        ).casefold(),
    )
    return dict(available[0]) if available else None


def _cached_artist(cid):
    entry = (store._load(config.MUSIC_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    item = entry.get("item")
    today = datetime.now(config.TZ).date().isoformat()
    styles = sorted(_music_styles(cid))
    if entry.get("date") != today or entry.get("styles") != styles or not isinstance(item, dict):
        return None
    return dict(item)


def _cache_artist(cid, item):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {
            "date": datetime.now(config.TZ).date().isoformat(),
            "styles": sorted(_music_styles(cid)),
            "item": dict(item or {}),
        }
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
    return rotation.recent(
        values if isinstance(values, list) else [], limit=_RECENT_ARTISTS_LIMIT,
    )


def _remember_artist(cid, artist):
    artist = str(artist or "").strip()
    if not artist:
        return
    def change(profile):
        values = profile.get("music_recent_artists", [])
        profile["music_recent_artists"] = rotation.remember(
            values if isinstance(values, list) else [], artist,
            limit=_RECENT_ARTISTS_LIMIT,
        )
        return profile, None

    store.mutate_profile(cid, change)


def _add_unique(key, cid, value):
    value = plain_label(value)
    items = store.get_list(key, cid)
    if value and value.lower() not in {_item_text(item).lower() for item in items}:
        store.set_list(key, cid, [*items, value])


def _kick_off_new_artist_concert_check(cid, artist_names):
    """Сразу проверяет нового артиста через обычную концертную цепочку."""
    import leisure_concerts
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"

    async def _run():
        for name in artist_names:
            try:
                await leisure_concerts.refresh_new_artist_concerts(cid, name, cc, cname)
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
            await q.message.edit_reply_markup(reply_markup=_listen_kb())


def _favorite_artist_style_labels(cid):
    selected = set(_music_styles(cid))
    return [label for key, label, _prompt_name in _MUSIC_GENRES if key in selected]


def _favorite_artist_added_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мои артисты", callback_data="artist_favorites")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_favorite_artists_added_card(bot, cid, artists):
    """Показывает результат ручного добавления, не подменяя его списком."""
    artists = [str(artist or "").strip() for artist in artists or [] if str(artist or "").strip()]
    if not artists:
        return
    cached = _cached_artist(cid)
    cached_artist = str((cached or {}).get("artist") or "").strip()
    if len(artists) == 1:
        data = cached if cached_artist.casefold() == artists[0].casefold() else None
        msg = leisure_ui.favorite_artist_added_card(
            artists[0], _favorite_artist_style_labels(cid), data=data)
    else:
        msg = leisure_ui.favorite_artists_added_card(artists, _favorite_artist_style_labels(cid))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=_favorite_artist_added_kb())


def _listen_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 По жанру", callback_data="music_genre_menu")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def music_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎸 Что послушать", callback_data="music_reco")],
        [InlineKeyboardButton("🎚️ Мои артисты", callback_data="artist_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _daily_music_rebus(day):
    return monthly_rebuses.cached_for_day("music", day, _MUSIC_REBUSES)


def _track_parts(track):
    if isinstance(track, dict):
        title = str(track.get("title") or track.get("track") or track.get("name") or "").strip()
        return title, str(track.get("note") or "").strip(), str(track.get("url") or "").strip()
    title, separator, note = str(track or "").partition(" - ")
    return title.strip(), note.strip() if separator else "", ""


def _youtube_music_search_url(track, artist):
    query = " ".join(part.strip() for part in (track, artist) if str(part or "").strip())
    return f"https://music.youtube.com/search?q={quote_plus(query)}" if query else ""


_TRACK_FALLBACK_NOTES = ("знаковый хит", "другая грань звучания", "для следующего шага")


async def _attach_track_links(data):
    """Добавляет к трекам карточки проверенную ссылку или точный поиск YouTube Music."""
    data = dict(data or {})
    artist = str(data.get("artist") or "").strip()
    tracks = list(data.get("tracks") or [])[:3]
    if not artist or not tracks:
        return data

    async def link_track(track, index):
        title, note, url = _track_parts(track)
        if not title:
            return None
        if not url:
            try:
                url = await asyncio.to_thread(youtube_tracks.find_track_url, title, artist)
            except Exception:
                url = ""
        return {
            "title": title,
            "note": note or _TRACK_FALLBACK_NOTES[min(index, len(_TRACK_FALLBACK_NOTES) - 1)],
            "url": url or _youtube_music_search_url(title, artist),
        }

    linked = await asyncio.gather(*(link_track(track, index) for index, track in enumerate(tracks)))
    data["tracks"] = [track for track in linked if track]
    return data


def _music_legend_cache_get(day):
    data = store._load(config.MUSIC_DAILY_CACHE_KEY)
    entry = data.get(day.isoformat()) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("version") != _MUSIC_LEGEND_CACHE_VERSION:
        return None
    legend = entry.get("legend") if isinstance(entry, dict) else None
    return dict(legend) if isinstance(legend, dict) else None


def _music_legend_cache_set(day, legend):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[day.isoformat()] = {
            "version": _MUSIC_LEGEND_CACHE_VERSION,
            "ts": time.time(),
            "legend": dict(legend or {}),
        }
        return data, None

    store.mutate_kv(config.MUSIC_DAILY_CACHE_KEY, mutate)


def _music_legend_detail(role):
    value = str(role or "").casefold()
    if "певиц" in value or "singer" in value:
        return "певица"
    if "певец" in value or "vocalist" in value:
        return "певец"
    if "композитор" in value or "composer" in value:
        return "композитор"
    return "музыкант"


def _load_music_legend(day):
    """Один именинник из музыки на день; запрос общий и не повторяется для каждого чата."""
    cached = _music_legend_cache_get(day)
    if cached is not None:
        return cached
    with _MUSIC_DAILY_LOCK:
        cached = _music_legend_cache_get(day)
        if cached is not None:
            return cached
        fallback = _MUSIC_LEGEND_FALLBACKS.get((day.month, day.day))
        if fallback:
            _music_legend_cache_set(day, fallback)
            return dict(fallback)
        query = """
            SELECT ?personLabel ?birth ?occupationLabel (wikibase:sitelinks(?person) AS ?sitelinks) WHERE {
              ?person wdt:P31 wd:Q5; wdt:P569 ?birth; wdt:P106 ?occupation.
              VALUES ?occupation { wd:Q639669 wd:Q177220 wd:Q36834 }
              FILTER(MONTH(?birth) = %d && DAY(?birth) = %d)
              SERVICE wikibase:label { bd:serviceParam wikibase:language \"ru,en\". }
            }
            ORDER BY DESC(?sitelinks)
            LIMIT 1
        """ % (day.month, day.day)
        try:
            response = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json", "User-Agent": "morning-bot/1.0"},
                timeout=6,
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
            if bindings:
                item = bindings[0]
                name = str((item.get("personLabel") or {}).get("value") or "").strip()
                if name:
                    legend = {
                        "name": name,
                        "detail": _music_legend_detail((item.get("occupationLabel") or {}).get("value")),
                    }
                    birth = str((item.get("birth") or {}).get("value") or "").strip()
                    if birth:
                        legend["birth"] = birth
                    _music_legend_cache_set(day, legend)
                    return legend
        except Exception as error:
            _log.info("music legend lookup unavailable: %s", type(error).__name__)
        _music_legend_cache_set(day, {})
        return {}

# Artist vitrine fact helper
_ARTIST_FACT_CACHE = {}
_ARTIST_FACT_LOCK = threading.Lock()
_ARTIST_FACT_WIKI_UA = {"User-Agent": "morning-bot/1.0"}


def _clean_artist_fact(value):
    text = " ".join(str(value or "").split()).strip()
    if len(text) > 220:
        text = text[:219].rstrip() + "..."
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _local_artist_fact(name):
    normalized = str(name or "").strip().casefold()
    if not normalized:
        return ""
    for items in _LOCAL_ARTIST_FALLBACKS.values():
        for item in items:
            if str(item.get("artist") or "").strip().casefold() == normalized:
                return _clean_artist_fact(item.get("fact"))
    return ""


def _artist_wiki_fact(name):
    query = " ".join(str(name or "").split()).strip()
    if not query:
        return ""
    for lang in ("ru", "en"):
        try:
            url = "https://" + lang + ".wikipedia.org/api/rest_v1/page/summary/" + quote_plus(query)
            response = requests.get(url, headers=_ARTIST_FACT_WIKI_UA, timeout=6)
            if response.status_code != 200:
                continue
            data = response.json()
            extract = " ".join(str(data.get("extract") or "").split()).strip()
            if extract:
                return _clean_artist_fact(extract.partition(". ")[0])
        except Exception as error:
            _log.info("artist wiki fact lookup unavailable for %r: %s", name, type(error).__name__)
    return ""


def _artist_fact(name):
    normalized = str(name or "").strip().casefold()
    if not normalized:
        return ""
    with _ARTIST_FACT_LOCK:
        if normalized in _ARTIST_FACT_CACHE:
            return _ARTIST_FACT_CACHE[normalized]
        fact = _local_artist_fact(name) or _artist_wiki_fact(name)
        _ARTIST_FACT_CACHE[normalized] = fact
        return fact


def _music_city(cid):
    settings_data = store.get_settings(cid)
    return str(settings_data.get("city") or config.DEFAULT_CITY.get("name") or "").strip()


async def _daily_music_content(cid):
    today = datetime.now(config.TZ).date()
    return {
        "rebus": await monthly_rebuses.for_day("music", today, _MUSIC_REBUSES),
    }


def _task_for_today(key):
    choices = _MUSIC_TASKS.get(key) or ()
    if not choices:
        return None
    index = datetime.now(config.TZ).date().timetuple().tm_yday % len(choices)
    return dict(choices[index])


def _music_task_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_music_task(bot, cid, key, *, status=None):
    task = _task_for_today(key)
    if not task:
        return
    msg = leisure_ui.music_activity_screen(task)
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=_music_task_keyboard())
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_music_task_keyboard())


def _music_home_context(cid):
    settings_data = store.get_settings(cid)
    return {
        "city": _music_city(cid),
        "country": str(settings_data.get("cc") or "NL").upper(),
        "artists": sorted(artist.casefold() for artist in _ensure_artists(cid)),
    }


def _music_home_cache_get(cid):
    data = store._load(config.MUSIC_HOME_CACHE_KEY)
    entry = data.get(str(cid)) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    if (
        entry.get("version") != _MUSIC_HOME_CACHE_VERSION
        or entry.get("date") != datetime.now(config.TZ).date().isoformat()
        or entry.get("context") != _music_home_context(cid)
        or not isinstance(entry.get("daily_music"), dict)
        or not isinstance(entry.get("concerts"), list)
    ):
        return None
    return {
        "city": str(entry.get("city") or _music_city(cid)),
        "daily_music": deepcopy(entry["daily_music"]),
        "concerts": deepcopy(entry["concerts"]),
    }


def _music_home_cache_set(cid, value):
    context = _music_home_context(cid)

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {
            "version": _MUSIC_HOME_CACHE_VERSION,
            "date": datetime.now(config.TZ).date().isoformat(),
            "context": context,
            "city": value["city"],
            "daily_music": deepcopy(value["daily_music"]),
            "concerts": deepcopy(value["concerts"]),
        }
        return data, None

    store.mutate_kv(config.MUSIC_HOME_CACHE_KEY, mutate)


async def _music_home_data(cid):
    cached = _music_home_cache_get(cid)
    if cached is not None:
        return cached
    lock = _MUSIC_HOME_LOCKS.setdefault(str(cid), asyncio.Lock())
    async with lock:
        cached = _music_home_cache_get(cid)
        if cached is not None:
            return cached
        daily_music, concerts = await asyncio.gather(
            _daily_music_content(cid), _weekly_concerts(cid),
        )
        value = {
            "city": _music_city(cid),
            "daily_music": daily_music,
            "concerts": concerts,
        }
        _music_home_cache_set(cid, value)
        return deepcopy(value)


async def send_music_home(bot, cid, q=None, status=None):
    """Открывает ежедневную музыкальную витрину; артиста выбирают отдельной кнопкой."""
    home = await _music_home_data(cid)
    msg = leisure_ui.music_week_screen(
        home["city"], home["daily_music"], home["concerts"],
        day=datetime.now(config.TZ).date(),
    )
    kb = music_home_keyboard()
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def warm_music_home_cache(cid):
    """Готовит данные музыкальной витрины без запроса персональной рекомендации."""
    await _music_home_data(cid)
    return True


async def _weekly_concerts(cid):
    """Ближайшие подтверждённые концерты любимых артистов из общей цепочки поиска."""
    import leisure_concerts
    from util import _MONTHS

    settings_data = store.get_settings(cid)
    cc = str(settings_data.get("cc") or "NL").upper()
    country = str(settings_data.get("country") or cc).strip()
    artists = leisure_concerts._ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return []
    events = leisure_concerts._concerts_cache_get(cid, cc)
    if events is None:
        events = await leisure_concerts._fetch_concerts(artists, cc, country, cid=cid)
        leisure_concerts._concerts_cache_set(cid, cc, events)

    today = datetime.now(config.TZ).date().isoformat()
    rows, seen = [], set()
    for event in sorted(
        events,
        key=lambda item: leisure_concerts._event_date(item) or "9999-99-99",
    ):
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
        rows.append({
            "artist": artist,
            "date": date_label,
            "place": city,
            "context": leisure_concerts._concert_context(event),
            "url": str(event.get("url") or "").strip(),
        })
        if len(rows) >= 5:
            break
    if rows:
        rows[0]["artist_fact"] = _artist_fact(rows[0]["artist"])
    return rows


def _music_genre(key):
    for genre_key, label, prompt_name in _MUSIC_GENRES:
        if genre_key == key:
            return label, prompt_name
    return "", ""


def _music_genre_menu_kb(cid):
    selected = set(_music_styles(cid))
    buttons = [InlineKeyboardButton(label, callback_data=f"music_g_{key}")
               for key, label, _prompt_name in _MUSIC_GENRES if key in selected]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_music_genre_menu(bot, cid, q=None):
    if not _music_styles(cid):
        await send_music_preferences(bot, cid, q)
        return
    text = "Выбери один из отмеченных стилей — подберу нового артиста в этом звучании."
    kb = _music_genre_menu_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def send_music_by_genre(bot, cid, genre_key, *, status=None):
    label, prompt_name = _music_genre(genre_key)
    if not prompt_name or genre_key not in _music_styles(cid):
        await _prompt_for_music_styles(bot, cid, status=status)
        return
    await send_listen(
        bot, cid,
        category={"kind": "genre", "value": genre_key, "label": label, "prompt_name": prompt_name},
        force=True,
        status=status,
    )


def _music_styles(cid):
    selected = settings.get(cid, _MUSIC_STYLE_KEY, [])
    if not isinstance(selected, list):
        return []
    valid = {key for key, _label, _prompt_name in _MUSIC_GENRES}
    return [key for key in selected if key in valid]


def _music_style_context(cid):
    selected = set(_music_styles(cid))
    labels = [prompt_name for key, _label, prompt_name in _MUSIC_GENRES if key in selected]
    if not labels:
        return ""
    return "Любимые стили пользователя: " + ", ".join(labels) + "."


def _music_preferences_kb(cid):
    selected = set(_music_styles(cid))
    buttons = [
        InlineKeyboardButton(("✅ " if key in selected else "⬜ ") + label,
                             callback_data=f"music_style_{key}")
        for key, label, _prompt_name in _MUSIC_GENRES
    ]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="artist_favorites"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_music_preferences(bot, cid, q=None):
    text = "🎧 Музыка\n\nВыбери хотя бы один стиль — рекомендации будут только из отмеченных жанров."
    kb = _music_preferences_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


def _music_preferences_required_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔣 Выбрать предпочтения", callback_data="music_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def _prompt_for_music_styles(bot, cid, *, status=None):
    text = "Сначала выбери хотя бы один музыкальный жанр."
    kb = _music_preferences_required_kb()
    if status is not None:
        await status.replace(text, reply_markup=kb)
        return
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def toggle_music_style(bot, cid, style_key, q=None):
    valid = {key for key, _label, _prompt_name in _MUSIC_GENRES}
    if style_key not in valid:
        return
    selected = _music_styles(cid)
    if style_key in selected:
        selected.remove(style_key)
    else:
        selected.append(style_key)
    settings.set_(cid, _MUSIC_STYLE_KEY, selected)
    _invalidate_artist(cid)
    await send_music_preferences(bot, cid, q)

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
    _log.info("send_listen: start cid=%s", cid)
    category = category if isinstance(category, dict) and category.get("kind") == "genre" else None
    selected_styles = _music_styles(cid)
    if not selected_styles or (category and category.get("value") not in selected_styles):
        if preview:
            return None
        await _prompt_for_music_styles(bot, cid, status=status)
        return
    cached = None if category or force else _cached_artist(cid)
    if cached:
        artist = str(cached.get("artist") or "")
        if artist:
            if preview:
                return cached
            cached = await _attach_track_links(cached)
            _cache_artist(cid, cached)
            store.last_recos[str(cid)] = {"kind": "listen", "items": [artist]}
            store.last_source[str(cid)] = "Музыка"
            msg = leisure_ui.artist_card(cached)
            await _deliver_artist_card(
                bot, cid, msg, _listen_kb(), status=status)
            return
    arts_raw = _ensure_artists(cid)
    arts = [_item_text(a) for a in arts_raw if _item_text(a)]
    anchors = ", ".join(arts[:25])
    if category:
        genre_context = (
            f"Подбор строго в жанре «{category.get('prompt_name') or category.get('label') or ''}». "
            "Не предлагай артиста из другого основного жанра."
        )
    else:
        allowed = [prompt_name for key, _label, prompt_name in _MUSIC_GENRES if key in selected_styles]
        genre_context = (
            "Подбор строго только в выбранных жанрах: " + ", ".join(allowed) + ". "
            "Не предлагай артиста из другого основного жанра."
        )
    style_context = _music_style_context(cid) if not category else ""
    fallback_category = category or {
        "value": selected_styles[datetime.now(config.TZ).date().timetuple().tm_yday % len(selected_styles)],
    }
    blocked = recommendation_stoplist.values(cid, "artist")
    recent = _recent_artists(cid)
    hard_blocked = (
        set(a.lower() for a in arts) | set(value.lower() for value in blocked)
    )
    known = hard_blocked | set(value.lower() for value in recent)
    avoid_all = ", ".join(list(arts) + blocked + recent)[:600]
    safe_anchors = secure.wrap_untrusted(anchors or "список пуст", "любимые артисты")
    safe_avoid = secure.wrap_untrusted(avoid_all or "список пуст", "исключённые артисты")
    data = None
    allowed_genres = {category["value"]} if category else set(selected_styles)
    try:
        generated = await ai.allm_json(
                "Ты — музыкальный эксперт-минималист. Пиши коротко, емко, без воды и лишних вводных слов "
                '(никаких "стоит отметить", "однако"). Используй контрастную структуру.\n'
                "Правила подбора ориентиров:\n"
                "1. Сравнивай только с релевантными группами из вкуса пользователя.\n"
                "2. Не смешивай полярные жанры: никакого симфо-метала, чистого клубного хауса "
                "и других дальних жанров в сравнениях, если их нет во вкусе пользователя.\n\n"
                f"Любимые исполнители пользователя (его вкус): {safe_anchors}.\n"
                f"{genre_context}\n"
                f"{style_context}\n"
                f"НЕ предлагай никого из этого списка (уже в любимых, отклонены или недавно показаны): {safe_avoid}.\n"
                "Предложи ТРЁХ РАЗНЫХ новых исполнителей, максимально близких по вкусу "
                "пользователя. Предпочитай современных активных артистов с выразительной, мелодичной, "
                "качественно спродюсированной музыкой. Исполнитель должен быть заметным, популярным или "
                "признанным в своей сцене — не выбирай чрезмерно малоизвестного артиста без сильного совпадения.\n"
                "Треки указывай ТОЛЬКО реально существующие — без выдуманных названий.\n"
                "В why дай 2 коротких контрастных пункта: сначала точное сходство, затем отличие/зацепку.\n"
                "Верни строго такой JSON:\n"
                '{"candidates": [{"artist": "имя исполнителя", '
                f'"genre": "один ключ из {", ".join(sorted(allowed_genres))}", '
                '"desc": "1-2 строки образно о звучании", '
                '"why": ["пункт 1 - на кого из его любимых похоже и чем", "пункт 2"], '
                '"tracks": ["трек 1 - короткая пометка", "трек 2 - короткая пометка", "трек 3 - короткая пометка"], '
                '"fact": "1 интересный факт об исполнителе"}]}',
                1500, tier="leisure", route="gemini", module="leisure")
    except Exception as e:
        _log.warning("send_listen: allm_json failed cid=%s: %r", cid, e, exc_info=True)
        generated = {}
    candidates = generated.get("candidates") if isinstance(generated, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    # Совместимость с валидным одиночным ответом старого кэша/резерва.
    if not candidates and isinstance(generated, dict) and generated.get("artist"):
        candidates = [generated]
    for cand in candidates[:3]:
        cand_artist = str(cand.get("artist") or "").strip() if isinstance(cand, dict) else ""
        cand_genre = str(cand.get("genre") or "").strip().casefold() if isinstance(cand, dict) else ""
        _log.info("send_listen: candidate cid=%s cand_type=%s cand_artist=%r",
                  cid, type(cand).__name__, cand_artist)
        if cand_artist and cand_genre in allowed_genres and cand_artist.lower() not in known:
            data = cand
            break
    if not data or not data.get("artist"):
        data = _local_artist_fallback(
            hard_blocked, fallback_category, recent=recent,
        )
    if not data or not data.get("artist"):
        _log.info("send_listen: no data after retries cid=%s data=%r", cid, data)
        if preview:
            return None
        text = "Не удалось подобрать. Попробуй ещё раз."
        kb = _music_home_only_kb()
        if status is not None:
            await status.replace(text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return
    artist = data.get("artist", "")
    data = await _attach_track_links(data)
    _cache_artist(cid, data)
    if preview:
        return data
    _remember_artist(cid, artist)
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
        bot, cid, msg, _listen_kb(), status=status)


async def _deliver_artist_card(bot, cid, msg, reply_markup, *, status=None):
    """Передаёт карточку через статусный сценарий либо отправляет новый экран."""
    if status is not None:
        await status.replace(
            msg.text, entities=msg.entities, reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
