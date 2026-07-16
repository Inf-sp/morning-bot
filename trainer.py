"""Telegram-сценарий адаптивного языкового тренажёра.

Соединяет чистые engine/exercises/grading, состояние сессии, SRS и UI.
Словарные операции пока получает через learning; эта зависимость заменяется
DictionaryRepository на следующем архитектурном этапе.
"""

import asyncio
import json
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import language_tool
import secure
import srs
import store
import trainer_engine
import trainer_exercises
import trainer_grading
import trainer_session
from learning_dictionary import DictionaryRepository, entry_language, entry_term, entry_translation
from trainer_engine import (
    EXERCISE_BUILD_SENTENCE,
    EXERCISE_CHOOSE_NATURAL,
    EXERCISE_CHOOSE_REACTION,
    EXERCISE_CHOOSE_TRANSLATION,
    EXERCISE_CONTINUE_DIALOGUE,
    EXERCISE_FILL_GAP,
    EXERCISE_FIND_ERROR,
    EXERCISE_RECALL_FREE,
    EXERCISE_TRANSLATE_CONTEXT,
)
from ui import learning as learning_ui


def _learning():
    import learning
    return learning


def _new_session():
    return {"consolidated": [], "returning": [], "no_hint_count": 0, "total": 0}


def _keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, callback_data=data) for text, data in row]
        for row in rows
    ])


def _nav_row():
    return [("⬅️ Назад", "m_learn"), ("#️⃣ Меню", "m_menu")]


def _options(data):
    options = [data["correct"], *(data.get("wrong") or [])]
    random.shuffle(options)
    return options


async def _generate_situation(entry, language):
    term = entry_term(entry)
    prompt = f"""Ты методист разговорной практики для языка: {language}.
Целевое слово/фраза: «{term}» — {entry_translation(entry)}.

Придумай ОДНУ короткую реплику собеседника на {language}, в ответ на которую
естественно употребить именно «{term}».

Верни JSON: {{"line": "реплика собеседника", "line_ru": "перевод реплики"}}"""
    try:
        result = await ai.allm_json(prompt, 300, tier="cheap", module="learning_trainer")
        line = str(result.get("line") or "").strip()
        line_ru = str(result.get("line_ru") or "").strip()
    except Exception:
        return None
    return {"line": line, "line_ru": line_ru} if line else None


async def _build_exercise(cid, item):
    repository = DictionaryRepository(cid)
    entry = item["entry"]
    exercise_type = item["exercise_type"]
    language = "английский" if entry_language(entry) == "en" else "нидерландский"
    other_entries = repository.training_entries(entry_language(entry))
    situation = None
    if exercise_type in (EXERCISE_CHOOSE_REACTION, EXERCISE_CONTINUE_DIALOGUE):
        situation = await _generate_situation(entry, language)
    data = trainer_exercises.build_exercise(
        entry, other_entries, exercise_type, situation=situation)
    if data is None:
        return None
    correction = repository.correction_for(entry)
    if correction:
        data.update({
            "result_correct": correction["term"],
            "ru": correction["translation"],
            "english": correction["english"],
            "bad_translation": correction["bad_translation"],
            "unneeded_preposition": correction["unneeded_preposition"],
        })
    return data


async def start(bot, cid, language, mode=None):
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    lang_code = language if language in ("nl", "en") else ("nl" if language == "нидерландский" else "en")
    repository = DictionaryRepository(cid)
    repository.apply_known_corrections(lang_code)
    if not repository.training_entries(lang_code):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Открыть словарь", callback_data=f"a_dictlang_{lang_code}_from_menu")]])
        await bot.send_message(
            chat_id=cid,
            text=f"{'🇳🇱' if lang_code == 'nl' else '🇬🇧'} В словаре нет слов или фраз с переводом. Добавь записи через словарь.",
            reply_markup=kb,
        )
        return
    import learning_dictionary as dictionary
    await dictionary.migrate_dict_entries_for_srs(cid, lang_code)
    queue = trainer_engine.build_training_queue(repository.training_entries(lang_code))
    if not queue:
        await bot.send_message(chat_id=cid, text="Не получилось собрать тренировку. Попробуй ещё раз.")
        return
    trainer_session.start(cid, language, queue, _new_session())
    await _render_next(bot, cid)


async def _render_next(bot, cid):
    state = trainer_session.get(cid)
    if not state:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново.")
        return
    queue = state["queue"]
    while state["queue_idx"] < len(queue):
        item = queue[state["queue_idx"]]
        state["queue_idx"] += 1
        if (int(item["entry"].get("srs_level") or 0) <= 1
                and item["exercise_type"] == state.get("last_exercise_type")):
            item = {"entry": item["entry"], "exercise_type": trainer_engine.select_exercise_type(
                item["entry"], avoid=state.get("last_exercise_type", ""))}
        data = await _build_exercise(cid, item)
        if data is None and int(item["entry"].get("srs_level") or 0) <= 1:
            fallback = next(kind for kind in (
                EXERCISE_RECALL_FREE, EXERCISE_CHOOSE_TRANSLATION)
                if kind != state.get("last_exercise_type"))
            data = await _build_exercise(cid, {"entry": item["entry"], "exercise_type": fallback})
        if data is None:
            continue
        data["hint_shown"] = False
        state["current"] = data
        state["last_exercise_type"] = data["exercise_type"]
        await _send_exercise(bot, cid, data)
        return
    await _finish(bot, cid, state)


async def _send_exercise(bot, cid, data):
    kind = data["exercise_type"]
    if kind == EXERCISE_CHOOSE_TRANSLATION:
        options = _options(data)
        data["_options"] = options
        message = await bot.send_poll(
            chat_id=cid,
            question=f"Что значит: {data['term']}?",
            options=[str(option)[:100] for option in options[:10]],
            type="quiz",
            correct_option_id=options.index(data["correct"]),
            is_anonymous=False,
            reply_markup=_keyboard([_nav_row()]),
        )
        if getattr(message, "poll", None):
            trainer_session.register_poll(cid, message.poll.id)
        return
    if kind in (EXERCISE_CHOOSE_NATURAL, EXERCISE_FILL_GAP,
                EXERCISE_CHOOSE_REACTION, EXERCISE_CONTINUE_DIALOGUE):
        renderers = {
            EXERCISE_CHOOSE_NATURAL: learning_ui.exercise_choose_natural,
            EXERCISE_FILL_GAP: learning_ui.exercise_fill_gap,
            EXERCISE_CHOOSE_REACTION: learning_ui.exercise_choose_reaction,
            EXERCISE_CONTINUE_DIALOGUE: learning_ui.exercise_continue_dialogue,
        }
        options = _options(data)
        data["_options"] = options
        message = renderers[kind](data)
        rows = [[(option, f"ex_pick_{index}")] for index, option in enumerate(options)]
        rows.append(_nav_row())
        await bot.send_message(chat_id=cid, text=message.text, entities=message.entities,
                               reply_markup=_keyboard(rows))
        return
    if kind == EXERCISE_RECALL_FREE:
        message = learning_ui.exercise_recall_free(data, hint_shown=data.get("hint_shown"))
        rows = []
        if data.get("hint") and not data.get("hint_shown"):
            rows.append([("💡 Подсказка", "ex_hint"), ("✍️ Написать", "ex_answer")])
        else:
            rows.append([("✍️ Написать", "ex_answer")])
        rows.extend([[("🫪 Не помню", "ex_giveup")], _nav_row()])
    elif kind == EXERCISE_TRANSLATE_CONTEXT:
        message = learning_ui.exercise_translate_context(data)
        rows = [[("✍️ Написать", "ex_answer")], [("Показать ответ", "ex_giveup")], _nav_row()]
    elif kind == EXERCISE_BUILD_SENTENCE:
        data.setdefault("_picked", [])
        message = learning_ui.exercise_build_sentence(data)
        remaining = [(token, index) for index, token in enumerate(data["shuffled"])
                     if index not in data.get("_picked_idx", [])]
        rows = [[(token, f"ex_tok_{index}")] for token, index in remaining[:6]]
        if data.get("_picked"):
            rows.append([("↩️ Сбросить", "ex_tok_reset")])
        rows.append(_nav_row())
    elif kind == EXERCISE_FIND_ERROR:
        message = learning_ui.exercise_find_error(data)
        rows = [[(token, f"ex_word_{index}")] for index, token in enumerate(data["tokens"][:6])]
        rows.append(_nav_row())
    else:
        return
    await bot.send_message(chat_id=cid, text=message.text, entities=message.entities,
                           reply_markup=_keyboard(rows))


async def handle_poll_answer(bot, poll_answer):
    cid = trainer_session.take_poll_chat(poll_answer.poll_id)
    option_ids = list(getattr(poll_answer, "option_ids", []) or [])
    if cid and option_ids:
        await pick_option(bot, cid, int(option_ids[0]))


async def _apply_result(bot, cid, state, grade, message):
    data = state["current"]
    if data.get("_answered"):
        return
    data["_answered"] = True
    DictionaryRepository(cid).record_answer(
        data["lang"], data["term"], data["exercise_type"], grade.quality)
    session = state["session"]
    session["total"] += 1
    if grade.quality in (srs.RECALLED_FREE, srs.USED_IN_SENTENCE, srs.CONFIDENT_NO_HINT):
        session["no_hint_count"] += 1
    if grade.correct and grade.quality in (srs.USED_IN_SENTENCE, srs.CONFIDENT_NO_HINT):
        session["consolidated"].append(data["term"])
    if not grade.correct:
        session["returning"].append(data["term"])
        _reinsert_failed(state, data)
    kb = _keyboard([[("Следующее задание", "ex_next")], _nav_row()])
    await bot.send_message(chat_id=cid, text=message.text, entities=message.entities, reply_markup=kb)


def _reinsert_failed(state, data):
    entry = next((item["entry"] for item in state["queue"]
                  if entry_term(item["entry"]) == data["term"]), None)
    if entry is None:
        return
    choices = [kind for kind in trainer_engine.ALL_EXERCISES if kind != data["exercise_type"]]
    kind = random.choice(choices) if choices else data["exercise_type"]
    position = min(len(state["queue"]), state["queue_idx"] + random.randint(2, 4))
    state["queue"].insert(position, {"entry": entry, "exercise_type": kind})


async def pick_option(bot, cid, index):
    state = trainer_session.get(cid)
    if not state or not state.get("current"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново.")
        return
    data = state["current"]
    options = data.get("_options") or []
    grade = trainer_grading.grade_choice(data, index, options)
    message = learning_ui.exercise_result(
        data, grade.correct, chosen=options[index] if index < len(options) else "")
    await _apply_result(bot, cid, state, grade, message)


async def show_hint(bot, cid):
    state = trainer_session.get(cid)
    if state and state.get("current"):
        state["current"]["hint_shown"] = True
        await _send_exercise(bot, cid, state["current"])


async def request_text_answer(bot, cid):
    state = trainer_session.get(cid)
    if state and state.get("current"):
        trainer_session.expect_text_answer(cid)
        await bot.send_message(chat_id=cid, text="Напиши свой ответ следующим сообщением.",
                               reply_markup=_keyboard([_nav_row()]))


async def give_up(bot, cid):
    state = trainer_session.get(cid)
    if not state or not state.get("current"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново.")
        return
    data = state["current"]
    grade = trainer_grading.GradeResult(False, trainer_grading.AnswerQuality.NOT_REMEMBERED)
    await _apply_result(bot, cid, state, grade, learning_ui.exercise_result(data, False, chosen=""))


async def handle_text(bot, cid, text):
    state = trainer_session.get(cid)
    if not state or not state.get("current"):
        return False
    store.pending_input.pop(str(cid), None)
    data = state["current"]
    language_report = None
    if data.get("lang") == "nl":
        grade, language_report = await _grade_dutch_written(data, text)
    elif data["exercise_type"] == EXERCISE_TRANSLATE_CONTEXT:
        grade = await _grade_context(data, text)
    else:
        grade = trainer_grading.grade_free_text(
            data, text, used_hint=bool(data.get("hint_shown")))
    await _apply_result(
        bot, cid, state, grade,
        learning_ui.exercise_result(
            data, grade.correct, chosen=text, language_report=language_report,
        ))
    return True


def _language_report_for_prompt(report) -> list[dict]:
    return [
        {
            "fragment": issue.get("original") or "",
            "message": issue.get("message") or "",
            "variants": issue.get("replacements") or [],
            "rule": issue.get("rule_id") or "",
            "type": issue.get("issue_type") or "",
        }
        for issue in (report.get("issues") or [])[:4]
    ]


def _needs_ai_explanation(report) -> bool:
    for issue in report.get("issues") or []:
        issue_type = str(issue.get("issue_type") or "").lower()
        replacements = issue.get("replacements") or []
        if issue_type not in ("misspelling", "typographical") or len(replacements) != 1:
            return True
    return False


async def _explain_dutch_review(text, report, *, expected="", task="") -> dict:
    prompt = (
        "Ты кратко проверяешь спорный нидерландский ответ ученика. "
        "LanguageTool уже нашёл возможные ошибки; не повторяй его сообщение дословно. "
        "Определи, приемлем ли ответ по смыслу и грамматике, и объясни максимум одним коротким предложением.\n"
        f"Задание: {secure.wrap_untrusted(task or 'проверить нидерландский текст', 'задание')}\n"
        f"Ожидаемый вариант: {secure.wrap_untrusted(expected or 'не задан', 'эталон')}\n"
        f"Ответ: {secure.wrap_untrusted(text, 'ответ ученика')}\n"
        f"Замечания LanguageTool: {secure.wrap_untrusted(json.dumps(_language_report_for_prompt(report), ensure_ascii=False), 'замечания')}\n"
        'JSON: {"acceptable":true,"explanation":"одно понятное предложение по-русски"}'
    )
    try:
        result = await ai.allm_json(
            prompt, 350, order=("groq", "gemini"), module="learning_trainer",
        )
    except Exception:
        return {}
    return {
        "acceptable": bool(result.get("acceptable")),
        "explanation": " ".join(str(result.get("explanation") or "").split())[:240],
    }


async def _grade_dutch_written(data, text):
    local = trainer_grading.grade_free_text(
        data, text, used_hint=bool(data.get("hint_shown")),
    )
    report = await asyncio.to_thread(language_tool.check_text, text, "nl-NL")
    effective_issues = language_tool.meaningful_issues(report)
    report = {
        **report,
        "issues": effective_issues,
        "corrected_text": language_tool.apply_first_replacements(text, effective_issues),
    }
    decision = {}
    needs_semantic_judgment = (
        data.get("exercise_type") == EXERCISE_TRANSLATE_CONTEXT and not local.correct
    )
    needs_dispute_explanation = bool(
        report.get("available") and _needs_ai_explanation(report)
    )
    if needs_semantic_judgment or needs_dispute_explanation:
        decision = await _explain_dutch_review(
            text,
            report,
            expected=str(data.get("correct") or ""),
            task=str(data.get("ru") or data.get("situation") or ""),
        )
    explanation = decision.get("explanation") or _default_language_reason(report)
    report = {**report, "explanation": explanation}
    if "acceptable" in decision:
        correct = bool(decision["acceptable"])
    elif report.get("available") and effective_issues:
        correct = False
    else:
        return local, report
    if correct:
        quality = (trainer_grading.AnswerQuality.HINT_USED
                   if data.get("hint_shown") else trainer_grading.AnswerQuality.RECALLED_FREE)
    else:
        quality = trainer_grading.AnswerQuality.NOT_REMEMBERED
    return trainer_grading.GradeResult(correct, quality), report


def _default_language_reason(report) -> str:
    issues = report.get("issues") or []
    if not issues:
        return ""
    issue_type = str(issues[0].get("issue_type") or "").lower()
    if issue_type == "grammar":
        return "В ответе есть грамматическая ошибка."
    if issue_type == "misspelling":
        return "Проверь написание слова."
    if issue_type == "typographical":
        return "Проверь пробелы, регистр и пунктуацию."
    return "Эту формулировку лучше исправить."


async def _grade_context(data, text):
    local = trainer_grading.grade_free_text(data, text)
    if local.correct:
        return local
    language = "нидерландский" if data["lang"] == "nl" else "английский"
    prompt = (
        f"Ученик переводит на {language}: {secure.wrap_untrusted(data['ru'], 'фраза для перевода')}\n"
        f"Ответ ученика: {secure.wrap_untrusted(text, 'ответ ученика')}\n"
        f"Эталон: {data['correct']}\n"
        'Верни JSON: {"ok": true/false} — true, если смысл и грамматика приемлемы.'
    )
    try:
        result = await ai.allm_json(prompt, 300, tier="cheap", module="learning_trainer")
        correct = bool(result.get("ok"))
    except Exception:
        correct = False
    quality = (trainer_grading.AnswerQuality.RECALLED_FREE if correct
               else trainer_grading.AnswerQuality.NOT_REMEMBERED)
    return trainer_grading.GradeResult(correct, quality)


async def pick_token(bot, cid, index):
    state = trainer_session.get(cid)
    if not state or not state.get("current"):
        return
    data = state["current"]
    if data["exercise_type"] == EXERCISE_BUILD_SENTENCE:
        picked_idx = data.setdefault("_picked_idx", [])
        picked = data.setdefault("_picked", [])
        if index not in picked_idx and index < len(data["shuffled"]):
            picked_idx.append(index)
            picked.append(data["shuffled"][index])
        if len(picked) == len(data["tokens"]):
            grade = trainer_grading.grade_sentence(data, picked)
            await _apply_result(bot, cid, state, grade,
                                learning_ui.exercise_result(data, grade.correct, chosen=" ".join(picked)))
        else:
            await _send_exercise(bot, cid, data)
    elif data["exercise_type"] == EXERCISE_FIND_ERROR and index < len(data["tokens"]):
        grade = trainer_grading.grade_error_position(data, index)
        await _apply_result(bot, cid, state, grade,
                            learning_ui.exercise_result(data, grade.correct, chosen=data["tokens"][index]))


async def reset_tokens(bot, cid):
    state = trainer_session.get(cid)
    if state and state.get("current"):
        state["current"]["_picked"] = []
        state["current"]["_picked_idx"] = []
        await _send_exercise(bot, cid, state["current"])


async def next_exercise(bot, cid):
    state = trainer_session.get(cid)
    if not state:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново.")
        return
    if state.get("current") and state["current"].get("_answered"):
        state["current"] = None
        await _render_next(bot, cid)


def cancel(cid):
    trainer_session.finish(cid)


async def _finish(bot, cid, state):
    trainer_session.finish(cid)
    message = learning_ui.training_result(state["session"])
    await bot.send_message(chat_id=cid, text=message.text, entities=message.entities,
                           reply_markup=_keyboard([_nav_row()]))


async def send_progress(bot, cid):
    return await _learning().send_progress(bot, cid)
