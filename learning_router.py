"""Callback-маршруты раздела «Обучение».

Модуль знает о callback_data и Telegram-переходах, а learning.py
остаётся модулем сценариев и бизнес-логики раздела.
"""

import learning
import learning_dictionary as dictionary
import dictionary_import
import dictionary_seed
import learning_game as game
import learning_settings as learning_preferences
import live_language
import store
import trainer
import util


async def handle_callback(bot, cid, data, run_with_status):
    """Callback-и заданий тренажёра с префиксом ex_."""
    if data == "ex_next":
        await run_with_status(lambda _s: trainer.next_exercise(bot, cid))
    elif data.startswith("ex_pick_"):
        await run_with_status(lambda _s: trainer.pick_option(
            bot, cid, int(data[len("ex_pick_"):])))
    elif data == "ex_hint":
        await trainer.show_hint(bot, cid)
    elif data == "ex_answer":
        await trainer.request_text_answer(bot, cid)
    elif data == "ex_giveup":
        await run_with_status(lambda _s: trainer.give_up(bot, cid))
    elif data.startswith("ex_tok_"):
        token = data[len("ex_tok_"):]
        if token == "reset":
            await trainer.reset_tokens(bot, cid)
        else:
            await run_with_status(lambda _s: trainer.pick_token(
                bot, cid, int(token)))
    elif data.startswith("ex_word_"):
        await run_with_status(lambda _s: trainer.pick_token(
            bot, cid, int(data[len("ex_word_"):])))
    else:
        return False
    return True


async def handle_action(bot, cid, q, act, run_with_status):
    """Действия раздела «Обучение» из общего префикса a_."""
    if act == "train":
        await learning.send_train_lang_select(bot, cid)
    elif act in ("train_nl", "train_en"):
        await run_with_status(lambda _s: trainer.start(
            bot, cid, learning.active_language(cid)))
    elif act == "train_progress":
        await run_with_status(lambda _s: trainer.send_progress(bot, cid))
    elif act in ("proverb", "proverb_nl", "proverb_en"):
        language = act.rsplit("_", 1)[-1] if act in ("proverb_nl", "proverb_en") else None
        await run_with_status(lambda _s: live_language.send_proverb(bot, cid, language))
    elif act == "dict":
        await dictionary.send_dict(bot, cid, q=q)
    elif act == "dictconfirm_add":
        await util.ack_loading(q)
        await dictionary_import.confirm_pending_dict_add(bot, cid)
        await util.clear_loading(q)
    elif act == "dictconfirm_retry":
        await util.ack_loading(q)
        await dictionary_import.retry_pending_dict_add(bot, cid)
        await util.clear_loading(q)
    elif act == "dictconfirm_cancel":
        await dictionary_import.cancel_pending_dict_add(bot, cid)
    elif act == "dictdone":
        store.pending_input.pop(str(cid), None)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        message_id = getattr(getattr(q, "message", None), "message_id", None)
        if store.last_inline_message.get(str(cid)) == message_id:
            store.last_inline_message.pop(str(cid), None)
    elif act == "dictbatch_add":
        await util.ack_loading(q)
        await dictionary_import.confirm_dict_batch(bot, cid)
        await util.clear_loading(q)
    elif act == "dictbatch_cancel":
        await util.ack_loading(q)
        await dictionary_import.cancel_dict_batch(bot, cid)
        await util.clear_loading(q)
    elif act.startswith("dictseed_start_"):
        await dictionary_seed.seed_start(bot, cid, act.split("_")[-1], q=q)
    elif act.startswith("dictseed_phrases_"):
        await dictionary_seed.seed_start(bot, cid, act.split("_")[-1], kind="phrase", q=q)
    elif act.startswith("dictseed_toggle_"):
        await dictionary_seed.seed_toggle(bot, cid, int(act.split("_")[-1]), q=q)
    elif act.startswith("dictseed_page_"):
        await dictionary_seed.seed_page(bot, cid, int(act.split("_")[-1]), q=q)
    elif act == "dictseed_add":
        await dictionary_seed.seed_add_selected(bot, cid, q=q)
    elif act == "dictseed_later":
        await dictionary_seed.seed_later(bot, cid)
    elif act == "dictseed_level":
        await dictionary_seed.seed_choose_level(bot, cid, q=q)
    elif act.startswith("dictseedlvl_"):
        _, lang, level = act.split("_", 2)
        await dictionary_seed.seed_set_level(bot, cid, lang, level, q=q)
    elif act in ("dictlang_nl", "dictlang_en"):
        await dictionary.send_dict_lang(bot, cid, act.rsplit("_", 1)[-1], q=q)
    elif act.startswith(("dictlang_nl_from_", "dictlang_en_from_")):
        lang = "nl" if act.startswith("dictlang_nl_") else "en"
        origin = act[len(f"dictlang_{lang}_from_"):]
        await dictionary.send_dict_lang(
            bot, cid, lang,
            back=dictionary._DICT_ORIGIN_TO_BACK.get(origin, "m_learn"), q=q)
    elif act.startswith("dictadd_smart_"):
        await dictionary.send_dict_manage(bot, cid, act.split("_")[2], q=q)
    elif act.startswith("dictadd_"):
        lang = act.split("_")[1]
        store.pending_input[str(cid)] = f"dictadd_{lang}"
        await bot.send_message(chat_id=cid, text=(
            "✏️ Пришли слова или фразы - можно сразу много, каждую с новой строки.\n"
            "Я сам приведу в правильную форму, переведу и разберу."))
    elif act.startswith("dictsearch_"):
        await dictionary.send_dict_search_prompt(bot, cid, act.split("_")[1], q=q)
    elif act.startswith("dictviewdel_"):
        _, lang, page, term_key = act.split("_", 3)
        await dictionary.del_dict_entry_by_term(
            bot, cid, lang, term_key, page=int(page), q=q)
    elif act.startswith("dictview_"):
        _, lang, page, term_key = act.split("_", 3)
        await dictionary.send_dict_entry_view(bot, cid, lang, int(page), term_key, q=q)
    elif act.startswith("dictdelok_"):
        _, lang, term_key = act.split("_", 2)
        await dictionary.del_dict_entry_by_term(bot, cid, lang, term_key, q=q)
    elif act.startswith("dictdel_"):
        _, lang, term_key = act.split("_", 2)
        await dictionary.confirm_delete_dict_entry(bot, cid, lang, term_key, q=q)
    elif act.startswith("dicteditpage_"):
        lang, page = act[len("dicteditpage_"):].rsplit("_", 1)
        await dictionary.send_dict_manage(bot, cid, lang, page=int(page), q=q)
    elif act.startswith("dictedit_"):
        rest = act[len("dictedit_"):]
        if "_" in rest:
            lang, page = rest.rsplit("_", 1)
            await dictionary.send_dict_manage(bot, cid, lang, page=int(page), q=q)
        else:
            await dictionary.send_dict_manage(bot, cid, rest, q=q)
    elif act == "game":
        await game.game_start(bot, cid)
    elif act == "levels":
        await learning_preferences.send_levels(bot, cid, back="m_learn")
    else:
        return False
    return True
