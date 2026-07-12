from .builder import MessageBuilder


def dict_overview(nl_total, en_total):
    """Короткая карточка-меню (заголовок + одна строка счётчиков)."""
    total = nl_total + en_total
    b = MessageBuilder()
    b.section("🗂️ Мой словарь")
    b.spacer()
    b.line(f"Всего: {total} (🇳🇱 {nl_total} · 🇬🇧 {en_total})")
    b.spacer()
    b.line("Добавляй слова прямо в чате: «Добавь в словарь de kater».")
    b.line("Бот сам сохранит слово и добавит его в тренировки.")
    b.spacer()
    b.line("Выбери язык.")
    return b.build_stripped()


def dict_deleted(removed=""):
    """Принимает сырое (не эскейпленное) имя удалённого слова и сама оборачивает его в bold()."""
    b = MessageBuilder()
    b.text_line("✅ Слово")
    if removed:
        b.text_line(" ")
        b.bold(removed)
    b.text_line(" удалено из текущего списка.")
    b.spacer()
    b.text_line("Если хочешь, можно сразу открыть словарь или добавить новое.")
    return b.build_stripped()
