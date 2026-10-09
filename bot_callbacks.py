"""Маршрутизация inline callback-кнопок."""

import logging
from collections.abc import Callable
from typing import NamedTuple

import access
import callback_topics
import cleanup
import cooking
import dictionary_tts
import home_cache
import learning_dictionary as dictionary
import learning_game
import learning_settings
import learning_router
import leisure_books
import leisure_concerts
import leisure_hub
import leisure_movies
import leisure_music
import leisure_novelty
import menu
import myday
import onboard
import retry_flow
import personal_collections
import settings
import store
import trainer
import util
import verify
import wardrobe
import weather
import yearly_tops
from util import ack_loading as _ack
from ui import menu as menu_ui

_log = logging.getLogger(__name__)

def _status_topic(data):
    """Совместимый внутренний вход для статусов ожидания."""
    return callback_topics.status_topic(data)


def _status_stages(data):
    """Возвращает статусы ожидания с понятным первым действием."""
    topic = _status_topic(data)
    stages = util.StatusManager.TOPIC_STAGES.get(topic) if topic else None
    if not stages:
        stages = util.StatusManager.STAGES

    def progress(first, second, final):
        return ((0, first), (2, second), (6, final))

    if data in ("m_food", "m_food_next") or data.startswith("food_go_"):
        return progress("🍳 Составляю меню...", "🧊 Сверяю холодильник...", "📝 Подбираю блюда...")
    if data.startswith("a_recipe_"):
        return progress("🍳 Готовлю рецепт...", "🧊 Сверяю холодильник...", "📝 Пишу шаги...")
    if data.startswith(("as_food", "as_fridge_cook", "food_")):
        first = "⏳ Ищу рецепт..."
    elif data == "w_look":
        first = "⏳ Ищу образ..."
    elif data.startswith(("movie_", "a_watch")):
        first = "🎬 Ищу кино..."
    elif data.startswith(("book_", "a_read")):
        first = "📚 Ищу книгу..."
    elif data.startswith(("music_", "listen", "a_listen")):
        first = "🎧 Ищу музыку..."
    elif data.startswith("a_concerts"):
        return progress("🎫 Ищу концерт...", "📅 Проверяю афишу...", "📝 Готовлю события...")
    elif data.startswith(("game", "a_game")):
        return progress("🕵️ Ищу загадку...", "📖 Проверяю текст...", "🧩 Собираю загадку...")
    elif data.startswith(("a_dict", "word_")):
        return progress("📖 Ищу слово...", "🔤 Проверяю форму...", "📝 Готовлю карточку...")
    elif data.startswith(("a_train", "a_tr_", "ex_", "again_tr_")):
        first = "🧠 Ищу задание..."
    elif data == "m_wardrobe":
        first = "⏳ Ищу образ..."
    elif data in ("a_plany", "m_myday", "weather_myday"):
        return progress("☀️ Собираю мой день...", "🌦️ Сверяю планы...", "📝 Готовлю сводку...")
    elif data in ("a_w_full", "a_w_week"):
        return progress("🌦️ Ищу прогноз...", "🗓️ Сверяю дни...", "📝 Готовлю прогноз...")
    elif topic == "wardrobe":
        first = "⏳ Ищу образ..."
    elif topic == "food":
        first = "⏳ Ищу рецепт..."
    elif topic == "learning":
        first = "🧠 Ищу задание..."
    elif topic == "leisure":
        first = "✨ Ищу рекомендацию..."
    else:
        return stages
    return ((0, first), *stages[1:])



class R(NamedTuple):
    """Правило маршрута. ``keys`` — строка или кортеж; ``*`` в конце ключа — префикс.

    ``sub`` называет под-роутер, которому уходит callback: его читает routing.py
    для статической проверки, поэтому ключи и ``sub`` пишутся только литералами.
    """
    keys: str | tuple
    handler: Callable
    sub: str | None = None


class Ctx(NamedTuple):
    bot: object
    cid: str
    q: object
    data: str
    status: Callable  # _inline_status: долгий сценарий с индикатором ожидания


class _DirectStatus:
    """Готовый экран без индикатора ожидания — с семантикой StatusManager.

    preserve_message=False заменяет текущее сообщение (вход в раздел, «Назад»),
    True присылает результат отдельным сообщением и оставляет прежнее в истории.
    """

    def __init__(self, bot, cid, q, preserve_message):
        self.bot, self.cid, self.q, self.preserve_message = bot, cid, q, preserve_message

    async def replace(self, text, **kwargs):
        if not self.preserve_message:
            try:
                await self.q.message.edit_text(text, **kwargs)
                return True
            except Exception:
                _log.debug("_DirectStatus.replace: edit failed, sending new message", exc_info=True)
        await self.bot.send_message(chat_id=self.cid, text=text, **kwargs)
        return True

    async def stop(self, delete=True):
        return None


def _matches(keys, data):
    keys = (keys,) if isinstance(keys, str) else keys
    return any(data.startswith(k[:-1]) if k.endswith("*") else data == k for k in keys)


def _first(routes, data):
    return next((route for route in routes if _matches(route.keys, data)), None)


def _safe(handler):
    async def run(c):
        try:
            await handler(c)
        except Exception as e:
            await verify.safe_error(c.bot, c.cid, e)
    return run


def _acked(handler):
    async def run(c):
        await _ack(c.q)
        await handler(c)
    return run


async def _noop(_c):
    return None


async def _open_collection_route(c):
    _, collection_id, back = c.data.split(":", 2)
    await cleanup.open_collection(c.bot, c.cid, collection_id, back=back)


async def _close(c):
    try:
        await c.q.message.edit_text("Готово.", reply_markup=menu.main_menu_kb())
    except Exception:
        _log.debug("_close: ignored error", exc_info=True)


async def _main_menu(c):
    text, entities, kb = menu.main_menu_screen(c.cid)
    # Главное меню открывается отдельным сообщением: полезная карточка
    # (рецепт, рекомендация, результат тренировки) остаётся в истории.
    await c.bot.send_message(
        chat_id=c.cid, text=text, reply_markup=kb, entities=entities, transient=True,
    )


async def _notify_learning(c):
    # Слова дня остаются в истории; учебный экран открывается отдельно.
    trainer.cancel(c.cid)
    text, entities, kb = menu.menu_screen("m_learn", c.cid)
    await c.bot.send_message(
        chat_id=c.cid, text=text, entities=entities, reply_markup=kb, transient=True,
    )


async def _submenu(c):
    # Навигация по подменю — редактируем сообщение на месте.
    text, entities, kb = menu.menu_screen(c.data, c.cid)
    try:
        await c.q.message.edit_text(text, reply_markup=kb, entities=entities)
    except Exception:
        await c.bot.send_message(chat_id=c.cid, text=text, reply_markup=kb, entities=entities)


async def _set_city(c):
    store.pending_input[c.cid] = "setcity"
    await c.bot.send_message(chat_id=c.cid, text="📍 Напиши название города — переключу на него.")


def _find_concerts(country):
    # Экран меняется на месте; при поиске сообщение само показывает «Проверяю афишу…».
    return lambda c: leisure_concerts.find_concerts(c.bot, c.cid, country(c), q=c.q)


def _food_menu(meal):
    return lambda c: c.status(lambda status: menu.send_food_menu(
        c.bot, c.cid, status=status, refresh=False, meal=meal))


# Действия a_<act>; учебные действия сначала получает learning_router.
ACTIONS = (
    R("plany", lambda c: c.status(
        lambda status: myday.send_plany(c.bot, c.cid, force=True, status=status))),
    R("w_week", lambda c: c.status(
        lambda status: weather.send_weather(c.bot, c.cid, "week", status=status))),
    R("w_full", lambda c: c.status(
        lambda status: weather.send_weather(c.bot, c.cid, "full", status=status))),
    R("setcity", _set_city),
    R(("watch", "read", "listen"), lambda c: leisure_hub.send_hub(c.bot, c.cid, q=c.q)),
    R(("watchlist", "watchclean"), lambda c: cleanup.open_collection(
        c.bot, c.cid, "cinema_favorites", back="lz_lib")),
    R(("concerts_find", "concerts_nearby", "artist_concerts"), _find_concerts(lambda _c: "home")),
    R("concerts_search", lambda c: leisure_concerts.prompt_artist_search(c.bot, c.cid)),
    R("concerts_pick", lambda c: leisure_concerts.concert_pick_country(c.bot, c.cid, q=c.q)),
    R(("concerts_nl", "concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
       "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
       "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"),
      _find_concerts(lambda c: c.data[2:].split("_")[1])),
    R("listen_no", lambda c: c.status(lambda _s: leisure_music.listen_dislike(c.bot, c.cid))),
    R(("food_breakfast", "recipe_breakfast"), _food_menu("breakfast")),
    R(("food_lunch", "recipe_lunch"), _food_menu("lunch")),
    R(("food_dinner", "recipe_dinner"), _food_menu("dinner")),
)


async def _action(c):
    act = c.data[2:]
    if act != "plany" and await learning_router.handle_action(c.bot, c.cid, c.q, act, c.status):
        return
    route = _first(ACTIONS, act)
    if route:
        await route.handler(c)


async def _yearly_tops(c):
    _prefix, kind, page = c.data.split(":", 2)
    if kind not in ("movie", "tv", "book"):
        return
    if page == "open":
        await c.status(lambda status: yearly_tops.send(c.bot, c.cid, kind, status=status))
    elif page.isdigit():
        await _ack(c.q)
        await yearly_tops.show_page(c.q, kind, int(page))


def _food_recipe(c):
    """food_go_day_<кухня>: «Новое меню» на весь день в выбранной кухне.

    Старые кнопки food_go_<приём пищи>_<кухня|any> тоже дают новое меню дня.
    """
    _meal, _sep, cuisine = c.data[len("food_go_"):].partition("_")
    return c.status(lambda status: menu.send_food_menu(
        c.bot, c.cid, status=status, refresh=True,
        cuisine=None if cuisine in ("", "any") else cuisine))


def _card_args(c):
    """token, short_id, genre_index, page из «op:token:id:genre:page»."""
    _op, token, short_id, genre_index, page = c.data.split(":", 4)
    return token, short_id, int(genre_index), int(page)


def _token_id(c):
    _op, token, short_id = c.data.split(":", 2)
    return token, short_id


def _genre_args(c):
    _op, token, genre_index, page = c.data.split(":", 3)
    return token, int(genre_index), int(page)


async def _delete_favorite_movie(c):
    parts = c.data.split(":")
    _op, token, short_id = parts[:3]
    genre_index = int(parts[3]) if len(parts) > 3 else None
    page = int(parts[4]) if len(parts) > 4 else 0
    await leisure_movies.delete_favorite_movie(c.bot, c.cid, token, short_id, genre_index, page, q=c.q)


def _tail_int(c):
    return int(c.data.split("_")[-1])


def _swap_kb(make_kb):
    """«Другой…» / «Назад» в выборе жанра: меняются только кнопки под текущей карточкой."""
    async def run(c):
        await _ack(c.q)
        try:
            await c.q.message.edit_reply_markup(reply_markup=make_kb(c))
        except Exception:
            _log.debug("_swap_kb: keyboard not changed data=%s", c.data, exc_info=True)
    return run


def _picked(handler):
    """Выбор жанра: новая карточка приходит отдельно, у прежней снимаются кнопки.

    Снимаем после handler: inline-статус на время подбора сам восстанавливает кнопки.
    """
    async def run(c):
        await handler(c)
        try:
            await c.q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            _log.debug("_picked: keyboard not cleared data=%s", c.data, exc_info=True)
    return run


def _page(c):
    return int(c.data.split(":", 1)[1])


ROUTES = (
    R("ob_*", lambda c: onboard.handle_callback(c.bot, c.cid, c.q, c.data), sub="onboard"),
    R("collection_pick:*", lambda c: personal_collections.handle_collection_callback(
        c.bot, c.cid, c.q, c.data), sub="personal_collections"),
    R(("book_add_ok:*", "book_add_next:*"), lambda c: leisure_books.handle_manual_book_add_callback(
        c.bot, c.cid, c.q, c.data)),
    # Раздела игр больше нет: старые кнопки игр из истории чата открывают хаб «Досуг».
    R(("game_add_ok:*", "game_add_next:*", "game_premiere_page:*", "game_prefs", "game_love",
       "game_no", "vg_*",
       "nov_game", "nov_pick_game", "nov_card_game", "nov_no_game", "lz_cfg_game"),
      lambda c: leisure_hub.send_hub(c.bot, c.cid, q=c.q)),
    # Старые callbacks карточек не меняют данные и ведут к актуальному экрану.
    R("fav_*", lambda c: personal_collections.handle_collection_callback(
        c.bot, c.cid, c.q, c.data), sub="personal_collections"),
    # answerCallbackQuery уже отправлен в bot.answer_callback: кнопка не крутится до озвучки.
    R("tts_word:*", lambda c: dictionary_tts.send_pronunciation(c.bot, c.cid, c.data.split(":", 1)[1])),
    R("ls_*", lambda c: personal_collections.handle_collection_callback(
        c.bot, c.cid, c.q, c.data), sub="personal_collections"),
    # Готовка vs личные коллекции.
    R(("as_food", "as_food_back", "as_fridge_cook"), lambda c: c.status(
        lambda status: cooking.handle_callback(c.bot, c.cid, c.q, c.data, status=status)), sub="cooking"),
    R(("as_food*", "as_fridge*", "as_recipe*"), lambda c: cooking.handle_callback(
        c.bot, c.cid, c.q, c.data), sub="cooking"),
    R("as_*", lambda c: personal_collections.handle_collection_callback(
        c.bot, c.cid, c.q, c.data), sub="personal_collections"),
    # Гардероб: инлайн-кабинет.
    R("w_look", lambda c: c.status(
        lambda status: wardrobe.handle_callback(c.bot, c.cid, c.q, c.data, status=status)), sub="wardrobe"),
    # «Новый образ»: выбор направления под образом, затем новый образ в этом стиле.
    R("w_pick", _swap_kb(lambda c: wardrobe.style_picker_kb())),
    R("w_card", _swap_kb(lambda c: wardrobe.build_wardrobe_keyboard())),
    R("w_lookst_*", _picked(lambda c: c.status(
        lambda status: wardrobe.handle_callback(c.bot, c.cid, c.q, c.data, status=status)))),
    R("w_*", lambda c: wardrobe.handle_callback(c.bot, c.cid, c.q, c.data), sub="wardrobe"),
    R("colr:*", _open_collection_route),
    # Настройки обучения и общие настройки.
    R(("set_learning", "set_learning_dict", "set_learning_dictionary",
       "toggle_learning_language", "toggle_learning_language_dict",
       "set_learning_language_*", "set_learning_level_*"),
      _safe(lambda c: learning_settings.handle_learning_settings_callback(c.bot, c.cid, c.q, c.data))),
    R(("set_*", "setadd_*", "setdel_*", "adm_*"),
      _safe(lambda c: settings.handle_callback(c.bot, c.cid, c.data, c.q)), sub="settings"),
    R("m_close", _close),
    R("m_settings", lambda c: settings.send_home(c.bot, c.cid, q=c.q)),
    R("m_notes", lambda c: settings.send_home(c.bot, c.cid)),
    R("m_food_next", lambda c: c.status(
        lambda status: menu.send_food_menu(c.bot, c.cid, status=status, refresh=True))),
    # «Новое меню»: кухня → новое меню на день (предпочтения не меняются).
    # food_meal_* — кнопки старых сообщений с шагом приёма пищи.
    R(("food_pick", "food_meal_*"), _swap_kb(lambda c: menu_ui.food_cuisine_kb(settings.CUISINE_OPTIONS))),
    R("food_card", _swap_kb(lambda c: menu_ui.food_card_kb(settings.food_meals(c.cid)))),
    R("food_go_*", _picked(_food_recipe)),
    R("m_menu", _main_menu),
    # Погодное предупреждение остаётся в истории, «Мой день» — отдельным сообщением.
    R("weather_myday", lambda c: c.status(lambda status: myday.send_plany(c.bot, c.cid, status=status))),
    R("notify_learning", _notify_learning),
    # Первый вход в раздел заменяет временное главное меню готовой карточкой;
    # новый вариант по кнопке под карточкой оставляет исходный результат в истории.
    R("m_myday", lambda c: c.status(
        lambda status: myday.send_plany(c.bot, c.cid, status=status), preserve_message=False)),
    R("m_wardrobe", lambda c: c.status(
        lambda status: wardrobe.send_home(c.bot, c.cid, status=status), preserve_message=False)),
    # Рецепт дня текущего приёма пищи; новый вариант — только по m_food_next.
    R("m_food", lambda c: c.status(
        lambda status: menu.send_food_menu(c.bot, c.cid, status=status), preserve_message=False)),
    # Хаб читает только готовые кэши — без статуса ожидания.
    R("m_leisure", lambda c: leisure_hub.send_hub(c.bot, c.cid, q=c.q)),
    R("m_*", _submenu),
    R("a_*", _safe(_action), sub="actions"),
    R("ex_*", lambda c: learning_router.handle_callback(c.bot, c.cid, c.data, c.status, q=c.q),
      sub="learning_router"),
    R("noop", _noop),
    # View-режим очистки (стабильный id + revision): двоеточие отличает его
    # от старого позиционного формата с подчёркиванием ниже.
    R(("clt:*", "clp:*", "cla:*", "clx:*", "cld:*", "cldc:*", "clact:*", "clactc:*",
       "clcancel:*", "cledit:*"), lambda c: cleanup.handle_view_callback(c.bot, c.cid, c.data, c.q)),
    R(("clt_*", "clp_*", "cla_*", "cld_*"), lambda c: cleanup.handle_cleanup(c.bot, c.cid, c.data, c.q)),
    R("worddel_*", lambda c: dictionary.del_word(c.bot, c.cid, int(c.data.split("_")[1]))),
    # Игра.
    R("game_again", lambda c: c.status(lambda status: learning_game.send_game(c.bot, c.cid, status=status))),
    R("game_hint", lambda c: learning_game.game_hint(c.bot, c.cid, c.q)),
    R("game_reveal", lambda c: learning_game.game_reveal(c.bot, c.cid, c.q)),
    # Старые кнопки общего экрана «Досуг» ведут в соответствующую категорию.
    R("leisure_prefs_movie", lambda c: leisure_movies.send_movie_prefs(c.bot, c.cid, c.q)),
    R("leisure_prefs_books", lambda c: leisure_books.send_book_preferences(c.bot, c.cid, c.q)),
    R("leisure_prefs_music", lambda c: leisure_music.send_music_preferences(c.bot, c.cid, c.q)),
    R("leisure_prefs_movie_favorites", lambda c: cleanup.open_collection(
        c.bot, c.cid, "cinema_favorites", back="movie_prefs")),
    R("leisure_prefs_books_favorites", lambda c: cleanup.open_collection(
        c.bot, c.cid, "books_favorites", back="book_prefs")),
    R("leisure_prefs_music_favorites", lambda c: cleanup.open_collection(
        c.bot, c.cid, "music_favorite_artists", back="music_prefs")),
    R("movie_prefs", lambda c: leisure_movies.send_movie_prefs(c.bot, c.cid, c.q)),
    R("lz_prem", lambda c: leisure_hub.send_premieres_menu(c.bot, c.cid, q=c.q)),
    R("lz_more", lambda c: leisure_hub.send_new_premieres(c.bot, c.cid, q=c.q)),
    R("lz_lib", lambda c: leisure_hub.send_library_menu(c.bot, c.cid, q=c.q)),
    # Книги.
    R("book_reco", lambda c: c.status(lambda status: leisure_books.send_books_reco(c.bot, c.cid, status=status))),
    R("book_next", _picked(lambda c: c.status(lambda _s: leisure_books._advance_book(c.bot, c.cid)))),
    R("book_pick_*", _swap_kb(lambda c: leisure_books._book_genre_menu_kb(back=f"book_card_{_tail_int(c)}"))),
    R("book_card_*", _swap_kb(lambda c: leisure_books._book_kb(_tail_int(c)))),
    R("yt:*", _yearly_tops),
    R("book_premieres", lambda c: c.status(
        lambda status: leisure_books.send_book_premieres(c.bot, c.cid, status=status))),
    R("book_premiere_page:*", lambda c: leisure_books.show_book_premiere_page(c.q, _page(c))),
    R("book_genre_menu", _acked(lambda c: leisure_books.send_book_genre_menu(c.bot, c.cid, c.q))),
    R("book_g_*", _picked(lambda c: c.status(
        lambda _s: leisure_books.send_book_by_genre(c.bot, c.cid, c.data[len("book_g_"):])))),
    # Музыка.
    R("music_reco", lambda c: c.status(lambda _s: leisure_music.send_listen(c.bot, c.cid))),
    R("music_next", _picked(lambda c: c.status(
        lambda status: leisure_music.listen_next(c.bot, c.cid, status=status)))),
    R("music_pick", _swap_kb(lambda c: leisure_music._music_genre_menu_kb(c.cid, back="music_card"))),
    R("music_card", _swap_kb(lambda c: leisure_music._listen_kb())),
    R("music_archive", lambda c: c.status(
        lambda status: leisure_music.send_music_task(c.bot, c.cid, "archive", status=status))),
    R("music_task_*", lambda c: c.status(lambda status: leisure_music.send_music_task(
        c.bot, c.cid, c.data[len("music_task_"):], status=status))),
    R("music_genre_menu", _acked(lambda c: leisure_music.send_music_genre_menu(c.bot, c.cid, c.q))),
    # «Настроить» под карточкой Досуга: настройки раздела новым сообщением, карточка остаётся.
    R("lz_cfg_movie", lambda c: leisure_movies.send_favorite_movies(c.bot, c.cid)),
    R("lz_cfg_book", lambda c: leisure_books.send_favorite_books(c.bot, c.cid)),
    R("lz_cfg_music", lambda c: cleanup.open_collection(c.bot, c.cid, "music_favorite_artists", back="lz_lib")),
    R("music_g_*", _picked(lambda c: c.status(lambda status: leisure_music.send_music_by_genre(
        c.bot, c.cid, c.data[len("music_g_"):], status=status)))),
    # Избранное кино и книги.
    R("movie_favorites", lambda c: leisure_movies.send_favorite_movies(c.bot, c.cid, q=c.q)),
    R("mfg:*", lambda c: leisure_movies.send_favorite_movie_genre(c.bot, c.cid, *_genre_args(c), q=c.q)),
    R("mfl:*", lambda c: leisure_movies.send_favorite_movie_list(c.bot, c.cid, *_genre_args(c), q=c.q)),
    R("mfi:*", lambda c: leisure_movies.send_favorite_movie_card(c.bot, c.cid, *_card_args(c))),
    # Удаление сразу, без отдельного подтверждения (mfdok/bfdok — кнопки старых сообщений).
    R("mfd:*", _delete_favorite_movie),
    R("mfdok:*", _delete_favorite_movie),
    R("book_favorites", lambda c: leisure_books.send_favorite_books(c.bot, c.cid, q=c.q)),
    R("bfg:*", lambda c: leisure_books.send_favorite_book_genre(c.bot, c.cid, *_genre_args(c), q=c.q)),
    R("bfi:*", lambda c: leisure_books.send_favorite_book_card(c.bot, c.cid, *_card_args(c))),
    R("bfd:*", lambda c: leisure_books.delete_favorite_book(c.bot, c.cid, *_card_args(c)[:2], q=c.q)),
    R("bfdok:*", lambda c: leisure_books.delete_favorite_book(c.bot, c.cid, *_token_id(c), q=c.q)),
    # Предпочтения.
    R("book_prefs", lambda c: leisure_books.send_book_preferences(c.bot, c.cid, c.q)),
    R("bookpref_*", _acked(lambda c: leisure_books.toggle_book_preference(c.bot, c.cid, c.data, c.q))),
    R("artist_favorites", lambda c: cleanup.open_collection(
        c.bot, c.cid, "music_favorite_artists", back="lz_lib")),
    R("music_prefs", lambda c: leisure_music.send_music_preferences(c.bot, c.cid, c.q)),
    R("music_style_*", _acked(lambda c: leisure_music.toggle_music_style(
        c.bot, c.cid, c.data[len("music_style_"):], c.q))),
    R("mpref_*", _acked(lambda c: leisure_movies.toggle_movie_pref(c.bot, c.cid, c.data, c.q))),
    # Кино: явный запрос всегда получает новый вариант, а не карточку дня из кэша.
    R("movie_reco", lambda c: c.status(
        lambda status: leisure_movies.send_current_movie(c.bot, c.cid, status=status))),
    R("movie_next", _picked(lambda c: c.status(
        lambda status: leisure_movies.send_recos(c.bot, c.cid, "movie", status=status)))),
    R("movie_pick_*", _swap_kb(lambda c: leisure_movies._movie_genre_menu_kb(back=f"movie_card_{_tail_int(c)}"))),
    R("movie_card_*", _swap_kb(lambda c: leisure_movies._movie_kb(_tail_int(c)))),
    R("movie_premieres", lambda c: c.status(
        lambda status: leisure_movies.send_combined_premieres(c.bot, c.cid, status=status))),
    R("combined_premiere_page:*", lambda c: leisure_movies.show_combined_premiere_page(c.cid, c.q, _page(c))),
    R("movie_premiere_page:*", _acked(lambda c: leisure_movies.show_movie_premiere_page(
        c.cid, c.q, int(c.data.rsplit(":", 1)[1])))),
    R("series_premieres", lambda c: c.status(
        lambda status: leisure_movies.send_series_premieres(c.bot, c.cid, status=status))),
    R("series_premiere_page:*", _acked(lambda c: leisure_movies.show_series_premiere_page(
        c.cid, c.q, int(c.data.rsplit(":", 1)[1])))),
    R("movie_genre_menu", _acked(lambda c: leisure_movies.send_movie_genre_menu(c.bot, c.cid, c.q))),
    R("movie_g_*", _picked(lambda c: c.status(
        lambda _s: leisure_movies.send_movie_by_genre(c.bot, c.cid, c.data[len("movie_g_"):])))),
    # Реакции на карточки.
    R("movie_love_*", lambda c: leisure_movies.movie_love(c.bot, c.cid, _tail_int(c), c.q)),
    R("book_love_*", lambda c: leisure_books.book_love(c.bot, c.cid, _tail_int(c), c.q)),
    # «Новинка» из выбора жанра: nov_pick_*, nov_card_* и nov_no_* раньше nov_*.
    R("nov_pick_*", _swap_kb(lambda c: leisure_novelty.genre_picker(
        c.cid, c.data[len("nov_pick_"):], back=f"nov_card_{c.data[len('nov_pick_'):]}"))),
    R("nov_card_*", _swap_kb(lambda c: leisure_novelty.card_keyboard(c.data[len("nov_card_"):]))),
    R("nov_no_*", lambda c: c.status(lambda status: leisure_novelty.dislike_novelty(
        c.bot, c.cid, c.data[len("nov_no_"):], status=status))),
    R("nov_*", _picked(lambda c: c.status(lambda status: leisure_novelty.send_novelty(
        c.bot, c.cid, c.data[len("nov_"):], status=status)))),
    R("listen_love", lambda c: leisure_music.listen_love(c.bot, c.cid, c.q)),
    R("movie_no_*", lambda c: c.status(lambda _s: leisure_movies.movie_dislike(c.bot, c.cid, _tail_int(c)))),
    R("book_no_*", lambda c: c.status(lambda _s: leisure_books.book_dislike(c.bot, c.cid, _tail_int(c)))),
    # «Продолжить / ещё раз» и «Короче / Глубже» для последнего ответа.
    R("chat_retry", lambda c: c.status(
        lambda status: retry_flow.retry_last_response(c.bot, c.cid, status=status))),
    R(("ans_short", "ans_deep"), lambda c: c.status(lambda _s: retry_flow.reword_last_response(
        c.bot, c.cid, "short" if c.data == "ans_short" else "deep"))),
)

# Удалённые разделы и старые главные экраны: (ключи, актуальный callback).
LEGACY_ALIASES = (
    (("m_travel*", "a_trav_*"), "m_menu"),
    (("m_movie", "m_music", "m_books", "m_games", "movie_now_playing"), "m_leisure"),
    ("m_food_gen", "m_food"),  # прежняя кнопка «🍳 Что приготовить»
)


def _legacy_alias(data):
    return next((target for keys, target in LEGACY_ALIASES if _matches(keys, data)), data)


async def handle(update, context, remove_reply_keyboard):
    q = update.callback_query
    cid = str(q.message.chat_id)
    bot = context.bot
    data = _legacy_alias(q.data)

    async def _inline_status(call, *, preserve_message=True):
        section = home_cache.SECTION_BY_CALLBACK.get(data)
        if section and home_cache.is_ready(section, cid):
            # Экран раздела уже готов («Назад», повторный вход): без индикатора.
            try:
                return await call(_DirectStatus(bot, cid, q, preserve_message))
            except Exception as e:
                _log.error("_inline_status: direct call failed data=%s cid=%s: %r", data, cid, e, exc_info=True)
                await verify.safe_error(bot, cid, e)
                return None
        topic = _status_topic(data)
        stages = _status_stages(data)
        _log.info("_inline_status: data=%s topic=%s cid=%s q_message_id=%s",
                  data, topic, cid, getattr(q.message, "message_id", None))
        status = await util.StatusManager.start_inline(
            q,
            bot=bot,
            cid=cid,
            stages=stages,
            preserve_message=preserve_message,
        )
        # Единый индикатор для долгих inline-сценариев: StatusManager сам
        # ставит тематическую одноколоночную кнопку и обновляет её по этапам.
        try:
            return await call(status)
        except Exception as e:
            _log.error("_inline_status: call failed data=%s cid=%s: %r", data, cid, e, exc_info=True)
            await verify.safe_error(bot, cid, e)
            return None
        finally:
            await status.stop(delete=True)
            _log.info("_inline_status: done data=%s cid=%s", data, cid)

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="❌ Бот приватный. Попроси владельца прислать инвайт.")
        return
    if data in ("m_learn", "m_menu"):
        trainer.cancel(cid)
    route = _first(ROUTES, data)
    if route:
        await route.handler(Ctx(bot, cid, q, data, _inline_status))
