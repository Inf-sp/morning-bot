import logging
from telegram import Update

_log = logging.getLogger(__name__)
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler)
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
import weather
import verify
import secure
import onboard
import firstvisit
from util import ack_loading as _ack

TZ = config.TZ
CHAT_ID = config.CHAT_ID

class _NokbBot:
    """Обёртка для push-джобов: убирает reply_markup из всех send_* (кнопки не нужны в уведомлениях)."""
    def __init__(self, bot): self._bot = bot
    def __getattr__(self, name):
        orig = getattr(self._bot, name)
        if name in ("send_message", "send_photo", "send_document", "send_animation", "send_chat_action"):
            async def _w(*a, **kw): kw.pop("reply_markup", None); return await orig(*a, **kw)
            return _w
        return orig



_WELCOME = menu.WELCOME


async def start(update, context):
    cid = str(update.effective_chat.id)
    args = context.args or []

    # Инвайт-код передан через /start <code>
    if args:
        code = args[0].strip()
        if access.is_allowed(cid):
            await update.message.reply_text(_WELCOME, parse_mode="HTML", reply_markup=menu.MAIN_KB)
            return
        if access.use_invite(code, cid):
            await onboard.start(context.bot, cid)
            return
        await update.message.reply_text("❌ Инвайт-код недействителен или устарел.")
        return

    if not access.is_allowed(cid):
        await update.message.reply_text("⛔ Бот приватный. Попроси владельца прислать инвайт.")
        return

    await update.message.reply_text(_WELCOME, parse_mode="HTML", reply_markup=menu.MAIN_KB)


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

    # Онбординг новых пользователей
    if data.startswith("ob_"):
        await onboard.handle_callback(bot, cid, q, data)
        return

    # Закладки: fav_view_* и fav_del_*
    if data.startswith("fav_"):
        await settings.handle_notes_callback(bot, cid, q, data)
        return
    # Микро-грамматика и тренажёр de/het
    if data.startswith("gm_") or data.startswith("dh_"):
        await learning.handle_callback(bot, cid, q, data)
        return
    # Баланс (врач/мотивация/рецепты/тревоги/холодильник) vs Закладки/Любимое
    if data.startswith("as_"):
        if data.startswith(("as_food", "as_fridge", "as_recipe", "as_my_recipe",
                             "as_daycheck", "as_motiv", "as_doctor")):
            await balance.handle_callback(bot, cid, q, data)
        else:
            await settings.handle_notes_callback(bot, cid, q, data)
        return
    # Гардероб: инлайн-кабинет
    if data.startswith("w_"):
        await wardrobe.handle_callback(bot, cid, q, data)
        return
    # Мой день: инлайн-кабинет
    if data.startswith("md_"):
        await myday.handle_callback(bot, cid, q, data)
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
            await q.message.edit_text("Готово 👇 Меню снизу.")
        except Exception:
            pass
        return
    if data == "m_food":
        await menu.send_food_menu(bot, cid); return
    if data == "m_notes":
        await settings.send_notes(bot, cid); return
    if data == "m_food_gen":
        await _ack(q); await balance.send_recipe_featured(bot, cid); return
    # Пропустить первичный опрос раздела
    if data.startswith("fv_skip_"):
        section = data[len("fv_skip_"):]
        await _ack(q)
        await firstvisit.skip(bot, cid, section); return
    # Первичный опрос при входе в раздел (wardrobe / learn / leisure / balance)
    _FV_SECTION = {"m_wardrobe": "wardrobe", "m_learn": "learn",
                   "m_leisure": "leisure", "m_balance": "balance"}
    if data in _FV_SECTION and firstvisit.needs_setup(cid, _FV_SECTION[data]):
        await _ack(q)
        await firstvisit.show_prompt(bot, cid, _FV_SECTION[data]); return
    if data.startswith("m_"):
        text, kb = menu.menu_screen(data)
        try:
            await q.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb, parse_mode="HTML")
        return

    # Действия
    if data.startswith("a_"):
        act = data[2:]
        try:
            if act == "plany":
                await myday.send_plany(bot, cid)
            elif act == "gram_nl":
                await _ack(q); await learning.send_grammar(bot, cid, "нидерландский", "🇳🇱")
            elif act == "gram_en":
                await _ack(q); await learning.send_grammar(bot, cid, "английский", "🇬🇧")
            elif act == "train":
                await learning.send_train_lang_select(bot, cid)
            elif act == "train_nl":
                await _ack(q); await learning.train_start(bot, cid, "нидерландский")
            elif act == "train_en":
                await _ack(q); await learning.train_start(bot, cid, "английский")
            elif act == "tr_nl":
                await _ack(q); await learning.do_translate(bot, cid, "нидерландский")
            elif act == "tr_en":
                await _ack(q); await learning.do_translate(bot, cid, "английский")
            elif act == "proverb":
                await learning.send_proverb_both(bot, cid)
            elif act == "proverb_nl":
                await learning.send_proverb(bot, cid, "нидерландский")
            elif act == "proverb_en":
                await learning.send_proverb(bot, cid, "английский")
            elif act == "topics_nl":
                await learning.send_topics(bot, cid, "нидерландский")
            elif act == "topics_en":
                await learning.send_topics(bot, cid, "английский")
            elif act == "topicadd_nl":
                store.pending_input[cid] = "topicadd_nl"
                await bot.send_message(chat_id=cid, text="🇳🇱 Напиши тему для изучения - можно сразу несколько, каждую с новой строки. Добавлю и разберу.")
            elif act == "topicadd_en":
                store.pending_input[cid] = "topicadd_en"
                await bot.send_message(chat_id=cid, text="🇬🇧 Напиши тему для изучения - можно сразу несколько, каждую с новой строки. Добавлю и разберу.")
            elif act.startswith("topicclean_"):
                await cleanup.open_cleanup(bot, cid, f"t_{act.split('_')[1]}")
            elif act == "dict":
                await learning.send_dict(bot, cid)
            elif act == "dictlang_nl":
                await learning.send_dict_lang(bot, cid, "nl")
            elif act == "dictlang_en":
                await learning.send_dict_lang(bot, cid, "en")
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
                await learning.send_levels(bot, cid)
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
                await _ack(q); await leisure.send_go(bot, cid)
            elif act == "trav_no":
                await leisure.travel_dislike(bot, cid)
            elif act == "trav_plan":
                await _ack(q); await leisure.send_plan(bot, cid)
            elif act == "trav_fav":
                await leisure.travel_fav(bot, cid)
            elif act == "trav_save":
                await leisure.save_plan(bot, cid)
            elif act == "watch":
                await _ack(q); await leisure.send_recos(bot, cid, "movie")
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
            elif act == "fav":
                await leisure.send_fav(bot, cid)
            elif act == "concerts_find":
                await leisure.find_concerts(bot, cid, "home")
            elif act == "concerts_pick":
                await leisure.concert_pick_country(bot, cid)
            elif act in ("concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
                         "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
                         "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"):
                await leisure.find_concerts(bot, cid, act.split("_")[1])
            elif act == "listen":
                await _ack(q); await leisure.send_listen(bot, cid)
            elif act == "listen_no":
                await leisure.listen_dislike(bot, cid)
            elif act == "food_breakfast":
                await _ack(q); await balance.send_recipe(bot, cid, "завтрак")
            elif act == "food_lunch":
                await _ack(q); await balance.send_recipe(bot, cid, "обед")
            elif act == "food_dinner":
                await _ack(q); await balance.send_recipe(bot, cid, "ужин")
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
    # Грамматика
    if data in ("gram_a", "gram_b"):
        await learning.grammar_answer(bot, cid, "a" if data == "gram_a" else "b")
        return
    # Тренажёр слов
    if data.startswith("train_"):
        sub = data[len("train_"):]
        if sub in ("a", "b"):
            await learning.train_answer(bot, cid, sub)
        elif sub in ("true", "false"):
            await learning.train_answer(bot, cid, sub == "true")
        elif sub == "reveal":
            await learning.train_reveal(bot, cid)
        elif sub == "next":
            await _ack(q); await learning.train_next(bot, cid)
        return
    # «Ещё»
    if data.startswith("again_"):
        what = data[len("again_"):]
        if what == "tr_nl":
            await _ack(q); await learning.do_translate(bot, cid, "нидерландский")
        elif what == "tr_en":
            await _ack(q); await learning.do_translate(bot, cid, "английский")
        elif what == "gram_nl":
            await _ack(q); await learning.again_grammar(bot, cid, "нидерландский")
        elif what == "gram_en":
            await _ack(q); await learning.again_grammar(bot, cid, "английский")
        return
    if data.startswith("next_gram_"):
        lang = "нидерландский" if data.endswith("_nl") else "английский"
        await _ack(q); await learning.next_grammar(bot, cid, lang)
        return
    if data.startswith("rand_gram_"):
        lang = "нидерландский" if data.endswith("_nl") else "английский"
        await _ack(q); await learning.random_grammar(bot, cid, lang)
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
        await learning.send_game(bot, cid)
        return
    if data == "game_change_diff":
        cfg = store.game_config.get(cid, {"lang": "русский"})
        await learning.ask_difficulty(bot, cid, cfg["lang"])
        return
    if data == "noop":
        return
    if data.startswith(("clt_", "clp_", "cla_", "cld_")):
        await cleanup.handle_cleanup(bot, cid, data, q)
        return
    if data.startswith("worddel_"):
        await learning.del_word(bot, cid, int(data.split("_")[1]))
        return
    if data.startswith("topicdel_"):
        parts = data.split("_")  # topicdel_nl_3
        await learning.del_topic(bot, cid, parts[1], int(parts[2]))
        return
    if data == "game_again":
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
    if data.startswith("movie_love_"):
        await leisure.movie_love(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("book_love_"):
        await leisure.book_love(bot, cid, int(data.split("_")[-1]))
        return
    if data == "listen_love":
        await leisure.listen_love(bot, cid)
        return
    if data.startswith("reco_"):
        await leisure.add_reco(bot, cid, int(data.split("_")[1]))
        return
    if data.startswith("movie_no_"):
        await leisure.movie_dislike(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("book_no_"):
        await leisure.book_dislike(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("listen_"):
        await leisure.add_listen(bot, cid, int(data.split("_")[1]))
        return
    # Проверка дня (тревоги)
    if data == "worry_clearall":
        await balance.worry_clear_all(bot, cid)
        return
    # «Продолжить / ещё раз»
    if data == "chat_retry":
        await balance.retry(bot, cid)
        return
    # «Короче / Глубже» - переписать последний ответ
    if data in ("ans_short", "ans_deep"):
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

    flags = secure.injection_flags(text)
    if flags:
        _log.warning("[secure] injection flags: %s", flags)

    # Нажата любая кнопка нижнего меню -> сбрасываем незавершённый ввод (чтобы чат не «съел» сообщение настроек)
    if text == "☀️ Мой день" or text in menu.LABEL_TO_KEY:
        store.pending_input.pop(cid, None)
        store.micro_state.pop(cid, None)

    if text == "☀️ Мой день":
        try:
            await myday.send_plany(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text == "🗂️ Моя база":
        try:
            await settings.send_notes(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    if text == "🍳 Готовка":
        try:
            await menu.send_food_menu(bot, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return
    # Нажатие нижнего reply-меню -> открыть инлайн-подменю
    if text in menu.LABEL_TO_KEY:
        key = menu.LABEL_TO_KEY[text]
        # Первый вход в раздел с пустым профилем — опрос
        _FV = {"m_wardrobe": "wardrobe", "m_learn": "learn",
               "m_leisure": "leisure", "m_balance": "balance"}
        if key in _FV and firstvisit.needs_setup(cid, _FV[key]):
            await firstvisit.show_prompt(bot, cid, _FV[key])
            return
        t, kb = menu.menu_screen(key)
        await bot.send_message(chat_id=cid, text=t, reply_markup=kb, parse_mode="HTML")
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

    # Микро-грамматика: практическое предложение
    if store.micro_state.get(cid, {}).get("awaiting_sentence"):
        if await learning.check_sentence(bot, cid, text):
            return

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.pop(cid)
        if kind == "worry":
            await balance.save_worries(bot, cid, text); return
        if kind == "favorite":
            await leisure.add_fav(bot, cid, text); return
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
        if kind == "set_mem_add":
            await settings.memory_add_done(bot, cid, text); return
        if kind == "setcity":
            await weather.set_city_text(bot, cid, text); return
        if kind.startswith("dictadd_"):
            await learning.add_words_batch(bot, cid, text, kind.split("_")[1]); return
        if kind == "topicadd_nl":
            await learning.add_topic(bot, cid, text, "нидерландский"); return
        if kind == "topicadd_en":
            await learning.add_topic(bot, cid, text, "английский"); return
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
        if kind == "train_translate":
            await learning.train_translate_answer(bot, cid, text); return
        if kind == "train_card":
            await learning.train_card_answer(bot, cid, text); return
        if kind.startswith("collect_"):
            await leisure.collect_done(bot, cid, kind[len("collect_"):], text); return
        if kind.startswith("firstvisit_"):
            await firstvisit.handle_response(bot, cid, kind[len("firstvisit_"):], text); return
        if kind.startswith("loveadd_"):
            await settings.love_add_done(bot, cid, kind[len("loveadd_"):], text); return
        if kind.startswith("gm_addtopic_"):
            code = kind[len("gm_addtopic_"):]
            await learning.add_topic_done(bot, cid, code, text); return

    # Fallback: pending_input мог быть сброшен при рестарте — проверяем профиль
    ob_step = onboard.get_text_step(cid)
    if ob_step == "name":
        await onboard.handle_name(bot, cid, text); return
    if ob_step == "city":
        await onboard.handle_city(bot, cid, text); return

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


# ---------- Команды-обёртки ----------
async def notes_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_notes(context.bot, update.effective_chat.id)

async def setup_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_home(context.bot, update.effective_chat.id)

async def health_command(update, context):
    import ai as _ai
    lines = ["<b>🩺 Health check</b>", ""]

    # Env keys
    keys = {
        "TELEGRAM_TOKEN": bool(config.TELEGRAM_TOKEN),
        "GEMINI_API_KEY": bool(config.GEMINI_API_KEY),
        "ANTHROPIC_API_KEY": bool(config.ANTHROPIC_API_KEY),
        "GROQ_API_KEY": bool(config.GROQ_API_KEY),
        "DATABASE_URL": bool(config.DATABASE_URL),
    }
    lines.append("<b>Env:</b>")
    for k, ok in keys.items():
        lines.append(f"  {'✅' if ok else '❌'} {k}")

    # DB
    try:
        store._load("__health__")
        lines.append("✅ DB: OK")
    except Exception as e:
        _log.warning("health: DB failed: %s", e)
        lines.append("❌ DB: недоступна")

    # Weather
    try:
        s = store.get_settings(update.effective_chat.id)
        weather.fetch_weather(s["lat"], s["lon"], 1)
        lines.append("✅ Weather API: OK")
    except Exception as e:
        _log.warning("health: weather failed: %s", e)
        lines.append("❌ Weather API: недоступна")

    # LLM cost summary (последние 7 дней)
    import ai as _ai2
    import time as _t
    log = _ai2.get_cost_log()
    week_ago = _t.time() - 7 * 86400
    recent = [e for e in log if e.get("ts", 0) >= week_ago]
    if recent:
        total_tok = sum(e.get("tokens", 0) for e in recent)
        lines.append(f"💸 LLM 7д: {len(recent)} вызовов, ~{total_tok:,} tok")
    else:
        lines.append("💸 LLM 7д: нет данных")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def remember_command(update, context):
    """Сохранить факт о пользователе в профиль памяти."""
    cid = update.effective_chat.id
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text(
            "💡 Использование: /remember <факт>\n\n"
            "Например: /remember не люблю острое\n/remember мёрзну утром"
        )
        return
    text = secure.clamp(text, 200)
    import memory
    memory.add_preference(cid, text)
    await update.message.reply_text(f"🧠 Запомнил: «{text}»")


async def cost_command(update, context):
    """Сводка расходов на LLM — только для администратора."""
    cid = update.effective_chat.id
    if config.CHAT_ID and str(cid) != str(config.CHAT_ID):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    await settings.send_admin_cost(context.bot, cid)


async def invite_command(update, context):
    """Создать одноразовый инвайт-код — только для owner."""
    cid = update.effective_chat.id
    if not access.is_owner(cid):
        await update.message.reply_text("⛔ Только для владельца бота.")
        return
    code = access.create_invite()
    bot_me = await context.bot.get_me()
    link = f"https://t.me/{bot_me.username}?start={code}"
    await update.message.reply_text(
        f"🔗 <b>Подарочный инвайт:</b>\n<a href=\"{link}\">{link}</a>\n\nОтправь другу — он нажмёт и получит доступ.",
        parse_mode="HTML", disable_web_page_preview=True
    )


# ---------- Расписание ----------
async def job_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "morning_brief"):
            continue
        try:
            bot = _NokbBot(context.bot)
            await weather.send_weather(bot, cid, "today")
            await learning.send_morning_word(bot, cid)
        except Exception:
            logging.exception("job_morning_brief failed for cid=%s", cid)

async def job_weather_warn(context: ContextTypes.DEFAULT_TYPE):
    _WARN_CODES = {95, 96, 99}
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weather_warn"):
            continue
        try:
            s = store.get_settings(cid)
            data = weather.fetch_weather(s["lat"], s["lon"], 2)
            d = data["daily"]
            wind = d["windspeed_10m_max"][0] or 0
            code = d["weathercode"][0]
            rain = d["precipitation_probability_max"][0] or 0
            rain_mm = (d.get("precipitation_sum") or [None])[0]
            if wind > 10 or code in _WARN_CODES or rain > 70:
                text = weather.storm_alert(wind, code, rain, rain_mm, cc=s.get("cc", ""))
                if not text:
                    parts = []
                    if wind > 10:
                        parts.append(f"💨 ветер до {wind:.0f} м/с")
                    if rain > 70:
                        parts.append(f"🌧 дождь {rain:.0f}%")
                    if code in _WARN_CODES:
                        parts.append("⛈ возможна гроза")
                    text = "⚠️ <b>Погодное предупреждение</b>\n\n" + " • ".join(parts)
                await context.bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
        except Exception:
            logging.exception("job_weather_warn failed for cid=%s", cid)

async def job_lagom(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "lagom_daily"):
            continue
        try:
            await balance.send_motiv_push(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_lagom failed for cid=%s", cid)

async def job_grammar(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "grammar"):
            continue
        try:
            await learning.send_morning_word(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_grammar failed for cid=%s", cid)

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "checkin_day"):
            continue
        try:
            store.pending_input[str(cid)] = "worry"
            await context.bot.send_message(chat_id=cid, parse_mode="HTML",
                text="🫣 <b>Дневная разгрузка</b>\n\nСейчас не анализируй, просто выгрузи мысли.\n\n"
                     "Каждая тревога - с новой строки.\n\nВечером проверим, что было фактами, а что шумом…")
        except Exception:
            logging.exception("job_checkin_day failed for cid=%s", cid)

async def job_recipe(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "recipe_daily"):
            continue
        try:
            await balance.send_recipe_push(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_recipe failed for cid=%s", cid)

async def job_vocab_review(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "vocab_review"):
            continue
        try:
            await learning.send_vocab_review(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_vocab_review failed for cid=%s", cid)

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "checkin_eve"):
            continue
        try:
            await balance.send_evening_review(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_checkin_evening failed for cid=%s", cid)

async def job_weekly_events(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekly_events"):
            continue
        try:
            await leisure.send_weekly_events(_NokbBot(context.bot), cid)
        except Exception:
            logging.exception("job_weekly_events failed for cid=%s", cid)

async def job_live_lang(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "live_lang"):
            continue
        try:
            await learning.send_proverb_both(context.bot, cid)
        except Exception:
            logging.exception("job_live_lang failed for cid=%s", cid)

async def job_weekly_forecast(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekly_forecast"):
            continue
        try:
            await weather.send_weather(_NokbBot(context.bot), cid, "week")
        except Exception:
            logging.exception("job_weekly_forecast failed for cid=%s", cid)


async def job_evening_weather(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "evening_weather"):
            continue
        try:
            await weather.send_weather(_NokbBot(context.bot), cid, "tomorrow_plain")
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
        BotCommand("start", "меню и описание"),
        BotCommand("setup", "настройки"),
    ])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("setup", setup_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("cost", cost_command))
    app.add_handler(CommandHandler("invite", invite_command))
    app.add_handler(CallbackQueryHandler(answer_callback))
    app.add_handler(MessageHandler(filters.LOCATION, weather.location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    def _t(hm):
        return datetime.strptime(hm, "%H:%M").replace(tzinfo=TZ).timetz()
    jq.run_daily(job_morning_brief,   time=_t("07:30"), days=tuple(range(7)))
    jq.run_daily(job_weather_warn,    time=_t("08:15"), days=tuple(range(7)))
    jq.run_daily(job_lagom,           time=_t("09:00"), days=tuple(range(7)))
    jq.run_daily(job_grammar,         time=_t("11:00"), days=tuple(range(7)))
    jq.run_daily(job_recipe,          time=_t("12:30"), days=tuple(range(7)))
    jq.run_daily(job_checkin_day,     time=_t("14:00"), days=tuple(range(7)))
    jq.run_daily(job_vocab_review,    time=_t("21:00"), days=tuple(range(7)))
    jq.run_daily(job_checkin_evening, time=_t("22:00"), days=tuple(range(7)))
    jq.run_daily(job_evening_weather,  time=_t("19:00"), days=tuple(range(7)))
    jq.run_daily(job_weekly_forecast,  time=_t("19:00"), days=(6,))            # вс
    jq.run_daily(job_weekly_events,    time=_t("10:00"), days=(6,))            # вс

    logging.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()