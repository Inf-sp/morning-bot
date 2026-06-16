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
import travel
import content
import weather
from util import send_long

TZ = config.TZ
CHAT_ID = config.CHAT_ID


async def start(update, context):
    await update.message.reply_text("Привет! 👋 Я DM.\n\nВыбери раздел в меню снизу.", reply_markup=menu.MAIN_KB)


# ---------- Диспетчер инлайн-кнопок ----------
async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

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
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return

    # Действия
    if data.startswith("a_"):
        act = data[2:]
        try:
            if act == "assist":
                await assistant.send_welcome(bot, cid)
            elif act == "plany":
                await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
                await myday.send_plany(bot, cid)
            elif act == "daycheck":
                await myday.send_daycheck(bot, cid)
            elif act == "diary":
                await myday.send_diary(bot, cid)
            elif act == "phrase":
                await myday.send_phrase(bot, cid)
            elif act == "look":
                await wardrobe.send_look(bot, cid)
            elif act == "wlist":
                await wardrobe.send_list(bot, cid)
            elif act == "wanalysis":
                await wardrobe.send_analysis(bot, cid)
            elif act == "shop":
                await wardrobe.send_shop(bot, cid)
            elif act == "wadd":
                await wardrobe.start_add(bot, cid)
            elif act == "gram_nl":
                await learning.send_grammar(bot, cid, "нидерландский", "🇳🇱")
            elif act == "gram_en":
                await learning.send_grammar(bot, cid, "английский", "🇬🇧")
            elif act == "tr_nl":
                await learning.do_translate(bot, cid, "нидерландский")
            elif act == "tr_en":
                await learning.do_translate(bot, cid, "английский")
            elif act == "game":
                await learning.game_start(bot, cid)
            elif act == "levels":
                await learning.send_levels(bot, cid)
            elif act == "w_today":
                await weather.send_weather(bot, cid, 1)
            elif act == "w_week":
                await weather.send_weather(bot, cid, 7)
            elif act == "setcity":
                await bot.send_message(chat_id=cid, text="Отправь геолокацию или команду: /setcity Амстердам")
            elif act == "trav_go":
                await travel.send_go(bot, cid)
            elif act == "trav_my":
                await travel.send_my(bot, cid)
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
            await learning.send_grammar(bot, cid, "нидерландский", "🇳🇱")
        elif what == "gram_en":
            await learning.send_grammar(bot, cid, "английский", "🇬🇧")
        return
    # Игра
    if data.startswith("gamelang_"):
        lang = {"ru": "русский", "en": "английский", "nl": "нидерландский"}[data.split("_")[1]]
        store.game_config[cid] = {"lang": lang, "difficulty": "med"}
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Лёгкая", callback_data="gamediff_easy"),
            InlineKeyboardButton("Средняя", callback_data="gamediff_med"),
            InlineKeyboardButton("Тяжёлая", callback_data="gamediff_hard"),
        ]])
        await q.message.reply_text(f"Язык: {lang}. Выбери сложность:", reply_markup=kb)
        return
    if data.startswith("gamediff_"):
        diff = data.split("_")[1]
        cfg = store.game_config.get(cid, {"lang": "нидерландский"})
        cfg["difficulty"] = diff
        store.game_config[cid] = cfg
        await learning.send_game(bot, cid)
        return
    if data == "game_again":
        await learning.send_game(bot, cid)
        return
    if data == "game_hint":
        st = store.game_state.get(cid)
        if st and st.get("hint"):
            await q.message.reply_text(f"💡 {st['hint']}\n\nНапиши ответ или нажми «Ответ».")
        else:
            await q.message.reply_text("Подсказок больше нет.")
        return
    if data == "game_reveal":
        st = store.game_state.pop(cid, None)
        if st:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from util import esc
            L = [f"👁 Это {st.get('answer','')}", "", f"💬 {st.get('quote','')}"]
            if st.get("quote_ru"):
                L.append(f"<i>{esc(st['quote_ru'])}</i>")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
            await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return
    if data == "game_change":
        await learning.game_start(bot, cid)
        return
    # Развлечения / путешествия
    if data.startswith("reco_"):
        await content.add_reco(bot, cid, int(data.split("_")[1]))
        return
    if data.startswith("facts_"):
        await travel.send_facts(bot, cid, int(data.split("_")[1]))
        return
    if data.startswith("delcountry_"):
        await travel.del_country(bot, cid, int(data.split("_")[1]))
        return
    # Проверка дня
    if data.startswith("worry_"):
        _, action, idx = data.split("_")
        await myday.worry_mark(bot, cid, int(idx), "real" if action == "real" else "let_go")
        return


# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    text = update.message.text
    bot = context.bot

    # Нажатие нижнего reply-меню -> открыть инлайн-подменю / ассистента
    if text in menu.LABEL_TO_KEY:
        key = menu.LABEL_TO_KEY[text]
        if key == "assist":
            await assistant.send_welcome(bot, cid)
        else:
            t, kb = menu.menu_screen(key)
            await bot.send_message(chat_id=cid, text=t, reply_markup=kb)
        return

    # Режим добавления одежды
    if store.add_wardrobe_mode.get(cid):
        await wardrobe.ingest(bot, cid, text)
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

    # Игра
    if cid in store.game_state:
        if await learning.game_answer(bot, cid, text):
            return
    # Перевод
    if cid in store.challenge_state:
        if await learning.translate_answer(bot, cid, text):
            return

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


# ---------- Расписание ----------
async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await send_long(context.bot, CHAT_ID, myday.assemble_morning(CHAT_ID))
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка сводки: {e}")

async def job_grammar(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await learning.send_grammar(context.bot, CHAT_ID, "нидерландский", "🇳🇱")
    except Exception:
        pass

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        store.pending_input[str(CHAT_ID)] = "worry"
        await context.bot.send_message(chat_id=CHAT_ID,
            text="🌤 Дим, что сейчас тревожит? Напиши одним сообщением, каждую тревогу с новой строки - вечером проверим.")
    except Exception:
        pass

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await myday.send_daycheck(context.bot, CHAT_ID)
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


async def post_init(app):
    from telegram import BotCommand
    await app.bot.set_my_commands([BotCommand("start", "меню")])


def main():
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
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

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
