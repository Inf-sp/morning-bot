from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len
from util import esc


def train_question(word):
    prefix = "Переведи слово «"
    suffix = "»"
    text = f"{prefix}{word}{suffix}"
    return MessageSpec(
        text=text,
        entities=[MessageEntity(MessageEntity.BOLD, u16_len(prefix), u16_len(str(word)))],
    )


def phrase_poll_question(blank_phrase, sentence_ru):
    b = MessageBuilder()
    b.section("Фраза-тренажёр")
    b.spacer()
    b.quote(str(blank_phrase or "").strip())
    if sentence_ru:
        b.spacer()
        b.bold("Перевод:")
        b.text_line(f" {str(sentence_ru).strip()}")
    b.spacer()
    b.text_line("Выбери пропущенное слово из вариантов ниже.")
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


def proverb_card(flag, original, analogs=None, meaning="", examples=None):
    b = MessageBuilder()
    header = f"💭{flag} Живой язык" if flag else "💭 Живой язык"
    b.section(header)
    b.spacer()

    if original:
        offset = u16_len(b.text)
        b.text_line(original)
        length = u16_len(original)
        b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
        b._entities.append(MessageEntity(MessageEntity.BLOCKQUOTE, offset, length))

    analogs = _as_list(analogs)
    if analogs:
        b.section("Как это переводится?")
        visible_analogs = analogs[:4]
        for i, analog in enumerate(visible_analogs):
            if i:
                b.text_line(" или " if i == len(visible_analogs) - 1 else ", ")
            b.text_line(f"«{_cap_first(analog) if i == 0 else analog}»")
        if meaning:
            b.text_line(f" ({meaning})")
        b.text_line(".")

    examples = _as_list(examples)
    if examples:
        b.section("Как говорить ПРАВИЛЬНО")
        b.text_line(examples[0])

    b.spacer()
    b.add("Прочитай вслух. Покрути в голове. Всё.", MessageEntity.ITALIC)
    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg


def train_result(state, idx, correct_idx, options, chosen_fl=""):
    word = state.get("word", "")
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


def train_lang_select():
    b = MessageBuilder()
    b.section("🧠 Тренажёр")
    b.spacer()
    b.text_line("Слова и фразы для тренировки добавляются в разделе ")
    b.bold("Словарь")
    b.text_line(".")
    b.spacer()
    b.bold("Выбери язык для тренировки 👇")
    return b.build()


def translate_prompt(flag, ru, lang):
    b = MessageBuilder()
    b.section(f"📝 {flag} Обратный перевод")
    b.spacer()
    b.line(f"Фраза: «{ru}»")
    b.spacer()
    b.text_line(f"Напиши перевод на {lang} следующим сообщением.")
    return b.build()


def translate_result(flag, lang, ru, answer, result):
    b = MessageBuilder()
    b.section(f"📝 {flag} Обратный перевод")
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


def morning_words(flag, method_line, phrases=None, words=None, empty_hint=False):
    """method_line приходит от вызывающего кода уже HTML-фрагментом (может быть обёрнут в <i>),
    а word/ru — esc()-нутые поля словаря -> остаётся на HTML parse_mode, как travel_plan в leisure.py."""
    lines = [f"📚{flag} <b>Слова и фразы дня</b>", "", method_line]
    if empty_hint:
        lines += ["", "📖 Открой словарь, если хочешь добавить что-то новое или быстро повторить текущее."]
        return MessageSpec(text="\n".join(lines), parse_mode="HTML")
    if phrases:
        lines += ["", "💬 <b>Фразы</b>"]
        for word, ru in phrases:
            lines.append(f"• {esc(word)} → {esc(ru)}")
    if words:
        lines += ["", "📖 <b>Слова</b>"]
        for word, ru in words:
            lines.append(f"• {esc(word)} → {esc(ru)}")
    if phrases or words:
        lines += ["", "<i>Попробуй использовать 1-2 элемента сегодня в сообщениях, мыслях или разговоре.</i>"]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def game_start():
    return MessageSpec(text="🕵️ Игра-детектив. На каком языке играем?")


def game_card(ui, clues):
    b = MessageBuilder()
    b.section(ui["title"])
    b.section(ui["suspect"])
    b.line(clues)
    b.section(f"{ui['who']} 🤔")
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


def levels(nl_label, en_label):
    b = MessageBuilder()
    b.section("🎚 Уровень языков")
    b.spacer()
    b.text_line("🇳🇱 Нидерландский: ")
    b.bold(nl_label)
    b.newline()
    b.text_line("🇬🇧 Английский: ")
    b.bold(en_label)
    b.newline()
    b.spacer()
    b.text_line("Нажми уровень чтобы изменить:")
    return b.build()
