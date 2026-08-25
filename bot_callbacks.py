"""Маршрутизация inline callback-кнопок."""

import logging
import re

import access
import callback_topics
import cleanup
import cooking
import dictionary_tts
import learning_dictionary as dictionary
import learning_game
import learning_settings
import learning_router
import leisure_books
import leisure_concerts
import leisure_games
import leisure_movies
import leisure_music
import menu
import myday
import onboard
import retry_flow
import personal_collections
import settings
import store
import trainer
import travel
import util
import verify
import wardrobe
import weather
from util import ack_loading as _ack

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

    if data.startswith(("as_food", "as_fridge_cook", "m_food", "food_")):
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
    elif data.startswith(("vg_", "m_games")):
        return progress("👾 Ищу игру...", "🎮 Сверяю платформы...", "📝 Готовлю карточку...")
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
    elif topic == "travel":
        first = "✈️ Ищу поездку..."
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
    # Онбординг новых пользователей
    if data.startswith("ob_"):
        await onboard.handle_callback(bot, cid, q, data)
        return
    if data.startswith("collection_pick:"):
        await personal_collections.handle_collection_callback(bot, cid, q, data)
        return
    if data.startswith(("book_add_ok:", "book_add_next:")):
        await leisure_books.handle_manual_book_add_callback(bot, cid, q, data)
        return

    # Старые callbacks карточек не меняют данные и ведут к актуальному экрану.
    if data.startswith("fav_"):
        await personal_collections.handle_collection_callback(bot, cid, q, data)
        return
    if data.startswith("tts_word:"):
        # answerCallbackQuery запускается заранее в bot.answer_callback, поэтому
        # кнопка перестаёт крутиться до сетевого запроса Azure.
        await dictionary_tts.send_pronunciation(bot, cid, data.split(":", 1)[1])
        return
    # Готовка vs личные коллекции
    if data.startswith("ls_"):
        await personal_collections.handle_collection_callback(bot, cid, q, data)
        return
    if data.startswith("as_"):
        if data in ("as_food", "as_food_back", "as_fridge_cook"):
            await _inline_status(
                lambda status: cooking.handle_callback(bot, cid, q, data, status=status),
                preserve_message=True)
            return
        if data.startswith(("as_food", "as_fridge", "as_recipe")):
            await cooking.handle_callback(bot, cid, q, data)
        else:
            await personal_collections.handle_collection_callback(bot, cid, q, data)
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
    if data.startswith("set_learning_language_"):
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
    if data == "m_settings":
        await settings.send_home(bot, cid, q=q); return
    if data == "m_notes":
        await settings.send_home(bot, cid); return
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
    if data == "weather_myday":
        # Погодное предупреждение — полезный результат: оставляем его в истории
        # и открываем «Мой день» отдельным сообщением.
        await _inline_status(
            lambda status: myday.send_plany(bot, cid, status=status),
            preserve_message=True,
        )
        return
    if data == "notify_learning":
        # Слова дня остаются в истории; учебный экран открывается отдельно.
        trainer.cancel(cid)
        text, entities, kb = menu.menu_screen("m_learn", cid)
        await bot.send_message(
            chat_id=cid,
            text=text,
            entities=entities,
            reply_markup=kb,
            transient=True,
        )
        return
    # Первый вход в раздел заменяет временное главное меню уже подготовленной
    # персональной карточкой. Новый вариант пользователь запрашивает кнопкой
    # под карточкой — тогда исходный результат остаётся в истории.
    if data == "m_myday":
        await _inline_status(
            lambda status: myday.send_plany(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_wardrobe":
        await _inline_status(
            lambda status: wardrobe.send_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_food":
        await _inline_status(
            lambda status: menu.send_food_menu(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_travel":
        await _inline_status(
            lambda status: travel.send_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_movie":
        await _inline_status(
            lambda status: leisure_movies.send_movie_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_books":
        await _inline_status(
            lambda status: leisure_books.send_books_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_music":
        await _inline_status(
            lambda status: leisure_music.send_music_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data == "m_games":
        await _inline_status(
            lambda status: leisure_games.send_games_home(bot, cid, status=status),
            preserve_message=False,
        )
        return
    if data.startswith("m_"):
        text, entities, kb = menu.menu_screen(data, cid)
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
            elif act == "w_full":
                await _inline_status(
                    lambda status: weather.send_weather(bot, cid, "full", status=status),
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
            elif re.fullmatch(r"trav_country_[A-Z0-9]+_\d+", act):
                await _inline_status(
                    lambda status: travel.handle_country_callback(bot, cid, q, act, status=status),
                    preserve_message=True,
                )
            elif act.startswith("trav_countries") or act.startswith("trav_country_"):
                await travel.handle_country_callback(bot, cid, q, act)
            elif act == "trav_transport":
                await travel.send_home(bot, cid, q)
            elif act.startswith("trav_mode_"):
                await travel.send_home(bot, cid, q)
            elif act == "watch":
                await _inline_status(
                    lambda status: leisure_movies.send_movie_home(bot, cid, q, status=status))
            elif act == "read":
                await _inline_status(lambda status: leisure_books.send_books_home(bot, cid, q, status=status))
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
                await _inline_status(lambda status: leisure_music.send_music_home(bot, cid, q, status=status))
            elif act == "listen_no":
                await _inline_status(
                    lambda _s: leisure_music.listen_dislike(bot, cid),
                    preserve_message=True,
                )
            elif act in ("food_breakfast", "recipe_breakfast"):
                await _inline_status(
                    lambda status: menu.send_food_menu(
                        bot, cid, status=status, refresh=False, meal="breakfast"),
                    preserve_message=True)
            elif act in ("food_lunch", "recipe_lunch"):
                await _inline_status(
                    lambda status: menu.send_food_menu(
                        bot, cid, status=status, refresh=False, meal="lunch"),
                    preserve_message=True)
            elif act in ("food_dinner", "recipe_dinner"):
                await _inline_status(
                    lambda status: menu.send_food_menu(
                        bot, cid, status=status, refresh=False, meal="dinner"),
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
        await _inline_status(lambda status: leisure_books.send_books_reco(bot, cid, status=status))
        return
    if data == "book_premieres":
        await _inline_status(
            lambda status: leisure_books.send_book_premieres(bot, cid, status=status),
            preserve_message=True,
        )
        return
    if data.startswith("book_premiere_page:"):
        await leisure_books.show_book_premiere_page(q, int(data.split(":", 1)[1]))
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
    if data == "music_archive":
        await _inline_status(
            lambda status: leisure_music.send_music_task(bot, cid, "archive", status=status),
            preserve_message=True,
        )
        return
    if data.startswith("music_task_"):
        await _inline_status(
            lambda status: leisure_music.send_music_task(
                bot, cid, data[len("music_task_"):], status=status),
            preserve_message=True,
        )
        return
    if data == "music_genre_menu":
        await _ack(q)
        await leisure_music.send_music_genre_menu(bot, cid, q)
        return
    if data == "vg_reco":
        await _inline_status(
            lambda status: leisure_games.send_game_recommendation(
                bot, cid, status=status, refresh=True,
            ),
            preserve_message=True,
        )
        return
    if data == "vg_set":
        await leisure_games.send_game_set(bot, cid, q=q)
        return
    if data.startswith("vg_setg:"):
        _op, token, genre_index, page = data.split(":", 3)
        await leisure_games.send_game_set_genre(bot, cid, token, int(genre_index), int(page), q=q)
        return
    if data.startswith("vg_seti:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_games.send_game_set_card(bot, cid, token, short_id, int(genre_index), int(page))
        return
    if data.startswith("vg_setd:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_games.confirm_game_set_delete(
            bot, cid, token, short_id, int(genre_index), int(page), q=q,
        )
        return
    if data.startswith("vg_setdok:"):
        _op, token, short_id = data.split(":", 2)
        await leisure_games.delete_game_set_item(bot, cid, token, short_id, q=q)
        return
    if data == "vg_board":
        await _inline_status(
            lambda status: leisure_games.send_game_recommendation(
                bot, cid, status=status, refresh=True, genre="board",
            ),
            preserve_message=True,
        )
        return
    if data == "vg_next":
        await _inline_status(
            lambda status: leisure_games.send_game_recommendation(
                bot, cid, status=status, refresh=True,
            ),
            preserve_message=True,
        )
        return
    if data.startswith("vg_next_"):
        await _inline_status(
            lambda status: leisure_games.send_game_recommendation(
                bot, cid, status=status, refresh=True, genre=data[len("vg_next_"):],
            ),
            preserve_message=True,
        )
        return
    if data == "vg_premieres":
        await _inline_status(
            lambda status: leisure_games.send_game_premieres(bot, cid, status=status),
            preserve_message=True,
        )
        return
    if data == "vg_genres":
        await _ack(q)
        await leisure_games.send_game_genres(bot, cid, q)
        return
    if data.startswith("vg_g_"):
        await _inline_status(
            lambda status: leisure_games.send_game_recommendation(
                bot, cid, status=status, refresh=True, genre=data[len("vg_g_"):],
            ),
            preserve_message=True,
        )
        return
    if data.startswith("music_g_"):
        await _inline_status(
            lambda status: leisure_music.send_music_by_genre(bot, cid, data[len("music_g_"):], status=status),
            preserve_message=True,
        )
        return
    if data == "movie_favorites":
        await leisure_movies.send_favorite_movies(bot, cid, q=q)
        return
    if data.startswith("mfg:"):
        _op, token, genre_index, page = data.split(":", 3)
        await leisure_movies.send_favorite_movie_genre(
            bot, cid, token, int(genre_index), int(page), q=q,
        )
        return
    if data.startswith("mfi:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_movies.send_favorite_movie_card(
            bot, cid, token, short_id, int(genre_index), int(page),
        )
        return
    if data.startswith("mfd:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_movies.send_favorite_movie_delete_confirmation(
            bot, cid, token, short_id, int(genre_index), int(page), q=q,
        )
        return
    if data.startswith("mfdok:"):
        parts = data.split(":")
        _op, token, short_id = parts[:3]
        genre_index = int(parts[3]) if len(parts) > 3 else None
        page = int(parts[4]) if len(parts) > 4 else 0
        await leisure_movies.delete_favorite_movie(
            bot, cid, token, short_id, genre_index, page, q=q,
        )
        return
    if data == "book_favorites":
        await leisure_books.send_favorite_books(bot, cid, q=q)
        return
    if data.startswith("bfg:"):
        _op, token, genre_index, page = data.split(":", 3)
        await leisure_books.send_favorite_book_genre(
            bot, cid, token, int(genre_index), int(page), q=q,
        )
        return
    if data.startswith("bfi:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_books.send_favorite_book_card(
            bot, cid, token, short_id, int(genre_index), int(page),
        )
        return
    if data.startswith("bfd:"):
        _op, token, short_id, genre_index, page = data.split(":", 4)
        await leisure_books.send_favorite_book_delete_confirmation(
            bot, cid, token, short_id, int(genre_index), int(page), q=q,
        )
        return
    if data.startswith("bfdok:"):
        _op, token, short_id = data.split(":", 2)
        await leisure_books.delete_favorite_book(bot, cid, token, short_id, q=q)
        return
    if data == "book_prefs":
        await leisure_books.send_book_preferences(bot, cid, q)
        return
    if data.startswith("bookpref_"):
        await _ack(q)
        await leisure_books.toggle_book_preference(bot, cid, data, q)
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
            lambda status: leisure_movies.send_recos(bot, cid, "movie", status=status),
            preserve_message=True)
        return
    if data == "movie_premieres":
        await _inline_status(
            lambda status: leisure_movies.send_movie_premieres(bot, cid, status=status),
            preserve_message=True,
        )
        return
    if data.startswith("movie_premiere_page:"):
        await _ack(q)
        await leisure_movies.show_movie_premiere_page(
            cid, q, int(data.rsplit(":", 1)[1]),
        )
        return
    if data == "series_premieres":
        await _inline_status(
            lambda status: leisure_movies.send_series_premieres(bot, cid, status=status),
            preserve_message=True,
        )
        return
    if data.startswith("series_premiere_page:"):
        await _ack(q)
        await leisure_movies.show_series_premiere_page(
            cid, q, int(data.rsplit(":", 1)[1]),
        )
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
    # «Продолжить / ещё раз»
    if data == "chat_retry":
        await _inline_status(lambda status: retry_flow.retry_last_response(bot, cid, status=status))
        return
    # «Короче / Глубже» - переписать последний ответ
    if data in ("ans_short", "ans_deep"):
        await _inline_status(
            lambda _s: retry_flow.reword_last_response(
                bot, cid, "short" if data == "ans_short" else "deep",
            )
        )
        return
