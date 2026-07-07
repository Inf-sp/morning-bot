import logging
from telegram import Update

_log = logging.getLogger(__name__)
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler, PollAnswerHandler)
from datetime import datetime

import config
import store
import access
import menu
import assistant
import balance
import myday
import wardrobe
import learning
import cleanup
import settings
import leisure
import travel
import weather
import verify
import secure
import onboard
import firstvisit
import tracking
import util
from util import ack_loading as _ack

TZ = config.TZ
CHAT_ID = config.CHAT_ID



_WELCOME = menu.WELCOME


async def start(update, context):
    cid = str(update.effective_chat.id)
    args = context.args or []

    # Инвайт-код передан через /start <code>
    if args:
        code = args[0].strip()
        if access.is_allowed(cid):
            await update.message.reply_text(_WELCOME, entities=menu.WELCOME_ENTITIES, reply_markup=menu.main_kb(cid))
            return
        if access.use_invite(code, cid):
            await onboard.start(context.bot, cid)
            return
        await update.message.reply_text("❌ Инвайт-код недействителен или устарел.")
        return

    if not access.is_allowed(cid):
        await update.message.reply_text("⛔ Бот приватный. Попроси владельца прислать инвайт.")
        return

    await update.message.reply_text(_WELCOME, entities=menu.WELCOME_ENTITIES, reply_markup=menu.main_kb(cid))


# ---------- Диспетчер инлайн-кнопок ----------
async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="⛔ Бот приватный. Попроси владельца прислать инвайт.")
        return
    tracking.touch(cid)

    # Онбординг новых пользователей
    if data.startswith("ob_"):
        await onboard.handle_callback(bot, cid, q, data)
        return

    # Закладки: fav_view_* и fav_del_*
    if data.startswith("fav_"):
        await settings.handle_notes_callback(bot, cid, q, data)
        return
    # Баланс (врач/мотивация/рецепты/тревоги/холодильник) vs Закладки/Любимое
    if data.startswith("ls_"):
        await settings.handle_notes_callback(bot, cid, q, data)
        return
    if data.startswith("as_"):
        if data.startswith(("as_food", "as_fridge", "as_recipe", "as_my_recipe",
                             "as_daycheck", "as_motiv", "as_doctor", "as_diary")):
            await balance.handle_callback(bot, cid, q, data)
        else:
            await settings.handle_notes_callback(bot, cid, q, data)
        return
    # Гардероб: инлайн-кабинет
    if data.startswith("w_"):
        await wardrobe.handle_callback(bot, cid, q, data)
        return
    # Настройки
    if data.startswith(("set_", "setadd_", "setdel_")):
        try:
            await settings.handle_callback(bot, cid, data, q)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    # Навигация по подменю - редактируем сообщение на месте
    if data == "m_close":
        try:
            await q.message.edit_text("Готово.")
        except Exception:
            pass
        return
    if data == "m_notes":
        await settings.send_notes(bot, cid); return
    if data == "m_food_gen":
        await _ack(q); await balance.send_recipe_featured(bot, cid); return
    # Пропустить первичный опрос раздела
    if data.startswith("fv_skip_"):
        section = data[len("fv_skip_"):]
        await _ack(q)
        await firstvisit.skip(bot, cid, section); return
    # Теги-чекбоксы в опросе (fv_tag_{section}_{key})
    if data == "fv_leisure_text":
        await _ack(q)
        await firstvisit.leisure_text_prompt(bot, cid); return
    if data.startswith("fv_tagdone_"):
        await _ack(q)
        await firstvisit.tags_done(bot, cid, data[len("fv_tagdone_"):]); return
    if data.startswith("fv_tag_"):
        rest = data[len("fv_tag_"):]
        section, _, key = rest.partition("_")
        await _ack(q)
        await firstvisit.toggle_tag(bot, cid, section, key, q); return
    # Первичный опрос при входе в раздел (wardrobe / learning / leisure / health / cooking)
    if data == "m_food" and firstvisit.needs_setup(cid, "cooking"):
        await _ack(q)
        await firstvisit.show_prompt(bot, cid, "cooking"); return
    if data == "m_food":
        await menu.send_food_menu(bot, cid); return
    _FV_SECTION = {"m_wardrobe": "wardrobe", "m_learn": "learning",
                   "m_leisure": "leisure", "m_balance": "health"}
    if data in _FV_SECTION and firstvisit.needs_setup(cid, _FV_SECTION[data]):
        await _ack(q)
        await firstvisit.show_prompt(bot, cid, _FV_SECTION[data]); return
    if data == "m_wardrobe":
        await wardrobe.send_home(bot, cid, q); return
    if data == "m_travel":
        await travel.send_home(bot, cid, q); return
    if data.startswith("m_"):
        text, entities, kb = menu.menu_screen(data)
        try:
            await q.message.edit_text(text, reply_markup=kb, entities=entities)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb, entities=entities)
        return

    # Действия
    if data.startswith("a_"):
        act = data[2:]
        try:
            if act == "plany":
                await _ack(q)
                await myday.send_plany(bot, cid)
            elif act == "train":
                await learning.send_train_lang_select(bot, cid)
            elif act == "train_nl":
                await _ack(q); await learning.train_start(bot, cid, "нидерландский")
            elif act == "train_en":
                await _ack(q); await learning.train_start(bot, cid, "английский")
            elif act == "train_words_nl":
                await _ack(q); await learning.train_start(bot, cid, "нидерландский", mode="word")
            elif act == "train_words_en":
                await _ack(q); await learning.train_start(bot, cid, "английский", mode="word")
            elif act == "train_phrases_nl":
                await _ack(q); await learning.train_start(bot, cid, "нидерландский", mode="phrase")
            elif act == "train_phrases_en":
                await _ack(q); await learning.train_start(bot, cid, "английский", mode="phrase")
            elif act == "tr_nl":
                await _ack(q); await learning.do_translate(bot, cid, "нидерландский")
            elif act == "tr_en":
                await _ack(q); await learning.do_translate(bot, cid, "английский")
            elif act == "proverb":
                await _ack(q)
                await learning.send_proverb_both(bot, cid)
            elif act == "proverb_nl":
                await _ack(q)
                await learning.send_proverb(bot, cid, "нидерландский")
            elif act == "proverb_en":
                await _ack(q)
                await learning.send_proverb(bot, cid, "английский")
            elif act == "dict":
                await learning.send_dict(bot, cid)
            elif act == "dictlang_nl":
                await learning.send_dict_lang(bot, cid, "nl")
            elif act == "dictlang_en":
                await learning.send_dict_lang(bot, cid, "en")
            elif act == "dictlang_nl_from_lang":
                await learning.send_dict_lang(bot, cid, "nl", back="m_nl")
            elif act == "dictlang_en_from_lang":
                await learning.send_dict_lang(bot, cid, "en", back="m_en")
            elif act == "dictlang_nl_from_notes":
                await learning.send_dict_lang(bot, cid, "nl", back="a_dict")
            elif act == "dictlang_en_from_notes":
                await learning.send_dict_lang(bot, cid, "en", back="a_dict")
            elif act == "dictlang_nl_from_learn":
                await learning.send_dict_lang(bot, cid, "nl", back="set_dict_g")
            elif act == "dictlang_en_from_learn":
                await learning.send_dict_lang(bot, cid, "en", back="set_dict_g")
            elif act == "dictlang_nl_from_settings":
                await learning.send_dict_lang(bot, cid, "nl", back="m_dict_settings")
            elif act == "dictlang_en_from_settings":
                await learning.send_dict_lang(bot, cid, "en", back="m_dict_settings")
            elif act.startswith("dictcheck_"):
                lang = act.split("_")[1]
                await cleanup.open_cleanup(bot, cid, f"d_broken_{lang}")
            elif act.startswith("dictadd_smart_"):
                lang = act.split("_")[2]
                store.pending_input[cid] = f"dictadd_smart_{lang}"
                await bot.send_message(chat_id=cid, text=(
                    "✍🏻 Пришли слово или фразу для изучения — можно сразу несколько.\n"
                    "Я сам пойму что это: слово или фраза."))
            elif act.startswith("dictadd_"):
                lang = act.split("_")[1]
                store.pending_input[cid] = f"dictadd_{lang}"
                await bot.send_message(chat_id=cid, text=(
                    "✍🏻 Пришли слова или фразы - можно сразу много, в столбик или через запятую.\n"
                    "Я разберу каждое отдельно, сам пойму слово это или фраза, язык и перевод."))
            elif act.startswith("dictedit_"):
                _, lang, dkind = act.split("_")
                await learning.send_dict_edit(bot, cid, lang, dkind)
            elif act == "game":
                await learning.game_start(bot, cid)
            elif act == "levels":
                await learning.send_levels(bot, cid, back="m_learn")
            elif act == "w_today":
                await weather.send_weather(bot, cid, "today")
            elif act == "w_tomorrow":
                await weather.send_weather(bot, cid, "tomorrow")
            elif act == "w_week":
                await weather.send_weather(bot, cid, "week")
            elif act == "setcity":
                store.pending_input[cid] = "setcity"
                await bot.send_message(chat_id=cid, text="🌍 Напиши название города - переключу на него!")
            elif act == "trav_go":
                await _ack(q); await travel.send_go(bot, cid)
            elif act == "trav_no":
                await _ack(q); await travel.travel_dislike(bot, cid)
            elif act == "trav_plan":
                await _ack(q); await travel.send_plan(bot, cid)
            elif act == "trav_fav":
                await _ack(q); await travel.travel_fav(bot, cid)
            elif act == "trav_save":
                await _ack(q); await travel.save_plan(bot, cid)
            elif act == "watch":
                await _ack(q); await leisure.send_movie_home(bot, cid, q)
            elif act == "now_playing":
                await _ack(q); await leisure.send_now_playing(bot, cid, q)
            elif act == "read":
                await _ack(q); await leisure.send_recos(bot, cid, "book")
            elif act == "watchlist":
                await leisure.send_watchlist(bot, cid)
            elif act == "readlist":
                await leisure.send_readlist(bot, cid)
            elif act == "watchclean":
                await cleanup.open_cleanup(bot, cid, "wl")
            elif act == "readclean":
                await cleanup.open_cleanup(bot, cid, "rl")
            elif act == "concerts_find":
                await _ack(q); await leisure.find_concerts(bot, cid, "home")
            elif act == "concerts_pick":
                await leisure.concert_pick_country(bot, cid)
            elif act in ("concerts_nl", "concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
                         "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
                         "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"):
                await _ack(q); await leisure.find_concerts(bot, cid, act.split("_")[1])
            elif act == "listen":
                await _ack(q); await leisure.send_listen(bot, cid)
            elif act == "listen_no":
                await _ack(q); await leisure.listen_dislike(bot, cid)
            elif act in ("food_breakfast", "recipe_breakfast"):
                await _ack(q); await balance.enter_meal(bot, cid, "breakfast")
            elif act in ("food_lunch", "recipe_lunch"):
                await _ack(q); await balance.enter_meal(bot, cid, "lunch")
            elif act in ("food_dinner", "recipe_dinner"):
                await _ack(q); await balance.enter_meal(bot, cid, "dinner")
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return

    # Уровни языка
    if data.startswith("lvl_"):
        parts = data.split("_")
        code, level = parts[1], parts[2]
        language = "нидерландский" if code == "nl" else "английский"
        store.set_level(cid, language, level)
        await learning.send_levels(bot, cid, q)
        return
    # Тренажёр слов
    if data.startswith("train_"):
        sub = data[len("train_"):]
        if sub.startswith("ans_"):
            try:
                ans_idx = int(sub[4:])
            except ValueError:
                return
            await learning.train_quiz_answer(bot, cid, ans_idx)
        elif sub == "next":
            await _ack(q); await learning.train_next(bot, cid)
        return
    # Тренажёр фраз: переход от учебной карточки к тесту
    if data in ("phrase_intro_test", "phrase_intro_go"):
        await learning.phrase_intro_continue(bot, cid)
        return
    if data == "phrase_intro_mastered":
        await _ack(q); await learning.phrase_intro_mastered(bot, cid)
        return
    if data == "phrase_new_example":
        await _ack(q); await learning.phrase_new_example(bot, cid)
        return
    if data == "phrase_explain":
        await learning.phrase_explain(bot, cid)
        return
    # «Ещё»
    if data.startswith("again_"):
        what = data[len("again_"):]
        if what == "tr_nl":
            await _ack(q); await learning.do_translate(bot, cid, "нидерландский")
        elif what == "tr_en":
            await _ack(q); await learning.do_translate(bot, cid, "английский")
        return
    # Игра
    if data.startswith("gamelang_"):
        lang = {"ru": "русский", "en": "английский", "nl": "нидерландский"}[data.split("_")[1]]
        store.game_config[cid] = {"lang": lang, "difficulty": "med"}
        await learning.ask_difficulty(bot, cid, lang)
        return
    if data.startswith("gamediff_"):
        diff = data.split("_")[1]
        cfg = store.game_config.get(cid, {"lang": "русский"})
        cfg["difficulty"] = diff
        store.game_config[cid] = cfg
        await _ack(q)
        await learning.send_game(bot, cid)
        return
    if data == "game_change_diff":
        cfg = store.game_config.get(cid, {"lang": "русский"})
        await learning.ask_difficulty(bot, cid, cfg["lang"])
        return
    if data == "noop":
        return
    if data.startswith(("clt:", "clp:", "cla:", "clx:", "cld:", "cldc:", "clcancel:")):
        # PR3a view-режим (стабильный id + revision) — двоеточие как разделитель
        # отличает его от старого позиционного формата ниже (символ подчёркивания).
        # clx:/cldc:/clcancel: — «Удалить все N» и confirm-экран (PR4, P2-2).
        await cleanup.handle_view_callback(bot, cid, data, q)
        return
    if data.startswith(("clt_", "clp_", "cla_", "cld_")):
        await cleanup.handle_cleanup(bot, cid, data, q)
        return
    if data.startswith("worddel_"):
        await learning.del_word(bot, cid, int(data.split("_")[1]))
        return
    if data == "game_again":
        await _ack(q)
        await learning.send_game(bot, cid)
        return
    if data == "game_hint":
        await learning.game_hint(bot, cid, q)
        return
    if data == "game_reveal":
        await learning.game_reveal(bot, cid, q)
        return
    if data == "game_change":
        await learning.game_start(bot, cid)
        return
    # Развлечения / путешествия
    if data == "movie_prefs":
        await _ack(q)
        await leisure.send_movie_prefs(bot, cid, q)
        return
    if data.startswith("mpref_"):
        await _ack(q)
        await leisure.toggle_movie_pref(bot, cid, data, q)
        return
    if data == "movie_reco":
        await util.ack_loading(q)
        try:
            await leisure.send_recos(bot, cid, "movie")
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data == "movie_genre_menu":
        await _ack(q)
        await leisure.send_movie_genre_menu(bot, cid, q)
        return
    if data == "movie_mood_menu":
        await _ack(q)
        await leisure.send_movie_mood_menu(bot, cid, q)
        return
    if data.startswith("movie_g_"):
        await util.ack_loading(q)
        try:
            await leisure.send_movie_by_genre(bot, cid, data[len("movie_g_"):])
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("movie_mood_"):
        await util.ack_loading(q)
        try:
            await leisure.send_movie_by_mood(bot, cid, data[len("movie_mood_"):])
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("movie_love_"):
        await util.ack_loading(q)
        try:
            await leisure.movie_love(bot, cid, int(data.split("_")[-1]))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("movie_seen_"):
        await util.ack_loading(q)
        try:
            await leisure.movie_seen(bot, cid, int(data.split("_")[-1]))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("book_love_"):
        await _ack(q)
        await leisure.book_love(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("book_seen_"):
        await _ack(q)
        await leisure.book_seen(bot, cid, int(data.split("_")[-1]))
        return
    if data == "listen_love":
        await _ack(q)
        await leisure.listen_love(bot, cid)
        return
    if data == "listen_seen":
        await _ack(q)
        await leisure.listen_seen(bot, cid)
        return
    if data.startswith("reco_"):
        await util.ack_loading(q)
        try:
            await leisure.add_reco(bot, cid, int(data.split("_")[1]))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("movie_no_"):
        await util.ack_loading(q)
        try:
            await leisure.movie_dislike(bot, cid, int(data.split("_")[-1]))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        finally:
            await util.clear_loading(q)
        return
    if data.startswith("book_no_"):
        await _ack(q)
        await leisure.book_dislike(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("listen_"):
        await _ack(q)
        await leisure.add_listen(bot, cid, int(data.split("_")[1]))
        return
    # Проверка дня (тревоги)
    if data == "worry_clearall":
        await balance.worry_clear_all(bot, cid)
        return
    # «Продолжить / ещё раз»
    if data == "chat_retry":
        await _ack(q)
        await balance.retry(bot, cid)
        return
    # «Короче / Глубже» - переписать последний ответ
    if data in ("ans_short", "ans_deep"):
        await _ack(q)
        await balance.reword(bot, cid, "short" if data == "ans_short" else "deep")
        return


# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    text = secure.clamp(update.message.text)        # лимит длины + чистка невидимых/управляющих
    bot = context.bot

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="⛔ Бот приватный. Попроси владельца прислать инвайт.")
        return
    tracking.touch(cid)

    flags = secure.injection_flags(text)
    if flags:
        _log.warning("[secure] injection flags: %s", flags)

    # Нажата любая кнопка нижнего меню -> сбрасываем незавершённый ввод (чтобы чат не «съел» сообщение настроек)
    if text in ("☀️ Мой день", "/setup", "/admin") or text in menu.LABEL_TO_KEY or text == "🗂️ Моя база":
        store.pending_input.pop(cid, None)

    if text == "☀️ Мой день":
        try:
            await myday.send_plany(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text == "/setup":
        try:
            await settings.send_notes(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text == "/admin":
        try:
            await settings.send_admin(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text in ("🎚️ Настройки", "🗂️ Моя база"):
        try:
            await settings.send_notes(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text == "🥣 Готовка":
        try:
            if firstvisit.needs_setup(cid, "cooking"):
                await firstvisit.show_prompt(bot, cid, "cooking")
                return
            await menu.send_food_menu(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    # Нажатие нижнего reply-меню -> открыть инлайн-подменю
    if text in menu.LABEL_TO_KEY:
        key = menu.LABEL_TO_KEY[text]
        # Первый вход в раздел с пустым профилем — опрос
        _FV = {"m_wardrobe": "wardrobe", "m_learn": "learning",
               "m_leisure": "leisure", "m_balance": "health"}
        if key in _FV and firstvisit.needs_setup(cid, _FV[key]):
            await firstvisit.show_prompt(bot, cid, _FV[key])
            return
        if key == "m_wardrobe":
            await wardrobe.send_home(bot, cid)
            return
        if key == "m_travel":
            await travel.send_home(bot, cid)
            return
        t, entities, kb = menu.menu_screen(key)
        await bot.send_message(chat_id=cid, text=t, reply_markup=kb, entities=entities)
        return

    # Режим добавления одежды (файлом)
    if store.add_wardrobe_mode.get(cid):
        await wardrobe.ingest(bot, cid, text)
        return

    # Игра и перевод проверяем ПЕРЕД pending - иначе ответ уходит не туда (в дневник)
    if cid in store.game_state:
        if await learning.game_answer(bot, cid, text):
            return
    if cid in store.challenge_state:
        if await learning.translate_answer(bot, cid, text):
            return

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.pop(cid)
        if kind == "worry":
            _log.info("worry: routed via pending_input for cid=%s", cid)
            await balance.save_worries(bot, cid, text); return
        if kind in ("role_doctor", "role_state"):
            await balance.handle_role(bot, cid, kind.split("_")[1], text); return
        if kind == "wardrobe_add":
            await wardrobe.add_item(bot, cid, text); return
        if kind == "wardrobe_add_set":
            await wardrobe.add_item_settings(bot, cid, text)
            await settings.send_wardrobe(bot, cid); return
        if kind == "wardrobe_check":
            await wardrobe.check_purchase(bot, cid, text); return
        if kind == "onboard_name":
            await onboard.handle_name(bot, cid, text); return
        if kind == "onboard_city":
            await onboard.handle_city(bot, cid, text); return
        if kind == "setcity":
            await weather.set_city_text(bot, cid, text); return
        if kind.startswith("dictadd_smart_"):
            await learning.add_smart_batch(bot, cid, text, kind.split("_")[2]); return
        if kind.startswith("dictadd_"):
            await learning.add_words_batch(bot, cid, text, kind.split("_")[1]); return
        if kind == "wardrobe_profile_input":
            settings.set_(cid, "wardrobe_profile", text.strip())
            await bot.send_message(chat_id=cid, text="🎚️ <b>Параметры сохранены</b>", parse_mode="HTML")
            await settings.send_wardrobe(bot, cid); return
        if kind == "bodyinput":
            settings.set_(cid, "body", text)
            await bot.send_message(chat_id=cid, text="Готово, параметры сохранены.")
            await settings.send_body(bot, cid); return
        if kind == "styleinput":
            settings.set_(cid, "style", text.strip())
            await bot.send_message(chat_id=cid, text="Стиль сохранён.")
            await settings.send_body(bot, cid); return
        if kind.startswith("fridge_add"):
            try:
                ci = int(kind.split("_")[-1])
            except (ValueError, IndexError):
                ci = -1
            await balance.fridge_add_done(bot, cid, text, ci); return
        if kind == "setadd_country":
            await settings.list_add_done(bot, cid, "country", text); return
        if kind == "setadd_artist":
            await settings.list_add_done(bot, cid, "artist", text); return
        if kind == "setadd_book":
            await settings.list_add_done(bot, cid, "book", text); return
        if kind == "setadd_lagom":
            import memory
            from util import esc
            added = memory.add_lagom_batch(cid, text)
            n = len(added)
            if n == 0:
                await bot.send_message(chat_id=cid, text="Эти принципы уже есть в Лагом.")
            else:
                label = "принцип" if n == 1 else ("принципа" if 2 <= n <= 4 else "принципов")
                preview = "\n".join(f"• {esc(it)}" for it in added[:10])
                suffix = f"\n<i>...и ещё {n - 10}</i>" if n > 10 else ""
                await bot.send_message(chat_id=cid,
                    text=f"✅ Добавлено {n} {label}:\n\n{preview}{suffix}",
                    parse_mode="HTML")
            await settings.send_lagom(bot, cid); return
        if kind.startswith("collect_"):
            await leisure.collect_done(bot, cid, kind[len("collect_"):], text); return
        if kind.startswith("firstvisit_"):
            await firstvisit.handle_response(bot, cid, kind[len("firstvisit_"):], text); return
        if kind.startswith("loveadd_"):
            await settings.love_add_done(bot, cid, kind[len("loveadd_"):], text); return
        if kind.startswith("loveaddls_"):
            await settings.love_add_done(bot, cid, kind[len("loveaddls_"):], text, origin="leisure"); return

    # Fallback: pending_input мог быть сброшен при рестарте — проверяем профиль
    ob_step = onboard.get_text_step(cid)
    if ob_step == "name":
        await onboard.handle_name(bot, cid, text); return
    if ob_step == "city":
        await onboard.handle_city(bot, cid, text); return

    # Fallback: недавняя "Дневная разгрузка" — pending_input мог потеряться,
    # но персистентная метка (survives рестарт) ещё в окне — не теряем текст.
    worry_ts = settings.get(cid, "_worry_prompt_ts", 0)
    if worry_ts and (datetime.now(config.TZ).timestamp() - worry_ts) < 1800:
        settings.set_(cid, "_worry_prompt_ts", 0)
        _log.info("worry: routed via fallback timestamp for cid=%s", cid)
        await balance.save_worries(bot, cid, text); return

    # Быстрая команда из чата: «добавь в словарь слово de Aandacht - внимание»
    if await learning.try_add_dict_from_chat(bot, cid, text):
        return
    # Быстрая команда из чата: «добавь в продукты крахмал»
    if await balance.try_add_fridge_from_chat(bot, cid, text):
        return

    # Свободный чат
    await assistant.chat_reply(bot, cid, text)


async def document_handler(update, context):
    cid = str(update.effective_chat.id)
    if not store.add_wardrobe_mode.get(cid):
        return
    doc = update.message.document
    if (doc.file_size or 0) > secure.MAX_DOC_BYTES:
        await update.message.reply_text("Файл слишком большой. Пришли список вещей текстом или файлом до 100 КБ.")
        return
    try:
        f = await context.bot.get_file(doc.file_id)
        body = await f.download_as_bytearray()
        txt = secure.clamp(body.decode("utf-8", errors="ignore"))
    except Exception as e:
        await verify.safe_error(context.bot, cid, e)
        return
    await wardrobe.ingest(context.bot, cid, txt)


async def poll_answer_handler(update, context):
    await learning.handle_train_poll_answer(context.bot, update.poll_answer)


# ---------- Команды-обёртки ----------
async def notes_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_notes(context.bot, update.effective_chat.id)

async def setup_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_notes(context.bot, update.effective_chat.id)

async def admin_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_admin(context.bot, update.effective_chat.id)


# ---------- Расписание ----------
async def job_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "morning_brief"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "morning_brief")
        except Exception:
            logging.exception("job_morning_brief failed for cid=%s", cid)

async def job_weather_warn(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weather_warn"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "weather_warn")
        except Exception:
            logging.exception("job_weather_warn failed for cid=%s", cid)

async def job_lagom(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "lagom_daily"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "lagom_daily")
        except Exception:
            logging.exception("job_lagom failed for cid=%s", cid)

async def job_daily_words(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        try:
            if settings.notif_on(cid, "daily_words_nl"):
                await settings.send_scheduled_notification(context.bot, cid, "daily_words_nl")
            if settings.notif_on(cid, "daily_words_en"):
                await settings.send_scheduled_notification(context.bot, cid, "daily_words_en")
        except Exception:
            logging.exception("job_daily_words failed for cid=%s", cid)

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "checkin_day"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "checkin_day")
        except Exception:
            logging.exception("job_checkin_day failed for cid=%s", cid)

async def job_recipe(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "recipe_daily"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "recipe_daily")
        except Exception:
            logging.exception("job_recipe failed for cid=%s", cid)

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "checkin_eve"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "checkin_eve")
        except Exception:
            logging.exception("job_checkin_evening failed for cid=%s", cid)

async def job_refresh_concerts_cache(context: ContextTypes.DEFAULT_TYPE):
    """Прогревает недельный кэш концертов перед рассылкой «Афиша недели» (10:00 вс),
    чтобы сама рассылка и последующие интерактивные «Концерты» читали кэш, а не ждали Ticketmaster."""
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekly_events"):
            continue
        try:
            await leisure.refresh_concerts_cache(cid)
        except Exception:
            logging.exception("job_refresh_concerts_cache failed for cid=%s", cid)

async def job_weekly_events(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekly_events"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "weekly_events")
        except Exception:
            logging.exception("job_weekly_events failed for cid=%s", cid)

async def job_favorite_artists(context: ContextTypes.DEFAULT_TYPE):
    """⭐ Новые концерты любимых артистов — шлёт только если появилось что-то новое."""
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "favorite_artists"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "favorite_artists")
        except Exception:
            logging.exception("job_favorite_artists failed for cid=%s", cid)

async def job_live_lang(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "live_lang"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "live_lang")
        except Exception:
            logging.exception("job_live_lang failed for cid=%s", cid)

async def job_weekly_forecast(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekly_forecast"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "weekly_forecast")
        except Exception:
            logging.exception("job_weekly_forecast failed for cid=%s", cid)


async def job_evening_weather(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "evening_weather"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "evening_weather")
        except Exception:
            logging.exception("job_evening_weather failed for cid=%s", cid)


async def post_init(app):
    try:
        if learning.migrate_dict_caps():
            logging.info("Dict caps migration: applied")
    except Exception:
        logging.exception("Dict caps migration failed")
    try:
        if leisure.dedupe_lists():
            logging.info("Dedupe lists: applied")
    except Exception:
        logging.exception("Dedupe lists failed")
    try:
        if leisure.seed_movies_from_content():
            logging.info("Movies seed: applied")
    except Exception:
        logging.exception("Movies seed failed")
    try:
        if memory.seed_owner_lagom():
            logging.info("Owner lagom seed: applied")
    except Exception:
        logging.exception("Owner lagom seed failed")
    try:
        unhandled = verify.audit_callbacks()
        if unhandled:
            logging.warning("Callback audit: unhandled -> %s", ", ".join(unhandled))
        else:
            logging.info("Callback audit: OK")
    except Exception:
        logging.exception("Callback audit failed")
    try:
        leaks = secure.scan_secrets()
        if leaks:
            logging.warning("Secrets scan: findings -> %s", "; ".join(leaks))
        else:
            logging.info("Secrets scan: OK")
    except Exception:
        logging.exception("Secrets scan failed")
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "главное меню"),
        BotCommand("setup", "настройки"),
        BotCommand("admin", "администратор"),
    ])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("setup", setup_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(answer_callback))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.LOCATION, weather.location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    def _t(hm):
        return datetime.strptime(hm, "%H:%M").replace(tzinfo=TZ).timetz()
    jq.run_daily(job_morning_brief,   time=_t("08:30"), days=tuple(range(7)))   # Мой день без кнопок
    jq.run_daily(job_weather_warn,    time=_t("08:45"), days=tuple(range(7)))
    jq.run_daily(job_lagom,           time=_t("09:00"), days=tuple(range(7)))
    jq.run_daily(job_refresh_concerts_cache, time=_t("09:50"), days=(6,))      # вс, прогрев кэша концертов
    jq.run_daily(job_weekly_events,   time=_t("10:00"), days=(6,))             # вс
    jq.run_daily(job_favorite_artists, time=_t("10:05"), days=(6,))            # вс, только если есть новое
    jq.run_daily(job_daily_words,     time=_t("11:00"), days=tuple(range(7)))
    jq.run_daily(job_live_lang,       time=_t("16:30"), days=tuple(range(7)))
    jq.run_daily(job_recipe,          time=_t("12:30"), days=tuple(range(7)))
    jq.run_daily(job_checkin_day,     time=_t("14:00"), days=tuple(range(7)))
    jq.run_daily(job_weekly_forecast, time=_t("19:00"), days=(6,))             # вс
    jq.run_daily(job_evening_weather, time=_t("21:30"), days=(0, 1, 2, 3, 4, 5))
    jq.run_daily(job_checkin_evening, time=_t("22:00"), days=tuple(range(7)))

    logging.info("Bot started via polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
