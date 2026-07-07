from .builder import MessageBuilder, MessageSpec


def notifications():
    b = MessageBuilder()
    b.section("Рассылки")
    b.line("Нажми для включения/выключения. 🟢 — включено.")
    return b.build_stripped()


def priorities(current):
    b = MessageBuilder()
    b.section("Приоритеты")
    b.line("Выбери, на что боту обращать больше внимания в брифе, советах и рекомендациях.")
    b.spacer()
    b.bold("Сейчас:")
    b.line(f" {current}")
    return b.build_stripped()


def cuisines(current):
    b = MessageBuilder()
    b.section("Кухни")
    b.line("Выбери кухни, которые нравятся — подберу рецепт дня и блюда из холодильника с их учётом.")
    b.spacer()
    b.bold("Сейчас:")
    b.line(f" {current}")
    return b.build_stripped()


def body_profile(profile_line):
    b = MessageBuilder()
    b.section("🎚️ Мои параметры")
    b.line(
        "Бот использует эти данные при подборе образа и оценке покупок — "
        "чтобы советы по размеру и силуэту подходили именно тебе."
    )
    b.section("Сейчас сохранено:")
    b.line(profile_line)
    b.section("Напиши одним сообщением:")
    b.line("рост, размеры одежды, обуви и брюк, а также стиль одежды.")
    b.spacer()
    b.italic(
        "Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32. "
        "Стиль: тёмные оттенки, оверсайз, минимум принтов."
    )
    return b.build_stripped()


def city_input():
    return MessageSpec(text="🌍 Напиши город - переключу.")


def wardrobe_item_input():
    b = MessageBuilder()
    b.text_line("🏷 Напиши вещь: тип + цвет + детали/бренд.\n")
    b.italic("Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.")
    return b.build()


def lagom_input():
    b = MessageBuilder()
    b.text_line("☕️ Напиши установку или принцип — добавлю в здоровье.")
    b.blank()
    b.italic("Например: «Меньше экрана, больше природы»")
    return b.build()


def list_add_prompt(kind):
    prompts = {
        "country": "🧳 Напиши страну - добавлю в список.",
        "artist": "🎤 Напиши имя артиста - добавлю в список.",
        "book": "📚 Напиши название книги - добавлю в список.",
    }
    return MessageSpec(text=prompts.get(kind, "Напиши элемент - добавлю в список."))


def list_added(kind, item):
    icons = {"country": "🧳", "artist": "🎤", "book": "📚"}
    return MessageSpec(text=f"✅ {icons.get(kind, '')} «{item}» добавлено.")


def style_custom_input():
    b = MessageBuilder()
    b.text_line("🎨 Опиши свой стиль — как хочешь выглядеть, что нравится, что нет.")
    b.blank()
    b.italic("Например: «Люблю тёмные оттенки, оверсайз-силуэты, минимум принтов. Стараюсь избегать костюмов.»")
    return b.build()


def body_input():
    b = MessageBuilder()
    b.section("🎚️ Параметры тела")
    b.line("Напиши свободным текстом — рост, размер одежды, размер обуви и брюк.")
    b.spacer()
    b.italic("Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32")
    return b.build_stripped()


def style_pick():
    b = MessageBuilder()
    b.section("🎨 Стиль одежды")
    b.line("Выбери из предложенных или опиши своими словами — бот учтёт при подборе образа:")
    return b.build_stripped()


def settings_home():
    b = MessageBuilder()
    b.section("🎚️ Настройки")
    b.line("Настройте бота под себя и управляйте личными данными.")
    return b.build_stripped()


def leisure_settings():
    b = MessageBuilder()
    b.section("🍿 Настройки досуга")
    b.line("Кино, страны, артисты и книги для рекомендаций.")
    return b.build_stripped()


def list_section(title, items, empty_hint="Пока пусто — добавь первый элемент 👇"):
    b = MessageBuilder()
    b.section(title)
    if not items:
        b.line(empty_hint)
    return b.build_stripped()


def wardrobe_home():
    b = MessageBuilder()
    b.section("👕 Мой гардероб")
    b.line("База вещей и параметры для подбора одежды.")
    return b.build_stripped()


def countries_home():
    return MessageBuilder().section("🗺️ Мои страны").build_stripped()


def artists_home(items):
    return list_section("🎤 Мои музыканты", items)


def books_home(items):
    return list_section("📚 Мои книги", items)


def lagom_home(items):
    b = MessageBuilder()
    b.section("☕️ Лагом")
    b.text_line("Лагом (швед. ")
    b.italic("lagom")
    b.line(
        " — «в самый раз») — твой личный свод принципов: "
        "что важно, как хочешь жить, что даёт энергию, а что забирает."
    )
    b.line("Бот использует их в ☕️ Мотивация — чтобы советы звучали именно про тебя, а не общими словами.")
    b.section("Примеры:")
    b.line(" «Меньше, но лучше» · «Физическая активность каждый день» · «Не сравниваю себя с другими»")
    if not items:
        b.line("Пока пусто — добавь первый принцип 👇")
    return b.build_stripped()


def nothing_to_save():
    return MessageSpec(text="Нечего сохранять.")


def saved_to_later():
    return MessageSpec(text="⏳ Сохранено во временные закладки.")


def note_blacklisted(preview, category):
    return MessageSpec(text=f"🚫 «{preview[:50]}» - в чёрный список «{category}». Больше не порекомендую.")


def note_removed_from_later():
    return MessageSpec(text="Удалил из закладок.")


def note_moved_to_favorites(preview, category):
    return MessageSpec(text=f"❤️ «{preview[:50]}» - в любимые, раздел «{category}».")


def note_deleted():
    return MessageSpec(text="❌ Удалил.")


def favorite_card(source, date, text, entities=None):
    """Заголовок заметки + произвольное тело. Тело приходит как (text, entities) напрямую
    из уже отправленного Telegram-сообщения (q.message.entities) — никакого HTML-парсинга
    не нужно, entities только сдвигаются под заголовок через embed()."""
    b = MessageBuilder()
    b.text_line("⭐ ")
    b.bold(source)
    if date:
        b.text_line(f" · {date}")
    b.newline()
    b.spacer()
    b.embed(MessageSpec(text=text, entities=entities))
    return b.build()


def trips_empty():
    b = MessageBuilder()
    b.section("🧳 Поездки")
    b.line("Пока пусто.")
    return b.build_stripped()


def trips_home():
    b = MessageBuilder()
    b.section("🧳 Мои поездки")
    b.line("Сохранённые планы поездок.")
    b.spacer()
    b.line("Выбери план 👇")
    return b.build_stripped()


def later_home_empty():
    b = MessageBuilder()
    b.section("⭐️ Сохранить")
    b.line(
        "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
        "поездки, еда, гардероб и всё прочее."
    )
    b.spacer()
    b.line("Пока пусто — сохраняй интересное кнопкой «⭐️ Сохранить» под ответами.")
    return b.build_stripped()


def later_home():
    b = MessageBuilder()
    b.section("⭐️ Сохранить")
    b.line(
        "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
        "поездки, еда, гардероб и всё прочее."
    )
    b.spacer()
    b.line("Открой категорию, чтобы посмотреть и почистить её.")
    return b.build_stripped()


def later_group(label, desc):
    b = MessageBuilder()
    b.section(f"⭐️ Сохранить · {label}")
    b.text_line(f"Здесь лежат временные закладки: {desc}.\n")
    b.line("Открой карточку, чтобы увидеть её в исходном виде или удалить.")
    return b.build_stripped()


def favorites_home():
    b = MessageBuilder()
    b.section("❤️ Любимые")
    b.line("Твои топ-категории.")
    b.spacer()
    b.line("Выбери раздел 👇")
    return b.build_stripped()


def favorite_section(title, items):
    b = MessageBuilder()
    b.section(title)
    b.spacer()
    if items:
        for it in items[:50]:
            b.bullet(it)
    else:
        b.italic("пусто")
    return b.build_stripped()


def favorite_add_prompt(name):
    return MessageSpec(text=f"Напиши {name} — добавлю в любимые.")


def favorite_added():
    return MessageSpec(text="Добавлено.")


def admin_only():
    return MessageSpec(text="⛔ Только для администратора.")


