from dictionary_model import display_term, study_card_is_complete

from .builder import MessageBuilder
from .learning_entry import render_learning_entry, render_study_card


def dict_overview(nl_total, en_total):
    """Короткая карточка-меню (заголовок + одна строка счётчиков)."""
    total = nl_total + en_total
    b = MessageBuilder()
    b.section("🎚️ Мой словарь")
    b.spacer()
    b.labeled_line("Всего", f"{total} (🇳🇱 {nl_total} · 🇬🇧 {en_total})", lowercase=False)
    b.spacer()
    b.line("Добавляй слова прямо в чате: «Добавь в словарь de kater».")
    b.line("Бот сам сохранит слово и добавит его в тренировки.")
    b.spacer()
    b.line("Выбери язык.")
    return b.build_stripped()


def dict_deleted(removed=None):
    """Короткий результат удаления именно из словаря."""
    b = MessageBuilder()
    b.section("✅ Удалено")
    entry = removed if isinstance(removed, dict) else {"term": str(removed or "")}
    term = display_term(entry.get("term") or "", entry.get("article") or "")
    if term:
        b.spacer()
        b.bold(term)
        translation = str(entry.get("translation") or entry.get("ru") or "").strip()
        if translation:
            b.text_line(f" → {translation[:1].upper() + translation[1:]}")
        b.newline()
    return b.build_stripped()


def dict_category_entry(category, index, total, entry):
    """Одна полная карточка внутри перелистываемой категории словаря."""
    lang = str((entry or {}).get("lang") or "nl")
    flag = "🇬🇧" if lang == "en" else "🇳🇱"
    b = MessageBuilder()
    b.section(f"{flag} {category} · {index + 1}/{total}")
    if study_card_is_complete(entry):
        render_study_card(b, entry or {})
    else:
        render_learning_entry(b, entry or {})
    return b.build_stripped()
