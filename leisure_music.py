"""Музыкальные рекомендации и управление любимыми артистами."""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
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
    await send_listen(bot, cid)


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
            cand = None
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
