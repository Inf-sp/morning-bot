from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from dictionary_model import display_term
from .builder import MessageBuilder, MessageSpec
from .constants import choose_label, ui_label
from .learning_entry import render_learning_entry


# ================= ТРЕНАЖЁР: 7 ФОРМАТОВ ЗАДАНИЙ =================
# Единый формат по всему тренажёру (см. docs/word-trainer.md): "**Название:**
# текст" одной строкой, переводы через →, без отдельной строки "Перевод:".

def _q(b, label, text):
    b.bold(f"{label}:")
    b.text_line(f" {text}")
    b.newline()
    return b


def exercise_choose_translation_question(term):
    """Вопрос для native quiz poll (формат 1) — сам poll строится в learning.py,
    эта функция только для текста вопроса, если понадобится вне poll."""
    return f"Что значит: {term}?"


def exercise_build_sentence(data):
    b = MessageBuilder()
    b.section("🧩 Собери фразу")
    b.spacer()
    b.bold(data["ru"])
    b.newline()
    b.spacer()
    b.line("Собери фразу:")
    picked = data.get("_picked") or []
    if picked:
        b.spacer()
        b.line(" ".join(picked))
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_find_error(data):
    b = MessageBuilder()
    b.section("🔍 Найди ошибку")
    b.spacer()
    _q(b, "Фраза", " ".join(data["tokens"]))
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_fill_gap(data):
    b = MessageBuilder()
    b.section("✏️ Вставь слово")
    b.spacer()
    b.quote(data["blank_phrase"])
    if data.get("hint"):
        b.spacer()
        _q(b, "Подсказка", data["hint"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_translate_context(data):
    b = MessageBuilder()
    b.section("🗣 Скажи в ситуации")
    b.spacer()
    if data.get("situation"):
        _q(b, "Ситуация", data["situation"])
        b.spacer()
    _q(b, "Напиши", data["ru"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_choose_reaction(data):
    b = MessageBuilder()
    b.section("💭 Что ответить")
    b.spacer()
    _q(b, "Тебе говорят", data["situation"])
    if data.get("situation_ru"):
        b.line(data["situation_ru"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


_SENTENCE_CONTEXT_FORMATS = {"fill_gap", "find_error", "build_sentence"}


def _bold_translation_line(b, label, original, translation=""):
    b.bold(f"{label}:")
    b.text_line(" ")
    b.bold(original)
    if translation:
        b.text_line(" → ")
        b.bold(translation)
    b.newline()


def _add_language_tool_report(b, report, explanation="", *, show_unavailable=False):
    report = report if isinstance(report, dict) else {}
    if not report.get("available"):
        if show_unavailable:
            b.spacer()
            b.line("Проверка LanguageTool сейчас недоступна.")
        return
    issues = report.get("issues") or []
    if not issues:
        return
    b.spacer()
    original = str(report.get("text") or "").strip()
    if original:
        b.labeled_line("Твой ответ", original, lowercase=False)
    corrected = str(report.get("corrected_text") or "").strip()
    if corrected and corrected != original:
        b.labeled_line("Лучше", corrected, lowercase=False)
    else:
        replacements = issues[0].get("replacements") or []
        if replacements:
            b.labeled_line("Лучше", str(replacements[0]), lowercase=False)
    explanation = " ".join(str(explanation or report.get("explanation") or "").split())
    if explanation:
        b.spacer()
        b.labeled_line("Почему", explanation, lowercase=False)


def exercise_result(data, is_correct, chosen="", language_report=None):
    """Единая карточка результата из уже сохранённой словарной записи."""
    entry = data.get("entry") if isinstance(data.get("entry"), dict) else {}
    forgot = chosen == "__forgot__"
    is_close = (not is_correct and not forgot
                and data.get("exercise_type") == "translate_context"
                and bool((language_report or {}).get("issues")))
    b = MessageBuilder()
    b.section("✅ Верно" if is_correct else ("📝 Ответ" if forgot else ("🟡 Почти" if is_close else "❌ Ошибка")))
    if is_close and chosen:
        b.spacer()
        b.labeled_line("Твой ответ", chosen, lowercase=False)
    if is_close:
        reason = str((language_report or {}).get("explanation") or "").strip()
        if reason:
            b.labeled_line("Почему", reason, lowercase=False)
    if data.get("bad_translation"):
        b.spacer()
        b.line("По-русски предлог «на» здесь не нужен.")
    _render_trainer_entry_card(b, entry, data)
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def _trainer_term(entry, data):
    term = str(entry.get("term") or entry.get("word") or data.get("term")
               or data.get("result_correct") or data.get("correct") or "").strip()
    return display_term(term, entry.get("article") or "")


def _trainer_breakdown(entry):
    raw = str(entry.get("breakdown") or "").strip().casefold()
    pos = str(entry.get("pos") or "").strip().casefold()
    if entry.get("construction") or "глагол + предлог" in raw:
        return "глагольная конструкция"
    if "разговор" in raw:
        return "разговорная фраза"
    is_verb = pos in {"глагол", "verb", "werkwoord"} or "глагол" in raw or "werkwoord" in raw
    if is_verb:
        verb_type = str(entry.get("verb_type") or "").strip().casefold()
        if verb_type == "strong":
            return "сильный глагол"
        if verb_type == "weak":
            return "слабый глагол"
        if verb_type == "irregular":
            return "неправильный глагол"
        return "глагол"
    is_noun = pos in {"существительное", "noun", "zelfstandig naamwoord"} or "существительн" in raw
    if is_noun:
        article = str(entry.get("article") or "").strip().casefold()
        return f"существительное · {article}-слово" if article in {"de", "het"} else "существительное"
    mapping = {
        "adj": "прилагательное", "adjective": "прилагательное", "прилагательное": "прилагательное",
        "adverb": "наречие", "наречие": "наречие", "preposition": "предлог", "предлог": "предлог",
        "phrase": "выражение", "фраза": "выражение", "expression": "выражение",
    }
    return mapping.get(pos) or (raw.replace(",", " · ") if raw else "выражение")


def _verified_verb_forms(entry):
    try:
        confidence = float(entry.get("analysis_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    forms = [str(entry.get(key) or "").strip() for key in ("infinitive", "past_singular", "perfect_form")]
    return forms if confidence >= 0.75 and all(forms) else []


def _trainer_example(entry):
    examples = entry.get("examples") or []
    if isinstance(examples, list):
        for example in examples:
            if not isinstance(example, dict):
                continue
            text = str(example.get("text") or "").strip()
            translation = str(example.get("translation") or "").strip()
            if text and translation:
                return text, translation
    text = str(entry.get("example_nl") or "").strip()
    translation = str(entry.get("example_ru") or "").strip()
    return (text, translation) if text and translation else ("", "")


def _render_trainer_entry_card(b, entry, data):
    render_learning_entry(
        b, entry,
        fallback_term=(data.get("term") or data.get("result_correct") or data.get("correct") or ""),
        fallback_translation=data.get("ru") or "",
    )


def progress_screen(data):
    """Экран прогресса — главная метрика доля самостоятельных ответов без
    подсказок, не процент правильных ответов в quiz (см. docs/word-trainer.md)."""
    b = MessageBuilder()
    b.section(f"📊 Прогресс · {data['lang_title']}")
    b.spacer()
    _q(b, "В активном изучении", str(data["total"]))
    _q(b, "Уверенно знаю", str(data["confident"]))
    _q(b, "Нужно повторить", str(data["due_count"]))
    if data.get("strongest"):
        _q(b, "Сильнее всего", data["strongest"])
    if data.get("weakest"):
        _q(b, "Нужно подтянуть", data["weakest"])
    _q(b, "Без подсказок", f"{data['no_hint_pct']}%")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def train_lang_select():
    b = MessageBuilder()
    b.section(ui_label("word_trainer", "Тренажёр"))
    b.spacer()
    b.text_line("Слова и фразы для тренировки добавляются в разделе ")
    b.bold(ui_label("dictionary", "Словарь"))
    b.text_line(".")
    b.spacer()
    b.bold("Выбери язык для тренировки.")
    return b.build()


def translate_prompt(flag, ru, lang):
    b = MessageBuilder()
    b.section(f"{flag} Обратный перевод")
    b.spacer()
    b.labeled_line("Фраза", f"«{ru}»", lowercase=False)
    b.spacer()
    b.text_line(f"Напиши перевод на {lang} следующим сообщением.")
    return b.build()


def translate_result(flag, lang, ru, answer, result):
    b = MessageBuilder()
    b.section(f"{flag} Обратный перевод")
    b.spacer()
    b.labeled_line("Твой ответ", answer, lowercase=False)
    b.spacer()
    if result.get("ok"):
        b.text_line("✅ Верно")
        if result.get("correct"):
            b.spacer()
            b.text_line(f"💡 {ru} → {result['correct']}")
    else:
        if result.get("error"):
            b.text_line("❌ ")
            b.label("Ошибка", result["error"])
        if result.get("correct"):
            b.spacer()
            b.text_line(f"✅ {ru} → {result['correct']}")
    if result.get("note"):
        b.spacer()
        b.text_line(f"💡 {result['note']}")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def morning_words(flag, words=None, empty_hint=False):
    """Ежедневная карточка повторения ранее изученных слов и фраз."""
    b = MessageBuilder()
    b.section(f"📚{flag} Слова и фразы дня")
    if empty_hint:
        b.line("В прошлых занятиях пока нет слов и фраз для повторения.")
        b.spacer()
        b.text_line("🎯 ")
        b.label("Мини-задача", "пройди короткую тренировку — следующая подборка соберётся из неё.")
        msg = b.build()
        msg.text = msg.text.rstrip("\n")
        return msg
    b.line("Сегодня повторяем слова и фразы из прошлых занятий. Сначала вспомни перевод сам, потом проверь себя.")
    entries = list(words or [])
    if entries:
        b.spacer()
        b.bold("Повтори:")
        b.newline()
        for word, ru in entries:
            b.text_line("• ")
            b.text_line(f"{word} → ")
            b.add(ru, MessageEntity.SPOILER)
            b.newline()
        b.spacer()
        b.text_line("🎯 ")
        b.label(
            "Мини-задача",
            "используй сегодня одно слово или одну фразу в сообщении, разговоре или мысленно составь с ними предложение.",
        )
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_card(ui, clues):
    b = MessageBuilder()
    b.section(f"🕵️ {ui['title']}")
    b.section(ui["suspect"])
    b.line(clues)
    b.section(ui["who"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_found(ui, answer, body=""):
    b = MessageBuilder()
    b.section(ui["found"])
    b.spacer()
    b.bold(answer)
    if body:
        b.spacer()
        b.bold("Почему:")
        b.newline()
        points = [part.strip(" •-\n") for part in str(body).replace("\n", ". ").split(".") if part.strip()]
        for point in points[:3]:
            b.bullet(point)
    return b.build()


def game_hint(ui, hint):
    b = MessageBuilder()
    b.section(ui["hint"])
    b.spacer()
    b.bold(hint)
    b.spacer()
    b.text_line(ui["who"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")]])
    return b.build(reply_markup=kb)


def learning_settings(active_language, active_level):
    b = MessageBuilder()
    b.section("🎚️ Настройки")
    b.spacer()
    b.labeled_line("Активный язык")
    b.bold(active_language)
    b.newline()
    b.spacer()
    b.labeled_line("Уровень")
    b.bold(active_level)
    b.newline()
    b.spacer()
    b.text_line("Эти настройки влияют на карточку обучения, тренажёры и уведомления.")
    return b.build()
