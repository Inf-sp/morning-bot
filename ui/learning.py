from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from dictionary_model import study_card_is_complete
from .builder import MessageBuilder
from .constants import ui_label
from .learning_entry import render_learning_entry, render_study_card


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
    b.section("✨ Подобрать новое задание")
    b.spacer()
    b.text_line("Слова и фразы для тренировки добавляются в разделе ")
    b.bold(ui_label("dictionary", "Словарь"))
    b.text_line(".")
    b.spacer()
    b.bold("Выбери язык для тренировки.")
    return b.build()


def morning_words(flag, words=None, empty_hint=False, *, entries=None, tip="", rule=""):
    """Глубокая карточка одного слова дня."""
    b = MessageBuilder()
    b.section(f"{flag} Слово дня")
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
        entry = prepared[0]
        if study_card_is_complete(entry):
            render_study_card(b, entry)
        else:
            render_learning_entry(b, entry)
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_card(ui, description, category=""):
    b = MessageBuilder()
    b.section(f"🕵️ {ui['title']}")
    if category:
        b.line(f"{ui.get('category', 'Категория')}: {category}")
        b.spacer()
    b.line(description)
    b.spacer()
    b.line(ui.get("reply_next", "Напиши ответ следующим сообщением — можно на любом языке."))
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


def game_found(ui, answer, body="", words=None):
    b = MessageBuilder()
    b.section(ui["found"])
    b.spacer()
    b.bold(answer)
    if body:
        b.spacer()
        b.line(str(body).strip())
    valid_words = [
        item for item in (words or [])
        if isinstance(item, dict) and item.get("word") and item.get("translation")
    ][:3]
    if valid_words:
        b.spacer()
        b.bold(ui.get("remember", "📚 Запомни:"))
        b.newline()
        for item in valid_words:
            b.bullet(f"{item['word']} → {item['translation']}")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def game_hint(ui, hint):
    b = MessageBuilder()
    b.section(ui.get("hint_title", ui["hint"]))
    b.spacer()
    b.bold(hint)
    b.spacer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["reveal"], callback_data="game_reveal", api_kwargs={"style": "danger"})],
        [
            InlineKeyboardButton(ui["back"], callback_data="m_learn"),
            InlineKeyboardButton(ui["home"], callback_data="m_menu"),
        ],
    ])
    return b.build(reply_markup=kb)


def learning_settings(active_language, active_level=""):
    b = MessageBuilder()
    b.section("📝 Предпочтения")
    b.spacer()
    b.labeled_line("Язык обучения")
    b.bold(active_language)
    b.newline()
    if active_level:
        b.spacer()
        b.labeled_line("Уровень")
        b.bold(active_level)
        b.newline()
    b.spacer()
    if active_level:
        b.text_line("Эти предпочтения влияют на карточку обучения, практику и уведомления.")
    else:
        b.text_line("Выбери язык, когда захочешь вернуться к практике.")
    return b.build()


def learning_level_settings(language):
    b = MessageBuilder()
    b.section("📌 Сложность обучения")
    b.spacer()
    b.bold(language)
    b.newline()
    b.spacer()
    b.text_line("Выбери сложность практики и словаря.")
    return b.build()
