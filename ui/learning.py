from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len


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


def phrase_intro_card(phrase, sentence_ru, construction, construction_meaning, other_forms):
    """Этап 1 тренажёра фраз: фраза целиком + разбор устойчивой конструкции, без пропусков."""
    b = MessageBuilder()
    b.section("🧩 Фраза-тренажёр")
    b.spacer()
    b.quote(str(phrase or "").strip())
    if sentence_ru:
        b.spacer()
        b.bold("Перевод:")
        b.text_line(f" {str(sentence_ru).strip()}")
        b.newline()

    if construction:
        b.tip(construction)
        if construction_meaning:
            b.text_line(f" = {str(construction_meaning).strip()}")
            b.newline()

    if other_forms:
        b.spacer()
        b.bold("Другие значения:")
        b.newline()
        for item in other_forms[:1]:
            pos = str(item.get("pos") or "").strip()
            meaning = str(item.get("meaning") or "").strip()
            if pos and meaning:
                b.bullet(f"{pos} = {meaning}")

    b.spacer()
    b.text_line("Дальше проверим это правило на новом примере.")
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
        b.section("💬 Фразы")
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


def learning_settings(active_language, active_level):
    b = MessageBuilder()
    b.section("🎚 Настройки обучения")
    b.spacer()
    b.line("Активный язык:")
    b.bold(active_language)
    b.newline()
    b.spacer()
    b.line("Уровень:")
    b.bold(active_level)
    b.newline()
    b.spacer()
    b.text_line("Эти настройки влияют на тренажёры, «Живой язык» и обучающие рассылки.")
    return b.build()
