import re

from .builder import MessageBuilder
from .constants import ui_label
from .learning_entry import render_word_card


# ================= ТРЕНАЖЁР: 7 ФОРМАТОВ ЗАДАНИЙ =================
# Единый формат по всему тренажёру (см. docs/word-trainer.md): "**Название:**
# текст" одной строкой, переводы через →, без отдельной строки "Перевод:".

def _q(b, label, text):
    b.bold(f"{label}:")
    b.text_line(f" {text}")
    b.newline()
    return b


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


def exercise_result(data, is_correct, chosen="", language_report=None):
    """Единая карточка результата из уже сохранённой словарной записи."""
    entry = data.get("entry") if isinstance(data.get("entry"), dict) else {}
    forgot = chosen == "__forgot__"
    is_close = (not is_correct and not forgot
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
    if not is_correct and data.get("exercise_type") == "find_error":
        correct_text = str(data.get("correct_text") or "").strip()
        if correct_text:
            b.spacer()
            b.labeled_line("Правильно", correct_text, lowercase=False)
    if not is_correct and data.get("exercise_type") == "fill_gap":
        result_sentence = str(data.get("result_sentence") or "").strip()
        if result_sentence:
            b.spacer()
            b.labeled_line("Правильно", result_sentence, lowercase=False)
    _render_trainer_entry_card(b, entry, data)
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def _render_trainer_entry_card(b, entry, data):
    """Слово после ответа — как «Слово дня»: «**Opletten** (слабый глагол) → Внимательно следить», формы, спряжение,
    «Пример:»; отдельной строки «Разбор:» с частью речи нет. Нет записи в словаре — термин из задания."""
    term = data.get("term") or data.get("result_correct") or data.get("correct") or ""
    card = {**entry} if entry else {}
    card.setdefault("term", term)
    if not card.get("translation") and data.get("ru"):
        card["translation"] = data["ru"]
    if card.get("term") or card.get("translation"):
        render_word_card(b, card, explain=True)


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
    b.section("✨ Подобрать новое задание")
    b.spacer()
    b.text_line("Слова и фразы для тренировки добавляются в разделе ")
    b.bold(ui_label("dictionary", "Словарь"))
    b.text_line(".")
    b.spacer()
    b.bold("Выбери язык для тренировки.")
    return b.build()


def morning_words(flag, words=None, empty_hint=False, *, entries=None, tip="", rule="", language="", stats=None):
    """Рассылка «🇳🇱 Слово дня · Нидерландский»: «**Immers** (наречие) → Ведь · же · действительно», «Разбор:» — суть
    и как употреблять, один «Пример:» (второй пример карточки — первый остаётся вечерней викторине); без
    произношения, «Живых примеров» и «Твоей очереди»; курсивом — «Твой нидерландский · 192 слова · 35 изученных тем»."""
    b = MessageBuilder()
    b.section(f"{flag} Слово дня" + (f" · {language}" if language else ""))
    if empty_hint:
        b.line("В словаре пока нет одиночных слов для разбора.")
        b.spacer()
        b.text_line("🎯 ")
        b.label("Мини-задача", "пройди короткую тренировку — следующая подборка соберётся из неё.")
        msg = b.build()
        msg.text = msg.text.rstrip("\n")
        return msg
    prepared = list(entries or [])
    if not prepared and words:
        prepared = [{"term": word, "translation": ru} for word, ru in words]
    if prepared:
        render_word_card(b, prepared[0], explain=True, example_index=1)
    if stats and stats.get("words") and language:
        from .menu import learning_stats_line
        b.spacer()
        b.italic(learning_stats_line(language, stats.get("words"), stats.get("topics")))
        b.newline()
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_card(ui, description, category=""):
    b = MessageBuilder()
    b.section(f"🕵️ {ui['title']}")
    if category:
        b.bold(f"{ui.get('category', 'Категория')}:")  # «Categorie:» — жирным
        b.text_line(f" {category}")
        b.newline()
        b.spacer()
    b.quote(" ".join(str(description or "").split()))  # загадка — цитатой
    b.newline()
    b.spacer()
    # Финальная подсказка экрана — курсивом: ответ пишется в чат; варианты кнопками — после «Подсказки».
    b.italic(ui.get("reply_next", "Напиши ответ следующим сообщением — можно на любом языке."))
    b.newline()
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_no_new_round(ui):
    """Короткий честный экран, когда без повтора нельзя собрать новый раунд."""
    b = MessageBuilder()
    b.section(f"🕵️ {ui['title']}")
    b.spacer()
    b.line(ui.get("no_new", "Новой загадки сейчас нет. Попробуй позже."))
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_found(ui, answer, body="", title=""):
    """«✅ Zaak opgelost!» (или «💡 Het juiste antwoord» после ошибки), ответ жирным и курсивом — факт об ответе."""
    b = MessageBuilder()
    b.section(title or ui["found"])
    b.spacer()
    b.bold(answer)
    b.newline()
    body = " ".join(str(body or "").split())
    if body:
        b.spacer()
        b.italic(body)  # интересный факт об ответе на языке загадки — последней строкой
        b.newline()
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def _without_leading_emoji(text) -> str:
    """«🔼 Сложный (B1+)» → «Сложный (B1+)», «🇳🇱 Нидерландский» → «Нидерландский»."""
    return re.sub(r"^[^\wА-Яа-яЁё(]+", "", str(text or "")).strip()


def learning_settings(active_language, active_level=""):
    """«Язык обучения · Нидерландский 🇳🇱», «Уровень: Сложный (B1+)» одной строкой, внизу курсивом — зачем."""
    language = str(active_language or "").strip()
    flag = language.split(" ", 1)[0] if language and not language[0].isalpha() else ""
    title = _without_leading_emoji(language)
    b = MessageBuilder()
    b.section(" ".join(part for part in (f"Язык обучения · {title}", flag if flag != "🚫" else "") if part))
    b.spacer()
    if active_level:
        b.labeled_line("Уровень", _without_leading_emoji(active_level), lowercase=False)
        b.spacer()
    b.italic("Эти предпочтения влияют на карточку обучения, практику и уведомления." if active_level
             else "Выбери язык, когда захочешь вернуться к практике.")
    b.newline()
    return b.build_stripped()


def learning_level_settings(language):
    b = MessageBuilder()
    b.section("📌 Сложность обучения")
    b.spacer()
    b.bold(language)
    b.newline()
    b.spacer()
    b.text_line("Выбери сложность практики и словаря.")
    return b.build()


def removed_from_training(term):
    """«❌ Слово **Bespreken** было удалено из обучения» — одной строкой, слово жирным."""
    b = MessageBuilder()
    b.text_line("❌ Слово ")
    b.bold(str(term or "").strip())
    b.text_line(" было удалено из обучения")
    return b.build_stripped()
