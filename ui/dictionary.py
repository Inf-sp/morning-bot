from .builder import MessageBuilder


def dict_overview(nl_total, en_total):
    """Короткая карточка-меню (заголовок + одна строка счётчиков)."""
    total = nl_total + en_total
    b = MessageBuilder()
    b.section("🗂️ Мой словарь")
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
    term = str(entry.get("term") or "").strip()
    article = str(entry.get("article") or "").strip()
    if article and not term.casefold().startswith(article.casefold() + " "):
        term = f"{article} {term}"
    if term:
        b.spacer()
        b.bold(term[:1].upper() + term[1:])
        translation = str(entry.get("translation") or entry.get("ru") or "").strip()
        if translation:
            b.text_line(f" → {translation[:1].upper() + translation[1:]}")
        b.newline()
    return b.build_stripped()
