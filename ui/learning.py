from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len
from .constants import ui_label


def train_question(phrase):
    prefix = "Переведи «"
    suffix = "»"
    text = f"{prefix}{phrase}{suffix}"
    return MessageSpec(
        text=text,
        entities=[MessageEntity(MessageEntity.BOLD, u16_len(prefix), u16_len(str(phrase)))],
    )


def phrase_poll_question(blank_phrase, sentence_ru):
    b = MessageBuilder()
    b.section("🧩 Проверь себя")
    b.spacer()
    b.quote(str(blank_phrase or "").strip())
    msg = b.build()
    stripped = msg.text.strip()
    leading_trim = u16_len(msg.text[:len(msg.text) - len(msg.text.lstrip())])
    limit = 300
    msg.text = stripped[:limit]
    new_len = u16_len(msg.text)
    kept_entities = []
    for e in msg.entities or []:
        offset = e.offset - leading_trim
        if offset < 0 or offset + e.length > new_len:
            continue
        kept_entities.append(MessageEntity(e.type, offset, e.length, url=getattr(e, "url", None)))
    msg.entities = kept_entities
    return msg


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
        b.section(ui_label("translation", "Как это переводится?"))
        main_analog = _strip_final_punctuation(_cap_first(analogs[0]))
        b.line(f"«{main_analog}».")

    meaning = str(meaning or "").strip()
    if meaning:
        b.section(ui_label("usage", "Когда это говорят?"))
        b.line(meaning)

    example, parsed_example_ru = _split_example(examples)
    example_ru = str(example_ru or parsed_example_ru or "").strip()
    if example:
        b.section(ui_label("example", "Пример из жизни:"))
        if example_ru:
            b.line(f"{example} →")
            b.line(example_ru)
        else:
            b.line(example)

    b.spacer()
    b.add("Прочитай вслух один раз. Этого достаточно.", MessageEntity.ITALIC)
    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg


def train_result(state, idx, correct_idx, options, chosen_fl=""):
    word = state.get("phrase") or state.get("word", "")
    correct = str(options[correct_idx])
    chosen = str(options[idx])
    sentence = state.get("sentence", "")
    sentence_ru = state.get("sentence_ru", "")
    meaning = state.get("meaning") or correct
    mode = state.get("mode", "word")

    b = MessageBuilder()
    if mode == "phrase":
        if idx == correct_idx:
            b.section("✅ Верно.")
        else:
            b.section("❌ Не совсем так.")
        b.spacer()
        b.text_line(f"{sentence} → ")
        b.bold(correct)
        b.newline()
        if idx != correct_idx:
            b.text_line(f"Твой ответ: «{chosen}».")
            b.newline()
        b.spacer()
        b.bold(word)
        b.newline()
        if sentence_ru:
            b.line(sentence_ru)
        if state.get("phrase_explanation"):
            b.spacer()
            b.line(state.get("phrase_explanation", ""))
        msg = b.build()
        msg.text = msg.text.rstrip("\n")
        return msg

    if idx == correct_idx:
        b.section("✅ Верно.")
    else:
        b.section("❌ Не совсем так.")
    b.spacer()
    b.bold(word)
    b.text_line(f" → {meaning}")
    b.newline()
    if idx != correct_idx:
        b.text_line(f"Твой ответ: «{chosen}»")
        if chosen_fl:
            b.text_line(" — это ")
            b.bold(chosen_fl)
            b.text_line(".")
        else:
            b.text_line(".")
        b.newline()
    if sentence:
        b.spacer()
        context = sentence
        if sentence_ru:
            context += f" → {sentence_ru}"
        b.line(context)
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def _strip_repeated_pattern(pattern, explanation):
    pattern = str(pattern or "").strip()
    explanation = str(explanation or "").strip()
    if not pattern or not explanation:
        return explanation
    low_pattern = pattern.casefold()
    low_explanation = explanation.casefold()
    if low_explanation == low_pattern:
        return ""
    for sep in (" — ", " - ", " = ", ": "):
        prefix = f"{low_pattern}{sep.casefold()}"
        if low_explanation.startswith(prefix):
            return explanation[len(pattern) + len(sep):].strip()
    return explanation


def phrase_intro_card(phrase, sentence_ru, pattern, explanation, example="", example_ru=""):
    """Этап 1 тренажёра фраз: фраза целиком + разбор устойчивой конструкции, без пропусков."""
    pattern = str(pattern or "").strip()
    explanation = _strip_repeated_pattern(pattern, explanation)
    example = str(example or "").strip()
    example_ru = str(example_ru or "").strip()

    b = MessageBuilder()
    b.section("🧩 Фраза-тренажёр")
    b.spacer()
    b.line(str(phrase or "").strip())
    if sentence_ru:
        b.line(f"Перевод: {str(sentence_ru).strip()}")

    if pattern or explanation:
        b.spacer()
        b.section("💡 Разбор")
        b.spacer()
        if pattern and explanation:
            b.line(f"{pattern} — {explanation}")
        else:
            b.line(pattern or explanation)

    if example:
        b.spacer()
        b.line("Пример:")
        b.line(example)
        if example_ru:
            b.line(f"→ {example_ru}")

    b.spacer()
    b.text_line("Дальше проверим это выражение на новом примере.")
    return b.build()


def phrase_quiz_result(state, is_correct, repeated_error=False):
    correct = str(state.get("meaning") or "").strip()
    full_phrase = str(state.get("phrase_test_full") or "").strip()
    sentence_ru = str(state.get("sentence_ru") or "").strip()
    short_rule = str(state.get("phrase_short_rule") or state.get("phrase_explanation") or "").strip()

    b = MessageBuilder()
    if is_correct:
        b.section("✅ Верно")
    elif repeated_error:
        b.section("❌ Не закрепилось")
        b.spacer()
        b.text_line("Правильный ответ: ")
        b.bold(correct)
        b.newline()
    else:
        b.section(f"❌ Правильный ответ: {correct}")

    if full_phrase:
        b.spacer()
        b.line(full_phrase)
    if sentence_ru:
        b.line(sentence_ru)
    if not is_correct and short_rule:
        b.spacer()
        b.tip(short_rule)

    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def phrase_rule_breakdown(state):
    correct = str(state.get("meaning") or "").strip()
    full_phrase = str(state.get("phrase_test_full") or "").strip()
    sentence_ru = str(state.get("sentence_ru") or "").strip()
    detail = str(state.get("phrase_detail") or state.get("phrase_explanation") or "").strip()

    b = MessageBuilder()
    b.section(f"💡 Почему `{correct}`?")
    if detail:
        b.spacer()
        b.line(detail[:450].rstrip())
    if full_phrase:
        b.spacer()
        b.line(full_phrase)
    if sentence_ru:
        b.line(sentence_ru)
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
    b.line(f"Фраза: «{ru}»")
    b.spacer()
    b.text_line(f"Напиши перевод на {lang} следующим сообщением.")
    return b.build()


def translate_result(flag, lang, ru, answer, result):
    b = MessageBuilder()
    b.section(f"{flag} Обратный перевод")
    b.spacer()
    b.line(f"Твой ответ: {answer}")
    b.spacer()
    if result.get("ok"):
        b.text_line("✅ Верно")
        if result.get("correct"):
            b.spacer()
            b.text_line(f"💡 {ru} → {result['correct']}")
    else:
        if result.get("error"):
            b.text_line(f"❌ Ошибка: {result['error']}")
        if result.get("correct"):
            b.spacer()
            b.text_line(f"✅ {ru} → {result['correct']}")
    if result.get("note"):
        b.spacer()
        b.text_line(f"💡 {result['note']}")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def morning_words(flag, method, is_read_aloud=False, phrases=None, words=None, empty_hint=False):
    """method приходит СЫРЫМ текстом (без esc()/HTML-тегов) — функция сама решает оформление:
    is_read_aloud оборачивает его в italic(), иначе выводится обычной строкой."""
    b = MessageBuilder()
    b.section(f"📚{flag} Слова и фразы дня")
    if is_read_aloud:
        b.italic(method)
        b.newline()
    else:
        b.line(method)
    if empty_hint:
        b.spacer()
        b.text_line("📖 Открой словарь, если хочешь добавить что-то новое или быстро повторить текущее.")
        msg = b.build()
        msg.text = msg.text.rstrip("\n")
        return msg
    if phrases:
        b.section(ui_label("phrases", "Фразы"))
        for word, ru in phrases:
            b.bullet(f"{word} → {ru}")
    if words:
        b.section("📖 Слова")
        for word, ru in words:
            b.bullet(f"{word} → {ru}")
    if phrases or words:
        b.spacer()
        b.italic("Попробуй использовать 1-2 элемента сегодня в сообщениях, мыслях или разговоре.")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_start():
    return MessageSpec(text="Игра-детектив. На каком языке играем?")


def game_card(ui, clues):
    b = MessageBuilder()
    b.section(ui["title"])
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
        b.text_line(body)
    return b.build()


def game_hint(ui, hint):
    b = MessageBuilder()
    b.section(ui["hint"])
    b.spacer()
    b.bold(hint)
    b.spacer()
    b.text_line(ui["who"])
    return b.build()


def learning_settings(active_language, active_level):
    b = MessageBuilder()
    b.section("🎚️ Настройки обучения")
    b.spacer()
    b.line("Активный язык:")
    b.bold(active_language)
    b.newline()
    b.spacer()
    b.line("Уровень:")
    b.bold(active_level)
    b.newline()
    b.spacer()
    b.text_line("Эти настройки влияют на тренажёры, «Живой язык» и обучающие уведомления.")
    return b.build()
