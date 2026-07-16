from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len
from .constants import ui_label


def _as_list(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _cap_first(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else text


def _split_example(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value or "").strip()
    if "→" in value:
        left, right = value.split("→", 1)
        return left.strip(), right.strip()
    return value, ""


def _strip_final_punctuation(text):
    return (text or "").strip().rstrip(".!?。！？").strip()


def proverb_card(flag, original, analogs=None, meaning="", examples=None, example_ru=""):
    b = MessageBuilder()
    b.section("💭 Живой язык")
    b.spacer()

    if original:
        offset = u16_len(b.text)
        b.text_line(original)
        length = u16_len(original)
        b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
        b._entities.append(MessageEntity(MessageEntity.BLOCKQUOTE, offset, length))

    analogs = _as_list(analogs)
    if analogs:
        main_analog = _strip_final_punctuation(_cap_first(analogs[0]))
        b.spacer()
        b.bold("Перевод:")
        b.italic(f" «{main_analog}».")
        b.newline()

    meaning = str(meaning or "").strip()
    if meaning:
        b.section("Когда это говорят?")
        b.line(meaning)

    example, parsed_example_ru = _split_example(examples)
    example_ru = str(example_ru or parsed_example_ru or "").strip()
    if example:
        b.spacer()
        b.bold("Пример из жизни:")
        example_line = f" {example} → {example_ru}" if example_ru else f" {example}"
        b.text_line(example_line)
        b.newline()

    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg


# ================= ТРЕНАЖЁР: 9 ФОРМАТОВ ЗАДАНИЙ =================
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


def exercise_recall_free(data, hint_shown=False):
    b = MessageBuilder()
    b.section("🧠 Вспомни")
    b.spacer()
    _q(b, "Как сказать", data["ru"])
    if hint_shown and data.get("hint"):
        b.spacer()
        _q(b, "Подсказка", data["hint"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_build_sentence(data):
    b = MessageBuilder()
    b.section("🧩 Собери предложение")
    b.spacer()
    _q(b, "Перевод", data["ru"])
    picked = data.get("_picked") or []
    if picked:
        b.spacer()
        b.line(" ".join(picked))
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_find_error(data):
    b = MessageBuilder()
    b.section("🔍 Где ошибка")
    b.spacer()
    _q(b, "Фраза", " ".join(data["tokens"]))
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_choose_natural(data):
    b = MessageBuilder()
    b.section("💬 Выбери естественный вариант")
    b.spacer()
    _q(b, "Перевод", data["ru"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_fill_gap(data):
    b = MessageBuilder()
    b.section("✏️ Заполни пропуск")
    b.spacer()
    b.quote(data["blank_phrase"])
    if data.get("ru"):
        b.spacer()
        _q(b, "Перевод", data["ru"])
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def exercise_translate_context(data):
    b = MessageBuilder()
    b.section("🗣 Переведи в контексте")
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


def exercise_continue_dialogue(data):
    b = MessageBuilder()
    b.section("💬 Продолжи диалог")
    b.spacer()
    _q(b, "Собеседник", data["line"])
    if data.get("line_ru"):
        b.line(data["line_ru"])
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


def exercise_result(data, is_correct, chosen=""):
    """Общий результат после ответа — единая структура для всех форматов
    (см. docs/word-trainer.md, 'Поведение после ошибки'): короткое подтверждение
    или короткое объяснение причины, без сухого 'Неверно. Правильный ответ: X.'

    Для форматов с целым предложением (fill_gap/find_error/build_sentence) ru —
    перевод ВСЕЙ фразы, а не отдельного слова, поэтому строится как отдельная
    строка полным предложением, а не 'слово → перевод'."""
    correct = str(data.get("result_correct") or data.get("correct") or data.get("correct_text") or "").strip()
    ru = str(data.get("ru") or "").strip()
    english = str(data.get("english") or "").strip()
    note = str(data.get("note") or "").strip()
    is_sentence = data.get("exercise_type") in _SENTENCE_CONTEXT_FORMATS

    if is_sentence and correct and not correct.endswith((".", "!", "?")):
        correct += "."

    b = MessageBuilder()
    if is_correct:
        b.section("✅ Верно")
        b.spacer()
        _bold_translation_line(b, "Правильно", correct, ru)
    else:
        b.section("Почти")
        b.spacer()
        _bold_translation_line(b, "Правильно", correct, ru)
        if english:
            b.spacer()
            _bold_translation_line(b, "По-английски", english)
        if note:
            b.spacer()
            _q(b, "Разбор", note)
        b.spacer()
        b.line("Это вернётся позже в тренировке.")
        if data.get("bad_translation"):
            b.spacer()
            b.text_line("Фраза ")
            b.bold(f"«{data['bad_translation']}»")
            b.text_line(" по-русски неграмотна. Предлог ")
            b.bold(f"«{data['unneeded_preposition']}»")
            b.text_line(" здесь не нужен.")
            b.newline()
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def training_result(session):
    """Компактный итог тренировки (см. ТЗ 'Завершение тренировки') — без
    процента правильных ответов как главной метрики и без таблиц."""
    b = MessageBuilder()
    b.section("✅ Готово")
    b.spacer()
    consolidated = list(dict.fromkeys(session.get("consolidated") or []))
    returning = list(dict.fromkeys(session.get("returning") or []))
    if consolidated:
        _q(b, "Закреплено", " · ".join(consolidated[:6]))
    if returning:
        _q(b, "Вернём позже", " · ".join(returning[:6]))
    _q(b, "Без подсказок", f"{session.get('no_hint_count', 0)} ответов")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


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


def game_start():
    return MessageSpec(text="🕵️ Игра-детектив. На каком языке играем?")


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
    b.text_line(f"{ui['answer']}:\n")
    b.bold(answer)
    if body:
        b.spacer()
        b.bold(ui.get("analyse", "Анализ:"))
        b.newline()
        b.text_line(body)
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
    b.section("Выбрать язык")
    b.spacer()
    b.labeled_line("Активный язык")
    b.bold(active_language)
    b.newline()
    b.spacer()
    b.labeled_line("Уровень")
    b.bold(active_level)
    b.newline()
    b.spacer()
    b.text_line("Эти настройки влияют на тренажёры, «Живой язык» и обучающие уведомления.")
    return b.build()
