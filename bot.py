import asyncio
import logging
from datetime import datetime

from telegram import ReplyKeyboardRemove
from telegram.error import Conflict
from telegram.request import HTTPXRequest
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler, PollAnswerHandler)

import config
import ai
import store
import callback_topics
import access
import menu
import recipe_generation
import bot_callbacks
import bot_text
import myday
import wardrobe
import learning_dictionary as dictionary
import trainer
import learning
import settings
import leisure_movies
import leisure_books
import leisure_games
import leisure_music
import leisure_collection
import leisure_concerts
import travel
import weather
import verify
import secure
import service_monitor
from process_guard import PollingLease, process_identity
import onboard
import tracking
from telegram_runtime import MenuCleanupBot as _MenuCleanupBot, RetryingHTTPXRequest as _RetryingHTTPXRequest
from deploy_report import (
    get_app_version,
    maybe_send_admin_deploy_notification,
)

_log = logging.getLogger(__name__)

TZ = config.TZ
CHAT_ID = config.CHAT_ID
_PROCESS_STARTED_AT = datetime.now(TZ).isoformat()
_RECENT_HOME_OPENINGS = {}
_HOME_OPENING_DEDUP_SECONDS = 3
_WEATHER_WARNING_TIME = "08:00"
_HOME_WARM_SCHEDULE = (
    ("myday", "07:00"),
    ("wardrobe", "08:05"),
    ("cooking", "03:20"),
    ("travel", "08:15"),
    ("cinema", "08:20"),
    ("books", "08:25"),
    ("music", "08:30"),
    ("learning", "08:35"),
)



def _claim_home_opening(cid, message_id, data):
    """Не запускает повторно долгий главный экран от двойного тапа."""
    if data not in callback_topics.LONG_HOME_CALLBACKS:
        return True
    now = asyncio.get_running_loop().time()
    key = (str(cid), str(message_id or ""), data)
    expired = [item for item, seen_at in _RECENT_HOME_OPENINGS.items()
               if now - seen_at >= _HOME_OPENING_DEDUP_SECONDS]
    for item in expired:
        _RECENT_HOME_OPENINGS.pop(item, None)
    if key in _RECENT_HOME_OPENINGS:
        return False
    _RECENT_HOME_OPENINGS[key] = now
    return True


def _looks_like_command(text: str) -> bool:
    """Текст похож на команду, а не на тревогу - не глотать его окном
    "Дневной разгрузки"."""
    t = (text or "").strip()
    return t.startswith("/")


async def _remove_reply_kb_once(bot, cid):
    """Разово снимает нижнюю Reply-клавиатуру «Ассистент» у профилей, где она уже
    была показана (Telegram держит клавиатуру, пока явно не пришлёт другую)."""
    prof = store.get_profile(cid)
    if prof.get(menu.REPLY_KB_REMOVED_FLAG):
        return
    try:
        msg = await bot.send_message(chat_id=cid, text=".", reply_markup=ReplyKeyboardRemove())
        await bot.delete_message(chat_id=cid, message_id=msg.message_id)
    except Exception:
        return
    store.mutate_profile(cid, lambda profile: (
        {**profile, menu.REPLY_KB_REMOVED_FLAG: True}, None,
    ))


async def start(update, context):
    cid = str(update.effective_chat.id)
    args = context.args or []
    await _remove_reply_kb_once(context.bot, cid)

    # Инвайт-код передан через /start <code>
    if args:
        code = args[0].strip()
        if access.is_allowed(cid):
            msg = menu.welcome_for(cid)
            await context.bot.send_message(
                chat_id=cid,
                text=msg.text,
                entities=msg.entities,
                reply_markup=menu.main_menu_kb(),
                transient=True,
            )
            return
        if access.use_invite(code, cid):
            tracking.touch(cid)
            await onboard.start(context.bot, cid)
            return
        await update.message.reply_text("❌ Инвайт-код недействителен или устарел.")
        return

    if not access.is_allowed(cid):
        await update.message.reply_text("❌ Бот приватный. Попроси владельца прислать инвайт.")
        return

    msg = menu.welcome_for(cid)
    await context.bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=menu.main_menu_kb(),
        transient=True,
    )


# ---------- Диспетчер инлайн-кнопок ----------
async def answer_callback(update, context):
    q = update.callback_query
    cid = str(q.message.chat_id)
    bot = context.bot
    data = str(getattr(q, "data", "") or "")
    if not _claim_home_opening(cid, getattr(q.message, "message_id", None), data):
        await q.answer()
        return
    topic = bot_callbacks._status_topic(data) or "Меню"
    budget = 15 if topic in {"myday", "wardrobe", "food", "leisure", "travel"} else 10
    trace = tracking.start_action(cid, topic, data or "callback", budget_seconds=budget)
    ok = True
    marker = getattr(bot, "mark_transient_message", None)
    if marker and menu.is_main_menu_markup(getattr(q.message, "reply_markup", None)):
        marker(cid, q.message.message_id)
    if access.is_allowed(cid):
        tracking.touch(cid)
    answer_task = asyncio.create_task(q.answer())
    answer_task.add_done_callback(
        lambda task: tracking.mark_first_feedback(trace)
        if not task.cancelled() and task.exception() is None else None
    )
    # Даём answerCallbackQuery начать отправку до любого синхронного чтения БД
    # внутри обработчика (особенно перед Azure Speech TTS).
    await asyncio.sleep(0)
    try:
        await bot_callbacks.handle(update, context, _remove_reply_kb_once)
    except Exception as e:
        ok = False
        # Страховка: необработанное исключение в ветке диспетчера без собственного
        # try/except иначе оставляло пользователя с "зависшей" кнопкой и без ответа.
        await verify.safe_error(bot, cid, e)
    finally:
        try:
            await answer_task
        except Exception:
            pass
        tracking.finish_action(trace, ok=ok)



# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    bot = context.bot
    trace = tracking.start_action(cid, "Ассистент", "text", budget_seconds=10)
    ok = True
    try:
        await bot_text.handle(update, context, _remove_reply_kb_once)
    except Exception as e:
        ok = False
        # Без этой страховки необработанное исключение внутри любой ветки роутера
        # (тренажёр, добавление в словарь и т.д.) оставляло пользователя без ответа.
        await verify.safe_error(bot, cid, e)
    finally:
        tracking.finish_action(trace, ok=ok)


async def message_activity_handler(update, _context):
    """Учитывает любое сообщение, включая команды, документы и геопозицию."""
    cid = getattr(getattr(update, "effective_chat", None), "id", None)
    if cid is not None and access.is_allowed(cid):
        tracking.touch(cid)



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


async def photo_handler(update, context):
    cid = str(update.effective_chat.id)
    if not access.is_allowed(cid):
        return
    tracking.touch(cid)
    pending = store.pending_input.get(cid)
    if pending not in ("wardrobe_add", "wardrobe_add_set"):
        return
    store.pending_input.pop(cid, None)
    photo = update.message.photo[-1]
    if (photo.file_size or 0) > 8 * 1024 * 1024:
        store.pending_input[cid] = "wardrobe_add"
        await update.message.reply_text("Фото слишком большое. Пришли снимок до 8 МБ или опиши вещь текстом.")
        return
    try:
        f = await context.bot.get_file(photo.file_id)
        body = await f.download_as_bytearray()
    except Exception as e:
        await verify.safe_error(context.bot, cid, e)
        return
    await wardrobe.add_item_photo(
        context.bot, cid, body, "image/jpeg", secure.clamp(update.message.caption or ""))


async def poll_answer_handler(update, context):
    await trainer.handle_poll_answer(context.bot, update.poll_answer)


# ---------- Команды-обёртки ----------
async def settings_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_home(context.bot, update.effective_chat.id)

async def admin_command(update, context):
    cid = update.effective_chat.id
    if not access.is_owner(cid):
        await settings.send_admin(context.bot, cid)
        return
    store.pending_input.pop(str(cid), None)
    await settings.send_admin(context.bot, cid)

async def menu_command(update, context):
    cid = str(update.effective_chat.id)
    store.pending_input.pop(cid, None)
    text, entities, kb = menu.main_menu_screen(cid)
    await context.bot.send_message(
        chat_id=cid,
        text=text,
        entities=entities,
        reply_markup=kb,
        transient=True,
    )


async def admin_debug_api_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    import admin as _admin
    await settings._admin_guard(context.bot, update.effective_chat.id, _admin.send_api_ai)


async def admin_debug_llm_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    import admin as _admin
    await settings._admin_guard(context.bot, update.effective_chat.id, _admin.send_api_ai)


async def admin_logs_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    import admin as _admin
    await settings._admin_guard(context.bot, update.effective_chat.id, _admin.send_logs)


# ---------- Расписание ----------
async def job_weather_warn(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weather_warn"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "weather_warn")
        except Exception:
            logging.exception("job_weather_warn failed for cid=%s", cid)


async def job_warm_weather_cache(context: ContextTypes.DEFAULT_TYPE):
    seen = set()
    for cid in access.get_allowed_cids():
        try:
            s = store.get_settings(cid)
            key = (round(s["lat"], 2), round(s["lon"], 2))
            if key in seen:
                continue
            seen.add(key)
            await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 2)
        except Exception:
            logging.exception("job_warm_weather_cache failed for cid=%s", cid)


async def job_warm_home_pages(context: ContextTypes.DEFAULT_TYPE):
    """Молча готовит один дорогой главный экран на день.

    Ошибка одного раздела не мешает прогреть остальные. Пользователю ничего
    не отправляется; при открытии раздела бот читает уже готовый кэш.
    """
    scheduled_section = str(getattr(getattr(context, "job", None), "data", "") or "")
    for cid in access.get_allowed_cids():
        if tracking.has_active_actions():
            logging.info("home cache warm skipped: user action active")
            return
        steps = (
            ("wardrobe", lambda: wardrobe.warm_home_cache(cid)),
            ("myday", lambda: myday.warm_day_cache(cid)),
            ("cooking", lambda: asyncio.to_thread(recipe_generation.warm_cooking_home_ideas, cid)),
            ("learning", lambda: asyncio.to_thread(learning.warm_home_cache, cid)),
            ("travel", lambda: travel.warm_home_cache(cid)),
            ("cinema", lambda: leisure_movies.warm_movie_home_cache(cid)),
            ("books", lambda: leisure_books.warm_books_home_cache(cid)),
            ("music", lambda: leisure_music.warm_music_home_cache(cid)),
        )
        if scheduled_section:
            steps = tuple(step for step in steps if step[0] == scheduled_section)
        warmed = []
        for name, call in steps:
            if tracking.has_active_actions():
                logging.info("home cache warm paused cid=%s before=%s", cid, name)
                break
            await asyncio.sleep(0)
            try:
                result = await call()
                if isinstance(result, dict):
                    if any(result.values()):
                        warmed.append(name)
                elif result is not False:
                    warmed.append(name)
            except Exception:
                logging.exception("home cache warm failed cid=%s section=%s", cid, name)
        logging.info("home cache warm complete cid=%s sections=%s", cid, ",".join(warmed))


async def job_warm_movie_premieres_cache(context: ContextTypes.DEFAULT_TYPE):
    """Ночью обновляет витрины кинопремьер по одной на страну."""
    seen_countries = set()
    for cid in access.get_allowed_cids():
        if tracking.has_active_actions():
            logging.info("movie premieres warm skipped: user action active")
            return
        try:
            country_code = str(store.get_settings(cid).get("cc") or "NL").upper()
            if country_code in seen_countries:
                continue
            seen_countries.add(country_code)
            await leisure_movies.warm_movie_premieres_cache(cid)
        except Exception:
            logging.exception("job_warm_movie_premieres_cache failed for cid=%s", cid)


async def job_warm_book_premieres_cache(context: ContextTypes.DEFAULT_TYPE):
    """Ночью обновляет единую книжную витрину один раз для всех пользователей."""
    if not access.get_allowed_cids() or tracking.has_active_actions():
        return
    try:
        await leisure_books.warm_book_premieres_cache()
    except Exception:
        logging.exception("job_warm_book_premieres_cache failed")


async def job_warm_game_premieres_cache(context: ContextTypes.DEFAULT_TYPE):
    """Ночью готовит игровые премьеры под платформы каждого пользователя."""
    for cid in access.get_allowed_cids():
        if tracking.has_active_actions():
            logging.info("game premieres warm skipped: user action active")
            return
        try:
            await leisure_games.warm_game_premieres_cache(cid)
        except Exception:
            logging.exception("job_warm_game_premieres_cache failed for cid=%s", cid)


async def job_daily_words(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "daily_words"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "daily_words")
        except Exception:
            logging.exception("job_daily_words failed for cid=%s", cid)

async def job_refresh_concerts_cache(context: ContextTypes.DEFAULT_TYPE):
    """Прогревает недельный кэш концертов перед уведомлением «Ближайшие события» (10:00 пт),
    чтобы само уведомление и последующие интерактивные «Концерты» читали кэш, а не ждали Ticketmaster."""
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekend_events"):
            continue
        try:
            await leisure_concerts.refresh_concerts_cache(cid)
        except Exception:
            logging.exception("job_refresh_concerts_cache failed for cid=%s", cid)

async def job_weekend_events(context: ContextTypes.DEFAULT_TYPE):
    """Компактные премьеры кино, концертов, книг и игр по пятницам."""
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekend_events"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "weekend_events")
        except Exception:
            logging.exception("job_weekend_events failed for cid=%s", cid)


async def job_evening_weather(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "evening_weather"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "evening_weather")
        except Exception:
            logging.exception("job_evening_weather failed for cid=%s", cid)


async def job_inactivity_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Одно напоминание после 72 часов; новый цикл начинается с новой активности."""
    for cid, since_ts in tracking.due_inactivity_reminders(access.get_allowed_cids()):
        try:
            msg = menu.inactivity_reminder()
            await context.bot.send_message(
                chat_id=cid,
                text=msg.text,
                entities=msg.entities,
                reply_markup=msg.reply_markup,
                transient=True,
            )
            tracking.mark_inactivity_reminded(cid, since_ts)
        except Exception:
            logging.exception("job_inactivity_reminders failed for cid=%s", cid)


def _run_startup_audits():
    """Проверить исходники после готовности polling, не задерживая запуск."""
    try:
        unhandled = verify.audit_callbacks()
        if unhandled:
            logging.warning("Callback audit: unhandled -> %s", ", ".join(unhandled))
        else:
            logging.info("Callback audit: OK")
    except Exception:
        logging.exception("Callback audit failed")
    try:
        violations = verify.audit_architecture()
        if violations:
            logging.warning("Architecture audit: violations -> %s", "; ".join(violations))
        else:
            logging.info("Architecture audit: OK")
    except Exception:
        logging.exception("Architecture audit failed")
    try:
        trainer_violations = verify.audit_trainer_contracts()
        if trainer_violations:
            logging.warning("Trainer contract audit: violations -> %s", "; ".join(trainer_violations))
        else:
            logging.info("Trainer contract audit: OK")
    except Exception:
        logging.exception("Trainer contract audit failed")
    try:
        navigation_violations = verify.audit_navigation_contracts()
        if navigation_violations:
            logging.warning("Navigation audit: violations -> %s", "; ".join(navigation_violations))
        else:
            logging.info("Navigation audit: OK")
    except Exception:
        logging.exception("Navigation audit failed")
    try:
        leaks = secure.scan_secrets()
        if leaks:
            logging.warning("Secrets scan: findings -> %s", "; ".join(leaks))
        else:
            logging.info("Secrets scan: OK")
    except Exception:
        logging.exception("Secrets scan failed")


async def job_startup_audits(context: ContextTypes.DEFAULT_TYPE):
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_startup_audits,
            when=30,
            name="startup_audits_once",
            job_kwargs={"id": "startup_audits_once", "replace_existing": True},
        )
        return
    await asyncio.to_thread(_run_startup_audits)


async def job_retry_dictionary_adds(context: ContextTypes.DEFAULT_TYPE):
    """Повторяет только сохранённые Add-запросы; пользователь ничего не вводит заново."""
    if tracking.has_active_actions():
        return
    import dictionary_import
    await dictionary_import.process_queued_dictionary_adds(
        context.bot, access.get_allowed_cids(), limit=10,
    )


async def job_dictionary_maintenance(context: ContextTypes.DEFAULT_TYPE):
    """Обновляет старые карточки вне пользовательского открытия словаря."""
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_dictionary_maintenance,
            when=60,
            name="dictionary_maintenance_once",
            job_kwargs={"id": "dictionary_maintenance_once", "replace_existing": True},
        )
        return
    for cid in access.get_allowed_cids():
        try:
            await dictionary.rebuild_dictionary_entries(cid)
            for lang in ("nl", "en"):
                await dictionary.migrate_dict_entries_for_srs(cid, lang)
        except Exception:
            logging.exception("Dictionary maintenance failed user_id=%s", cid)


async def job_requested_dictionary_rechecks(context: ContextTypes.DEFAULT_TYPE):
    """Забирает пользовательские запросы полной проверки по одному за проход."""
    if tracking.has_active_actions():
        return
    await dictionary.process_requested_dictionary_rechecks(
        context.bot, access.get_allowed_cids(), limit=1,
    )


async def job_normalize_favorite_collections(context: ContextTypes.DEFAULT_TYPE):
    """Один спокойный проход по старым личным спискам после запуска.

    TMDb уже кэширует резолв названий на сутки, поэтому миграция не создаёт
    повторных запросов при перезапуске и не задерживает запуск бота.
    """
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_normalize_favorite_collections,
            when=60,
            name="normalize_favorite_collections_once",
            job_kwargs={"id": "normalize_favorite_collections_once", "replace_existing": True},
        )
        return
    try:
        if await asyncio.to_thread(leisure_collection.normalize_favorite_collections, True):
            logging.info("Favorite collections: canonical labels applied")
    except Exception:
        logging.exception("Favorite collections normalization failed")


async def post_init(app):
    initialized = tracking.initialize_inactivity_tracking(access.get_allowed_cids())
    if initialized:
        logging.info("Inactivity reminders: initialized %s users", initialized)
    try:
        if dictionary.migrate_dict_caps():
            logging.info("Dict caps migration: applied")
            # Сохранённые карточки дня могли содержать старый регистр. После
            # миграции строим их заново из единого словаря без AI-вызова.
            for cid in access.get_allowed_cids():
                learning.reset_daily_material_cache(cid)
                myday.reset_day_cache(cid)
    except Exception:
        logging.exception("Dict caps migration failed")
    try:
        if leisure_collection.dedupe_lists():
            logging.info("Dedupe lists: applied")
    except Exception:
        logging.exception("Dedupe lists failed")
    app.job_queue.run_once(
        job_normalize_favorite_collections,
        when=120,
        name="normalize_favorite_collections_once",
        job_kwargs={"id": "normalize_favorite_collections_once", "replace_existing": True},
    )
    from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
    common_commands = [
        BotCommand("menu", "Главное меню"),
        BotCommand("settings", "Настройки"),
    ]
    await app.bot.set_my_commands(common_commands, scope=BotCommandScopeDefault())
    if config.CHAT_ID:
        admin_chat_id = int(config.CHAT_ID) if str(config.CHAT_ID).lstrip("-").isdigit() else config.CHAT_ID
        await app.bot.set_my_commands([
            BotCommand("menu", "Главное меню"),
            BotCommand("settings", "Настройки"),
            BotCommand("admin", "Админ"),
        ], scope=BotCommandScopeChat(chat_id=admin_chat_id))
    await maybe_send_admin_deploy_notification(app.bot)


async def global_error_handler(update, context):
    error = context.error
    identity = process_identity(_PROCESS_STARTED_AT)
    if isinstance(error, Conflict):
        context.application.bot_data["polling_conflict"] = True
        _log.critical(
            "Telegram polling conflict; stopping this process pid=%s hostname=%s deployment=%s",
            identity["pid"], identity["hostname"], identity["deployment"],
        )
        context.application.stop_running()
        return
    _log.error(
        "Unhandled Telegram error pid=%s hostname=%s",
        identity["pid"], identity["hostname"],
        exc_info=(type(error), error, error.__traceback__),
    )


def _job_options(job_id):
    return {
        "name": job_id,
        "job_kwargs": {"id": job_id, "replace_existing": True},
    }


def _build_application():
    request = _RetryingHTTPXRequest(
        connection_pool_size=16,
        connect_timeout=7,
        read_timeout=20,
        write_timeout=20,
        pool_timeout=5,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=2,
        connect_timeout=10,
        read_timeout=35,
        write_timeout=10,
        pool_timeout=5,
    )
    bot = _MenuCleanupBot(
        token=config.TELEGRAM_TOKEN,
        request=request,
        get_updates_request=updates_request,
    )
    app = Application.builder().bot(bot).post_init(post_init).build()
    app.add_error_handler(global_error_handler)
    app.add_handler(MessageHandler(filters.ALL, message_activity_handler), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("setup", settings_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("admin_debug_api", admin_debug_api_command))
    app.add_handler(CommandHandler("admin_debug_llm", admin_debug_llm_command))
    app.add_handler(CommandHandler("admin_logs", admin_logs_command))
    app.add_handler(CallbackQueryHandler(answer_callback))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.LOCATION, weather.location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    def _t(hm):
        return datetime.strptime(hm, "%H:%M").replace(tzinfo=TZ).timetz()
    jq.run_once(job_startup_audits, when=2, **_job_options("startup_audits_once"))
    jq.run_once(
        job_dictionary_maintenance,
        when=180,
        **_job_options("dictionary_maintenance_once"),
    )
    for index, (section, _time_label) in enumerate(_HOME_WARM_SCHEDULE):
        jq.run_once(
            job_warm_home_pages,
            when=5 + index * 60,
            data=section,
            **_job_options(f"warm_home_{section}_startup"),
        )
    jq.run_once(service_monitor.monitoring_job, when=10, **_job_options("monitoring_startup"))
    jq.run_repeating(
        service_monitor.monitoring_job,
        interval=300,
        first=310,
        **_job_options("monitoring_repeating"),
    )
    jq.run_repeating(
        job_retry_dictionary_adds,
        interval=300,
        first=90,
        **_job_options("dictionary_add_retry_repeating"),
    )
    jq.run_repeating(
        job_requested_dictionary_rechecks,
        interval=60,
        first=30,
        **_job_options("dictionary_recheck_repeating"),
    )
    for section, time_label in _HOME_WARM_SCHEDULE:
        weekly_home_sections = {"travel", "cinema", "books", "music"}
        warm_days = (0,) if section in weekly_home_sections else tuple(range(7))
        cadence = "weekly" if section in weekly_home_sections else "daily"
        jq.run_daily(
            job_warm_home_pages,
            time=_t(time_label),
            days=warm_days,
            data=section,
            **_job_options(f"warm_home_{section}_{cadence}"),
        )
    jq.run_daily(job_warm_weather_cache, time=_t("07:55"), days=tuple(range(7)), **_job_options("warm_weather_cache_daily"))
    # Премьеры фильмов обновляются по недельному ключу в понедельник. Остальные
    # витрины сами проверяют свой TTL и не делают лишний внешний запрос.
    jq.run_daily(
        job_warm_movie_premieres_cache, time=_t("02:10"), days=(0,),
        **_job_options("movie_premieres_cache_weekly"),
    )
    jq.run_daily(
        job_weather_warn,
        time=_t(_WEATHER_WARNING_TIME),
        days=tuple(range(7)),
        **_job_options("weather_warn_daily"),
    )
    jq.run_daily(job_weekend_events, time=_t("10:00"), days=(4,), **_job_options("weekend_events_weekly"))
    jq.run_daily(job_daily_words, time=_t("11:00"), days=tuple(range(7)), **_job_options("daily_words"))
    jq.run_daily(
        job_evening_weather,
        time=_t(settings.EVENING_WEATHER_TIME),
        days=tuple(range(7)),
        **_job_options("evening_weather_daily"),
    )
    jq.run_daily(job_inactivity_reminders, time=_t("09:00"), days=tuple(range(7)), **_job_options("inactivity_reminders_daily"))
    _log.info("Scheduler configured jobs=%s", len(jq.jobs()))
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # HTTPX пишет полный Telegram URL вместе с bot token — не допускаем токен в логах.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    identity = process_identity(_PROCESS_STARTED_AT)
    version = get_app_version()
    _log.info(
        "Process starting version=%s deployment=%s replica=%s pid=%s ai_route_version=%s hostname=%s started_at=%s",
        version, identity["deployment"], identity["replica"], identity["pid"],
        ai.FREE_CHAT_ROUTE_VERSION,
        identity["hostname"], identity["started_at"],
    )
    lease = PollingLease()
    if not lease.acquire():
        raise SystemExit("Polling lease was not acquired")
    app = None
    try:
        app = _build_application()
        _log.info(
            "Polling starting pid=%s hostname=%s deployment=%s application=%s",
            identity["pid"], identity["hostname"], identity["deployment"], id(app),
        )
        app.run_polling(drop_pending_updates=True, bootstrap_retries=0)
    finally:
        _log.info(
            "Process stopping pid=%s hostname=%s deployment=%s",
            identity["pid"], identity["hostname"], identity["deployment"],
        )
        lease.release()


if __name__ == "__main__":
    main()
