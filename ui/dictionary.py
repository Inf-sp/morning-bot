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


def dict_language(lang, count):
    """Единый экран словаря: счётчик + короткое описание, что делает каждая кнопка."""
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    name = "Нидерландский" if lang == "nl" else "Английский"
    b = MessageBuilder()
    b.section(f"{flag} Мой словарь · {name}")
    b.spacer()
    b.line(f"Записей: {count}")
    b.spacer()
    b.line("Добавить — пришли слово или фразу, бот сам приведёт в правильную форму, переведёт и разберёт.")
    b.line("Найти — быстро открыть одну запись по слову и удалить её, если нужно.")
    b.line("Весь список — посмотреть всё сразу и удалить несколько записей за раз.")
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
