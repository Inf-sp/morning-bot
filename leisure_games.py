"""Игры: локальные рекомендации, жанры, платформы и проверяемые премьеры."""

import asyncio
import hashlib
import re
import secrets
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import ai
import config
import igdb
import research
import secure
import settings
import store
from ui import leisure as leisure_ui


GAME_PLATFORMS = (
    ("pc", "💻 ПК"),
    ("ps5", "🎮 PS5"),
    ("xbox", "🟢 Xbox Series"),
    ("switch", "🔴 Nintendo Switch"),
    ("mobile", "📱 Мобильные"),
    ("board", "🎲 Настолки"),
)
GAME_GENRES = (
    ("rpg", "🧙 RPG"),
    ("action", "⚔️ Экшен"),
    ("strategy", "🧠 Стратегии"),
    ("adventure", "🗺️ Приключения"),
    ("cozy", "🌿 Уютные"),
    ("horror", "👻 Хоррор"),
)

_PLATFORM_LABEL = dict(GAME_PLATFORMS)
_PLATFORM_LABEL["other"] = "🕹️ Прочее"
_GENRE_LABEL = dict(GAME_GENRES)
_GENRE_LABEL["board"] = "🎲 Настолки"
_GAME_PREMIERES_VERSION = 3
_GAME_SET_PAGE_SIZE = 8
_GAME_SET_VIEW_TTL = 24 * 3600
_game_set_views = {}

_GAME_RECENCY_OPTIONS = (
    ("🆕 Новинки", "new"),
    ("📅 2020-е", "2020s"),
    ("До 2020 года", "classic"),
    ("Любые годы", ""),
)
_GAME_RATING_OPTIONS = (
    ("7.5+", "7.5"),
    ("8.0+", "8.0"),
    ("8.5+", "8.5"),
)

_GAME_DAILY_CONTENT = (
    {
        "emoji": "🧙 🚪 3️⃣",
        "answer": "Baldur’s Gate 3",
        "fact": "В Baldur’s Gate 3 неудачная проверка часто открывает отдельную сцену, а не просто закрывает путь.",
    },
    {
        "emoji": "🔥 🏛️ ⚔️",
        "answer": "Hades",
        "fact": "В Hades поражение продолжает историю: после каждого возвращения появляются новые диалоги.",
    },
    {
        "emoji": "💃 💿 🕵️",
        "answer": "Disco Elysium",
        "fact": "В Disco Elysium внутренние качества героя спорят друг с другом как отдельные персонажи.",
    },
    {
        "emoji": "2️⃣ 🤝 🧩",
        "answer": "It Takes Two",
        "fact": "It Takes Two полностью построена для двоих: у каждого игрока своя роль в головоломках.",
    },
    {
        "emoji": "⭐ 💧 🏞️",
        "answer": "Stardew Valley",
        "fact": "Stardew Valley не требует идеального расписания: основные занятия можно проходить в своём темпе.",
    },
    {
        "emoji": "🪽 📏 🌍",
        "answer": "Wingspan",
        "fact": "Карты птиц в Wingspan основаны на реальных видах и содержат короткие сведения о них.",
    },
    {
        "emoji": "🔵 🧱 👑",
        "answer": "Азул",
        "fact": "Название «Азул» связано с португальской традицией декоративной плитки азулежу.",
    },
)

_GAME_CATALOG = (
    {
        "id": "baldurs-gate-3", "name": "Baldur’s Gate 3", "platforms": ["pc", "ps5", "xbox"],
        "year": 2023, "rating": 9.0,
        "genres": ["rpg", "strategy"],
        "description": "Большая ролевая история, где решения заметно меняют отношения, задания и финал.",
        "reasons": ["Можно проходить одному или в кооперативе", "Тактика в боях не мешает сильным персонажам"],
        "start": "создай героя под любимый стиль и не пытайся увидеть всё за одно прохождение",
    },
    {
        "id": "hades", "name": "Hades", "platforms": ["pc", "ps5", "xbox", "switch", "mobile"],
        "year": 2020, "rating": 9.0,
        "genres": ["action"],
        "description": "Быстрый экшен с короткими забегами, живыми диалогами и понятным ростом между попытками.",
        "reasons": ["Удобно играть короткими сессиями", "Каждое поражение продолжает историю"],
        "start": "попробуй несколько видов оружия и выбери то, с которым легче держать ритм",
    },
    {
        "id": "alan-wake-2", "name": "Alan Wake 2", "platforms": ["pc", "ps5", "xbox"],
        "year": 2023, "rating": 8.5,
        "genres": ["horror", "adventure"],
        "description": "Мрачный детективный хоррор с двумя героями, сильной постановкой и необычной структурой.",
        "reasons": ["Сюжет важнее бесконечных сражений", "Атмосфера работает как хороший сериал"],
        "start": "играй вечером в наушниках и внимательно изучай доску расследования",
    },
    {
        "id": "disco-elysium", "name": "Disco Elysium", "platforms": ["pc", "ps5", "xbox", "switch"],
        "year": 2019, "rating": 9.0,
        "genres": ["rpg", "adventure"],
        "description": "Детективная RPG почти без обычных боёв, зато с сильными диалогами и свободой характера.",
        "reasons": ["Ошибки открывают не менее интересные сцены", "Подходит, если хочется взрослой истории"],
        "start": "не загружай сохранение после каждого провала — игра умеет превращать его в сюжет",
    },
    {
        "id": "it-takes-two", "name": "It Takes Two", "platforms": ["pc", "ps5", "xbox", "switch"],
        "year": 2021, "rating": 8.5,
        "genres": ["adventure", "action"],
        "description": "Кооперативное приключение для двоих, которое постоянно меняет правила и механики.",
        "reasons": ["Каждый игрок получает свою роль", "Новые идеи появляются почти в каждой главе"],
        "start": "пригласи человека, с которым комфортно договариваться вслух",
    },
    {
        "id": "stardew-valley", "name": "Stardew Valley", "platforms": ["pc", "ps5", "xbox", "switch", "mobile"],
        "year": 2016, "rating": 9.0,
        "genres": ["cozy", "strategy"],
        "description": "Спокойная игра о ферме, соседях и маленьких целях без обязательной гонки за результатом.",
        "reasons": ["Темп легко настроить под себя", "Есть одиночный и совместный режимы"],
        "start": "в первый сезон посади понемногу разных культур и познакомься с жителями",
    },
    {
        "id": "cocoon", "name": "Cocoon", "platforms": ["pc", "ps5", "xbox", "switch"],
        "year": 2023, "rating": 8.0,
        "genres": ["adventure"],
        "description": "Компактное приключение-головоломка без текста, где целые миры становятся инструментами.",
        "reasons": ["Механики объясняются без длинного обучения", "Прохождение не требует десятков часов"],
        "start": "не спеши искать подсказки — окружение почти всегда показывает следующий шаг",
    },
    {
        "id": "dune-imperium", "name": "Дюна: Империя", "platforms": ["board"],
        "year": 2020, "rating": 8.4,
        "genres": ["board", "strategy"],
        "description": "Настольная стратегия, соединяющая колодостроение, борьбу за влияние и конфликт на карте.",
        "reasons": ["Партии дают много решений без лишних правил", "Хорошо раскрывается при повторных встречах"],
        "start": "первую партию сыграй базовыми лидерами без дополнений",
    },
    {
        "id": "cascadia", "name": "Каскадия", "platforms": ["board"],
        "year": 2021, "rating": 8.0,
        "genres": ["board", "cozy", "strategy"],
        "description": "Спокойная настолка о создании природных зон и сочетании животных с подходящей местностью.",
        "reasons": ["Правила объясняются за несколько минут", "Есть хороший одиночный режим"],
        "start": "используй простые карточки целей из первого сценария",
    },
    {
        "id": "wingspan", "name": "Крылья", "platforms": ["board", "pc", "xbox", "switch", "mobile"],
        "year": 2019, "rating": 8.1,
        "genres": ["board", "strategy", "cozy"],
        "description": "Красочная стратегия о птицах, где постепенно собирается эффективная природная система.",
        "reasons": ["Работает и как настолка, и в цифровой версии", "Сочетает спокойный темп и продуманные комбинации"],
        "start": "в первой партии следи прежде всего за едой и яйцами, а не за идеальным движком",
    },
    {
        "id": "azul", "name": "Азул", "platforms": ["board"],
        "year": 2017, "rating": 7.8,
        "genres": ["board", "strategy"],
        "description": "Абстрактная настолка о сборе узоров с простыми ходами и неприятно приятной конкуренцией.",
        "reasons": ["Подходит для короткого вечера", "Правила простые, а решения быстро становятся глубокими"],
        "start": "первую партию сыграй вдвоём — так легче заметить цену каждого выбора",
    },
    {
        "id": "spirit-island", "name": "Остров духов", "platforms": ["board", "pc", "mobile"],
        "year": 2017, "rating": 8.3,
        "genres": ["board", "strategy"],
        "description": "Сложная кооперативная стратегия, где духи острова вместе отражают вторжение колонизаторов.",
        "reasons": ["У каждого духа действительно разный стиль", "Командное планирование важнее удачи"],
        "start": "возьми духа низкой сложности и играй без сценария и противника",
    },
)


def game_platforms(cid):
    selected = settings.get(cid, "game_platforms", [])
    valid = {key for key, _label in GAME_PLATFORMS}
    if not isinstance(selected, list):
        return []
    normalized = []
    for key in selected:
        values = ("xbox", "switch", "mobile") if key == "other" else (key,)
        for value in values:
            if value in valid and value not in normalized:
                normalized.append(value)
    return normalized


def game_platform_labels(cid):
    return [_PLATFORM_LABEL[key] for key in game_platforms(cid)]


def _effective_platforms(cid):
    return game_platforms(cid) or [key for key, _label in GAME_PLATFORMS]


def _game_recency(cid):
    value = str(settings.get(cid, "game_recency", "") or "")
    return value if value in {option for _label, option in _GAME_RECENCY_OPTIONS} else ""


def _game_min_rating(cid):
    value = str(settings.get(cid, "game_min_rating", "") or "")
    valid = {option for _label, option in _GAME_RATING_OPTIONS}
    return float(value) if value in valid else None


def _platform_signature(cid):
    return hashlib.sha256("|".join(sorted(_effective_platforms(cid))).encode()).hexdigest()[:16]


def _favorite_game_name(item):
    if isinstance(item, dict):
        return str(item.get("name") or item.get("value") or "").strip()
    return str(item or "").strip()


def normalize_favorite_game(value):
    """Сохраняет известную игру с локальными метаданными, неизвестную — без выдумок."""
    name = " ".join(_favorite_game_name(value).split()).strip(" ,;.-")
    if not name:
        return None
    is_board = bool(re.search(r"\b(настолка|настольная игра)\b", name, flags=re.IGNORECASE))
    if is_board:
        name = re.sub(
            r"\b(настолка|настольная игра)\b", "", name, flags=re.IGNORECASE,
        ).strip(" ,;.-")
    match = next((item for item in _GAME_CATALOG if item["name"].casefold() == name.casefold()), None)
    if match:
        return dict(match)
    return {
        "name": name,
        "genres": ["board"] if is_board else [],
        "platforms": ["board"] if is_board else [],
    }


def enrich_favorite_game(item):
    """Дополняет неизвестную цифровую игру проверенными метаданными IGDB."""
    prepared = dict(item or {})
    if prepared.get("platforms") == ["board"]:
        return prepared
    return igdb.enrich_game_recommendation(prepared)


def _favorite_games(cid):
    return store.get_list(config.FAVORITE_GAMES_KEY, cid)


def _favorite_game_signature(cid):
    names = sorted(_favorite_game_name(item).casefold() for item in _favorite_games(cid)
                   if _favorite_game_name(item))
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:16]


def _game_signature(cid):
    payload = (
        f"{_platform_signature(cid)}|{_game_recency(cid)}|{_game_min_rating(cid) or ''}"
        f"|{_favorite_game_signature(cid)}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _youtube_trailer_search_url(title):
    query = " ".join(str(title or "").split())
    return (
        f"https://www.youtube.com/results?search_query={quote_plus(f'{query} official trailer')}"
        if query else ""
    )


def _eligible_games(cid, genre=None):
    platforms = {"board"} if genre == "board" else set(_effective_platforms(cid))
    candidates = [
        item for item in _GAME_CATALOG
        if platforms.intersection(item["platforms"])
        and (not genre or genre in item["genres"])
    ]
    current_year = datetime.now(config.TZ).year
    recency = _game_recency(cid)
    min_rating = _game_min_rating(cid)

    def preferred(item):
        year = int(item.get("year") or 0)
        if recency == "new" and year < current_year - 3:
            return False
        if recency == "2020s" and year < 2020:
            return False
        if recency == "classic" and (not year or year >= 2020):
            return False
        return min_rating is None or float(item.get("rating") or 0) >= min_rating

    filtered = [item for item in candidates if preferred(item)]
    return filtered or candidates


def _decorate_game(item, cid, *, genre=None):
    platforms = {"board"} if genre == "board" else set(_effective_platforms(cid))
    visible_platforms = [key for key in item["platforms"] if key in platforms]
    primary_genre = item["genres"][0] if item.get("genres") else ""
    return {
        **dict(item),
        "platform_labels": [_PLATFORM_LABEL[key] for key in visible_platforms],
        "genre_label": _GENRE_LABEL.get(primary_genre, primary_genre),
    }


def pick_game(cid, *, genre=None, refresh=False):
    """Локальный подбор без AI: платформы + жанр + защита от недавних повторов."""
    profile = store.get_profile(cid)
    today = datetime.now(config.TZ).date().isoformat()
    signature = _game_signature(cid)
    cached = profile.get("game_daily") or {}
    if (not refresh and not genre and cached.get("date") == today
            and cached.get("signature") == signature and isinstance(cached.get("item"), dict)):
        return _decorate_game(cached["item"], cid, genre=genre)

    pool = _eligible_games(cid, genre=genre)
    if not pool:
        return {}
    favorites = _favorite_games(cid)
    favorite_names = {_favorite_game_name(item).casefold() for item in favorites if _favorite_game_name(item)}
    favorite_genres = {
        str(value) for item in favorites if isinstance(item, dict)
        for value in (item.get("genres") or []) if str(value)
    }
    not_favorite = [item for item in pool if item["name"].casefold() not in favorite_names]
    if not_favorite:
        pool = not_favorite
    if favorite_genres:
        best_overlap = max(len(favorite_genres.intersection(item.get("genres") or [])) for item in pool)
        if best_overlap:
            pool = [item for item in pool
                    if len(favorite_genres.intersection(item.get("genres") or [])) == best_overlap]
    seen = [str(value) for value in profile.get("game_seen", []) if str(value)]
    fresh = [item for item in pool if item["id"] not in seen]
    candidates = fresh or pool
    seed = int(hashlib.sha256(f"{cid}|{today}|{genre or ''}".encode()).hexdigest()[:8], 16)
    item = candidates[seed % len(candidates)]
    seen = [value for value in seen if value != item["id"]]
    daily_entry = {"date": today, "signature": signature, "item": dict(item)}

    def save_selection(current):
        current_seen = [str(value) for value in current.get("game_seen", []) if str(value)]
        current_seen = [value for value in current_seen if value != item["id"]]
        current["game_seen"] = [*current_seen, item["id"]][-20:]
        if not genre:
            current["game_daily"] = daily_entry
        return current, None

    store.mutate_profile(cid, save_selection)
    return _decorate_game(item, cid, genre=genre)


def _game_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать новую игру", callback_data="vg_reco")],
        [InlineKeyboardButton("🎮 Премьеры игр", callback_data="vg_premieres")],
        [InlineKeyboardButton("🎲 Настолки", callback_data="vg_board")],
        [InlineKeyboardButton("🎚️ Мой набор", callback_data="vg_set")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _game_keyboard(*, no_match=False, genre=None):
    next_callback = f"vg_next_{genre}" if genre else "vg_next"
    rows = [
        [InlineKeyboardButton("✨ Другая игра", callback_data=next_callback)],
        [InlineKeyboardButton("🎭 По жанру", callback_data="vg_genres")],
        [InlineKeyboardButton("🎚️ Мой набор", callback_data="vg_set")],
    ]
    if no_match:
        rows.append([InlineKeyboardButton("📝 Платформы", callback_data="set_pref_games")])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_games"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _game_genre_title(value):
    label = _GENRE_LABEL.get(str(value), str(value or ""))
    return label.split(" ", 1)[-1] if " " in label else (label or "Без жанра")


def _game_set_records(cid):
    records = []
    for raw in store.ensure_list_ids(config.FAVORITE_GAMES_KEY, cid):
        item = dict(raw)
        value = item.get("value")
        if value and not item.get("name"):
            item = {**(normalize_favorite_game(value) or {}), "id": item.get("id")}
        name = _favorite_game_name(item)
        genres = [str(value) for value in item.get("genres") or [] if str(value)]
        actual_genres = [value for value in genres if value != "board"]
        if "board" in (item.get("platforms") or []):
            genre = "Настолки"
        else:
            genre = _game_genre_title(actual_genres[0]) if actual_genres else "Без жанра"
        item.update({"name": name, "genre": genre, "genre_label": genre})
        records.append(item)
    return records


def _new_game_set_view(cid):
    now = time.time()
    for token, view in list(_game_set_views.items()):
        if now - view.get("created_at", 0) > _GAME_SET_VIEW_TTL:
            _game_set_views.pop(token, None)
    genres = {}
    for item in _game_set_records(cid):
        genres.setdefault(item["genre"], []).append(item)
    for items in genres.values():
        items.sort(key=lambda item: item["name"].casefold())
    ordered = sorted(genres, key=lambda value: (value == "Без жанра", value.casefold()))
    token = secrets.token_hex(3)
    view = {"cid": str(cid), "created_at": now,
            "genres": [(genre, genres[genre]) for genre in ordered]}
    _game_set_views[token] = view
    return token, view


def _game_set_view(cid, token):
    view = _game_set_views.get(token)
    if not view or view.get("cid") != str(cid) or time.time() - view.get("created_at", 0) > _GAME_SET_VIEW_TTL:
        _game_set_views.pop(token, None)
        return None
    return view


async def send_game_set(bot, cid, q=None):
    token, view = _new_game_set_view(cid)
    total = sum(len(items) for _genre, items in view["genres"])
    msg = leisure_ui.game_set_home(total, [
        {"genre": genre, "names": [item["name"] for item in items]}
        for genre, items in view["genres"]
    ])
    rows = [[InlineKeyboardButton(f"{genre} · {len(items)}", callback_data=f"vg_setg:{token}:{index}:0")]
            for index, (genre, items) in enumerate(view["genres"])]
    rows.append([InlineKeyboardButton("🆕 Добавить игру", callback_data="as_loveadd_games")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_games"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    await _deliver(bot, cid, msg, InlineKeyboardMarkup(rows), q=q)


async def send_game_set_genre(bot, cid, token, genre_index, page=0, q=None):
    view = _game_set_view(cid, token)
    if view is None or not 0 <= genre_index < len(view["genres"]):
        await send_game_set(bot, cid, q=q)
        return
    genre, items = view["genres"][genre_index]
    pages = max(1, (len(items) + _GAME_SET_PAGE_SIZE - 1) // _GAME_SET_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * _GAME_SET_PAGE_SIZE:(page + 1) * _GAME_SET_PAGE_SIZE]
    rows = [[InlineKeyboardButton(item["name"][:48], callback_data=f"vg_seti:{token}:{item['id'][:8]}:{genre_index}:{page}")]
            for item in chunk]
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"vg_setg:{token}:{genre_index}:{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"vg_setg:{token}:{genre_index}:{(page + 1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("🆕 Добавить игру", callback_data="as_loveadd_games")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="vg_set"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    await _deliver(bot, cid, leisure_ui.game_set_genre(genre, len(items)), InlineKeyboardMarkup(rows), q=q)


def _game_set_item(cid, token, short_id):
    view = _game_set_view(cid, token)
    if view is None:
        return None
    return next((item for _genre, items in view["genres"] for item in items
                 if str(item.get("id") or "").startswith(short_id)), None)


async def send_game_set_card(bot, cid, token, short_id, genre_index, page):
    item = _game_set_item(cid, token, short_id)
    if item is None:
        await send_game_set(bot, cid)
        return
    card = _decorate_game(item, cid) if item.get("platforms") else dict(item)
    card = await asyncio.to_thread(enrich_favorite_game, card)
    card["platform_labels"] = [
        _PLATFORM_LABEL[key] for key in card.get("platforms") or [] if key in _PLATFORM_LABEL
    ]
    msg = leisure_ui.game_set_card(card)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"vg_setd:{token}:{short_id}:{genre_index}:{page}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"vg_setg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    if card.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=card["poster"], caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def confirm_game_set_delete(bot, cid, token, short_id, genre_index, page, q=None):
    item = _game_set_item(cid, token, short_id)
    if item is None:
        await send_game_set(bot, cid, q=q)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"vg_setdok:{token}:{short_id}")],
        [InlineKeyboardButton("Отмена", callback_data=f"vg_setg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    await _deliver(bot, cid, leisure_ui.game_delete_confirmation(item["name"]), kb, q=q)


async def delete_game_set_item(bot, cid, token, short_id, q=None):
    item = _game_set_item(cid, token, short_id)
    if item:
        store.remove_from_list_by_ids(config.FAVORITE_GAMES_KEY, cid, [item["id"]])
        _reset_game_daily(cid)
    _game_set_views.pop(token, None)
    await send_game_set(bot, cid, q=q)


async def send_favorite_games_added_card(bot, cid, items):
    items = [dict(item) for item in items or [] if isinstance(item, dict)]
    if len(items) != 1:
        await send_game_set(bot, cid)
        return
    item = items[0]
    genres = [str(value) for value in item.get("genres") or [] if str(value)]
    item["genre_label"] = _game_genre_title(genres[0]) if genres else "Без жанра"
    if item.get("platforms"):
        item = _decorate_game(item, cid)
    item = await asyncio.to_thread(enrich_favorite_game, item)
    item["platform_labels"] = [
        _PLATFORM_LABEL[key] for key in item.get("platforms") or [] if key in _PLATFORM_LABEL
    ]
    genres = [value for value in item.get("genres") or [] if value != "board"]
    item["genre_label"] = (
        _game_genre_title(genres[0]) if genres else
        ("Настолки" if "board" in (item.get("platforms") or []) else "Без жанра")
    )
    msg = leisure_ui.favorite_game_added_card(item)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мой набор", callback_data="vg_set")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_games"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    if item.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=item["poster"], caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _genre_keyboard():
    buttons = [InlineKeyboardButton(label, callback_data=f"vg_g_{key}") for key, label in GAME_GENRES]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_games"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def _deliver(bot, cid, msg, markup, *, q=None, status=None):
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=markup,
                             disable_web_page_preview=True)
        return
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup,
                                      disable_web_page_preview=True)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, disable_web_page_preview=True)


async def send_games_home(bot, cid, *, q=None, status=None):
    items = await get_game_premieres(cid)
    if not items:
        items = await get_game_premieres(cid, refresh=True)
    city = str(
        store.get_settings(cid).get("city") or config.DEFAULT_CITY.get("city") or "Алкмар"
    ).strip()
    today = datetime.now(config.TZ).date()
    daily = dict(_GAME_DAILY_CONTENT[(today.toordinal() - 1) % len(_GAME_DAILY_CONTENT)])
    home_items = []
    for source in items[:3]:
        item = dict(source)
        item["trailer_url"] = str(item.get("trailer_url") or "").strip() or (
            _youtube_trailer_search_url(item.get("title"))
        )
        home_items.append(item)
    msg = leisure_ui.game_home_screen(city, home_items, daily)
    markup = _game_home_keyboard()
    poster = next(
        (str(item.get("poster") or "").strip() for item in home_items
         if str(item.get("poster") or "").strip()),
        "",
    )
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=poster,
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=markup,
            )
            return
        except Exception:
            pass
    await _deliver(bot, cid, msg, markup, q=q, status=status)


async def send_game_recommendation(
    bot, cid, *, q=None, status=None, refresh=False, genre=None,
):
    item = pick_game(cid, genre=genre, refresh=refresh)
    if item:
        item = await asyncio.to_thread(igdb.enrich_game_recommendation, item)
    msg = leisure_ui.game_card(item)
    markup = _game_keyboard(no_match=not item, genre=genre)
    poster = str(item.get("poster") or "").strip() if item else ""
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=poster,
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=markup,
            )
            return
        except Exception:
            pass
    await _deliver(bot, cid, msg, markup, q=q, status=status)


async def send_game_genres(bot, cid, q=None):
    await _deliver(bot, cid, leisure_ui.game_genres_screen(), _genre_keyboard(), q=q)


def _preferences_keyboard(cid):
    selected = set(game_platforms(cid))
    recency = _game_recency(cid)
    rating = str(settings.get(cid, "game_min_rating", "") or "")
    rows = [[InlineKeyboardButton(
        ("✅ " if key in selected else "□ ") + label,
        callback_data=f"set_game_platform_{key}",
    )] for key, label in GAME_PLATFORMS]
    rows.extend([[InlineKeyboardButton(
        ("✅ " if recency == value else "") + label,
        callback_data=f"set_game_recency_{value or 'any'}",
    )] for label, value in _GAME_RECENCY_OPTIONS])
    rows.extend([[InlineKeyboardButton(
        ("✅ " if rating == value else "") + f"⭐ {label}",
        callback_data=f"set_game_rating_{value}",
    )] for label, value in _GAME_RATING_OPTIONS])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="set_preferences"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_game_preferences(bot, cid, q=None):
    recency_labels = dict((value, label) for label, value in _GAME_RECENCY_OPTIONS)
    rating = _game_min_rating(cid)
    msg = leisure_ui.game_preferences(
        game_platform_labels(cid),
        recency_labels.get(_game_recency(cid), "Любые годы"),
        f"от {rating:.1f}" if rating is not None else "любая",
    )
    await _deliver(bot, cid, msg, _preferences_keyboard(cid), q=q)


async def toggle_game_platform(bot, cid, platform, q=None):
    valid = {key for key, _label in GAME_PLATFORMS}
    if platform in valid:
        selected = game_platforms(cid)
        if platform in selected:
            selected = [key for key in selected if key != platform]
        else:
            selected.append(platform)
        settings.set_(cid, "game_platforms", selected)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


def _reset_game_daily(cid):
    def change(profile):
        profile.pop("game_daily", None)
        return profile, None

    store.mutate_profile(cid, change)


async def toggle_game_recency(bot, cid, value, q=None):
    if value in {"new", "2020s", "classic", "any"}:
        settings.set_(cid, "game_recency", "" if value == "any" else value)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


async def toggle_game_rating(bot, cid, value, q=None):
    valid = {option for _label, option in _GAME_RATING_OPTIONS}
    if value in valid:
        current = str(settings.get(cid, "game_min_rating", "") or "")
        settings.set_(cid, "game_min_rating", "" if current == value else value)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


def _premiere_cache_get(signature, today, *, allow_stale=False):
    data = store._load(config.GAME_PREMIERES_CACHE_KEY) or {}
    entry = data.get(signature) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("version") != _GAME_PREMIERES_VERSION:
        return None
    if bool(entry.get("igdb_configured")) != igdb.configured():
        return None
    try:
        expires = date.fromisoformat(str(entry.get("expires") or ""))
    except ValueError:
        return None
    if expires < today and not allow_stale:
        return None
    items = entry.get("items")
    return [dict(item) for item in items] if isinstance(items, list) else None


def _premiere_cache_set(signature, today, items):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[signature] = {
            "version": _GAME_PREMIERES_VERSION,
            "igdb_configured": igdb.configured(),
            "expires": (today + timedelta(days=7)).isoformat(),
            "items": [dict(item) for item in items],
        }
        return data, None
    store.mutate_kv(config.GAME_PREMIERES_CACHE_KEY, mutate)


def _premiere_date_label(value):
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return ""
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def _normalize_premieres(payload, source_urls, selected_platforms, today):
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(raw_items, list):
        return []
    aliases = {
        "pc": "pc", "пк": "pc", "windows": "pc",
        "ps5": "ps5", "playstation 5": "ps5",
        "xbox": "xbox", "xbox series": "xbox", "xbox series x|s": "xbox",
        "switch": "switch", "nintendo switch": "switch", "switch 2": "switch",
        "mobile": "mobile", "ios": "mobile", "android": "mobile", "мобильные": "mobile",
        "board": "board", "board game": "board", "настолки": "board",
    }
    result, seen = [], set()
    latest = today + timedelta(days=180)
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        url = str(item.get("url") or "").strip()
        try:
            release = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        platforms = []
        for value in item.get("platforms") or []:
            normalized = aliases.get(str(value).strip().casefold())
            if normalized and normalized not in platforms:
                platforms.append(normalized)
        if (not title or title.casefold() in seen or url not in source_urls
                or not (today <= release <= latest)
                or not set(platforms).intersection(selected_platforms)):
            continue
        seen.add(title.casefold())
        summary = " ".join(str(item.get("summary") or "").split())
        if summary and summary[-1] not in ".!?…":
            summary += "."
        result.append({
            "title": title,
            "date": release.isoformat(),
            "date_label": _premiere_date_label(release.isoformat()),
            "platforms": platforms,
            "platform_label": " · ".join(_PLATFORM_LABEL[key] for key in platforms),
            "genre": " ".join(str(item.get("genre") or "").split()),
            "summary": summary,
            "url": url,
        })
    result.sort(key=lambda item: item["date"])
    return result[:8]


async def get_game_premieres(cid, *, refresh=False):
    today = datetime.now(config.TZ).date()
    signature = _platform_signature(cid)
    cached = _premiere_cache_get(signature, today)
    if cached is not None:
        return cached
    if not refresh:
        return _premiere_cache_get(signature, today, allow_stale=True) or []

    search_labels = {
        "pc": "PC Windows", "ps5": "PlayStation 5",
        "xbox": "Xbox Series X S", "switch": "Nintendo Switch Switch 2",
        "mobile": "iOS Android mobile games", "board": "board games",
    }
    platform_labels = [label for key, label in GAME_PLATFORMS if key in _effective_platforms(cid)]
    search_platforms = " ".join(search_labels[key] for key in _effective_platforms(cid))
    query = f"upcoming game release dates {search_platforms} {today.year}"
    sources = await asyncio.to_thread(
        research.web_search, query, 8,
        scenario="game_releases", allow_tavily=True, search_priority="tavily",
    )
    sources = [item for item in sources if item.get("url") and (item.get("content") or item.get("title"))]
    if not sources:
        items = await asyncio.to_thread(
            igdb.get_upcoming_games, set(_effective_platforms(cid)), today=today, days=180,
        )
        for item in items:
            item["date_label"] = _premiere_date_label(item.get("date"))
        if items:
            _premiere_cache_set(signature, today, items)
        return items
    source_urls = {str(item["url"]).strip() for item in sources}
    source_text = "\n---\n".join(
        f"URL: {item['url']}\n{item.get('title', '')}\n{str(item.get('content') or '')[:700]}"
        for item in sources
    )[:7000]
    prompt = (
        f"Сегодня {today.isoformat()}. Извлеки ближайшие подтверждённые премьеры игр на 180 дней "
        f"для платформ: {', '.join(platform_labels)}. Используй только факты и URL из материалов. "
        "Не придумывай дату, платформу или ссылку. Название оставь официальным, genre и summary пиши по-русски; "
        "summary — одно короткое предложение без рекламы.\n"
        f"{secure.wrap_untrusted(source_text, 'материалы о релизах игр')}\n"
        'JSON без markdown: {"items":[{"title":"Название","date":"YYYY-MM-DD",'
        '"platforms":["pc","ps5","xbox","switch","mobile","board"],"genre":"жанр",'
        '"summary":"одно предложение","url":"точный URL из материалов"}]}'
    )
    try:
        payload = await ai.allm_json(
            prompt, 1400, tier="cheap", module="leisure_games",
            fallback_allowed=True, privacy_level="public",
            cache_context={
                "scenario": "game_premieres", "date": today.isoformat(),
                "platforms": sorted(_effective_platforms(cid)), "sources": sorted(source_urls),
                "schema_version": 1,
            },
        )
    except Exception:
        payload = {}
    items = _normalize_premieres(payload, source_urls, set(_effective_platforms(cid)), today)
    if not items:
        items = await asyncio.to_thread(
            igdb.get_upcoming_games, set(_effective_platforms(cid)), today=today, days=180,
        )
        for item in items:
            item["date_label"] = _premiere_date_label(item.get("date"))
    if items:
        items = await asyncio.to_thread(igdb.enrich_game_premieres, items)
        _premiere_cache_set(signature, today, items)
    return items


async def warm_game_premieres_cache(cid):
    """Обновляет премьерную витрину для платформ пользователя перед рассылкой."""
    await get_game_premieres(cid, refresh=True)


async def send_game_premieres(bot, cid, *, status=None):
    items = await get_game_premieres(cid)
    if not items:
        items = await get_game_premieres(cid, refresh=True)
    items = [
        item for item in items
        if str(item.get("poster") or "").strip()
    ][:7]
    msg = leisure_ui.game_premieres_screen(items)
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m_games"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])
    posters = [
        InputMediaPhoto(media=str(item.get("poster") or "").strip())
        for item in items
    ]
    if len(posters) >= 2:
        try:
            await bot.send_media_group(
                chat_id=cid,
                media=posters,
                caption=msg.text,
                caption_entities=msg.entities,
            )
            return
        except Exception:
            pass
    if len(posters) == 1:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=posters[0].media,
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=markup,
            )
            return
        except Exception:
            pass
    if posters:
        msg = leisure_ui.game_premieres_screen([])
    await _deliver(bot, cid, msg, markup, status=status)
