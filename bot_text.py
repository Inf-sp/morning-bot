"""Маршрутизация входящих текстовых сообщений."""

import logging
from datetime import datetime

import access
import assistant
import balance
import config
import cooking
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
_WORRY_PROMPT_WINDOW_S = 1800

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
        await wardrobe.ingest(bot, cid, text)
        return

    # Игра и перевод проверяем ПЕРЕД pending - иначе ответ уходит не туда (в дневник)
    if cid in store.game_state:
        if await learning_game.game_answer(bot, cid, text):
            return
    # Pending-ввод
    if cid in store.pending_input:
        kind = store.pending_input.pop(cid)
        if kind == "worry":
            worry_ts = settings.get(cid, "_worry_prompt_ts", 0)
            stale = worry_ts and (datetime.now(config.TZ).timestamp() - worry_ts) >= _WORRY_PROMPT_WINDOW_S
            if not stale and not _looks_like_command(text):
                _log.info("worry: routed via pending_input for cid=%s", cid)
                await balance.save_worries(bot, cid, text); return
            settings.set_(cid, "_worry_prompt_ts", 0)
            # застрявший pending_input от старого приглашения "Дневная разгрузка" -
            # не глотаем никак не связанное сообщение, продолжаем обычную обработку ниже
        if kind == trainer_session.PENDING_ANSWER:
            if await trainer.handle_text(bot, cid, text):
                return
        if kind in ("role_doctor", "role_state"):
            await balance.handle_role(bot, cid, kind.split("_")[1], text); return
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
        if kind == "trav_facts_country":
            await travel.handle_facts_country_input(bot, cid, text); return
        if kind.startswith("dictadd_smart_"):
            await dictionary_import.add_smart_batch(bot, cid, text, kind.split("_")[2]); return
        if kind.startswith("dictadd_"):
            await dictionary_import.add_words_batch(bot, cid, text, kind.split("_")[1]); return
        if kind.startswith("dictsearch_"):
            await dictionary.handle_dict_search(bot, cid, kind.split("_")[1], text); return
        if kind == "styleinput":
            custom = text.strip()
            if custom:
                settings.set_(cid, "wardrobe_style_custom", custom[:200])
            await bot.send_message(chat_id=cid, text="Стиль сохранён.")
            await settings.send_wardrobe_style(bot, cid); return
        if kind.startswith("fridge_add"):
            try:
                ci = int(kind.split("_")[-1])
            except (ValueError, IndexError):
                ci = -1
            await fridge.fridge_add_done(bot, cid, text, ci); return
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
            import leisure_collection
            await leisure_collection.collect_done(bot, cid, kind[len("collect_"):], text); return
        if kind.startswith("firstvisit_"):
            await firstvisit.handle_response(bot, cid, kind[len("firstvisit_"):], text); return
        if kind.startswith("loveadd_"):
            await saved_items.love_add_done(bot, cid, kind[len("loveadd_"):], text); return
        if kind.startswith("loveaddls_"):
            await saved_items.love_add_done(bot, cid, kind[len("loveaddls_"):], text, origin="leisure"); return

    # Fallback: pending_input мог быть сброшен при рестарте — проверяем профиль
    ob_step = onboard.get_text_step(cid)
    if ob_step == "name":
        await onboard.handle_name(bot, cid, text); return
    if ob_step == "city":
        await onboard.handle_city(bot, cid, text); return

    # Fallback: недавняя "Дневная разгрузка" — pending_input мог потеряться,
    # но персистентная метка (survives рестарт) ещё в окне — не теряем текст.
    worry_ts = settings.get(cid, "_worry_prompt_ts", 0)
    if worry_ts and (datetime.now(config.TZ).timestamp() - worry_ts) < _WORRY_PROMPT_WINDOW_S and not _looks_like_command(text):
        settings.set_(cid, "_worry_prompt_ts", 0)
        _log.info("worry: routed via fallback timestamp for cid=%s", cid)
        await balance.save_worries(bot, cid, text); return

    # Быстрая команда из чата: «добавь в словарь слово de Aandacht - внимание»
    if await dictionary_import.try_add_dict_from_chat(bot, cid, text):
        return
    # Быстрая команда из чата: «добавь в продукты крахмал»
    if await cooking.try_add_fridge_from_chat(bot, cid, text):
        return
    # Быстрая команда из чата: «добавь в любимые фильм Дюна»
    if await assistant.try_add_love_from_chat(bot, cid, text):
        return

    # Свободный чат
    await assistant.chat_reply(bot, cid, text)
