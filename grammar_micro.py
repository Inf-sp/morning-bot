import uuid

import ai
import config
import secure
import store
import verify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from util import esc

_ikb = lambda rows: InlineKeyboardMarkup(
    [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
)

SYSTEM_TOPICS = {
    "нидерландский": {
        "A1": [
            "Порядок слов (SVO)",
            "Артикли de/het",
            "Спряжение глаголов",
            "Отрицание niet/geen",
            "Вопросительные предложения",
        ],
        "A2": [
            "Perfectum",
            "Инверсия",
            "Разделяемые глаголы",
            "Er-конструкции",
            "Степени сравнения",
        ],
        "B1": [
            "Страдательный залог",
            "Косвенная речь",
            "Придаточные с omdat/want",
            "Модальные глаголы",
            "Относительные местоимения",
        ],
    },
    "английский": {
        "A1": [
            "Present Simple",
            "Артикли a/an/the",
            "Вопросы с do/does",
            "Отрицание don't/doesn't",
            "There is/are",
        ],
        "A2": [
            "Present Continuous",
            "Past Simple",
            "Going to",
            "Модальные can/must/should",
            "Степени сравнения",
        ],
        "B1": [
            "Present Perfect",
            "Passive Voice",
            "Reported Speech",
            "Conditionals 1 & 2",
            "Придаточные предложения",
        ],
    },
}

_LANG_NAME = {"nl": "нидерландский", "en": "английский"}
_LANG_CODE = {"нидерландский": "nl", "английский": "en"}
_LANG_FLAG = {"нидерландский": "🇳🇱", "английский": "🇬🇧"}
_LEVEL_EMOJI = {"A1": "📘", "A2": "📙", "B1": "📗"}


def _lang(code):
    return _LANG_NAME.get(code, "нидерландский")


# --- KV helpers ---

def _topics(cid, lang):
    raw = store.get_list(config.MICRO_TOPICS_KEY, cid)
    return raw.get(lang, []) if isinstance(raw, dict) else []


def _save_topics(cid, lang, topics):
    raw = store.get_list(config.MICRO_TOPICS_KEY, cid)
    d = raw if isinstance(raw, dict) else {}
    d[lang] = topics
    store.set_list(config.MICRO_TOPICS_KEY, cid, d)


def _progress(cid):
    raw = store.get_list(config.MICRO_PROGRESS_KEY, cid)
    return raw if isinstance(raw, dict) else {}


def _save_progress(cid, prog):
    store.set_list(config.MICRO_PROGRESS_KEY, cid, prog)


def _lesson(topic_id):
    raw = store.get_list(config.MICRO_LESSONS_KEY, topic_id)
    return raw if isinstance(raw, dict) and raw else None


def _save_lesson(topic_id, lesson):
    store.set_list(config.MICRO_LESSONS_KEY, topic_id, lesson)


def _ensure_system_topics(cid, lang):
    topics = _topics(cid, lang)
    existing = {t["title"] for t in topics if t.get("system")}
    changed = False
    for level, titles in SYSTEM_TOPICS.get(lang, {}).items():
        for title in titles:
            if title not in existing:
                topics.append({
                    "id": uuid.uuid4().hex[:12],
                    "level": level,
                    "title": title,
                    "system": True,
                })
                changed = True
    if changed:
        _save_topics(cid, lang, topics)
    return _topics(cid, lang)


def _find_topic(cid, topic_id):
    for lang in ("нидерландский", "английский"):
        for t in _topics(cid, lang):
            if t["id"] == topic_id:
                return t, lang
    return None, None


# --- LLM ---

def _gen_lesson_data(lang, title):
    prompt = (
        f"Создай микро-урок по грамматике ({lang}), тема: «{title}».\n"
        "JSON (без переносов внутри строк):\n"
        "{\n"
        ' "pattern": "шаблон предложения [Субъект + Глагол + ...], коротко",\n'
        ' "rule": "правило 1-2 предложения: когда применяется и как строится",\n'
        ' "examples": [\n'
        f'   {{"foreign": "пример на изучаемом языке", "ru": "перевод"}},\n'
        f'   {{"foreign": "второй пример на изучаемом языке", "ru": "перевод"}}\n'
        " ],\n"
        ' "hint": "шаблон с заменителями слов для составления своего предложения"\n'
        "}"
    )
    return ai.llm_json(prompt, 600, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _check_data(lang, title, pattern, sentence):
    prompt = (
        f"Пользователь изучает грамматику ({lang}), тема «{title}», паттерн: {pattern}.\n"
        f"Его предложение: {sentence}\n"
        "Проверь ТОЛЬКО применение паттерна (не орфографию, не стиль).\n"
        'JSON: {"ok": true/false, "feedback": "фидбек 1-2 строки на русском"}'
    )
    return ai.llm_json(prompt, 200, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _gen_dehet_words():
    prompt = (
        "Дай 7 нидерландских существительных уровня A1-A2 с правильным артиклем de или het.\n"
        "Разные темы: дом, природа, еда, тело, транспорт, вещи. Примерно 4 de и 3 het (или наоборот).\n"
        'JSON (только массив): [{"word": "huis", "article": "het"}, ...]'
    )
    return ai.llm_json(prompt, 300, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _dehet_card(st):
    idx = st["idx"]
    total = len(st["words"])
    word = st["words"][idx]["word"]
    return f"🧩 <b>de / het</b>  ·  {idx + 1} из {total}\n\n<b>      {esc(word)}</b>"


_DEHET_KB = _ikb([
    [("de", "dh_de"), ("het", "dh_het")],
    [("⬅️ Стоп", "gm_lang_nl")],
])


# --- UI ---

async def send_home(bot, cid):
    kb = _ikb([
        [("🇳🇱 Нидерландский", "gm_lang_nl"), ("🇬🇧 Английский", "gm_lang_en")],
        [("⬅️ Назад", "m_learn")],
    ])
    await bot.send_message(
        chat_id=cid,
        text="📘 <b>Микро-грамматика</b>\n\nОдин шаблон — один урок. Читаешь, пробуешь, идёшь дальше.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def send_lang(bot, cid, code):
    lang = _lang(code)
    flag = _LANG_FLAG[lang]
    rows = [
        [("📘 A1 · Основы", f"gm_level_{code}_A1")],
        [("📙 A2 · Продолжение", f"gm_level_{code}_A2")],
        [("📗 B1 · Уверенный", f"gm_level_{code}_B1")],
        [("📝 Мои темы", f"gm_custom_{code}")],
        [("⬅️ Назад", "gm_home")],
    ]
    if code == "nl":
        rows.insert(0, [("🧩 Тренажёр de/het", "dh_start")])
    await bot.send_message(
        chat_id=cid,
        text=f"📘 <b>Грамматика · {flag} {lang.capitalize()}</b>\n\nВыбери курс:",
        parse_mode="HTML",
        reply_markup=_ikb(rows),
    )


async def send_level(bot, cid, code, level):
    lang = _lang(code)
    flag = _LANG_FLAG[lang]
    topics = _ensure_system_topics(cid, lang)
    prog = _progress(cid)
    level_topics = [t for t in topics if t.get("level") == level and t.get("system")]

    rows = []
    for t in level_topics:
        status = prog.get(t["id"], "new")
        icon = "✅" if status == "done" else ("📍" if status == "current" else "▸")
        rows.append([(f"{icon} {t['title']}", f"gm_topic_{t['id']}")])
    rows.append([("⬅️ Назад", f"gm_lang_{code}")])

    emoji = _LEVEL_EMOJI.get(level, "📘")
    done_count = sum(1 for t in level_topics if prog.get(t["id"]) == "done")
    await bot.send_message(
        chat_id=cid,
        text=f"{emoji} <b>{level} · {flag} {lang.capitalize()}</b>\n\n{done_count}/{len(level_topics)} пройдено",
        parse_mode="HTML",
        reply_markup=_ikb(rows),
    )


async def send_topic(bot, cid, topic_id):
    topic, lang = _find_topic(cid, topic_id)
    if not topic:
        await bot.send_message(chat_id=cid, text="Тема не найдена.")
        return

    flag = _LANG_FLAG[lang]
    title = topic["title"]
    level = topic.get("level", "A1")
    code = _LANG_CODE[lang]

    lesson = _lesson(topic_id)
    if not lesson:
        await bot.send_message(chat_id=cid, text="⏳ Генерирую урок...")
        try:
            lesson = _gen_lesson_data(lang, title)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
            return
        _save_lesson(topic_id, lesson)

    prog = _progress(cid)
    if prog.get(topic_id) != "done":
        prog[topic_id] = "current"
        _save_progress(cid, prog)

    pattern = lesson.get("pattern", "")
    rule = lesson.get("rule", "")
    examples = lesson.get("examples", [])
    hint = lesson.get("hint", pattern)

    L = [f"📘 {flag} <b>{esc(title)}</b>", ""]
    L.append(f"<b>Шаблон:</b> {esc(pattern)}")
    L += ["", f"<b>Правило:</b> {esc(rule)}"]
    if examples:
        L += ["", "<b>Примеры:</b>"]
        for i, ex in enumerate(examples[:3], 1):
            L.append(f"{i}. {esc(ex.get('foreign', ''))} — <i>{esc(ex.get('ru', ''))}</i>")
    L += ["", "<i>Прочитай вслух. Покрути в голове. Всё.</i>"]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")

    store.micro_state[cid] = {
        "topic_id": topic_id,
        "lang": lang,
        "title": title,
        "pattern": pattern,
        "level": level,
        "code": code,
        "awaiting_sentence": True,
    }
    kb = _ikb([
        [("✅ Усвоил, далее →", f"gm_done_{topic_id}"), ("⬅️ К темам", f"gm_level_{code}_{level}")],
    ])
    await bot.send_message(
        chat_id=cid,
        text=f"✍️ <b>Твоя очередь!</b>\n\nНапиши ОДНО предложение по шаблону:\n<code>{esc(hint)}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def check_sentence(bot, cid, text):
    st = store.micro_state.get(cid, {})
    if not st.get("awaiting_sentence"):
        return False
    store.micro_state[cid] = {**st, "awaiting_sentence": False}

    topic_id = st["topic_id"]
    lang = st["lang"]
    title = st["title"]
    pattern = st["pattern"]
    level = st.get("level", "A1")
    code = st.get("code", _LANG_CODE.get(lang, "nl"))

    try:
        result = _check_data(lang, title, pattern, secure.wrap_untrusted(text, "предложение"))
    except Exception as e:
        await verify.safe_error(bot, cid, e)
        return True

    ok = result.get("ok", False)
    feedback = result.get("feedback", "")
    icon = "✅" if ok else "🤔"

    kb = _ikb([
        [("➡️ Следующая тема", f"gm_done_{topic_id}")],
        [("🔄 Ещё раз", f"gm_topic_{topic_id}"), ("⬅️ К темам", f"gm_level_{code}_{level}")],
    ])
    L = [f"{icon} <i>{esc(text)}</i>", "", esc(feedback)]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
    return True


async def mark_done(bot, cid, topic_id):
    store.micro_state.pop(cid, None)
    topic, lang = _find_topic(cid, topic_id)

    prog = _progress(cid)
    prog[topic_id] = "done"
    _save_progress(cid, prog)

    if not lang:
        await bot.send_message(chat_id=cid, text="✅ Тема пройдена.")
        return

    level = topic.get("level", "A1")
    code = _LANG_CODE[lang]
    level_topics = [t for t in _topics(cid, lang) if t.get("level") == level and t.get("system")]

    next_topic = None
    found = False
    for t in level_topics:
        if found and prog.get(t["id"]) != "done":
            next_topic = t
            break
        if t["id"] == topic_id:
            found = True

    if next_topic:
        kb = _ikb([
            [("➡️ Следующая тема", f"gm_topic_{next_topic['id']}")],
            [("📋 К списку тем", f"gm_level_{code}_{level}")],
        ])
        await bot.send_message(
            chat_id=cid,
            text=f"✅ Тема пройдена!\n\nСледующая: <b>{esc(next_topic['title'])}</b>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        emoji = _LEVEL_EMOJI.get(level, "📘")
        kb = _ikb([
            [("📋 Ещё раз", f"gm_level_{code}_{level}"), ("⬅️ К языку", f"gm_lang_{code}")],
        ])
        await bot.send_message(
            chat_id=cid,
            text=f"{emoji} <b>Курс {level} завершён!</b>\n\nВсе темы пройдены. Отличная работа!",
            parse_mode="HTML",
            reply_markup=kb,
        )


async def send_custom(bot, cid, code):
    lang = _lang(code)
    flag = _LANG_FLAG[lang]
    topics = [t for t in _topics(cid, lang) if not t.get("system")]

    rows = []
    for t in topics:
        rows.append([
            (f"📝 {t['title'][:28]}", f"gm_topic_{t['id']}"),
            ("❌", f"gm_deltopic_{t['id']}"),
        ])
    rows.append([("📝 Добавить тему", f"gm_addtopic_{code}")])
    rows.append([("⬅️ Назад", f"gm_lang_{code}")])

    header = f"📝 <b>Мои темы · {flag} {lang.capitalize()}</b>"
    body = "\n\nСвоих тем пока нет. Добавь первую!" if not topics else ""
    await bot.send_message(
        chat_id=cid, text=header + body, parse_mode="HTML", reply_markup=_ikb(rows)
    )


async def delete_topic(bot, cid, topic_id):
    topic, lang = _find_topic(cid, topic_id)
    if not lang:
        await bot.send_message(chat_id=cid, text="Тема не найдена.")
        return
    code = _LANG_CODE[lang]
    topics = [t for t in _topics(cid, lang) if t["id"] != topic_id]
    _save_topics(cid, lang, topics)
    _save_lesson(topic_id, {})
    prog = _progress(cid)
    prog.pop(topic_id, None)
    _save_progress(cid, prog)
    await bot.send_message(chat_id=cid, text="✅ Тема удалена.")
    await send_custom(bot, cid, code)


async def add_topic_done(bot, cid, code, name):
    lang = _lang(code)
    name = name.strip()
    if not name:
        await bot.send_message(chat_id=cid, text="Название не может быть пустым.")
        return
    topics = _topics(cid, lang)
    topics.append({"id": uuid.uuid4().hex[:12], "level": "custom", "title": name, "system": False})
    _save_topics(cid, lang, topics)
    await bot.send_message(chat_id=cid, text=f"✅ Тема «{esc(name)}» добавлена.", parse_mode="HTML")
    await send_custom(bot, cid, code)


async def send_dehet_trainer(bot, cid):
    try:
        words = _gen_dehet_words()
    except Exception as e:
        await verify.safe_error(bot, cid, e)
        return
    if not isinstance(words, list) or not words:
        await bot.send_message(chat_id=cid, text="Не удалось сгенерировать слова, попробуй ещё.")
        return
    store.dehet_state[cid] = {"words": words, "idx": 0, "score": 0, "results": []}
    await bot.send_message(
        chat_id=cid,
        text=_dehet_card(store.dehet_state[cid]),
        parse_mode="HTML",
        reply_markup=_DEHET_KB,
    )


async def dehet_answer(bot, cid, q, chosen):
    st = store.dehet_state.get(cid)
    if not st:
        await bot.send_message(chat_id=cid, text="Сессия устарела. Начни заново через меню Грамматика.")
        return
    words = st["words"]
    idx = st["idx"]
    word_data = words[idx]
    correct = word_data["article"]
    ok = chosen == correct
    if ok:
        st["score"] += 1
    st["results"].append({"word": word_data["word"], "article": correct, "ok": ok})
    st["idx"] += 1

    feedback = f"{'✅' if ok else f'❌ (верно: {correct})'} <b>{esc(word_data['word'])}</b>\n\n"

    if st["idx"] >= len(words):
        score = st["score"]
        total = len(words)
        lines = [f"🎯 <b>Результат: {score}/{total}</b>", ""]
        for r in st["results"]:
            mark = "✅" if r["ok"] else f"❌ ({r['article']})"
            lines.append(f"{mark} <b>{esc(r['word'])}</b> — {r['article']}")
        store.dehet_state.pop(cid, None)
        kb = _ikb([[("🔄 Ещё раз", "dh_start"), ("⬅️ Назад", "gm_lang_nl")]])
        try:
            await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=kb)
    else:
        text = feedback + _dehet_card(st)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=_DEHET_KB)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_DEHET_KB)


async def handle_callback(bot, cid, q, data):
    if data == "gm_home":
        await send_home(bot, cid)
    elif data.startswith("gm_lang_"):
        await send_lang(bot, cid, data[8:])
    elif data.startswith("gm_level_"):
        rest = data[len("gm_level_"):]
        code, level = rest.split("_", 1)
        await send_level(bot, cid, code, level)
    elif data.startswith("gm_topic_"):
        await send_topic(bot, cid, data[len("gm_topic_"):])
    elif data.startswith("gm_done_"):
        await mark_done(bot, cid, data[len("gm_done_"):])
    elif data.startswith("gm_custom_"):
        await send_custom(bot, cid, data[len("gm_custom_"):])
    elif data.startswith("gm_addtopic_"):
        code = data[len("gm_addtopic_"):]
        lang = _lang(code)
        store.pending_input[cid] = f"gm_addtopic_{code}"
        flag = _LANG_FLAG[lang]
        await bot.send_message(
            chat_id=cid, text=f"✍️ Введи название темы для {flag} {lang}:"
        )
    elif data.startswith("gm_deltopic_"):
        await delete_topic(bot, cid, data[len("gm_deltopic_"):])
    elif data == "dh_start":
        await send_dehet_trainer(bot, cid)
    elif data in ("dh_de", "dh_het"):
        await dehet_answer(bot, cid, q, data[3:])
