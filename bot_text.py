"""Маршрутизация входящих текстовых сообщений."""

import logging

import access
import assistant
import fridge
import learning_dictionary as dictionary
import dictionary_import
import onboard
import personal_collections
import secure
import settings
import learning_game
import store
import tracking
import trainer
import trainer_session
import wardrobe
import weather

_log = logging.getLogger(__name__)


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
        await wardrobe.ingest(bot, cid, text)
        return

    # «Добавь музыку / фильмы X» — явная команда библиотеки: раньше словаря, который иначе
    # принял бы «музыку X» за слово; прежний ввод снимается, как у словаря.
    if assistant._detect_love_add(text):
        store.pending_input.pop(cid, None)
        await assistant.try_add_love_from_chat(bot, cid, text)
        return

    # Сбрасываем только прежний pending: обработчик словаря может открыть новый
    # шаг уточнения перевода.
    previous_kind = store.pending_input.get(cid)
    if await dictionary_import.try_add_dict_from_chat(bot, cid, text):
        if store.pending_input.get(cid) == previous_kind:
            store.pending_input.pop(cid, None)
        learning_game.drop_round(cid)
        store.challenge_state.pop(cid, None)
        return

    # Ответ на загадку текстом — пока игра ждёт ответа (любая другая кнопка бота снимает ожидание).
    if learning_game.has_round(cid):  # раунд хранится и в профиле — переживает перезапуск
        if await learning_game.game_answer(bot, cid, text):
            return

    pending_kind = store.pending_input.get(cid)
    if pending_kind is None:
        if await fridge.try_add_fridge_from_chat(bot, cid, text):
            return

    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.get(cid)
        store.pending_input.pop(cid, None)
        if kind == trainer_session.PENDING_ANSWER:
            if await trainer.handle_text(bot, cid, text):
                return
        if kind == "wardrobe_add":
            await wardrobe.add_item(bot, cid, text); return
        if kind == "wardrobe_fill":
            await wardrobe.add_item(bot, cid, text, return_to_home=True); return
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
        if kind == "wardrobe_buy":
            await wardrobe.recommend_purchase(bot, cid, text); return
        if kind == "onboard_name":
            await onboard.handle_name(bot, cid, text); return
        if kind == "onboard_city":
            await onboard.handle_city(bot, cid, text); return
        if kind == "setcity":
            import vacation
            vacation.forget(cid)  # обычная смена города заканчивает отпуск: новый город — дом
            await weather.set_city_text(bot, cid, text); return
        if kind == "vacation_city":
            import vacation
            await vacation.city_entered(bot, cid, text); return
        if kind == "vacation_until":
            import vacation
            await vacation.until_entered(bot, cid, text); return
        if kind == "suitcase_trip":  # «Кейптаун, 20–27 октября» — чемодан в поездку
            import wardrobe_suitcase
            await wardrobe_suitcase.trip_entered(bot, cid, text); return
        if kind == "concert_artist_search":
            import leisure_concerts
            await leisure_concerts.find_artist_concerts(bot, cid, text); return
        if kind and kind.startswith("dictadd_smart_"):
            await dictionary_import.add_smart_batch(bot, cid, text, kind.split("_")[2]); return
        if kind and kind.startswith("dictadd_"):
            await dictionary_import.add_words_batch(bot, cid, text, kind.split("_")[1]); return
        if kind and kind.startswith("dictclarify_"):
            await dictionary_import.add_dict_clarification(bot, cid, text, kind.split("_")[1]); return
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
        if kind and kind.startswith("loveadd_"):
            await personal_collections.love_add_done(bot, cid, kind[len("loveadd_"):], text); return
        if kind and kind.startswith("loveaddls_"):
            await personal_collections.love_add_done(bot, cid, kind[len("loveaddls_"):], text, origin="leisure"); return

    # Fallback: pending_input мог быть сброшен при рестарте — проверяем профиль
    ob_step = onboard.get_text_step(cid)
    if ob_step == "name":
        await onboard.handle_name(bot, cid, text); return
    if ob_step == "city":
        await onboard.handle_city(bot, cid, text); return

    # Свободный чат
    await assistant.chat_reply(bot, cid, text)
