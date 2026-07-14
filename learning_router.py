"""Callback-маршруты раздела «Обучение».

Модуль знает о callback_data и Telegram-переходах, а learning.py
остаётся модулем сценариев и бизнес-логики раздела.
"""

import learning
import store
import util


async def handle_callback(bot, cid, data, run_with_status):
    """Callback-и заданий тренажёра с префиксом ex_."""
    if data == "ex_next":
        await run_with_status(lambda _s: learning.train_next(bot, cid))
    elif data.startswith("ex_pick_"):
        await run_with_status(lambda _s: learning.handle_pick(
            bot, cid, int(data[len("ex_pick_"):])))
    elif data == "ex_hint":
        await learning.handle_hint(bot, cid)
    elif data == "ex_answer":
        await learning.handle_answer_prompt(bot, cid)
    elif data == "ex_giveup":
        await run_with_status(lambda _s: learning.handle_giveup(bot, cid))
    elif data.startswith("ex_tok_"):
        token = data[len("ex_tok_"):]
        if token == "reset":
            await learning.handle_token_reset(bot, cid)
        else:
            await run_with_status(lambda _s: learning.handle_token_pick(
                bot, cid, int(token)))
    elif data.startswith("ex_word_"):
        await run_with_status(lambda _s: learning.handle_token_pick(
            bot, cid, int(data[len("ex_word_"):])))
    else:
        return False
    return True


async def handle_action(bot, cid, q, act, run_with_status):
    """Действия раздела «Обучение» из общего префикса a_."""
    if act == "train":
        await learning.send_train_lang_select(bot, cid)
    elif act in ("train_nl", "train_en"):
        await run_with_status(lambda _s: learning.train_start(
            bot, cid, learning.active_language(cid)))
    elif act == "train_progress":
        await run_with_status(lambda _s: learning.send_progress(bot, cid))
    elif act == "tr_nl":
        await run_with_status(lambda _s: learning.do_translate(
            bot, cid, "нидерландский"))
    elif act == "tr_en":
        await run_with_status(lambda _s: learning.do_translate(
            bot, cid, "английский"))
    elif act in ("proverb", "proverb_nl", "proverb_en"):
        language = act.rsplit("_", 1)[-1] if act in ("proverb_nl", "proverb_en") else None
        await run_with_status(lambda _s: learning.send_proverb(bot, cid, language))
    elif act == "dict":
        await learning.send_dict(bot, cid, q=q)
    elif act == "dictconfirm_add":
        await util.ack_loading(q)
        await learning.confirm_pending_dict_add(bot, cid)
        await util.clear_loading(q)
    elif act == "dictconfirm_retry":
        await util.ack_loading(q)
        await learning.retry_pending_dict_add(bot, cid)
        await util.clear_loading(q)
    elif act == "dictconfirm_cancel":
        await learning.cancel_pending_dict_add(bot, cid)
    elif act == "dictbatch_add":
        await util.ack_loading(q)
        await learning.confirm_dict_batch(bot, cid)
        await util.clear_loading(q)
    elif act == "dictbatch_cancel":
        await util.ack_loading(q)
        await learning.cancel_dict_batch(bot, cid)
        await util.clear_loading(q)
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
    elif act in ("dictlang_nl", "dictlang_en"):
        await learning.send_dict_lang(bot, cid, act.rsplit("_", 1)[-1], q=q)
    elif act.startswith(("dictlang_nl_from_", "dictlang_en_from_")):
        lang = "nl" if act.startswith("dictlang_nl_") else "en"
        origin = act[len(f"dictlang_{lang}_from_"):]
        await learning.send_dict_lang(
            bot, cid, lang,
            back=learning._DICT_ORIGIN_TO_BACK.get(origin, "m_learn"), q=q)
    elif act.startswith("dictadd_smart_"):
        await learning.send_dict_manage(bot, cid, act.split("_")[2], q=q)
    elif act.startswith("dictadd_"):
        lang = act.split("_")[1]
        store.pending_input[str(cid)] = f"dictadd_{lang}"
        await bot.send_message(chat_id=cid, text=(
            "✏️ Пришли слова или фразы - можно сразу много, каждую с новой строки.\n"
            "Я сам приведу в правильную форму, переведу и разберу."))
    elif act.startswith("dictsearch_"):
        await learning.send_dict_search_prompt(bot, cid, act.split("_")[1], q=q)
    elif act.startswith("dictviewdel_"):
        _, lang, page, term_key = act.split("_", 3)
        await learning.del_dict_entry_by_term(
            bot, cid, lang, term_key, page=int(page), q=q)
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
        lang, page = act[len("dicteditpage_"):].rsplit("_", 1)
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
    else:
        return False
    return True
