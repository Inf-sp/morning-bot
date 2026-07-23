"""Маршрутизация входящих текстовых сообщений."""

import logging

import access
import assistant
import balance
import dictionary_seed
import firstvisit
import fridge
import learning_dictionary as dictionary
import dictionary_import
import learning_game
import learning_settings
import leisure_movies
import memory
import myday
import onboard
import saved_items
import secure
import settings
import store
import tracking
import trainer
import trainer_session
import travel
import verify
import wardrobe
import weather

_log = logging.getLogger(__name__)
def _looks_like_command(text):
    return str(text or "").strip().startswith("/")

async def handle(update, context, remove_reply_keyboard):
    cid = str(update.effective_chat.id)
    text = secure.clamp(update.message.text)        # лимит длины + чистка невидимых/управляющих
    bot = context.bot

    if not access.is_allowed(cid):
        await bot.send_message(chat_id=cid, text="❌ Бот приватный. Попроси владельца прислать инвайт.")
        return
    tracking.touch(cid)
    await remove_reply_keyboard(bot, cid)

    flags = secure.injection_flags(text)
    if flags:
        _log.warning("[secure] injection flags: %s", flags)

    # Режим добавления одежды (файлом)
    if store.add_wardrobe_mode.get(cid):
        balance.thoughts.cancel_capture(cid, clear_pending=False)
        await wardrobe.ingest(bot, cid, text)
        return

    # Явная команда словаря сильнее открытого режима ожидания любого раздела.
    # Например, «Добавить *twijfelt*» из экрана «Мысли» должна попасть в словарь,
    # а не сохраниться как мысль. После успешной команды старый pending сбрасываем.
    if await dictionary_import.try_add_dict_from_chat(bot, cid, text):
        previous_kind = store.pending_input.pop(cid, None)
        store.game_state.pop(cid, None)
        store.challenge_state.pop(cid, None)
        if previous_kind in ("worry", "thought", "thought_reminder"):
            balance.thoughts.cancel_capture(cid, clear_pending=False)
        return

    # Игра и перевод проверяем ПЕРЕД pending - иначе ответ уходит не туда (в дневник)
    if cid in store.game_state:
        if await learning_game.game_answer(bot, cid, text):
            balance.thoughts.cancel_capture(cid, clear_pending=False)
            return

    # Команда добавления лайфхака сильнее пассивного ожидания ответа в разделе.
    if await assistant.try_add_lifehack_from_chat(bot, cid, text):
        balance.thoughts.cancel_capture(cid)
        return

    pending_kind = store.pending_input.get(cid)
    thought_waiting = balance.thoughts.capture_waiting(cid)

    # Явные текстовые действия сильнее пассивного ожидания ответа на напоминание.
    # Активный специализированный workflow при этом остаётся первым.
    if pending_kind is None or pending_kind in balance.thoughts.CAPTURE_PENDING_KINDS:
        if await fridge.try_add_fridge_from_chat(bot, cid, text):
            balance.thoughts.cancel_capture(cid)
            return
        if await assistant.try_add_love_from_chat(bot, cid, text):
            balance.thoughts.cancel_capture(cid)
            return

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.get(cid)
        if kind in balance.thoughts.CAPTURE_PENDING_KINDS:
            if not thought_waiting:
                balance.thoughts.cancel_capture(cid)
            kind = None
        else:
            store.pending_input.pop(cid, None)
            if thought_waiting:
                balance.thoughts.cancel_capture(cid, clear_pending=False)
        if kind == trainer_session.PENDING_ANSWER:
            if await trainer.handle_text(bot, cid, text):
                return
        if kind in ("role_doctor", "role_state"):
            await balance.handle_role(bot, cid, kind.split("_")[1], text); return
        if kind == "role_medicine":
            import medicine
            await medicine.answer(bot, cid, text); return
        if kind == "wardrobe_add":
            await wardrobe.add_item(bot, cid, text); return
        if kind == "wardrobe_add_set":
            await wardrobe.add_item_settings(bot, cid, text)
            return
        if kind == "wardrobe_add_edit":
            await wardrobe.edit_add_preview(bot, cid, text); return
        if kind == "wardrobe_edit":
            await wardrobe.edit_item_text(bot, cid, text); return
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
        if kind == "trav_country_add":
            await travel.add_visited_country(bot, cid, text); return
        if kind == "concert_artist_search":
            import leisure_concerts
            await leisure_concerts.find_artist_concerts(bot, cid, text); return
        if kind and kind.startswith("dictadd_smart_"):
            await dictionary_import.add_smart_batch(bot, cid, text, kind.split("_")[2]); return
        if kind and kind.startswith("dictadd_"):
            await dictionary_import.add_words_batch(bot, cid, text, kind.split("_")[1]); return
        if kind and kind.startswith("dictsearch_"):
            await dictionary.handle_dict_search(bot, cid, kind.split("_")[1], text); return
        if kind == "styleinput":
            custom = text.strip()
            if custom:
                settings.set_(cid, "wardrobe_style_custom", custom[:200])
            await bot.send_message(chat_id=cid, text="Стиль сохранён.")
            await settings.send_wardrobe_style(bot, cid); return
        if kind and kind.startswith("fridge_add"):
            try:
                ci = int(kind.split("_")[-1])
            except (ValueError, IndexError):
                ci = -1
            await fridge.fridge_add_done(bot, cid, text, ci); return
        if kind == "setadd_lagom":
            await bot.send_message(chat_id=cid, text="Раздел «Лагом» удалён.")
            return
        if kind and kind.startswith("collect_"):
            import leisure_collection
            await leisure_collection.collect_done(bot, cid, kind[len("collect_"):], text); return
        if kind and kind.startswith("firstvisit_"):
            await firstvisit.handle_response(bot, cid, kind[len("firstvisit_"):], text); return
        if kind and kind.startswith("loveadd_"):
            await saved_items.love_add_done(bot, cid, kind[len("loveadd_"):], text); return
        if kind and kind.startswith("loveaddls_"):
            await saved_items.love_add_done(bot, cid, kind[len("loveaddls_"):], text, origin="leisure"); return

    # Fallback: pending_input мог быть сброшен при рестарте — проверяем профиль
    ob_step = onboard.get_text_step(cid)
    if ob_step == "name":
        await onboard.handle_name(bot, cid, text); return
    if ob_step == "city":
        await onboard.handle_city(bot, cid, text); return

    # Персистентное ожидание мыслей идёт после всех специализированных workflow.
    # Время хранится только как метаданные и не определяет принадлежность текста.
    if (not _looks_like_command(text)
            and balance.thoughts.claim_capture(cid)):
        _log.info("thought: routed via capture state for cid=%s", cid)
        await balance.thoughts.capture(bot, cid, text)
        return

    # Свободный чат
    await assistant.chat_reply(bot, cid, text)
