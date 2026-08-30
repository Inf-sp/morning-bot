"""Представление мастера начального наполнения словаря."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


LEVEL_LABELS = {
    "simple": "🔽 Простой (A1 - A2)",
    "hard": "🔼 Сложный (B1+)",
}
SEED_LEVELS = tuple(LEVEL_LABELS)
PAGE_SIZE = 5
SOURCE_NOTE = (
    "Списки собраны как частотный старт: Oxford 3000/5000, Cambridge/English "
    "Vocabulary Profile и частотные разговорные списки; редкие книжные слова исключены."
)


def _item_line(item):
    text = f"{item.get('word')} — {item.get('ru')}"
    if item.get("note"):
        text += f" ({item['note']})"
    return text


def render_text(state):
    level = state.get("level", "simple")
    items = state.get("items") or []
    selected = set(state.get("selected") or [])
    page = int(state.get("page") or 0)
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]
    label = LEVEL_LABELS.get(level, level)
    header = f"📚 Популярные слова · {label}"
    lines = [
        header,
        f"Страница {page + 1} из {total_pages}",
        "",
        "Отметьте слова, которые хотите добавить в словарь:",
        "",
    ]
    for offset, item in enumerate(chunk):
        index = start + offset
        mark = "✅" if index in selected else "□"
        lines.append(f"{mark} {_item_line(item)}")
    lines.extend(["", SOURCE_NOTE])
    return "\n".join(lines)


def render_keyboard(state):
    items = state.get("items") or []
    selected = set(state.get("selected") or [])
    page = int(state.get("page") or 0)
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    rows = []
    for offset, item in enumerate(items[start:start + PAGE_SIZE]):
        index = start + offset
        mark = "✅" if index in selected else "□"
        rows.append([InlineKeyboardButton(
            f"{mark} {item.get('word')[:38]}", callback_data=f"a_dictseed_toggle_{index}")])
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("◀️", callback_data=f"a_dictseed_page_{page - 1}"))
    if page < total_pages - 1:
        navigation.append(InlineKeyboardButton("▶️", callback_data=f"a_dictseed_page_{page + 1}"))
    if navigation:
        rows.append(navigation)
    label = LEVEL_LABELS.get(state.get("level"), LEVEL_LABELS["simple"])
    rows.append([InlineKeyboardButton(
        f"📶 Другой уровень ({label})", callback_data="a_dictseed_level")])
    add_label = f"🆕 Добавить отмеченные ({len(selected)})" if selected else "🆕 Добавить отмеченные"
    rows.insert(0, [InlineKeyboardButton(add_label, callback_data="a_dictseed_add")])
    return InlineKeyboardMarkup(rows)


def level_keyboard(code, current):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'✅ ' if level == current else ''}{LEVEL_LABELS[level]}",
            callback_data=f"a_dictseedlvl_{code}_{level}")]
        for level in SEED_LEVELS
    ] + [
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictseed_start_{code}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
