"""Маршрутизация inline callback-кнопок."""

import logging

import access
import balance
import cleanup
import cooking
import dictionary_tts
import firstvisit
import fridge
import learning_dictionary as dictionary
import learning_game
import learning_settings
import learning_router
import leisure_books
import leisure_concerts
import leisure_home
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
    ("m_food", "food"), ("as_food", "food"), ("as_fridge", "food"), ("as_recipe", "food"), ("as_my_recipe", "food"),
    ("a_recipe_", "food"), ("food_", "food"),
    ("a_dict", "learning"), ("a_train", "learning"), ("a_tr_", "learning"),
    ("ex_", "learning"), ("again_tr_", "learning"), ("game", "learning"),
    ("a_game", "learning"), ("gamediff_", "learning"),
    ("movie_", "leisure"), ("book_", "leisure"), ("listen", "leisure"), ("reco_", "leisure"), ("a_concerts", "leisure"),
    ("m_travel", "travel"), ("a_trav_", "travel"),
    ("as_daycheck", "health"), ("as_motiv", "health"), ("as_doctor", "health"), ("as_health_", "health"), ("role_", "health"), ("ans_", "health"), ("chat_retry", "health"),
)

def _status_topic(data):
    for prefix, topic in _STATUS_TOPIC_PREFIXES:
        if data.startswith(prefix):
            return topic
    return None

async def handle(update, context, remove_reply_keyboard):
    q = update.callback_query
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

    async def _inline_status(call):
        topic = _status_topic(data)
        stages = util.StatusManager.TOPIC_STAGES.get(topic) if topic else None
        _log.info("_inline_status: data=%s topic=%s cid=%s q_message_id=%s",
                  data, topic, cid, getattr(q.message, "message_id", None))
        status = await util.StatusManager.start_inline(q, bot=bot, cid=cid, stages=stages)
        try:
            return await call(status)
        except Exception as e:
            _log.error("_inline_status: call failed data=%s cid=%s: %r", data, cid, e, exc_info=True)
            await verify.safe_error(bot, cid, e)
            return None
        finally:
            await status.stop(delete=False)
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
        if data.startswith(("as_food", "as_fridge", "as_recipe", "as_my_recipe")):
            await cooking.handle_callback(bot, cid, q, data)
        elif data.startswith(("as_daycheck", "as_motiv", "as_doctor", "as_medicine", "as_health_")):
            await balance.handle_callback(bot, cid, q, data)
        else:
            await saved_items.handle_notes_callback(bot, cid, q, data)
        return
    # Гардероб: инлайн-кабинет
    if data.startswith("w_"):
        await wardrobe.handle_callback(bot, cid, q, data)
        return
    if data.startswith("colr:"):
        _, collection_id, back = data.split(":", 2)
        await cleanup.open_collection(bot, cid, collection_id, back=back)
        return
    # Настройки обучения
    if data in ("set_learning", "toggle_learning_language"):
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
        await _inline_status(lambda status: cooking.send_recipe_featured(bot, cid, status=status)); return
    if data == "m_food_next":
        await _inline_status(
            lambda status: menu.send_food_menu(bot, cid, status=status, refresh=True)); return
    # Пропустить первичный опрос раздела
    if data.startswith("fv_skip_"):
        section = data[len("fv_skip_"):]
        await _ack(q)
        await firstvisit.skip(bot, cid, section)
        await _unack(q); return
    # Теги-чекбоксы в опросе (fv_tag_{section}_{key})
    if data == "fv_leisure_text":
        await _ack(q)
        await firstvisit.leisure_text_prompt(bot, cid)
        await _unack(q); return
    if data.startswith("fv_tagdone_"):
        await _ack(q)
        await firstvisit.tags_done(bot, cid, data[len("fv_tagdone_"):])
        await _unack(q); return
    if data.startswith("fv_tag_"):
        rest = data[len("fv_tag_"):]
        section, _, key = rest.partition("_")
        await _ack(q)
        await firstvisit.toggle_tag(bot, cid, section, key, q); return
    if data in ("m_learn", "m_menu"):
        trainer.cancel(cid)

    # Первичный опрос при входе в раздел (wardrobe / learning / leisure / health / cooking)
    if data == "m_food" and firstvisit.needs_setup(cid, "cooking"):
        await _ack(q)
        await firstvisit.show_prompt(bot, cid, "cooking")
        await _unack(q); return
    if data == "m_food":
        await menu.send_food_menu(bot, cid); return
    _FV_SECTION = {"m_wardrobe": "wardrobe", "m_learn": "learning",
                   "m_leisure": "leisure", "m_balance": "health"}
    if data in _FV_SECTION and firstvisit.needs_setup(cid, _FV_SECTION[data]):
        await _ack(q)
        await firstvisit.show_prompt(bot, cid, _FV_SECTION[data])
        await _unack(q); return
    if data == "m_leisure":
        # send_home сам редактирует исходное сообщение и ставит финальную
        # клавиатуру; inline-статус не должен менять её.
        await leisure_home.send_home(bot, cid, q)
        return
    if data == "m_wardrobe":
        # Образ — полезный результат, поэтому открываем его отдельным сообщением,
        # а временное главное меню после этого удаляется автоматически.
        await wardrobe.send_home(bot, cid); return
    if data == "m_travel":
        await travel.send_home(bot, cid, q); return
    if data == "m_myday":
        await myday.send_plany(bot, cid, force=True); return
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
                await _inline_status(lambda _s: myday.send_plany(bot, cid, force=True))
            elif await learning_router.handle_action(bot, cid, q, act, _inline_status):
                pass
            elif act == "w_week":
                await _inline_status(lambda _s: weather.send_weather(bot, cid, "week"))
            elif act == "setcity":
                store.pending_input[cid] = "setcity"
                await bot.send_message(chat_id=cid, text="📍 Напиши название города — переключу на него.")
            elif act == "trav_go":
                await _inline_status(lambda _s: travel.send_go(bot, cid))
            elif act == "trav_no":
                await _inline_status(lambda _s: travel.travel_dislike(bot, cid))
            elif act == "trav_plan":
                await _inline_status(lambda _s: travel.send_plan(bot, cid))
            elif act == "trav_fav":
                await _inline_status(lambda _s: travel.travel_fav(bot, cid))
            elif act == "trav_save":
                await travel.save_plan(bot, cid, q)
            elif act.startswith("trav_countries") or act.startswith("trav_country_"):
                await travel.handle_country_callback(bot, cid, q, act)
            elif act == "trav_transport":
                await travel.send_transport_settings(bot, cid, q)
            elif act.startswith("trav_mode_"):
                await travel.toggle_transport(bot, cid, act[len("trav_mode_"):], q)
            elif act == "watch":
                await _ack(q); await leisure_movies.send_movie_home(bot, cid, q)
            elif act == "read":
                await _ack(q); await leisure_books.send_books_home(bot, cid, q)
            elif act == "readlist":
                await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
            elif act == "watchlist":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="a_watch")
            elif act == "readlist":
                await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
            elif act == "watchclean":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="a_watch")
            elif act == "readclean":
                await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
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
                await _ack(q); await leisure_music.send_music_home(bot, cid, q)
            elif act == "listen_no":
                await _inline_status(lambda _s: leisure_music.listen_dislike(bot, cid))
            elif act in ("food_breakfast", "recipe_breakfast"):
                await _inline_status(lambda status: cooking.enter_meal(bot, cid, "breakfast", status=status))
            elif act in ("food_lunch", "recipe_lunch"):
                await _inline_status(lambda status: cooking.enter_meal(bot, cid, "lunch", status=status))
            elif act in ("food_dinner", "recipe_dinner"):
                await _inline_status(lambda status: cooking.enter_meal(bot, cid, "dinner", status=status))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return

    if data.startswith("ex_"):
        await learning_router.handle_callback(bot, cid, data, _inline_status)
        return
    # Игра
    if data.startswith("gamediff_"):
        diff = data.split("_")[1]
        cfg = store.game_config.get(str(cid), {"lang": "русский"})
        cfg["difficulty"] = diff
        store.game_config[str(cid)] = cfg
        await _inline_status(lambda _s: learning_game.send_game(bot, cid))
        try:
            await q.message.delete()
        except Exception as e:
            _log.info("game difficulty prompt delete failed cid=%s: %r", cid, e)
        return
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
        await _inline_status(lambda _s: learning_game.send_game(bot, cid))
        return
    if data == "game_hint":
        await learning_game.game_hint(bot, cid, q)
        return
    if data == "game_reveal":
        await learning_game.game_reveal(bot, cid, q)
        return
    # Развлечения / путешествия
    if data == "movie_prefs":
        await _ack(q)
        await leisure_movies.send_movie_prefs(bot, cid, q)
        return
    if data == "book_reco":
        await _inline_status(lambda _s: leisure_books.send_books_reco(bot, cid))
        return
    if data == "music_reco":
        await _inline_status(lambda _s: leisure_music.send_listen(bot, cid))
        return
    if data == "movie_saved":
        await cleanup.open_collection(bot, cid, "cinema_saved", back="a_watch")
        return
    if data == "book_favorites":
        await cleanup.open_collection(bot, cid, "books_favorites", back="a_read")
        return
    if data == "book_saved":
        await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
        return
    if data == "book_prefs":
        await leisure_books.send_book_preferences(bot, cid, q)
        return
    if data == "artist_favorites":
        await cleanup.open_collection(bot, cid, "music_favorite_artists", back="a_listen")
        return
    if data == "artist_saved":
        await cleanup.open_collection(bot, cid, "music_saved", back="a_listen")
        return
    if data == "music_prefs":
        await leisure_music.send_music_preferences(bot, cid, q)
        return
    if data.startswith("mpref_"):
        await _ack(q)
        await leisure_movies.toggle_movie_pref(bot, cid, data, q)
        return
    if data == "movie_reco":
        await _inline_status(lambda _s: leisure_movies.send_recos(bot, cid, "movie"))
        return
    if data == "movie_now_playing":
        await _ack(q)
        await leisure_movies.send_movie_now_playing(bot, cid, q)
        return
    if data == "movie_genre_menu":
        await _ack(q)
        await leisure_movies.send_movie_genre_menu(bot, cid, q)
        return
    if data == "movie_mood_menu":
        await _ack(q)
        await leisure_movies.send_movie_mood_menu(bot, cid, q)
        return
    if data.startswith("movie_g_"):
        await _inline_status(lambda _s: leisure_movies.send_movie_by_genre(bot, cid, data[len("movie_g_"):]))
        return
    if data.startswith("movie_mood_"):
        await _inline_status(lambda _s: leisure_movies.send_movie_by_mood(bot, cid, data[len("movie_mood_"):]))
        return
    if data.startswith("movie_love_"):
        await _inline_status(lambda _s: leisure_movies.movie_love(bot, cid, int(data.split("_")[-1]), q))
        return
    if data.startswith("book_love_"):
        await _inline_status(lambda _s: leisure_books.book_love(bot, cid, int(data.split("_")[-1]), q))
        return
    if data == "listen_love":
        await _inline_status(lambda _s: leisure_music.listen_love(bot, cid, q))
        return
    if data.startswith("reco_"):
        await leisure_movies.add_reco(bot, cid, int(data.split("_")[1]), q)
        return
    if data.startswith("movie_no_"):
        await _inline_status(lambda _s: leisure_movies.movie_dislike(bot, cid, int(data.split("_")[-1])))
        return
    if data.startswith("book_no_"):
        await _inline_status(lambda _s: leisure_books.book_dislike(bot, cid, int(data.split("_")[-1])))
        return
    if data.startswith("listen_"):
        await leisure_music.add_listen(bot, cid, int(data.split("_")[1]), q)
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
