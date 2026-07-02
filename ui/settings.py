from .builder import MessageBuilder, MessageSpec

ADMIN_RUN_NOTIF_TITLE = "Превью рассылки"


def notifications():
    b = MessageBuilder()
    b.bold("🔔 Уведомления")
    b.blank()
    b.text_line("Нажми для включения/выключения. 🟢 — включено.")
    return b.build()


def priorities(current):
    b = MessageBuilder()
    b.bold("🎯 Приоритеты")
    b.blank()
    b.text_line("Выбери, на что боту обращать больше внимания в брифе, советах и рекомендациях.")
    b.blank()
    b.bold("Сейчас:")
    b.text_line(f" {current}")
    return b.build()


def body_profile(profile_line):
    b = MessageBuilder()
    b.bold("🎚️ Мои параметры")
    b.blank()
    b.text_line(
        "Бот использует эти данные при подборе образа и оценке покупок — "
        "чтобы советы по размеру и силуэту подходили именно тебе."
    )
    b.blank()
    b.bold("Сейчас сохранено:")
    b.newline()
    b.text_line(profile_line)
    b.blank()
    b.bold("Напиши одним сообщением:")
    b.newline()
    b.text_line("рост, размеры одежды, обуви и брюк, а также стиль одежды.")
    b.blank()
    b.italic(
        "Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32. "
        "Стиль: тёмные оттенки, оверсайз, минимум принтов."
    )
    return b.build()


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
    b.bold("🎚️ Параметры тела")
    b.blank()
    b.text_line("Напиши свободным текстом — рост, размер одежды, размер обуви и брюк.")
    b.blank()
    b.italic("Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32")
    return b.build()


def style_pick():
    b = MessageBuilder()
    b.bold("🎨 Стиль одежды")
    b.blank()
    b.text_line("Выбери из предложенных или опиши своими словами — бот учтёт при подборе образа:")
    return b.build()


def settings_home():
    b = MessageBuilder()
    b.bold("🎚️ Настройки")
    b.blank()
    b.text_line("Настройте бота под себя и управляйте личными данными.")
    return b.build()


def leisure_settings():
    b = MessageBuilder()
    b.bold("🍿 Настройки досуга")
    b.blank()
    b.text_line("Кино, страны, артисты и книги для рекомендаций.")
    return b.build()


def list_section(title, items, empty_hint="Пока пусто — добавь первый элемент 👇"):
    b = MessageBuilder()
    b.bold(title)
    if not items:
        b.blank()
        b.text_line(empty_hint)
    return b.build()


def wardrobe_home():
    b = MessageBuilder()
    b.bold("👕 Мой гардероб")
    b.blank()
    b.text_line("База вещей и параметры для подбора одежды.")
    return b.build()


def countries_home():
    return MessageBuilder().bold("🗺️ Мои страны").build()


def artists_home(items):
    return list_section("🎤 Мои музыканты", items)


def books_home(items):
    return list_section("📚 Мои книги", items)


def lagom_home(items):
    b = MessageBuilder()
    b.bold("☕️ Лагом")
    b.blank()
    b.text_line("Лагом (швед. ")
    b.italic("lagom")
    b.text_line(
        " — «в самый раз») — твой личный свод принципов: "
        "что важно, как хочешь жить, что даёт энергию, а что забирает."
    )
    b.blank()
    b.text_line("Бот использует их в ☕️ Мотивация — чтобы советы звучали именно про тебя, а не общими словами.")
    b.blank()
    b.bold("Примеры:")
    b.text_line(" «Меньше, но лучше» · «Физическая активность каждый день» · «Не сравниваю себя с другими»")
    if not items:
        b.blank()
        b.text_line("Пока пусто — добавь первый принцип 👇")
    return b.build()


def nothing_to_save():
    return MessageSpec(text="Нечего сохранять.")


def saved_to_later():
    return MessageSpec(text="⏳ Сохранено во временные закладки.")


def note_action_prompt(preview):
    return MessageSpec(text=f"Что сделать с «{preview[:60]}»?")


def note_blacklisted(preview, category):
    return MessageSpec(text=f"🚫 «{preview[:50]}» - в чёрный список «{category}». Больше не порекомендую.")


def note_removed_from_later():
    return MessageSpec(text="Удалил из закладок.")


def note_moved_to_favorites(preview, category):
    return MessageSpec(text=f"❤️ «{preview[:50]}» - в любимые, раздел «{category}».")


def note_deleted():
    return MessageSpec(text="❌ Удалил.")


def favorite_card(source, date, text):
    """Текст заметки произвольный (из q.message.text_html) — держим на HTML, рендерится через send_html."""
    from util import esc
    header = f"⭐ <b>{esc(source)}</b>" + (f" · {esc(date)}" if date else "")
    return MessageSpec(text=header + "\n\n" + text, parse_mode="HTML")


def trips_empty():
    b = MessageBuilder()
    b.bold("🧳 Поездки")
    b.blank()
    b.text_line("Пока пусто.")
    return b.build()


def trips_home():
    b = MessageBuilder()
    b.bold("🧳 Мои поездки")
    b.blank()
    b.text_line("Сохранённые планы поездок.")
    b.blank()
    b.text_line("Выбери план 👇")
    return b.build()


def later_home_empty():
    b = MessageBuilder()
    b.bold("⏳ Позже")
    b.blank()
    b.text_line(
        "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
        "поездки, еда, гардероб и всё прочее."
    )
    b.blank()
    b.text_line("Пока пусто — сохраняй интересное кнопкой «⏳ Позже» под ответами.")
    return b.build()


def later_home():
    b = MessageBuilder()
    b.bold("⏳ Позже")
    b.blank()
    b.text_line(
        "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
        "поездки, еда, гардероб и всё прочее."
    )
    b.blank()
    b.text_line("Открой категорию, чтобы посмотреть и почистить её.")
    return b.build()


def later_group(label, desc):
    b = MessageBuilder()
    b.bold(f"⭐️ Позже · {label}")
    b.blank()
    b.text_line(f"Здесь лежат временные закладки: {desc}.\n")
    b.text_line("Открой карточку, чтобы увидеть её в исходном виде или удалить.")
    return b.build()


def favorites_home():
    b = MessageBuilder()
    b.bold("❤️ Любимые")
    b.blank()
    b.text_line("Твои топ-категории.")
    b.blank()
    b.text_line("Выбери раздел 👇")
    return b.build()


def favorite_section(title, items):
    b = MessageBuilder()
    b.bold(title)
    b.blank()
    if items:
        b.text_line("\n".join(f"• {it}" for it in items[:50]))
    else:
        b.italic("пусто")
    return b.build()


def favorite_add_prompt(name):
    return MessageSpec(text=f"Напиши {name} — добавлю в любимые.")


def favorite_added():
    return MessageSpec(text="Добавлено.")


def admin_only():
    return MessageSpec(text="⛔ Только для администратора.")


def admin_home():
    b = MessageBuilder()
    b.bold("🔐 Администратор")
    b.blank()
    b.text_line("Сервисный раздел. Только для владельца.")
    return b.build()


def admin_users(entries, pending_count=0):
    b = MessageBuilder()
    b.bold("👥 Пользователи")
    for uid, name, is_owner in entries:
        name_part = f" · {name}" if name else ""
        b.newline()
        b.text_line(f"👑 Owner{name_part}" if is_owner else f"👤 {uid}{name_part}")
    if pending_count:
        b.blank()
        b.text_line(f"⏳ Активных инвайтов: {pending_count}")
    return b.build()


def admin_cost_empty():
    b = MessageBuilder()
    b.bold("💸 Расходы за 7 дней")
    b.blank()
    b.text_line("Данных пока нет.")
    return b.build()


def admin_cost_summary(call_count, total_tokens, providers, modules):
    b = MessageBuilder()
    b.bold("💸 Расходы за 7 дней")
    b.newline()
    b.newline()
    b.text_line(f"Вызовов: {call_count}\nТокенов: ~{total_tokens:,}")
    b.blank()
    b.bold("По провайдерам:")
    for label, configured, tokens, percent in providers:
        b.newline()
        if not configured:
            b.text_line(f"  {label}: —")
        elif tokens:
            b.text_line(f"  {label}: {tokens:,} tok ({percent})")
        else:
            b.text_line(f"  {label}: 0 tok")
    if modules:
        b.blank()
        b.bold("Где тратится:")
        for label, tokens, percent in modules:
            b.newline()
            b.text_line(f"  {label}: {tokens:,} tok ({percent})")
    return b.build()


def admin_health(required, optional, state_lines):
    b = MessageBuilder()
    b.bold("Статус сервисов")
    b.blank()
    b.bold("Обязательные ключи")
    for key, ok in required:
        b.newline()
        b.text_line(f"  {'✅' if ok else '❌'} ")
        b.code(key)
    b.blank()
    b.bold("Опциональные ключи")
    for key, ok in optional:
        b.newline()
        b.text_line(f"  {'✅' if ok else '⚪'} ")
        b.code(key)
    b.blank()
    b.bold("Состояние")
    for line in state_lines:
        b.newline()
        b.text_line(line)
    return b.build()


def admin_llm_check(results):
    b = MessageBuilder()
    b.bold("LLM check")
    b.blank()
    b.text_line("Проверяю провайдеров по очереди…")
    for label, ok, detail in results:
        b.blank()
        if ok:
            b.text_line(f"✅ {label}: Хорошо")
        else:
            b.text_line(f"❌ {label}: {detail}")
    b.blank()
    b.italic("Проверка идёт последовательно, чтобы увидеть реальный ответ каждого провайдера.")
    return b.build()


def admin_run_notifications():
    b = MessageBuilder()
    b.bold(ADMIN_RUN_NOTIF_TITLE)
    b.blank()
    b.text_line("Выбери уведомление — оно придёт тебе прямо сейчас.\nВремя в кнопках показывает обычное расписание.")
    return b.build()


def admin_invite(link):
    b = MessageBuilder()
    b.text_line("🔗 ")
    b.bold("Подарочный инвайт:")
    b.newline()
    b.link(link, link)
    return b.build()
