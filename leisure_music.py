"""Музыкальные рекомендации и управление любимыми артистами."""

import asyncio
import logging
from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import recommendation_stoplist
import recommendation_rotation as rotation
import secure
import settings
import store
import util
import youtube_tracks
from ui import leisure as leisure_ui
from leisure_collection import plain_label
from ui.navigation import nav_row
import rich_delivery

_log = logging.getLogger(__name__)
_BACKGROUND_TASKS = set()


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
    # Артисты без жанра — первыми и без заголовка группы, чтобы не казались частью последнего жанра.
    order["Другие артисты"] = -1
    return sorted(
        list(items or []),
        key=lambda item: (
            order.get(favorite_artist_genre(cid, item[1]), len(order)),
            str(item[1] or "").casefold(),
        ),
    )

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
_LITTLE_SIMZ = {
    "artist": "Little Simz", "style": "британский хип-хоп",
    "desc": "Плотный, умный рэп на фоне оркестровых струнных, соула и джаза. Little Simz читает быстро и точно, "
            "но без показной агрессии — её песни больше похожи на честный разговор с собой.",
    "tracks": ["Introvert - кинематографичный, с оркестром и хором",
               "Woman - мягкий соул-грув, ода женщинам",
               "Gorilla - жёстче и веселее, на фанковом бите"],
    "fact": "Альбом «Sometimes I Might Be Introvert» получил Mercury Prize в 2022 году.",
}
_LOCAL_ARTIST_FALLBACKS = {
    "indie": [
        {"artist": "Big Thief", "style": "инди-фолк",
         "desc": "Песни Big Thief звучат так, будто их записали в одной комнате с первого дубля: тихие гитары, "
                 "шорох и голос Adrianne Lenker, который ломается на самых важных словах. Через минуту та же "
                 "песня может взорваться шумом и перегрузом.",
         "tracks": ["Masterpiece - тёплый гитарный инди-рок о памяти и близких",
                    "Not - то же напряжение, разогнанное до гитарного шторма",
                    "Simulation Swarm - тихий фолк с почти колыбельной мелодией"],
         "fact": "Альбом «Dragon New Warm Mountain I Believe in You» (2022) номинировали на «Грэмми» "
                 "как лучший альтернативный альбом."},
        {"artist": "Alvvays", "style": "дрим-поп",
         "desc": "Звонкие гитары в духе джэнгл-попа 80-х, лёгкий шум плёнки и чистый голос Molly Rankin поверх "
                 "него. Меланхоличные истории о неудачной любви звучат у них солнечно и почти беззаботно.",
         "tracks": ["Archie, Marry Me - солнечный хит с припевом, который хочется петь",
                    "Dreams Tonite - медленнее и мечтательнее, синты вместо напора",
                    "Belinda Says - нарастает от шёпота до шумной кульминации"],
         "fact": "Альбом «Blue Rev» (2022) номинировали на «Грэмми» как лучший альтернативный альбом."},
    ],
    "pop": [
        {"artist": "Caroline Polachek", "style": "арт-поп",
         "desc": "Поп, в котором голос делает невероятные кульбиты: от шёпота до почти оперных высот за одну фразу. "
                 "Под ним — блестящая электроника, волынки, фламенко-гитары и много воздуха, так что каждая песня "
                 "звучит как маленький фильм.",
         "tracks": ["So Hot You're Hurting My Feelings - лёгкий, почти танцевальный поп-хит",
                    "Bunny Is a Rider - минималистичнее и холоднее, на упругом басе",
                    "Welcome to My Island - громкий и театральный, с криком в начале"],
         "fact": "До сольной карьеры была вокалисткой и соавтором песен инди-поп-группы Chairlift."},
        {"artist": "Rina Sawayama", "style": "поп",
         "desc": "Нулевые в лучшем смысле: большие поп-припевы, ню-металовые гитары, R&B и клубные биты в одной "
                 "песне. Rina Sawayama поёт о семье, идентичности и потреблении — с юмором и без пафоса.",
         "tracks": ["XS - поп-сатира на тягу к роскоши с внезапными гитарами",
                    "STFU! - самая тяжёлая, почти ню-метал",
                    "This Hell - кантри-поп с весёлым драйвом"],
         "fact": "После её кампании BRIT Awards и Mercury Prize в 2021 году изменили правила и стали допускать "
                 "артистов без британского гражданства, давно живущих в UK."},
    ],
    "electronic": [
        {"artist": "Fred again..", "style": "хаус",
         "desc": "Музыка из голосовых сообщений, обрывков видео и случайно подслушанных фраз, собранная в "
                 "эйфорические хаус-треки. Она одновременно танцевальная и очень личная — будто чужой дневник под бит.",
         "tracks": ["Marea (We've Lost Dancing) - эйфорический хаус с голосом The Blessed Madonna",
                    "Delilah (pull me out of this) - драматичнее, на сэмпле Delilah Montagu",
                    "Jungle - жёстче и быстрее, почти рейв"],
         "fact": "В 2020 году получил BRIT Award как продюсер года — ещё до выхода первого сольного альбома."},
        {"artist": "Bicep", "style": "клубная электроника",
         "desc": "Тёплые синтезаторы, рейвовые брейкбиты и ностальгия по клубам 90-х. Треки раскручиваются "
                 "медленно, но к середине накрывают волной — под неё одинаково хорошо и танцевать, и ехать ночью "
                 "по трассе.",
         "tracks": ["Glue - медленный и щемящий гимн ностальгии по рейвам",
                    "Atlas - быстрее и светлее, с вокальным сэмплом",
                    "Apricots - мягче, на сэмплах старых хоровых записей"],
         "fact": "Начинали с музыкального блога Feel My Bicep, который потом вырос в их лейбл и клубные вечеринки."},
    ],
    "rnb": [
        {"artist": "Kelela", "style": "альтернативный R&B",
         "desc": "R&B, растворённый в клубной электронике: мягкий высокий голос плывёт над холодными синтами и "
                 "ломаными битами. Музыка звучит интимно и при этом футуристично — как поздний вечер в пустом городе.",
         "tracks": ["LMK - самый лёгкий и ироничный, почти поп",
                    "Rewind - быстрее, с клубным битом и нервным пульсом",
                    "Washed Away - почти без бита, медленный эмбиент"],
         "fact": "Дебютный альбом «Take Me Apart» (2017) вышел на британском лейбле Warp."},
        {"artist": "Jorja Smith", "style": "неосоул",
         "desc": "Тёплый низкий голос и сдержанный грув: Jorja Smith поёт негромко, но каждая фраза звучит как "
                 "признание. Под ней — джазовые аккорды, лёгкий гэридж и много пространства.",
         "tracks": ["Blue Lights - тихая песня под фортепиано о полицейских сиренах",
                    "Be Honest - солнечнее: афробит-ритм и дуэт с Burna Boy",
                    "Little Things - танцевальная, на быстром гэридж-бите"],
         "fact": "В 2019 году получила BRIT Award как лучшая британская исполнительница."},
    ],
    "rock": [
        {"artist": "Fontaines D.C.", "style": "пост-панк",
         "desc": "Дублинский пост-панк с шершавыми гитарами и вокалом Grian Chatten, который скорее читает стихи, "
                 "чем поёт. Песни звучат мрачно и упрямо, но в последних альбомах прорывается почти стадионная "
                 "мелодичность.",
         "tracks": ["Boys in the Better Land - быстрый и злой, как концерт в маленьком клубе",
                    "A Hero's Death - мантра «Life ain't always empty» на одном риффе",
                    "Starburster - тревожный, с одышкой и хип-хоп-битом"],
         "fact": "Альбом «A Hero's Death» (2020) номинировали на «Грэмми» как лучший рок-альбом."},
        {"artist": "Wolf Alice", "style": "альтернативный рок",
         "desc": "Группа, которая не сидит в одном жанре: шугейз-туман, гранжевый напор и почти поп-баллады "
                 "уживаются на одной пластинке. Голос Ellie Rowsell легко переходит от шёпота к крику.",
         "tracks": ["Don't Delete the Kisses - мечтательный синти-поп с проговорённым куплетом",
                    "Yuk Foo - две минуты чистой панк-ярости",
                    "The Last Man on Earth - фортепианная баллада с большим финалом"],
         "fact": "Альбом «Visions of a Life» получил Mercury Prize в 2018 году."},
    ],
    "hiphop": [
        dict(_LITTLE_SIMZ),
        {"artist": "Noname", "style": "джазовый рэп",
         "desc": "Рэп, который звучит как поэзия на джазовом концерте: тихая подача, живые клавишные и ироничные "
                 "строки о больших темах. Noname почти не повышает голос — сила в том, что она говорит.",
         "tracks": ["Diddy Bop - солнечный, с детскими воспоминаниями о Чикаго",
                    "Song 31 - упругий бас и острее по смыслу",
                    "Rainforest - медленный грув в духе неосоула"],
         "fact": "Дебютный микстейп «Telefone» (2016) выпустила самостоятельно, без лейбла."},
    ],
    "default": [
        {"artist": "FKA twigs", "style": "арт-поп",
         "desc": "Хрупкий высокий голос, ломаные электронные биты и много тишины между звуками. Её песни бывают "
                 "одновременно чувственными и пугающими — R&B, разобранный на детали и собранный заново.",
         "tracks": ["Two Weeks - самый тёплый и чувственный хит",
                    "cellophane - почти только голос и фортепиано",
                    "Eusexua - быстрее, в духе ночного техно"],
         "fact": "Дебютный альбом «LP1» (2014) номинировали на Mercury Prize."},
        {"artist": "Khruangbin", "style": "психоделический фанк",
         "desc": "Неспешная гитара с восточными ладами, упругий бас и барабаны, которые никуда не торопятся. Почти "
                 "без слов — музыка для долгой дороги, жаркого вечера или фона, к которому всё время возвращаешься.",
         "tracks": ["Time (You and I) - лёгкое диско для медленного танца",
                    "Maria También - быстрее и острее, на восточном риффе",
                    "Texas Sun - соул-баллада с голосом Leon Bridges"],
         "fact": "Название группы — тайское слово «самолёт»: на их звук сильно повлияли тайский фанк и рок 60–70-х."},
        dict(_LITTLE_SIMZ),
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


def _cached_artist(cid, stale=False):
    """Артист дня; stale — резерв до недели, пока нового не подобрать."""
    entry = (store._load(config.MUSIC_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    item = entry.get("item")
    styles = sorted(_music_styles(cid))
    if (not util.reco_date_ok(entry.get("date"), stale) or entry.get("styles") != styles
            or not isinstance(item, dict)):
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
    _warm_latest_album(str((item or {}).get("artist") or ""))


def _warm_latest_album(artist):
    """Последний альбом артиста дня для строки «Досуга на сегодня» — в фоне: карточка не ждёт MusicBrainz."""
    if not artist or not util.on_event_loop():
        return
    import countries
    util.background(asyncio.to_thread(countries.artist_latest_album, artist), "artist album")


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

    # Держим ссылку: иначе event loop хранит только weakref и задача может быть собрана GC.
    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def listen_love(bot, cid, q=None):
    """Добавляет артиста в любимые без дублей."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        category = rec.get("category") if isinstance(rec.get("category"), dict) else None
        _add_unique(config.FAVORITE_ARTISTS_KEY, cid, artist)
        _invalidate_artist(cid)
        _kick_off_new_artist_concert_check(cid, [artist])
        await send_listen(bot, cid, category=category, force=True)


def _favorite_artist_style_labels(cid):
    selected = set(_music_styles(cid))
    return [label for key, label, _prompt_name in _MUSIC_GENRES if key in selected]


def _favorite_artist_added_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мои артисты", callback_data="artist_favorites")],
        nav_row("lz_lib"),
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
        [InlineKeyboardButton("Не интересно", callback_data="listen_no", api_kwargs={"style": "danger"})],
        [InlineKeyboardButton("По жанру", callback_data="music_pick")],
        nav_row("m_leisure"),
    ])


def _track_parts(track):
    if isinstance(track, dict):
        title = str(track.get("title") or track.get("track") or track.get("name") or "").strip()
        return title, str(track.get("note") or "").strip(), str(track.get("url") or "").strip()
    title, separator, note = str(track or "").partition(" - ")
    return title.strip(), note.strip() if separator else "", ""


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
            "url": url or leisure_ui.youtube_music_link(title, artist),
        }

    linked = await asyncio.gather(*(link_track(track, index) for index, track in enumerate(tracks)))
    data["tracks"] = [track for track in linked if track]
    return data


def _task_for_today(key):
    choices = _MUSIC_TASKS.get(key) or ()
    if not choices:
        return None
    index = datetime.now(config.TZ).date().timetuple().tm_yday % len(choices)
    return dict(choices[index])


def _music_task_keyboard():
    return InlineKeyboardMarkup([
        nav_row("m_leisure"),
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


def _music_genre(key):
    for genre_key, label, prompt_name in _MUSIC_GENRES:
        if genre_key == key:
            return label, prompt_name
    return "", ""


def _music_genre_menu_kb(cid, back="m_leisure"):
    """Выбор после «По жанру»: зелёная новинка или стиль (стандартный, без цвета)."""
    selected = set(_music_styles(cid))
    rows = [
        [InlineKeyboardButton("🆕 Новинка", callback_data="nov_music")],
        *[[InlineKeyboardButton(label, callback_data=f"music_g_{key}")]
          for key, label, _prompt_name in _MUSIC_GENRES if key in selected],
    ]
    rows.append(nav_row(back))
    return InlineKeyboardMarkup(rows)


async def send_music_genre_menu(bot, cid, q=None):
    if not _music_styles(cid):
        await send_music_preferences(bot, cid, q)
        return
    text = "Выбери один из отмеченных стилей — подберу нового артиста в этом звучании."
    kb = _music_genre_menu_kb(cid)
    await rich_delivery.show(bot, cid, text, reply_markup=kb, query=q)


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
    rows.append(nav_row("artist_favorites"))
    return InlineKeyboardMarkup(rows)


async def send_music_preferences(bot, cid, q=None):
    text = "🎧 Музыка\n\nВыбери хотя бы один стиль — рекомендации будут только из отмеченных жанров."
    kb = _music_preferences_kb(cid)
    await rich_delivery.show(bot, cid, text, reply_markup=kb, query=q)


def _music_preferences_required_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="music_prefs")],
        nav_row("m_leisure"),
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
    # store.last_recos живёт в памяти и пустеет после перезапуска (автообновление каждые 5 минут) —
    # текущий артист берётся и из сохранённой карточки дня, иначе «Не интересно» ничего не скрывало.
    artist = (rec["items"][0] if isinstance(rec, dict) and rec.get("kind") == "listen" and rec.get("items")
              else str((_cached_artist(cid) or {}).get("artist") or ""))
    if artist:
        recommendation_stoplist.add(cid, "artist", artist, "hidden")
    _invalidate_artist(cid)
    await send_listen(bot, cid, category=category, force=bool(category), status=status)

async def listen_next(bot, cid, *, status=None):
    """music_next (старые кнопки из истории чата): новый живой подбор без скрытия текущего артиста."""
    rec = store.last_recos.get(str(cid))
    category = rec.get("category") if isinstance(rec, dict) else None
    await send_listen(bot, cid, category=category, force=True, status=status)


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
            cached = await _with_country(cached)
            _cache_artist(cid, cached)
            store.last_recos[str(cid)] = {"kind": "listen", "items": [artist]}
            store.last_source[str(cid)] = "Музыка"
            msg = leisure_ui.artist_card(cached)
            await _deliver_artist_card(
                bot, cid, msg, _listen_kb(), status=status)
            return
    arts = _ensure_artists(cid)
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
    import vacation
    style_context = _music_style_context(cid) if not category else ""
    trip_rule = vacation.trip_priority_rule(cid, "исполнителей")
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
                "Ты — меломан и музыкальный критик. Советуй исполнителя так, как советует друг, который хорошо "
                "разбирается в музыке, а не как энциклопедическая справка: живо, образно, с конкретными деталями.\n"
                f"Любимые исполнители пользователя (его вкус): {safe_anchors}.\n"
                f"{genre_context}\n"
                f"{style_context}\n"
                + (f"{trip_rule}\n" if trip_rule else "") +
                f"НЕ предлагай никого из этого списка (уже в любимых, отклонены или недавно показаны): {safe_avoid}.\n"
                "Предложи ТРЁХ РАЗНЫХ новых исполнителей, максимально близких по вкусу "
                "пользователя. Предпочитай современных активных артистов с выразительной, мелодичной, "
                "качественно спродюсированной музыкой. Исполнитель должен быть заметным, популярным или "
                "признанным в своей сцене — не выбирай чрезмерно малоизвестного артиста без сильного совпадения.\n"
                "Правила карточки:\n"
                "- style — точный жанр по-русски, 1–3 слова со строчной буквы: дрим-поп, пост-панк, "
                "альтернативный R&B.\n"
                "- desc — 2–3 предложения о том, как звучит музыка: характер звучания, настроение, вокал, "
                "аранжировки, конкретные детали. Без штампов вроде «уникальный талант», «неповторимый стиль», "
                "«никого не оставит равнодушным»; без биографии и цифр.\n"
                "- tracks — ровно 3 реально существующих трека для первого знакомства, по возможности с разными "
                "сторонами звучания, а не три похожих. Формат «Название - пометка»: у первого — коротко о звучании "
                "или настроении, у второго — чем он отличается от первого, у третьего — ещё одна сторона "
                "творчества. Пометка со строчной буквы, до 8 слов, не повторяет desc.\n"
                "- fact — одно короткое предложение с проверяемым фактом о карьере: прежняя группа, прорывной "
                "альбом, значимая награда, сотрудничество или важный поворот. Не повторяй жанр, страну и "
                "описание звучания. Не выдумывай даты, награды и подробности: если не уверен — пустая строка.\n"
                "Используй только проверяемые факты. Пиши по-русски естественно и выразительно; имена "
                "исполнителей, названия альбомов и песен — в оригинальном написании.\n"
                "Верни строго такой JSON:\n"
                '{"candidates": [{"artist": "имя исполнителя", '
                f'"genre": "один ключ из {", ".join(sorted(allowed_genres))}", '
                '"style": "точный жанр", '
                '"desc": "2–3 предложения о звучании", '
                '"tracks": ["Название - пометка", "Название - пометка", "Название - пометка"], '
                '"fact": "проверяемый факт или пустая строка"}]}',
                2200, tier="leisure", module="leisure")
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
    if (not data or not data.get("artist")) and not category:  # свой список жанра кончился — списки других
        data = next((found for style in selected_styles
                     if (found := _local_artist_fallback(hard_blocked, {"value": style}, recent=recent))), None)
    if not data or not data.get("artist"):
        data = await _release_artist(cid, sorted(allowed_genres), known)
    if (not data or not data.get("artist")) and not category:
        stale = _cached_artist(cid, stale=True)  # AI и резерв не дали нового — последний артист недели
        if stale and str(stale.get("artist") or "").lower() not in hard_blocked:
            data = stale
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
    data = await _with_country(data)
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


async def _release_artist(cid, genres, known):
    """Резерв без AI: реальный артист из свежих альбомов жанров (Apple Music, иначе Deezer) — не из любимых,
    не скрытый и не показанный недавно, без DJ-миксов."""
    import apple_music
    cc = (store.get_settings(cid).get("cc") or "NL").lower()
    for genre in genres:
        try:
            releases = await asyncio.to_thread(apple_music.new_releases, genre, cc)
        except Exception:
            _log.warning("send_listen: releases unavailable genre=%s", genre, exc_info=True)
            continue
        for item in releases:
            if item["artist"].lower() not in known:
                released = date.fromisoformat(item["date"])
                return {"artist": item["artist"], "genre": genre,
                        "desc": f"Свежий альбом «{item['title']}» — вышел {released.day} {util._MONTHS[released.month - 1]}."}
    return None


async def _with_country(data):
    """Жанр (ключ стиля из подбора → «Инди»), страна артиста и годы группы из MusicBrainz (один запрос);
    нет данных — без них."""
    labels = {key: label for key, label, _prompt in _MUSIC_GENRES}
    data = {**data, "genre_label": data.get("genre_label") or labels.get(str(data.get("genre") or "").casefold(), "")}
    need_country, need_years = data.get("country_label") is None, data.get("years") is None
    if not (need_country or need_years):
        return data
    import countries
    artist = data.get("artist", "")

    def lookup():
        return (countries.artist_label(artist) if need_country else data["country_label"],
                countries.artist_years(artist) if need_years else data["years"])

    try:
        label, years = await asyncio.wait_for(asyncio.to_thread(lookup), 6)
    except Exception:
        label, years = data.get("country_label") or "", data.get("years") or ""
    return {**data, "country_label": label, "years": years}


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
