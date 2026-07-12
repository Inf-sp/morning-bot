import asyncio
import logging

_log = logging.getLogger(__name__)
from telegram.ext import (Application, CommandHandler, MessageHandler, filters,
                          ContextTypes, CallbackQueryHandler, PollAnswerHandler)
from datetime import datetime, timezone
from pathlib import Path

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
import memory
import onboard
import firstvisit
import tracking
import util
from ui import admin as admin_ui
from util import ack_loading as _ack
from util import clear_loading as _unack

TZ = config.TZ
CHAT_ID = config.CHAT_ID



_WELCOME = menu.WELCOME
_ROOT = Path(__file__).parent
_DEFAULT_DEPLOY_NOTE = "Бот получил небольшие внутренние улучшения."
_DEFAULT_DEPLOY_TITLE = "Обновление"
_WORRY_PROMPT_WINDOW_S = 1800  # окно, в течение которого свободный текст ещё считается ответом на "Дневную разгрузку"


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
        return line[2:].strip()
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
    clean_notes = [str(note).strip() for note in (release_notes or []) if str(note).strip()]
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


async def start(update, context):
    cid = str(update.effective_chat.id)
    args = context.args or []

    # Инвайт-код передан через /start <code>
    if args:
        code = args[0].strip()
        if access.is_allowed(cid):
            await update.message.reply_text(_WELCOME, entities=menu.WELCOME_ENTITIES, reply_markup=menu.main_menu_kb())
            return
        if access.use_invite(code, cid):
            await onboard.start(context.bot, cid)
            return
        await update.message.reply_text("❌ Инвайт-код недействителен или устарел.")
        return

    if not access.is_allowed(cid):
        await update.message.reply_text("❌ Бот приватный. Попроси владельца прислать инвайт.")
        return

    await update.message.reply_text(_WELCOME, entities=menu.WELCOME_ENTITIES, reply_markup=menu.main_menu_kb())


# ---------- Диспетчер инлайн-кнопок ----------
async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data
    bot = context.bot

    async def _inline_status(call):
        status = await util.StatusManager.start_inline(q, bot=bot, cid=cid)
        try:
            return await call(status)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
            return None
        finally:
            await status.stop(delete=False)

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="❌ Бот приватный. Попроси владельца прислать инвайт.")
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
                             "as_daycheck", "as_motiv", "as_doctor")):
            await balance.handle_callback(bot, cid, q, data)
        else:
            await settings.handle_notes_callback(bot, cid, q, data)
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
    if data == "set_learning" or data.startswith("toggle_learning_language") or data.startswith("set_learning_level_"):
        try:
            await learning.handle_learning_settings_callback(bot, cid, q, data)
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
            await q.message.edit_text("Готово.")
        except Exception:
            pass
        return
    if data == "m_notes":
        await settings.send_notes(bot, cid); return
    if data == "m_food_gen":
        await _inline_status(lambda status: balance.send_recipe_featured(bot, cid, status=status)); return
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
    if data == "m_wardrobe":
        await wardrobe.send_home(bot, cid, q); return
    if data == "m_travel":
        await travel.send_home(bot, cid, q); return
    if data == "m_myday":
        await myday.send_plany(bot, cid); return
    if data == "m_menu":
        text, entities, kb = menu.main_menu_screen()
        try:
            await q.message.edit_text(text, reply_markup=kb, entities=entities)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb, entities=entities)
        return
    if data.startswith("m_"):
        text, entities, kb = menu.menu_screen(data, cid)
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
                await _inline_status(lambda _s: myday.send_plany(bot, cid))
            elif act == "train":
                await learning.send_train_lang_select(bot, cid)
            elif act in ("train_nl", "train_en"):
                await _inline_status(lambda _s: learning.train_start(bot, cid, learning.active_language(cid)))
            elif act == "tr_nl":
                await _inline_status(lambda _s: learning.do_translate(bot, cid, "нидерландский"))
            elif act == "tr_en":
                await _inline_status(lambda _s: learning.do_translate(bot, cid, "английский"))
            elif act in ("proverb", "proverb_nl", "proverb_en"):
                language = act.rsplit("_", 1)[-1] if act in ("proverb_nl", "proverb_en") else None
                await _inline_status(lambda _s: learning.send_proverb(bot, cid, language))
            elif act == "dict":
                await learning.send_dict(bot, cid, q=q)
            elif act == "dictconfirm_add":
                await _ack(q)
                await learning.confirm_pending_dict_add(bot, cid)
                await _unack(q)
            elif act == "dictconfirm_retry":
                await _ack(q)
                await learning.retry_pending_dict_add(bot, cid)
                await _unack(q)
            elif act == "dictconfirm_cancel":
                await learning.cancel_pending_dict_add(bot, cid)
            elif act == "dictbatch_add":
                await _ack(q)
                await learning.confirm_dict_batch(bot, cid)
                await _unack(q)
            elif act == "dictbatch_cancel":
                await _ack(q)
                await learning.cancel_dict_batch(bot, cid)
                await _unack(q)
            elif act.startswith("dictseed_start_"):
                await learning.seed_start(bot, cid, act.split("_")[-1], q=q)
            elif act.startswith("dictseed_phrases_"):
                await learning.seed_start(bot, cid, act.split("_")[-1], kind="phrase", q=q)
            elif act.startswith("dictseed_toggle_"):
                await learning.seed_toggle(bot, cid, int(act.split("_")[-1]), q=q)
            elif act.startswith("dictseed_page_"):
                await learning.seed_page(bot, cid, int(act.split("_")[-1]), q=q)
            elif act == "dictseed_add":
                await learning.seed_add_selected(bot, cid, q=q)
            elif act == "dictseed_later":
                await learning.seed_later(bot, cid)
            elif act == "dictseed_level":
                await learning.seed_choose_level(bot, cid, q=q)
            elif act.startswith("dictseedlvl_"):
                _, lang, level = act.split("_", 2)
                await learning.seed_set_level(bot, cid, lang, level, q=q)
            elif act == "dictlang_nl":
                await learning.send_dict_lang(bot, cid, "nl", q=q)
            elif act == "dictlang_en":
                await learning.send_dict_lang(bot, cid, "en", q=q)
            elif act == "dictlang_nl_from_menu":
                await learning.send_dict_lang(bot, cid, "nl", back="m_learn", q=q)
            elif act == "dictlang_en_from_menu":
                await learning.send_dict_lang(bot, cid, "en", back="m_learn", q=q)
            elif act.startswith("dictadd_smart_"):
                lang = act.split("_")[2]
                await learning.send_dict_manage(bot, cid, lang, q=q)
            elif act.startswith("dictadd_"):
                lang = act.split("_")[1]
                store.pending_input[cid] = f"dictadd_{lang}"
                await bot.send_message(chat_id=cid, text=(
                    "✏️ Пришли слова или фразы - можно сразу много, каждую с новой строки.\n"
                    "Я сам приведу в правильную форму, переведу и разберу."))
            elif act.startswith("dictsearch_"):
                lang = act.split("_")[1]
                await learning.send_dict_search_prompt(bot, cid, lang, q=q)
            elif act.startswith("dictviewdel_"):
                _, lang, page, term_key = act.split("_", 3)
                await learning.del_dict_entry_by_term(bot, cid, lang, term_key, page=int(page), q=q)
            elif act.startswith("dictview_"):
                _, lang, page, term_key = act.split("_", 3)
                await learning.send_dict_entry_view(bot, cid, lang, int(page), term_key, q=q)
            elif act.startswith("dictdelok_"):
                _, lang, term_key = act.split("_", 2)
                await learning.del_dict_entry_by_term(bot, cid, lang, term_key, q=q)
            elif act.startswith("dictdel_"):
                _, lang, term_key = act.split("_", 2)
                await learning.confirm_delete_dict_entry(bot, cid, lang, term_key, q=q)
            elif act.startswith("dicteditpage_"):
                rest = act[len("dicteditpage_"):]
                lang, page = rest.rsplit("_", 1)
                await learning.send_dict_manage(bot, cid, lang, page=int(page), q=q)
            elif act.startswith("dictedit_"):
                rest = act[len("dictedit_"):]
                if "_" in rest:
                    lang, page = rest.rsplit("_", 1)
                    await learning.send_dict_manage(bot, cid, lang, page=int(page), q=q)
                else:
                    await learning.send_dict_manage(bot, cid, rest, q=q)
            elif act == "game":
                await learning.game_start(bot, cid)
            elif act == "levels":
                await learning.send_levels(bot, cid, back="m_learn")
            elif act == "w_week":
                await _inline_status(lambda _s: weather.send_weather(bot, cid, "week"))
            elif act == "setcity":
                store.pending_input[cid] = "setcity"
                await bot.send_message(chat_id=cid, text="🌍 Напиши название города - переключу на него!")
            elif act == "trav_go":
                await _inline_status(lambda _s: travel.send_go(bot, cid))
            elif act == "trav_no":
                await _inline_status(lambda _s: travel.travel_dislike(bot, cid))
            elif act == "trav_plan":
                await _inline_status(lambda _s: travel.send_plan(bot, cid))
            elif act == "trav_fav":
                await _inline_status(lambda _s: travel.travel_fav(bot, cid))
            elif act == "trav_save":
                await _inline_status(lambda _s: travel.save_plan(bot, cid))
            elif act == "watch":
                await _ack(q); await leisure.send_movie_home(bot, cid, q)
            elif act == "read":
                await _inline_status(lambda _s: leisure.send_recos(bot, cid, "book"))
            elif act == "watchlist":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="a_watch")
            elif act == "readlist":
                await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
            elif act == "watchclean":
                await cleanup.open_collection(bot, cid, "cinema_favorites", back="a_watch")
            elif act == "readclean":
                await cleanup.open_collection(bot, cid, "books_saved", back="a_read")
            elif act == "concerts_find":
                await _inline_status(lambda _s: leisure.find_concerts(bot, cid, "home"))
            elif act == "concerts_pick":
                await leisure.concert_pick_country(bot, cid)
            elif act in ("concerts_nl", "concerts_be", "concerts_de", "concerts_fr", "concerts_gb",
                         "concerts_es", "concerts_it", "concerts_at", "concerts_ch",
                         "concerts_pl", "concerts_se", "concerts_dk", "concerts_pt"):
                await _inline_status(lambda _s: leisure.find_concerts(bot, cid, act.split("_")[1]))
            elif act == "listen":
                await _inline_status(lambda _s: leisure.send_listen(bot, cid))
            elif act == "listen_no":
                await _inline_status(lambda _s: leisure.listen_dislike(bot, cid))
            elif act in ("food_breakfast", "recipe_breakfast"):
                await _inline_status(lambda status: balance.enter_meal(bot, cid, "breakfast", status=status))
            elif act in ("food_lunch", "recipe_lunch"):
                await _inline_status(lambda status: balance.enter_meal(bot, cid, "lunch", status=status))
            elif act in ("food_dinner", "recipe_dinner"):
                await _inline_status(lambda status: balance.enter_meal(bot, cid, "dinner", status=status))
        except Exception as e:
            await verify.safe_error(bot, cid, e)
        return

    # Тренажёр слов
    if data.startswith("train_"):
        sub = data[len("train_"):]
        if sub == "next":
            await _inline_status(lambda _s: learning.train_next(bot, cid))
        return
    # Тренажёр фраз: переход от учебной карточки к тесту
    if data == "phrase_intro_test":
        await _inline_status(lambda _s: learning.phrase_intro_continue(bot, cid))
        return
    if data == "phrase_intro_mastered":
        await _inline_status(lambda _s: learning.phrase_intro_mastered(bot, cid))
        return
    if data == "phrase_new_example":
        await _inline_status(lambda _s: learning.phrase_new_example(bot, cid))
        return
    if data == "phrase_explain":
        await _inline_status(lambda _s: learning.phrase_explain(bot, cid))
        return
    if data in ("phrase_tf_yes", "phrase_tf_no"):
        await _inline_status(lambda _s: learning.phrase_truefalse_answer(bot, cid, data == "phrase_tf_yes"))
        return
    # Диалоговый тренажёр
    if data == "dlg_start":
        await _inline_status(lambda _s: learning.dialogue_start(bot, cid))
        return
    if data.startswith("dlg_pick_"):
        opt_idx = int(data[len("dlg_pick_"):])
        await _inline_status(lambda _s: learning.dialogue_pick(bot, cid, opt_idx))
        return
    if data == "dlg_next":
        await _inline_status(lambda _s: learning.dialogue_next(bot, cid))
        return
    # Повторение ошибок (mistakeReview)
    if data == "mistake_review":
        await _inline_status(lambda _s: learning.send_mistake_review(bot, cid))
        return
    if data.startswith("mistake_retry_"):
        mid = data[len("mistake_retry_"):]
        await _inline_status(lambda _s: learning.mistake_retry(bot, cid, mid))
        return
    if data.startswith("mistake_understood_"):
        mid = data[len("mistake_understood_"):]
        await _inline_status(lambda _s: learning.mistake_understood(bot, cid, mid))
        return
    # Умное раскрытие ответа
    if data == "smart_hint":
        await learning.smart_reveal_show_hint(bot, cid, q)
        return
    if data == "smart_answer":
        await learning.smart_reveal_ask_answer(bot, cid)
        return
    if data == "smart_skip":
        await _inline_status(lambda _s: learning.smart_reveal_skip(bot, cid))
        return
    if data == "smart_understood":
        await learning.smart_reveal_understood(bot, cid)
        return
    if data == "smart_later":
        await learning.smart_reveal_later(bot, cid)
        return
    # «Ещё»
    if data.startswith("again_"):
        what = data[len("again_"):]
        if what == "tr_nl":
            await _inline_status(lambda _s: learning.do_translate(bot, cid, "нидерландский"))
        elif what == "tr_en":
            await _inline_status(lambda _s: learning.do_translate(bot, cid, "английский"))
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
        await _inline_status(lambda _s: learning.send_game(bot, cid))
        return
    if data == "noop":
        return
    if data.startswith(("clt:", "clp:", "cla:", "clx:", "cld:", "cldc:", "clact:", "clactc:", "clcancel:")):
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
        await _inline_status(lambda _s: learning.send_game(bot, cid))
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
        await _inline_status(lambda _s: leisure.send_recos(bot, cid, "movie"))
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
        await _inline_status(lambda _s: leisure.send_movie_by_genre(bot, cid, data[len("movie_g_"):]))
        return
    if data.startswith("movie_mood_"):
        await _inline_status(lambda _s: leisure.send_movie_by_mood(bot, cid, data[len("movie_mood_"):]))
        return
    if data.startswith("movie_love_"):
        await _inline_status(lambda _s: leisure.movie_love(bot, cid, int(data.split("_")[-1])))
        return
    if data.startswith("book_love_"):
        await _inline_status(lambda _s: leisure.book_love(bot, cid, int(data.split("_")[-1])))
        return
    if data == "listen_love":
        await _inline_status(lambda _s: leisure.listen_love(bot, cid))
        return
    if data.startswith("reco_"):
        await _inline_status(lambda _s: leisure.add_reco(bot, cid, int(data.split("_")[1])))
        return
    if data.startswith("movie_no_"):
        await _inline_status(lambda _s: leisure.movie_dislike(bot, cid, int(data.split("_")[-1])))
        return
    if data.startswith("book_no_"):
        await _inline_status(lambda _s: leisure.book_dislike(bot, cid, int(data.split("_")[-1])))
        return
    if data.startswith("listen_"):
        await _inline_status(lambda _s: leisure.add_listen(bot, cid, int(data.split("_")[1])))
        return
    # Проверка дня (тревоги)
    if data == "worry_clearall":
        await balance.worry_clear_all(bot, cid)
        return
    # «Продолжить / ещё раз»
    if data == "chat_retry":
        await _inline_status(lambda _s: balance.retry(bot, cid))
        return
    # «Короче / Глубже» - переписать последний ответ
    if data in ("ans_short", "ans_deep"):
        await _inline_status(lambda _s: balance.reword(bot, cid, "short" if data == "ans_short" else "deep"))
        return


# ---------- Текстовый роутер ----------
async def text_router(update, context):
    cid = str(update.effective_chat.id)
    text = secure.clamp(update.message.text)        # лимит длины + чистка невидимых/управляющих
    bot = context.bot

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="❌ Бот приватный. Попроси владельца прислать инвайт.")
        return
    tracking.touch(cid)

    flags = secure.injection_flags(text)
    if flags:
        _log.warning("[secure] injection flags: %s", flags)

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
    if cid in store.smart_reveal_state and store.pending_input.get(cid) == "smart_reveal_answer":
        if await learning.smart_reveal_answer(bot, cid, text):
            return

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.pop(cid)
        if kind == "worry":
            worry_ts = settings.get(cid, "_worry_prompt_ts", 0)
            stale = worry_ts and (datetime.now(config.TZ).timestamp() - worry_ts) >= _WORRY_PROMPT_WINDOW_S
            if not stale:
                _log.info("worry: routed via pending_input for cid=%s", cid)
                await balance.save_worries(bot, cid, text); return
            settings.set_(cid, "_worry_prompt_ts", 0)
            # застрявший pending_input от старого приглашения "Дневная разгрузка" -
            # не глотаем никак не связанное сообщение, продолжаем обычную обработку ниже
        if kind in ("role_doctor", "role_state"):
            await balance.handle_role(bot, cid, kind.split("_")[1], text); return
        if kind == "wardrobe_add":
            await wardrobe.add_item(bot, cid, text); return
        if kind == "wardrobe_add_set":
            await wardrobe.add_item_settings(bot, cid, text)
            await wardrobe.send_wardrobe_zones(bot, cid); return
        if kind == "wardrobe_search":
            await wardrobe.handle_wardrobe_search(bot, cid, text); return
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
        if kind.startswith("dictsearch_"):
            await learning.handle_dict_search(bot, cid, kind.split("_")[1], text); return
        if kind == "wardrobe_constraints_input":
            settings.set_(cid, "wardrobe_constraints", text.strip())
            await bot.send_message(chat_id=cid, text="🎚️ <b>Ограничения сохранены</b>", parse_mode="HTML")
            await settings.send_wardrobe_prefs(bot, cid); return
        if kind == "wardrobe_colors_love_input":
            settings.set_(cid, "wardrobe_colors_love", settings.normalize_colors(text))
            await bot.send_message(chat_id=cid, text="Любимые цвета сохранены.")
            await settings.send_wardrobe_prefs(bot, cid); return
        if kind == "wardrobe_colors_avoid_input":
            settings.set_(cid, "wardrobe_colors_avoid", settings.normalize_colors(text))
            await bot.send_message(chat_id=cid, text="Нежелательные цвета сохранены.")
            await settings.send_wardrobe_prefs(bot, cid); return
        if kind == "styleinput":
            custom = text.strip()
            if custom:
                settings.set_(cid, "wardrobe_constraints",
                    (settings.get(cid, "wardrobe_constraints", "") + " " + custom).strip())
            await bot.send_message(chat_id=cid, text="Стиль сохранён в ограничениях.")
            await settings.send_wardrobe_prefs(bot, cid); return
        if kind.startswith("fridge_add"):
            try:
                ci = int(kind.split("_")[-1])
            except (ValueError, IndexError):
                ci = -1
            await balance.fridge_add_done(bot, cid, text, ci); return
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
    if worry_ts and (datetime.now(config.TZ).timestamp() - worry_ts) < _WORRY_PROMPT_WINDOW_S:
        settings.set_(cid, "_worry_prompt_ts", 0)
        _log.info("worry: routed via fallback timestamp for cid=%s", cid)
        await balance.save_worries(bot, cid, text); return

    # Быстрая команда из чата: «добавь в словарь слово de Aandacht - внимание»
    if await learning.try_add_dict_from_chat(bot, cid, text):
        return
    # Быстрая команда из чата: «добавь в продукты крахмал»
    if await balance.try_add_fridge_from_chat(bot, cid, text):
        return
    # Быстрая команда из чата: «добавь в любимые фильм Дюна»
    if await assistant.try_add_love_from_chat(bot, cid, text):
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

async def menu_command(update, context):
    cid = str(update.effective_chat.id)
    store.pending_input.pop(cid, None)
    text, entities, kb = menu.main_menu_screen()
    await update.message.reply_text(text, entities=entities, reply_markup=kb)


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
            await leisure.refresh_concerts_cache(cid)
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
        BotCommand("start", "начало"),
        BotCommand("menu", "меню"),
        BotCommand("setup", "настройки"),
        BotCommand("admin", "администратор"),
    ])
    await maybe_send_admin_deploy_notification(app.bot)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    def _t(hm):
        return datetime.strptime(hm, "%H:%M").replace(tzinfo=TZ).timetz()
    jq.run_daily(job_warm_weather_cache, time=_t("08:10"), days=tuple(range(7)))   # прогрев погоды перед брифом
    jq.run_daily(job_morning_brief,   time=_t("08:30"), days=tuple(range(7)))   # Утро: Мой день + погода + мотивация
    jq.run_daily(job_weather_warn,    time=_t("08:45"), days=tuple(range(7)))   # экстренное предупреждение, если нужно
    jq.run_daily(job_refresh_concerts_cache, time=_t("09:50"), days=(4,))      # пт, прогрев кэша концертов
    jq.run_daily(job_weekend_events,  time=_t("10:00"), days=(4,))             # пт, «Куда сходить»
    jq.run_daily(job_daily_words,     time=_t("11:00"), days=tuple(range(7)))  # «Практика языка»
    jq.run_daily(job_checkin_day,     time=_t("14:00"), days=tuple(range(7)))
    jq.run_daily(job_evening_weather, time=_t("19:00"), days=tuple(range(7)))  # «Погода на завтра»
    jq.run_daily(job_checkin_evening, time=_t("21:30"), days=tuple(range(7)))

    logging.info("Bot started via polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
