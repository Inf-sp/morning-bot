import asyncio
import logging

_log = logging.getLogger(__name__)
from telegram import InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler, PollAnswerHandler, ExtBot)
from datetime import datetime, timezone
from pathlib import Path

import config
import store
import trainer_session
import access
import menu
import assistant
import balance
import cooking
import recipe_generation
import fridge
import retry_flow
import bot_callbacks
import bot_text
import myday
import wardrobe
import learning_dictionary as dictionary
import learning_game
import learning_settings
import trainer
import learning_router
import learning
import cleanup
import settings
import saved_items
import leisure_movies
import leisure_concerts
import leisure_music
import leisure_books
import travel
import weather
import verify
import secure
import service_monitor
import onboard
import firstvisit
import tracking
import util
from ui import admin as admin_ui
from util import ack_loading as _ack
from util import clear_loading as _unack

TZ = config.TZ
CHAT_ID = config.CHAT_ID



_ROOT = Path(__file__).parent
_DEFAULT_DEPLOY_NOTE = "Бот получил небольшие внутренние улучшения."
_DEFAULT_DEPLOY_TITLE = "Обновление"
_WORRY_PROMPT_WINDOW_S = 1800  # окно, в течение которого свободный текст ещё считается ответом на "Дневную разгрузку"


class _RetryingHTTPXRequest(HTTPXRequest):
    """Отдельный пул Telegram API с одним безопасным повтором ConnectTimeout.

    Повторяем только ошибку установления соединения: запрос ещё не был отправлен,
    поэтому sendMessage не может продублироваться.
    """

    async def do_request(self, *args, **kwargs):
        try:
            return await super().do_request(*args, **kwargs)
        except TimedOut as error:
            cause = error.__cause__
            if type(cause).__name__ != "ConnectTimeout":
                raise
            _log.warning("Telegram connect timeout; retrying request once")
            await asyncio.sleep(0.25)
            return await super().do_request(*args, **kwargs)

# callback-префикс -> тема для тематических фраз ожидания (util.StatusManager.TOPIC_STAGES)
_STATUS_TOPIC_PREFIXES = (
    ("w_", "wardrobe"),
    ("m_food", "food"), ("as_food", "food"), ("as_fridge", "food"), ("as_recipe", "food"), ("as_my_recipe", "food"),
    ("a_recipe_", "food"), ("food_", "food"),
    ("a_dict", "learning"), ("a_train", "learning"), ("a_tr_", "learning"), ("a_proverb", "learning"),
    ("ex_", "learning"), ("again_tr_", "learning"), ("game", "learning"),
    ("gamelang_", "learning"), ("gamediff_", "learning"),
    ("movie_", "leisure"), ("book_", "leisure"), ("listen", "leisure"), ("reco_", "leisure"), ("a_concerts", "leisure"),
    ("m_travel", "travel"), ("a_trav_", "travel"),
    ("as_daycheck", "health"), ("as_motiv", "health"), ("as_doctor", "health"), ("as_health_", "health"), ("role_", "health"), ("ans_", "health"), ("chat_retry", "health"),
)


def _status_topic(data: str) -> str | None:
    for prefix, topic in _STATUS_TOPIC_PREFIXES:
        if data.startswith(prefix):
            return topic
    return None


def _looks_like_command(text: str) -> bool:
    """Текст похож на команду, а не на тревогу - не глотать его окном
    "Дневной разгрузки"."""
    t = (text or "").strip()
    return t.startswith("/")


def _normalize_app_version(version: str) -> str:
    version = str(version or "").strip()
    if version.lower().startswith("v") and len(version) > 1:
        return version[1:].strip()
    return version


def get_app_version() -> str:
    return _normalize_app_version(config.APP_VERSION or config._read_text_file("VERSION"))


def _release_heading(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line.startswith("## "):
        return None
    title = line[3:].strip()
    version = title.split()[0] if title else ""
    release_title = ""
    for separator in (" · ", " - ", " — "):
        if separator in title:
            release_title = title.split(separator, 1)[1].strip()
            break
    return _normalize_app_version(version), release_title


def _clean_release_note_line(line: str) -> str:
    line = line.strip()
    if line.startswith("- ") or line.startswith("* "):
        line = line[2:].strip()
    plain_line = line.strip("*_ ").casefold()
    if plain_line in {
        "бот развёрнут и работает ✅",
        "готово к развёртыванию ✅",
    }:
        return ""
    return line


def load_release_notes() -> tuple[list[str], str]:
    version = get_app_version()
    if not version:
        return [], "empty"

    path = _ROOT / "RELEASE_NOTES.md"
    if not path.exists():
        return [], "missing"

    current_lines = []
    in_current_section = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        heading = _release_heading(raw_line)
        if heading is not None:
            if in_current_section:
                break
            heading_version, _ = heading
            in_current_section = heading_version == version
            continue
        if in_current_section:
            line = _clean_release_note_line(raw_line)
            if line:
                current_lines.append(line)

    if not current_lines:
        return [], "fallback"
    return current_lines, "file"


def load_release_title(version, release_notes) -> str:
    version = _normalize_app_version(version)
    path = _ROOT / "RELEASE_NOTES.md"
    if path.exists() and version:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            heading = _release_heading(raw_line)
            if not heading:
                continue
            heading_version, heading_title = heading
            if heading_version == version and heading_title:
                return heading_title

    text = " ".join(str(note) for note in (release_notes or [])).lower()
    if not text:
        return _DEFAULT_DEPLOY_TITLE
    if any(word in text for word in ("история", "релиз", "релизов", "обновлен", "обновлений")):
        return "Чистые обновления"
    if "новост" in text:
        return "Умнее новости"
    if "эмодз" in text or "ui-словар" in text or "централизованные значки" in text:
        return "Единый UI-стиль"
    if "рецепт" in text:
        return "Быстрее рецепты"
    if "гардероб" in text:
        return "Аккуратнее гардероб"
    if "уведом" in text:
        return "Тише уведомления"
    if "обуч" in text or "словар" in text:
        return "Лучше обучение"
    return _DEFAULT_DEPLOY_TITLE


def build_deploy_report_message(version, release_notes, check_list=None):
    clean_notes = []
    for note in release_notes or []:
        line = _clean_release_note_line(str(note))
        if line:
            clean_notes.append(line)
    if not clean_notes:
        clean_notes = [_DEFAULT_DEPLOY_NOTE]
    title = load_release_title(version, clean_notes)
    return admin_ui.deploy_report(_normalize_app_version(version), title, clean_notes)


async def maybe_send_admin_deploy_notification(bot):
    version = get_app_version()
    deploy_key = version
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    release_notes, release_notes_source = load_release_notes()

    if not config.ADMIN_CHAT_ID:
        logging.warning(
            "Deploy report skipped: admin chat id is not configured app_version=%s deploy_key=%s release_notes_source=%s railway_environment=%s railway_service=%s started_at=%s result=skipped",
            version,
            deploy_key,
            release_notes_source,
            config.RAILWAY_ENVIRONMENT,
            config.RAILWAY_SERVICE_NAME,
            started_at,
        )
        return

    if not version:
        logging.warning(
            "Deploy report skipped: APP_VERSION is not configured release_notes_source=%s railway_environment=%s railway_service=%s started_at=%s result=skipped",
            release_notes_source,
            config.RAILWAY_ENVIRONMENT,
            config.RAILWAY_SERVICE_NAME,
            started_at,
        )
        return

    last_notified_version = store.get_last_admin_deploy_notified_version()
    if last_notified_version == version:
        logging.info(
            "Deploy report skipped: already sent for app_version=%s deploy_key=%s release_notes_source=%s railway_environment=%s railway_service=%s started_at=%s result=skipped",
            version,
            deploy_key,
            release_notes_source,
            config.RAILWAY_ENVIRONMENT,
            config.RAILWAY_SERVICE_NAME,
            started_at,
        )
        return

    msg = build_deploy_report_message(version, release_notes)
    try:
        await bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=msg.text, entities=msg.entities)
        store.set_last_admin_deploy_notified_version(version, sent_at)
        logging.info(
            "Deploy report sent: version=%s deploy_key=%s release_notes_source=%s railway_environment=%s railway_service=%s admin_chat_id=%s sent_at=%s result=sent",
            version,
            deploy_key,
            release_notes_source,
            config.RAILWAY_ENVIRONMENT,
            config.RAILWAY_SERVICE_NAME,
            config.ADMIN_CHAT_ID,
            sent_at,
        )
    except Exception:
        logging.exception(
            "Deploy report failed: version=%s deploy_key=%s release_notes_source=%s railway_environment=%s railway_service=%s admin_chat_id=%s started_at=%s result=failed",
            version,
            deploy_key,
            release_notes_source,
            config.RAILWAY_ENVIRONMENT,
            config.RAILWAY_SERVICE_NAME,
            config.ADMIN_CHAT_ID,
            started_at,
        )


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
    prof[menu.REPLY_KB_REMOVED_FLAG] = True
    store.set_profile(cid, prof)


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
    marker = getattr(bot, "mark_transient_message", None)
    if marker and menu.is_main_menu_markup(getattr(q.message, "reply_markup", None)):
        marker(cid, q.message.message_id)
    if access.is_allowed(cid):
        tracking.touch(cid)
    answer_task = asyncio.create_task(q.answer())
    # Даём answerCallbackQuery начать отправку до любого синхронного чтения БД
    # внутри обработчика (особенно перед Azure Speech TTS).
    await asyncio.sleep(0)
    try:
        await bot_callbacks.handle(update, context, _remove_reply_kb_once)
    except Exception as e:
        # Страховка: необработанное исключение в ветке диспетчера без собственного
        # try/except иначе оставляло пользователя с "зависшей" кнопкой и без ответа.
        await verify.safe_error(bot, cid, e)
    finally:
        try:
            await answer_task
        except Exception:
            pass



# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    bot = context.bot
    try:
        await bot_text.handle(update, context, _remove_reply_kb_once)
    except Exception as e:
        # Без этой страховки необработанное исключение внутри любой ветки роутера
        # (тренажёр, добавление в словарь и т.д.) оставляло пользователя без ответа.
        await verify.safe_error(bot, cid, e)


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
async def notes_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await saved_items.send_notes(context.bot, update.effective_chat.id)

async def setup_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await saved_items.send_notes(context.bot, update.effective_chat.id)

async def admin_command(update, context):
    store.pending_input.pop(str(update.effective_chat.id), None)
    await settings.send_admin(context.bot, update.effective_chat.id)

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


async def job_warm_weather_cache(context: ContextTypes.DEFAULT_TYPE):
    seen = set()
    for cid in access.get_allowed_cids():
        if not (settings.notif_on(cid, "morning_brief") or settings.notif_on(cid, "weather_warn")):
            continue
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
    """В 08:00 молча готовит дорогие главные экраны на день.

    Ошибка одного раздела не мешает прогреть остальные. Пользователю ничего
    не отправляется; при открытии раздела бот читает уже готовый кэш.
    """
    for cid in access.get_allowed_cids():
        steps = (
            ("wardrobe", lambda: wardrobe.warm_home_cache(cid)),
            ("myday", lambda: myday.warm_day_cache(cid)),
            ("cooking", lambda: asyncio.to_thread(recipe_generation.warm_cooking_home_ideas, cid)),
            ("learning", lambda: asyncio.to_thread(learning.warm_home_cache, cid)),
            ("travel", lambda: travel.warm_home_cache(cid)),
            ("cinema", lambda: leisure_movies.warm_movie_home_cache(cid)),
        )
        warmed = []
        for name, call in steps:
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

async def job_daily_words(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "daily_words"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "daily_words")
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

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "checkin_eve"):
            continue
        try:
            await settings.send_scheduled_notification(context.bot, cid, "checkin_eve")
        except Exception:
            logging.exception("job_checkin_evening failed for cid=%s", cid)

async def job_refresh_concerts_cache(context: ContextTypes.DEFAULT_TYPE):
    """Прогревает недельный кэш концертов перед уведомлением «Куда сходить» (10:00 пт),
    чтобы само уведомление и последующие интерактивные «Концерты» читали кэш, а не ждали Ticketmaster."""
    for cid in access.get_allowed_cids():
        if not settings.notif_on(cid, "weekend_events"):
            continue
        try:
            await leisure_concerts.refresh_concerts_cache(cid)
        except Exception:
            logging.exception("job_refresh_concerts_cache failed for cid=%s", cid)

async def job_weekend_events(context: ContextTypes.DEFAULT_TYPE):
    """«Куда сходить» — афиша недели (концерты + кино) и новые концерты любимых артистов
    одним сообщением по пятницам."""
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


async def post_init(app):
    initialized = tracking.initialize_inactivity_tracking(access.get_allowed_cids())
    if initialized:
        logging.info("Inactivity reminders: initialized %s users", initialized)
    try:
        if dictionary.migrate_dict_caps():
            logging.info("Dict caps migration: applied")
    except Exception:
        logging.exception("Dict caps migration failed")
    try:
        if leisure_movies.dedupe_lists():
            logging.info("Dedupe lists: applied")
    except Exception:
        logging.exception("Dedupe lists failed")
    try:
        if leisure_movies.seed_movies_from_content():
            logging.info("Movies seed: applied")
    except Exception:
        logging.exception("Movies seed failed")
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
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("menu", "меню"),
        BotCommand("admin", "администратор"),
    ])
    await maybe_send_admin_deploy_notification(app.bot)


class _MenuCleanupBot(ExtBot):
    """Bot, который перед каждой отправкой снимает инлайн-кнопки с предыдущего
    сообщения этого чата. Временные экраны навигации при следующей отправке
    удаляются целиком, а полезные результаты остаются в истории без кнопок."""

    def mark_transient_message(self, chat_id, message_id):
        """Помечает служебный экран для удаления, сохраняя id между рестартами."""
        if message_id:
            key = str(chat_id)
            store.transient_message[key] = message_id
            store.set_persisted_transient_message_id(key, message_id)

    def mark_persistent_inline_message(self, chat_id, message_id):
        """Не снимает кнопки с полезной карточки при следующих сообщениях бота."""
        key = str(chat_id)
        if message_id and store.last_inline_message.get(key) == message_id:
            store.last_inline_message.pop(key, None)
        if message_id and store.transient_message.get(key) == message_id:
            store.transient_message.pop(key, None)
        if message_id:
            store.clear_persisted_transient_message_id(key, message_id)

    async def _delete_transient(self, chat_id):
        key = str(chat_id)
        runtime_id = store.transient_message.pop(key, None)
        persisted_id = store.get_persisted_transient_message_id(key)
        message_ids = list(dict.fromkeys(
            msg_id for msg_id in (runtime_id, persisted_id) if msg_id
        ))
        for msg_id in message_ids:
            if store.last_inline_message.get(key) == msg_id:
                store.last_inline_message.pop(key, None)
            cleaned = False
            try:
                await self.delete_message(chat_id=chat_id, message_id=msg_id)
                cleaned = True
            except Exception:
                # Если Telegram уже не разрешает удаление, хотя бы выключаем кнопки.
                try:
                    await self.edit_message_reply_markup(
                        chat_id=chat_id, message_id=msg_id, reply_markup=None)
                    cleaned = True
                except Exception:
                    pass
            if cleaned:
                store.clear_persisted_transient_message_id(key, msg_id)

    async def _pre_send(self, chat_id):
        await self._delete_transient(chat_id)
        msg_id = store.last_inline_message.get(str(chat_id))
        if not msg_id:
            return
        store.last_inline_message.pop(str(chat_id), None)
        try:
            await self.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
        except Exception:
            pass

    def _post_send(self, chat_id, msg, transient=False, persistent_inline=False):
        if (not persistent_inline
                and isinstance(getattr(msg, "reply_markup", None), InlineKeyboardMarkup)):
            store.last_inline_message[str(chat_id)] = msg.message_id
        if transient:
            self.mark_transient_message(chat_id, msg.message_id)

    async def send_message(self, chat_id, *args, **kwargs):
        transient = kwargs.pop("transient", False)
        preserve_previous_inline = kwargs.pop("preserve_previous_inline", False)
        persistent_inline = kwargs.pop("persistent_inline", False)
        send = super().send_message(chat_id, *args, **kwargs)
        if preserve_previous_inline:
            msg = await send
        else:
            msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(
            chat_id, msg, transient=transient, persistent_inline=persistent_inline)
        return msg

    async def send_photo(self, chat_id, *args, **kwargs):
        send = super().send_photo(chat_id, *args, **kwargs)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg

    async def send_document(self, chat_id, *args, **kwargs):
        send = super().send_document(chat_id, *args, **kwargs)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg

    async def send_poll(self, chat_id, *args, **kwargs):
        send = super().send_poll(chat_id, *args, **kwargs)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # HTTPX пишет полный Telegram URL вместе с bot token — не допускаем токен в логах.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
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
    app.add_handler(MessageHandler(filters.ALL, message_activity_handler), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("setup", setup_command))
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
    jq.run_once(job_warm_home_pages, when=5)                                   # заполнить отсутствующий кэш после запуска
    jq.run_once(service_monitor.monitoring_job, when=10)                       # первая независимая проверка сервисов
    jq.run_repeating(service_monitor.monitoring_job, interval=300, first=310)  # затем каждые 5 минут
    jq.run_daily(job_warm_home_pages, time=_t("08:00"), days=tuple(range(7)))    # главные экраны без сообщений
    jq.run_daily(job_warm_weather_cache, time=_t("08:10"), days=tuple(range(7)))   # страховочный прогрев погоды перед брифом
    jq.run_daily(job_morning_brief,   time=_t("08:30"), days=tuple(range(7)))   # Утро: Мой день + погода + мотивация
    jq.run_daily(job_weather_warn,    time=_t("08:45"), days=tuple(range(7)))   # экстренное предупреждение, если нужно
    jq.run_daily(job_refresh_concerts_cache, time=_t("09:50"), days=(4,))      # пт, прогрев кэша концертов
    jq.run_daily(job_weekend_events,  time=_t("10:00"), days=(4,))             # пт, «Куда сходить»
    jq.run_daily(job_daily_words,     time=_t("11:00"), days=tuple(range(7)))  # «Слова и фразы дня»
    jq.run_daily(job_checkin_day,     time=_t("14:00"), days=tuple(range(7)))
    jq.run_daily(job_evening_weather, time=_t("20:30"), days=tuple(range(7)))  # «Прогноз на завтра»
    jq.run_daily(job_checkin_evening, time=_t("21:00"), days=tuple(range(7)))
    jq.run_daily(job_inactivity_reminders, time=_t("09:00"), days=tuple(range(7)))

    logging.info("Bot started via polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
