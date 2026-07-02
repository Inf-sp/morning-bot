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
    b.bold("Фраза-тренажёр")
    b.blank()
    b.quote(str(blank_phrase or "").strip())
    if sentence_ru:
        b.blank()
        b.bold("Перевод:")
        b.text_line(f" {str(sentence_ru).strip()}")
    b.blank()
    b.text_line("Выбери пропущенное слово из вариантов ниже.")
    msg = b.build()
    msg.text = msg.text.strip()[:300]
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
    b.bold(header)
    b.blank()

    if original:
        offset = u16_len(b.text)
        b.text_line(original)
        length = u16_len(original)
        b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
        b._entities.append(MessageEntity(MessageEntity.BLOCKQUOTE, offset, length))

    analogs = _as_list(analogs)
    if analogs:
        b.blank()
        b.bold("Как это переводится?")
        b.newline()
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
        b.blank()
        b.bold("Как говорить ПРАВИЛЬНО")
        b.newline()
        b.text_line(examples[0])

    b.blank()
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

    if mode == "phrase":
        if idx == correct_idx:
            lines = ["✅ <b>Верно.</b>", "", f"{esc(sentence)} → <b>{esc(correct)}</b>"]
        else:
            lines = [
                "❌ <b>Не совсем так.</b>",
                "",
                f"{esc(sentence)} → <b>{esc(correct)}</b>",
                f"Твой ответ: «{esc(chosen)}».",
            ]
        lines += ["", f"<b>{esc(word)}</b>"]
        if sentence_ru:
            lines.append(esc(sentence_ru))
        if state.get("phrase_explanation"):
            lines += ["", esc(state.get("phrase_explanation", ""))]
        return MessageSpec(text="\n".join(lines), parse_mode="HTML")

    if idx == correct_idx:
        lines = ["✅ <b>Верно.</b>", "", f"<b>{esc(word)}</b> → {esc(meaning)}"]
    else:
        lines = [
            "❌ <b>Не совсем так.</b>",
            "",
            f"<b>{esc(word)}</b> → {esc(meaning)}",
            f"Твой ответ: «{esc(chosen)}»" + (f" — это <b>{esc(chosen_fl)}</b>." if chosen_fl else "."),
        ]
    if sentence:
        context = f"{esc(sentence)}"
        if sentence_ru:
            context += f" → {esc(sentence_ru)}"
        lines += ["", context]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def train_lang_select():
    return MessageSpec(
        text=(
            "🧠 <b>Тренажёр</b>\n\n"
            "Слова и фразы для тренировки добавляются в разделе <b>Словарь</b>.\n\n"
            "<b>Выбери язык для тренировки 👇</b>"
        ),
        parse_mode="HTML",
    )


def translate_prompt(flag, ru, lang):
    return MessageSpec(
        text=f"📝 <b>{flag} Обратный перевод</b>\n\nФраза: «{esc(ru)}»\n\nНапиши перевод на {lang} следующим сообщением.",
        parse_mode="HTML",
    )


def translate_result(flag, lang, ru, answer, result):
    lines = [f"📝 <b>{flag} Обратный перевод</b>", "", f"Твой ответ: {esc(answer)}", ""]
    if result.get("ok"):
        lines.append("✅ Верно")
        if result.get("correct"):
            lines += ["", f"💡 {esc(ru)} → {esc(result['correct'])}"]
    else:
        if result.get("error"):
            lines += [f"❌ Ошибка: {esc(result['error'])}"]
        if result.get("correct"):
            lines += ["", f"✅ {esc(ru)} → {esc(result['correct'])}"]
    if result.get("note"):
        lines += ["", f"💡 {esc(result['note'])}"]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def morning_words(flag, method_line, phrases=None, words=None, empty_hint=False):
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
    lines = [f"<b>{ui['title']}</b>", "", f"<b>{ui['suspect']}</b>", clues, "", f"<b>{ui['who']} 🤔</b>"]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def game_found(ui, answer, body=""):
    text = f"<b>{ui['found']}</b>\n\n{ui['answer']}:\n<b>{esc(answer)}</b>"
    if body:
        text += f"\n\n{esc(body)}"
    return MessageSpec(text=text, parse_mode="HTML")


def game_hint(ui, hint):
    return MessageSpec(text=f"<b>{ui['hint']}</b>\n\n<b>{esc(hint)}</b>\n\n{ui['who']}", parse_mode="HTML")


def levels(nl_label, en_label):
    return MessageSpec(
        text=(
            "🎚 <b>Уровень языков</b>\n\n"
            f"🇳🇱 Нидерландский: <b>{nl_label}</b>\n"
            f"🇬🇧 Английский: <b>{en_label}</b>\n\n"
            "Нажми уровень чтобы изменить:"
        ),
        parse_mode="HTML",
    )
