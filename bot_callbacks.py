"""Маршрутизация inline callback-кнопок."""

import logging
import re

import access
import balance
import cleanup
import cooking
import dictionary_seed
import dictionary_tts
import fridge
import learning_dictionary as dictionary
import learning
import learning_game
import learning_settings
import learning_router
import leisure_books
import leisure_concerts
import leisure_movies
import leisure_music
import memory
import menu
import myday
import onboard
import retry_flow
import saved_items
import settings
import store
import trainer
import travel
import util
import verify
import wardrobe
import weather
from util import ack_loading as _ack, clear_loading as _unack

_log = logging.getLogger(__name__)

_STATUS_TOPIC_PREFIXES = (
    ("w_", "wardrobe"),
    ("m_food", "food"), ("as_food", "food"), ("as_fridge", "food"), ("as_recipe", "food"),
    ("a_recipe_", "food"), ("food_", "food"),
    ("a_dict", "learning"), ("a_train", "learning"), ("a_tr_", "learning"),
    ("ex_", "learning"), ("again_tr_", "learning"), ("game", "learning"),
    ("a_game", "learning"),
    ("m_movie", "leisure"), ("m_books", "leisure"), ("m_music", "leisure"),
    ("movie_", "leisure"), ("book_", "leisure"), ("music_", "leisure"), ("listen", "leisure"), ("a_concerts", "leisure"),
    ("m_travel", "travel"), ("a_trav_", "travel"),
    ("as_daycheck", "health"), ("as_motiv", "health"), ("as_doctor", "health"), ("role_", "health"), ("ans_", "health"), ("chat_retry", "health"),
)

def _status_topic(data):
    for prefix, topic in _STATUS_TOPIC_PREFIXES:
        if data.startswith(prefix):
            return topic
    return None


def _status_stages(data):
    """Возвращает статусы ожидания с понятным первым действием."""
    topic = _status_topic(data)
    stages = util.StatusManager.TOPIC_STAGES.get(topic) if topic else None
    if not stages:
        stages = util.StatusManager.STAGES

    def progress(first, second, final):
        return ((0, first), (2, second), (6, final))

    if data.startswith(("as_food", "as_fridge_cook", "m_food", "food_")):
        first = "⏳ Ищу рецепт..."
    elif data == "as_daycheck":
        return progress("🧠 Разбираю мысль...", "💭 Ищу опору в записи...", "📝 Готовлю разбор...")
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
    elif re.fullmatch(r"a_trav_country_[A-Z0-9]+_\d+", data):
        return progress("🗺️ Открываю страну...", "🔍 Собираю факты...", "📝 Готовлю карточку страны...")
    elif data.startswith(("a_trav_", "m_travel")):
        first = "✈️ Ищу поездку..."
    elif data == "m_food":
        first = "⏳ Ищу рецепт..."
    elif data == "m_wardrobe":
        first = "⏳ Ищу образ..."
    elif data == "m_movie":
        first = "🎬 Ищу кино..."
    elif data == "m_books":
        first = "📚 Ищу книгу..."
    elif data == "m_music":
        first = "🎧 Ищу музыку..."
    elif data in ("a_plany", "m_myday"):
        return progress("☀️ Собираю мой день...", "🌦️ Сверяю планы...", "📝 Готовлю сводку...")
    elif data == "a_w_week":
        return progress("🌦️ Ищу прогноз...", "🗓️ Сверяю дни...", "📝 Готовлю прогноз...")
    elif topic == "wardrobe":
        first = "⏳ Ищу образ..."
    elif topic == "food":
        first = "⏳ Ищу рецепт..."
    elif topic == "learning":
        first = "🧠 Ищу задание..."
    elif topic == "leisure":
        first = "✨ Ищу рекомендацию..."
    elif topic == "travel":
        first = "✈️ Ищу поездку..."
    elif topic == "health":
        first = "💬 Ищу ответ..."
    else:
        return stages
    return ((0, first), *stages[1:])

async def handle(update, context, remove_reply_keyboard):
    q = update.callback_query
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

    async def _inline_status(call, *, preserve_message=True):
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
    # Любое действие кнопкой означает, что пользователь начал новый сценарий.
    # Исключение — явная кнопка входа в режим выгрузки мыслей.
    if data != "thought_capture":
        balance.thoughts.cancel_capture(cid)
    pending_kind = store.pending_input.get(cid)
    if data.startswith("m_") and pending_kind in ("role_doctor", "role_medicine"):
        store.pending_input.pop(cid, None)
        if pending_kind == "role_doctor":
            store.doctor_context.pop(cid, None)
    # Онбординг новых пользователей
    if data.startswith("ob_"):
        await onboard.handle_callback(bot, cid, q, data)
        return

    # Закладки: fav_view_* и fav_del_*
    if data.startswith("fav_"):
        await saved_items.handle_notes_callback(bot, cid, q, data)
        return
    if data.startswith("thought_"):
        await balance.thoughts.handle_callback(bot, cid, q, data)
        return
    if data.startswith("tts_word:"):
        # answerCallbackQuery запускается заранее в bot.answer_callback, поэтому
        # кнопка перестаёт крутиться до сетевого запроса Azure.
        await dictionary_tts.send_pronunciation(bot, cid, data.split(":", 1)[1])
        return
    # Здоровье/готовка vs Закладки/Любимое
    if data.startswith("ls_"):
        await saved_items.handle_notes_callback(bot, cid, q, data)
        return
    if data.startswith("as_"):
        if data == "as_daycheck":
            await _inline_status(
                lambda status: balance.handle_callback(bot, cid, q, data, status=status),
                preserve_message=True,
            )
            return
        if data in ("as_food", "as_food_back", "as_fridge_cook"):
            await _inline_status(
                lambda status: cooking.handle_callback(bot, cid, q, data, status=status),
                preserve_message=True)
            return
        if data.startswith(("as_food", "as_fridge", "as_recipe")):
            await cooking.handle_callback(bot, cid, q, data)
        elif data.startswith(("as_daycheck", "as_motiv", "as_doctor", "as_medicine")):
            await balance.handle_callback(bot, cid, q, data)
        else:
            await saved_items.handle_notes_callback(bot, cid, q, data)
        return
    # Гардероб: инлайн-кабинет
    if data.startswith("w_"):
        if data == "w_look":
            await _inline_status(
                lambda status: wardrobe.handle_callback(bot, cid, q, data, status=status),
                preserve_message=True)
        else:
            await wardrobe.handle_callback(bot, cid, q, data)
        return
    if data.startswith("colr:"):
        _, collection_id, back = data.split(":", 2)
        await cleanup.open_collection(bot, cid, collection_id, back=back)
        return
    # Настройки обучения
    if data in ("set_learning", "set_learning_dict", "toggle_learning_language", "toggle_learning_language_dict"):
        try:
            await learning_settings.handle_learning_settings_callback(bot, cid, q, data)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if data.startswith("set_learning_level_"):
        try:
            await learning_settings.handle_learning_settings_callback(bot, cid, q, data)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    # Настройки
    if data.startswith(("set_", "setadd_", "setdel_", "adm_")):
        try:
            await settings.handle_callback(bot, cid, data, q)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    # Навигация по подменю - редактируем сообщение на месте
    if data == "m_close":
        try:
            await q.message.edit_text("Готово.", reply_markup=menu.main_menu_kb())
        except Exception:
            pass
        return
    if data == "m_notes":
        await saved_items.send_notes(bot, cid); return
    if data == "m_food_gen":
        await _inline_status(
            lambda status: cooking.send_recipe_featured(bot, cid, status=status),
            preserve_message=True); return
    if data == "m_food_next":
        await _inline_status(
            lambda status: menu.send_food_menu(bot, cid, status=status, refresh=True),
            preserve_message=True); return
    if data in ("m_learn", "m_menu"):
        trainer.cancel(cid)

    if data == "m_learn" and not learning.build_learning_home(cid).get("has_material"):
        await dictionary_seed.send_seed_intro(bot, cid, q=q)
        return

    if data == "m_food":
        if not menu.has_available_fridge(cid):
            await menu.send_food_menu(bot, cid, q=q)
            return
        await _inline_status(
            lambda status: menu.send_food_menu(bot, cid, status=status, q=q),
        )
        return
    if data == "m_movie":
        await _inline_status(
            lambda _status: leisure_movies.send_movie_home(bot, cid, q),
        )
        return
    if data == "m_books":
        await _inline_status(lambda _status: leisure_books.send_books_home(bot, cid, q))
        return
    if data == "m_music":
        await _inline_status(lambda _status: leisure_music.send_music_home(bot, cid, q))
        return
    if data == "m_leisure":
        # Старые карточки не ведут в удалённый агрегатор Досуга.
        data = "m_menu"
    if data == "m_wardrobe":
        if not wardrobe.has_wardrobe_items(cid):
            await wardrobe.send_home(bot, cid, q=q)
            return
        await _inline_status(
            lambda status: wardrobe.send_home(bot, cid, q=q, status=status),
        )
        return
    if data == "m_travel":
        await _inline_status(
            lambda status: travel.send_home(bot, cid, q, status=status),
        )
        return
    if data == "m_myday":
        await _inline_status(
            lambda status: myday.send_plany(bot, cid, status=status),
        ); return
    if data == "m_menu":
        text, entities, kb = menu.main_menu_screen(cid)
        # Главное меню открывается отдельным сообщением: полезная карточка
        # (рецепт, рекомендация, результат тренировки) остаётся в истории.
        await bot.send_message(
            chat_id=cid,
            text=text,
            reply_markup=kb,
            entities=entities,
            transient=True,
        )
        return
    if data.startswith("m_"):
        text, entities, kb = menu.menu_screen(data, cid)
        if data == "m_balance":
            await bot.send_message(
                chat_id=cid,
                text=text,
                reply_markup=kb,
                entities=entities,
                transient=True,
            )
            return
        try:
            await q.message.edit_text(text, reply_markup=kb, entities=entities)
        except Exception:
            await bot.send_message(
                chat_id=cid,
                text=text,
                reply_markup=kb,
                entities=entities,
            )
        return

    # Действия
    if data.startswith("a_"):
        act = data[2:]
        try:
            if act == "plany":
                await _inline_status(
                    lambda status: myday.send_plany(bot, cid, force=True, status=status),
                )
            elif await learning_router.handle_action(bot, cid, q, act, _inline_status):
                pass
            elif act == "w_week":
                await _inline_status(
                    lambda status: weather.send_weather(bot, cid, "week", status=status),
                    preserve_message=True,
                )
            elif act == "setcity":
                store.pending_input[cid] = "setcity"
                await bot.send_message(chat_id=cid, text="📍 Напиши название города — переключу на него.")
            elif act == "trav_go":
                await _inline_status(
                    lambda status: travel.send_go(bot, cid, status=status),
                    preserve_message=True,
                )
            elif act == "trav_no":
                await _inline_status(
                    lambda status: travel.travel_dislike(bot, cid, status=status),
                    preserve_message=True,
                )
            elif act == "trav_plan":
                await _inline_status(lambda _s: travel.send_plan(bot, cid))
            elif act == "trav_fav":
                await _inline_status(lambda status: travel.travel_fav(bot, cid, status=status))
            elif act == "trav_save":
                await travel.save_plan(bot, cid, q)
            elif re.fullmatch(r"trav_country_[A-Z0-9]+_\d+", act):
                await _inline_status(
                    lambda status: travel.handle_country_callback(bot, cid, q, act, status=status),
                    preserve_message=True,
                )
            elif act.startswith("trav_countries") or act.startswith("trav_country_"):
                await travel.handle_country_callback(bot, cid, q, act)
            elif act == "trav_transport":
                await travel.send_transport_settings(bot, cid, q)
            elif act.startswith("trav_mode_"):
                await travel.toggle_transport(bot, cid, act[len("trav_mode_"):], q)
            elif act == "watch":
                await _inline_status(
                    lambda _s: leisure_movies.send_movie_home(bot, cid, q))
            elif act == "read":
                await _inline_status(lambda _s: leisure_books.send_books_home(bot, cid, q))
            elif act == "watchlist":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="m_movie")
            elif act == "watchclean":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="m_movie")
            elif act == "concerts_find":
                await _inline_status(lambda _s: leisure_concerts.find_concerts(bot, cid, "home"))
            elif act == "concerts_nearby":
                await _inline_status(lambda _s: leisure_concerts.find_concerts(bot, cid, "home"))
            elif act == "concerts_search":
                await leisure_concerts.prompt_artist_search(bot, cid)
            elif act == "artist_concerts":
                await _inline_status(lambda _s: leisure_concerts.find_concerts(bot, cid, "home"))
            elif act == "concerts_pick":
                await leisure_concerts.concert_pick_country(bot, cid)
            elif act in ("concerts_nl", "concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
                         "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
                         "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"):
                await _inline_status(lambda _s: leisure_concerts.find_concerts(bot, cid, act.split("_")[1]))
            elif act == "listen":
                await _inline_status(lambda _s: leisure_music.send_music_home(bot, cid, q))
            elif act == "listen_no":
                await _inline_status(
                    lambda _s: leisure_music.listen_dislike(bot, cid),
                    preserve_message=True,
                )
            elif act in ("food_breakfast", "recipe_breakfast"):
                await _inline_status(
                    lambda status: cooking.enter_meal(bot, cid, "breakfast", status=status),
                    preserve_message=True)
            elif act in ("food_lunch", "recipe_lunch"):
                await _inline_status(
                    lambda status: cooking.enter_meal(bot, cid, "lunch", status=status),
                    preserve_message=True)
            elif act in ("food_dinner", "recipe_dinner"):
                await _inline_status(
                    lambda status: cooking.enter_meal(bot, cid, "dinner", status=status),
                    preserve_message=True)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return

    if data.startswith("ex_"):
        await learning_router.handle_callback(bot, cid, data, _inline_status, q=q)
        return
    # Игра
    if data == "noop":
        return
    if data.startswith(("clt:", "clp:", "cla:", "clx:", "cld:", "cldc:", "clact:", "clactc:", "clcancel:", "cledit:")):
        # PR3a view-режим (стабильный id + revision) — двоеточие как разделитель
        # отличает его от старого позиционного формата ниже (символ подчёркивания).
        # clx:/cldc:/clcancel: — «Удалить все N» и confirm-экран (PR4, P2-2).
        await cleanup.handle_view_callback(bot, cid, data, q)
        return
    if data.startswith(("clt_", "clp_", "cla_", "cld_")):
        await cleanup.handle_cleanup(bot, cid, data, q)
        return
    if data.startswith("worddel_"):
        await dictionary.del_word(bot, cid, int(data.split("_")[1]))
        return
    if data == "game_again":
        await _inline_status(
            lambda status: learning_game.send_game(bot, cid, status=status),
            preserve_message=True)
        return
    if data == "game_hint":
        await learning_game.game_hint(bot, cid, q)
        return
    if data == "game_reveal":
        await learning_game.game_reveal(bot, cid, q)
        return
    # Старые кнопки общего экрана «Досуг»: направляем в соответствующую категорию.
    if data == "leisure_prefs_movie":
        await leisure_movies.send_movie_prefs(bot, cid, q)
        return
    if data == "leisure_prefs_books":
        await leisure_books.send_book_preferences(bot, cid, q)
        return
    if data == "leisure_prefs_music":
        await leisure_music.send_music_preferences(bot, cid, q)
        return
    if data == "leisure_prefs_movie_favorites":
        await cleanup.open_collection(bot, cid, "cinema_favorites", back="movie_prefs")
        return
    if data == "leisure_prefs_books_favorites":
        await cleanup.open_collection(bot, cid, "books_favorites", back="book_prefs")
        return
    if data == "leisure_prefs_music_favorites":
        await cleanup.open_collection(bot, cid, "music_favorite_artists", back="music_prefs")
        return
    if data == "movie_prefs":
        await leisure_movies.send_movie_prefs(bot, cid, q)
        return
    if data == "book_reco":
        await _inline_status(lambda _s: leisure_books.send_books_reco(bot, cid))
        return
    if data == "book_genre_menu":
        await _ack(q)
        await leisure_books.send_book_genre_menu(bot, cid, q)
        return
    if data.startswith("book_g_"):
        await _inline_status(
            lambda _s: leisure_books.send_book_by_genre(bot, cid, data[len("book_g_"):]),
            preserve_message=True,
        )
        return
    if data == "music_reco":
        await _inline_status(lambda _s: leisure_music.send_listen(bot, cid))
        return
    if data == "music_genre_menu":
        await _ack(q)
        await leisure_music.send_music_genre_menu(bot, cid, q)
        return
    if data.startswith("music_g_"):
        await _inline_status(
            lambda status: leisure_music.send_music_by_genre(bot, cid, data[len("music_g_"):], status=status),
            preserve_message=True,
        )
        return
    if data == "movie_favorites":
        await cleanup.open_collection(bot, cid, "cinema_favorites", back="m_movie")
        return
    if data == "book_favorites":
        await cleanup.open_collection(bot, cid, "books_favorites", back="m_books")
        return
    if data == "book_prefs":
        await leisure_books.send_book_preferences(bot, cid, q)
        return
    if data == "artist_favorites":
        await cleanup.open_collection(bot, cid, "music_favorite_artists", back="m_music")
        return
    if data == "music_prefs":
        await leisure_music.send_music_preferences(bot, cid, q)
        return
    if data.startswith("music_style_"):
        await _ack(q)
        await leisure_music.toggle_music_style(bot, cid, data[len("music_style_"):], q)
        return
    if data.startswith("mpref_"):
        await _ack(q)
        await leisure_movies.toggle_movie_pref(bot, cid, data, q)
        return
    if data == "movie_reco":
        await _inline_status(
            lambda _s: leisure_movies.send_recos(bot, cid, "movie"),
            preserve_message=True)
        return
    if data == "movie_now_playing":
        await _inline_status(
            lambda status: leisure_movies.send_movie_now_playing(bot, cid, q, status=status),
        )
        return
    if data == "movie_genre_menu":
        await _ack(q)
        await leisure_movies.send_movie_genre_menu(bot, cid, q)
        return
    if data.startswith("movie_g_"):
        await _inline_status(
            lambda _s: leisure_movies.send_movie_by_genre(bot, cid, data[len("movie_g_"):]),
            preserve_message=True)
        return
    if data.startswith("movie_love_"):
        await leisure_movies.movie_love(bot, cid, int(data.split("_")[-1]), q)
        return
    if data.startswith("book_love_"):
        await leisure_books.book_love(bot, cid, int(data.split("_")[-1]), q)
        return
    if data == "listen_love":
        await leisure_music.listen_love(bot, cid, q)
        return
    if data.startswith("movie_no_"):
        await _inline_status(
            lambda _s: leisure_movies.movie_dislike(bot, cid, int(data.split("_")[-1])),
            preserve_message=True)
        return
    if data.startswith("book_no_"):
        await _inline_status(
            lambda _s: leisure_books.book_dislike(bot, cid, int(data.split("_")[-1])),
            preserve_message=True,
        )
        return
    # Совместимость со старыми сообщениями дневника тревог.
    if data == "worry_clearall":
        await balance.worry_clear_all(bot, cid)
        return
    # «Продолжить / ещё раз»
    if data == "chat_retry":
        await _inline_status(lambda status: retry_flow.retry_last_response(bot, cid, status=status))
        return
    # «Короче / Глубже» - переписать последний ответ
    if data in ("ans_short", "ans_deep"):
        await _inline_status(lambda _s: balance.reword(bot, cid, "short" if data == "ans_short" else "deep"))
        return
