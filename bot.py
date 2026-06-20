from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler)
from datetime import datetime

import config
import store
import menu
import assistant
import myday
import wardrobe
import learning
import settings
import travel
import content
import weather
from util import send_long

TZ = config.TZ
CHAT_ID = config.CHAT_ID


async def start(update, context):
    txt = (
        "👋 <b>Привет! Я DM</b> - твой ежедневный помощник.\n"
        "Погода, обучение, идеи и весь твой день в одном месте.\n\n"
        "<b>Что я умею:</b>\n"
        "☀️ <b>Мой день</b> - погода, образ, слово дня, идея, факты, цитата\n"
        "👕 <b>Гардероб</b> - луки по погоде, разбор шкафа, проверка покупок\n"
        "🧠 <b>Баланс</b> - врач, мотивация, рецепты\n"
        "📚 <b>Обучение</b> - нидерландский/английский, игра, словарь, экзамен\n"
        "🍿 <b>Досуг</b> - фильмы, книги, музыка, концерты, путешествия\n\n"
        "💬 Любой вопрос можно просто написать в чат - отвечу.\n\n"
        "<b>Команды:</b>\n"
        "/start - меню и описание\n"
        "/setup - настройки (язык, город, уведомления, параметры шкафа)\n\n"
        "⭐ Сохранять можно кнопкой «Добавить в избранное» под ответами. "
        "Потом всё найдёшь в /notes по категориям (Идеи, Цитаты, События...)."
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=menu.MAIN_KB)


# ---------- Диспетчер инлайн-кнопок ----------
async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

    # Ассистент: инлайн-кабинет
    if data.startswith("as_"):
        await assistant.handle_callback(bot, cid, q, data)
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
    if data.startswith("set_"):
        await handle_settings(bot, cid, data)
        return
    # Навигация по подменю - редактируем сообщение на месте
    if data == "m_close":
        try:
            await q.message.edit_text("Готово 👇 Меню снизу.")
        except Exception:
            pass
        return
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
                await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
                await myday.send_plany(bot, cid)
            elif act == "daycheck":
                await myday.send_daycheck(bot, cid)
            elif act == "diary":
                await myday.send_diary(bot, cid)
            elif act == "phrase":
                await myday.send_phrase(bot, cid)
            elif act == "gram_nl":
                await learning.send_grammar(bot, cid, "нидерландский", "🇳🇱")
            elif act == "gram_en":
                await learning.send_grammar(bot, cid, "английский", "🇬🇧")
            elif act == "tr_nl":
                await learning.do_translate(bot, cid, "нидерландский")
            elif act == "tr_en":
                await learning.do_translate(bot, cid, "английский")
            elif act == "verb_nl":
                await learning.send_verb(bot, cid, "нидерландский")
            elif act == "verb_en":
                await learning.send_verb(bot, cid, "английский")
            elif act == "proverb_nl":
                await learning.send_proverb(bot, cid, "нидерландский")
            elif act == "proverb_en":
                await learning.send_proverb(bot, cid, "английский")
            elif act == "dict":
                await learning.send_dict(bot, cid)
            elif act == "dictadd_nl":
                store.pending_input[cid] = "dictadd_nl"
                await bot.send_message(chat_id=cid, text="🇳🇱 Напиши нидерландское слово или фразу - добавлю с переводом.")
            elif act == "dictadd_en":
                store.pending_input[cid] = "dictadd_en"
                await bot.send_message(chat_id=cid, text="🇬🇧 Напиши английское слово или фразу - добавлю с переводом.")
            elif act == "addword":
                await learning.add_word(bot, cid)
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
                await bot.send_message(chat_id=cid, text="📍 Напиши название города - переключу прогноз на него.")
            elif act == "trav_go":
                await travel.send_go(bot, cid)
            elif act == "trav_no":
                await travel.travel_dislike(bot, cid)
            elif act == "watch":
                await content.send_recos(bot, cid, "movie")
            elif act == "read":
                await content.send_recos(bot, cid, "book")
            elif act == "watchlist":
                await content.send_watchlist(bot, cid)
            elif act == "readlist":
                await content.send_readlist(bot, cid)
            elif act == "fav":
                await content.send_fav(bot, cid)
            elif act == "artists":
                await content.send_artists(bot, cid)
            elif act == "artadd":
                await content.start_add_artist(bot, cid)
            elif act == "concerts_find":
                await content.find_concerts(bot, cid, "home")
            elif act == "concerts_pick":
                await content.concert_pick_country(bot, cid)
            elif act in ("concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
                         "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
                         "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"):
                await content.find_concerts(bot, cid, act.split("_")[1])
            elif act == "listen":
                await content.send_listen(bot, cid)
            elif act == "food_breakfast":
                await assistant.send_recipe(bot, cid, "завтрак")
            elif act == "food_lunch":
                await assistant.send_recipe(bot, cid, "обед")
            elif act == "food_dinner":
                await assistant.send_recipe(bot, cid, "ужин")
        except Exception as e:
            await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return

    # Уровни языка
    if data.startswith("lvl_"):
        _, code, level = data.split("_")
        language = "нидерландский" if code == "nl" else "английский"
        store.set_level(cid, language, level)
        await q.message.reply_text(f"Уровень {language} установлен: {level}")
        return
    # Грамматика
    if data in ("gram_a", "gram_b"):
        await learning.grammar_answer(bot, cid, "a" if data == "gram_a" else "b")
        return
    # «Ещё»
    if data.startswith("again_"):
        what = data[len("again_"):]
        if what == "tr_nl":
            await learning.do_translate(bot, cid, "нидерландский")
        elif what == "tr_en":
            await learning.do_translate(bot, cid, "английский")
        elif what == "gram_nl":
            await learning.again_grammar(bot, cid, "нидерландский")
        elif what == "gram_en":
            await learning.again_grammar(bot, cid, "английский")
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
    if data.startswith("worddel_"):
        await learning.del_word(bot, cid, int(data.split("_")[1]))
        return
    if data == "game_again":
        await learning.send_game(bot, cid)
        return
    if data == "game_hint":
        st = store.game_state.get(cid)
        ui = learning.GAME_UI.get(store.game_config.get(cid, {}).get("lang", "русский"), learning.GAME_UI["русский"])
        if st and st.get("hint"):
            from util import esc
            await q.message.reply_text(f"💡 <b>{esc(st['hint'])}</b>\n\n{ui['give']}", parse_mode="HTML")
        else:
            await q.message.reply_text("Подсказок больше нет.")
        return
    if data == "game_reveal":
        st = store.game_state.pop(cid, None)
        ui = learning.GAME_UI.get(store.game_config.get(cid, {}).get("lang", "русский"), learning.GAME_UI["русский"])
        if st:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from util import esc
            body = st.get("explain") or st.get("quote", "")
            txt = f"{ui['found']}\n\n{ui['answer']}: <b>{esc(st.get('answer',''))}</b>"
            if body:
                txt += f"\n\n{esc(body)}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
            await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)
        return
    if data == "game_change":
        await learning.game_start(bot, cid)
        return
    # Развлечения / путешествия
    if data.startswith("reco_"):
        await content.add_reco(bot, cid, int(data.split("_")[1]))
        return
    if data.startswith("movie_no_"):
        await content.movie_dislike(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("book_no_"):
        await content.book_dislike(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("listen_"):
        await content.add_listen(bot, cid, int(data.split("_")[1]))
        return
    # Проверка дня
    if data == "worry_clearall":
        await myday.worry_clear_all(bot, cid)
        return
    if data.startswith("worry_del_"):
        await myday.worry_delete(bot, cid, int(data.split("_")[-1]))
        return
    if data.startswith("worry_"):
        _, action, idx = data.split("_")
        await myday.worry_mark(bot, cid, int(idx), "real" if action == "real" else "let_go")
        return
    # Ассистент: ещё раз
    if data == "chat_retry":
        await assistant.retry(bot, cid)
        return


# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    text = update.message.text
    bot = context.bot

    # Нажата любая кнопка нижнего меню -> сбрасываем незавершённый ввод (чтобы чат не «съел» сообщение настроек)
    if text in ("☀️ Мой день", "👕 Гардероб") or text in menu.LABEL_TO_KEY:
        store.pending_input.pop(cid, None)

    if text == "☀️ Мой день":
        try:
            await myday.send_plany(bot, cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    if text == "👕 Гардероб":
        await wardrobe.send_home(bot, cid)
        return
    # Нажатие нижнего reply-меню -> открыть инлайн-подменю
    if text in menu.LABEL_TO_KEY:
        key = menu.LABEL_TO_KEY[text]
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

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.pop(cid)
        if kind == "diary":
            await myday.save_diary(bot, cid, text); return
        if kind == "worry":
            await myday.save_worries(bot, cid, text); return
        if kind == "favorite":
            await content.add_fav(bot, cid, text); return
        if kind == "artist":
            await content.add_artist(bot, cid, text); return
        if kind == "favcountry":
            await travel.add_country(bot, cid, text); return
        if kind in ("role_letter", "role_doctor", "role_state"):
            await assistant.handle_role(bot, cid, kind.split("_")[1], text); return
        if kind == "leftovers":
            await assistant.send_leftovers(bot, cid, text); return
        if kind == "wardrobe_add":
            await wardrobe.add_item(bot, cid, text); return
        if kind == "wardrobe_check":
            await wardrobe.check_purchase(bot, cid, text); return
        if kind == "setcity":
            await weather.set_city_text(bot, cid, text); return
        if kind == "dictadd_nl":
            await learning.add_word_manual(bot, cid, text, "nl"); return
        if kind == "dictadd_en":
            await learning.add_word_manual(bot, cid, text, "en"); return
        if kind == "bodyinput":
            settings.set_(cid, "body", text)
            await bot.send_message(chat_id=cid, text="Готово, параметры сохранены.")
            await settings.send_body(bot, cid); return
        if kind == "setadd_country":
            await settings.list_add_done(bot, cid, "country", text); return
        if kind == "setadd_artist":
            await settings.list_add_done(bot, cid, "artist", text); return
        if kind == "setadd_book":
            await settings.list_add_done(bot, cid, "book", text); return

    # Свободный чат
    await assistant.chat_reply(bot, cid, text)


async def document_handler(update, context):
    cid = str(update.effective_chat.id)
    if not store.add_wardrobe_mode.get(cid):
        return
    doc = update.message.document
    try:
        f = await context.bot.get_file(doc.file_id)
        body = await f.download_as_bytearray()
        txt = body.decode("utf-8", errors="ignore")
    except Exception as e:
        await update.message.reply_text(f"Не смог прочитать файл: {e}")
        return
    await wardrobe.ingest(context.bot, cid, txt)


# ---------- Команды-обёртки ----------
async def plany_command(update, context):
    await update.message.reply_text("Собираю сводку дня...")
    try:
        await myday.send_plany(context.bot, update.effective_chat.id)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def weather_command(update, context):
    days = 1
    if context.args:
        try:
            days = max(1, min(7, int(context.args[0])))
        except Exception:
            pass
    try:
        await weather.send_weather(context.bot, update.effective_chat.id, days)
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")

async def notes_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await assistant.send_notes(context.bot, update.effective_chat.id)

async def setup_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_home(context.bot, update.effective_chat.id)

async def reload_wardrobe_command(update, context):
    import json
    try:
        with open(config.WARDROBE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        store.save_wardrobe(data)
        n = sum(len(v) for v in data.values())
        await update.message.reply_text(f"Шкаф обновлён: {n} вещей в {len(data)} категориях.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def reload_content_command(update, context):
    import json
    cid = update.effective_chat.id
    try:
        with open("content.json", encoding="utf-8") as f:
            data = json.load(f)
        watch = list(data.get("films", [])) + list(data.get("series", [])) + list(data.get("docs", []))
        read = list(data.get("books", []))
        store.set_list(config.WATCHLIST_KEY, cid, watch)
        store.set_list(config.READLIST_KEY, cid, read)
        await update.message.reply_text(f"Досуг обновлён: {len(watch)} в списке просмотра, {len(read)} в списке чтения.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def reload_artists_command(update, context):
    import json
    cid = update.effective_chat.id
    try:
        with open("artists.json", encoding="utf-8") as f:
            data = json.load(f)
        store.set_list(config.ARTISTS_KEY, cid, data)
        await update.message.reply_text(f"Артисты обновлены: {len(data)}.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


# ---------- Расписание ----------
async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID or not settings.notif_on(CHAT_ID, "morning"):
        return
    try:
        await send_long(context.bot, CHAT_ID, myday.assemble_morning(CHAT_ID))
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка сводки: {e}")

async def job_grammar(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID or not settings.notif_on(CHAT_ID, "grammar"):
        return
    try:
        lang = settings.study_lang(CHAT_ID)
        flag = "🇳🇱" if lang == "нидерландский" else "🇬🇧"
        await learning.send_grammar(context.bot, CHAT_ID, lang, flag)
    except Exception:
        pass

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID or not settings.notif_on(CHAT_ID, "checkin_day"):
        return
    try:
        store.pending_input[str(CHAT_ID)] = "worry"
        await context.bot.send_message(chat_id=CHAT_ID, parse_mode="HTML",
            text="🫣 <b>Дневная разгрузка</b>\n\nСейчас не анализируй, просто выгрузи мысли.\n"
                 "Каждая тревога - с новой строки.\nВечером проверим, что было фактами, а что шумом.")
    except Exception:
        pass

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID or not settings.notif_on(CHAT_ID, "checkin_eve"):
        return
    try:
        await myday.send_evening_review(context.bot, CHAT_ID)
    except Exception:
        pass

async def job_vocab(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID or not settings.notif_on(CHAT_ID, "vocab"):
        return
    try:
        await learning.send_vocab_cards(context.bot, CHAT_ID)
    except Exception:
        pass

async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    import ai
    entries = store.get_list(config.DIARY_KEY, CHAT_ID)
    diary = "; ".join(e["text"] for e in entries[-7:]) if entries else "нет записей"
    try:
        prompt = (f"Тёплый короткий итог недели для Дмитрия. Записи: {diary}. "
                  f"Формат: 📊 Итоги недели. 3-4 строки: инсайт, что получилось, настрой на следующую. "
                  f"{config.LAGOM} Без markdown.")
        await send_long(context.bot, CHAT_ID, ai.llm(prompt, 500, 0.8))
    except Exception:
        pass
    try:
        await content.find_concerts(context.bot, CHAT_ID, "home")
    except Exception:
        pass


async def handle_settings(bot, cid, data):
    if data == "set_home":
        await settings.send_home(bot, cid)
    elif data == "set_notif":
        await settings.send_notif(bot, cid)
    elif data.startswith("set_notiftgl_"):
        await settings.toggle_notif(bot, cid, data[len("set_notiftgl_"):])
    elif data == "set_lang":
        await settings.send_lang(bot, cid)
    elif data == "set_lang_nl":
        await settings.set_lang(bot, cid, "нидерландский")
    elif data == "set_lang_en":
        await settings.set_lang(bot, cid, "английский")
    elif data == "set_levels":
        await learning.send_levels(bot, cid)
    elif data == "set_city":
        store.pending_input[cid] = "setcity"
        await bot.send_message(chat_id=cid, text="🌍 Напиши город - переключу.")
    elif data == "set_body":
        await settings.send_body(bot, cid)
    elif data == "set_wardrobe":
        await settings.send_wardrobe(bot, cid)
    elif data == "set_countries":
        await settings.send_countries(bot, cid)
    elif data == "set_artists":
        await settings.send_artists(bot, cid)
    elif data == "set_books":
        await settings.send_books(bot, cid)
    elif data == "setadd_country":
        store.pending_input[cid] = "setadd_country"
        await bot.send_message(chat_id=cid, text="🧳 Напиши страну - добавлю в список.")
    elif data == "setadd_artist":
        store.pending_input[cid] = "setadd_artist"
        await bot.send_message(chat_id=cid, text="🎤 Напиши имя артиста - добавлю в список.")
    elif data == "setadd_book":
        store.pending_input[cid] = "setadd_book"
        await bot.send_message(chat_id=cid, text="📚 Напиши название книги - добавлю в список.")
    elif data.startswith("setdel_country_"):
        await settings.list_delete(bot, cid, "country", int(data.split("_")[-1]))
    elif data.startswith("setdel_artist_"):
        await settings.list_delete(bot, cid, "artist", int(data.split("_")[-1]))
    elif data.startswith("setdel_book_"):
        await settings.list_delete(bot, cid, "book", int(data.split("_")[-1]))
    elif data.startswith("set_style_"):
        await settings.set_style(bot, cid, int(data.split("_")[-1]))
    elif data == "set_bodyinput":
        store.pending_input[cid] = "bodyinput"
        await bot.send_message(chat_id=cid, text="✏️ Напиши параметры: рост, вес, обувь, размер брюк и одежды.")


async def post_init(app):
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "меню и описание"),
        BotCommand("setup", "настройки"),
        BotCommand("notes", "избранное"),
    ])


def main():
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("setup", setup_command))
    app.add_handler(CommandHandler("reload_wardrobe", reload_wardrobe_command))
    app.add_handler(CommandHandler("reload_content", reload_content_command))
    app.add_handler(CommandHandler("reload_artists", reload_artists_command))
    app.add_handler(CommandHandler("plany", plany_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("setcity", weather.setcity_command))
    app.add_handler(CallbackQueryHandler(answer_callback))
    app.add_handler(MessageHandler(filters.LOCATION, weather.location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(job_morning, time=datetime.strptime("08:30", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_grammar, time=datetime.strptime("11:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_day, time=datetime.strptime("14:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_evening, time=datetime.strptime("20:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_weekly, time=datetime.strptime("19:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=(6,))
    jq.run_daily(job_vocab, time=datetime.strptime("12:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=(6,))

    print("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()