from .builder import MessageSpec
from util import esc

ADMIN_RUN_NOTIF_TITLE = "Превью рассылки"


def notifications():
    return MessageSpec(
        text="🔔 <b>Уведомления</b>\n\nНажми для включения/выключения. 🟢 — включено.",
        parse_mode="HTML",
    )


def priorities(current):
    return MessageSpec(
        text=(
            "🎯 <b>Приоритеты</b>\n\n"
            "Выбери, на что боту обращать больше внимания в брифе, советах и рекомендациях.\n\n"
            f"<b>Сейчас:</b> {esc(current)}"
        ),
        parse_mode="HTML",
    )


def body_profile(profile_line):
    return MessageSpec(
        text=(
            "🎚️ <b>Мои параметры</b>\n\n"
            "Бот использует эти данные при подборе образа и оценке покупок — "
            "чтобы советы по размеру и силуэту подходили именно тебе.\n\n"
            f"<b>Сейчас сохранено:</b>\n{profile_line}\n\n"
            "<b>Напиши одним сообщением:</b>\n"
            "рост, размеры одежды, обуви и брюк, а также стиль одежды.\n\n"
            "<i>Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32. "
            "Стиль: тёмные оттенки, оверсайз, минимум принтов.</i>"
        ),
        parse_mode="HTML",
    )


def city_input():
    return MessageSpec(text="🌍 Напиши город - переключу.")


def wardrobe_item_input():
    return MessageSpec(
        text=(
            "🏷 Напиши вещь: тип + цвет + детали/бренд.\n"
            "<i>Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.</i>"
        ),
        parse_mode="HTML",
    )


def lagom_input():
    return MessageSpec(
        text=(
            "☕️ Напиши установку или принцип — добавлю в здоровье.\n\n"
            "<i>Например: «Меньше экрана, больше природы»</i>"
        ),
        parse_mode="HTML",
    )


def list_add_prompt(kind):
    prompts = {
        "country": "🧳 Напиши страну - добавлю в список.",
        "artist": "🎤 Напиши имя артиста - добавлю в список.",
        "book": "📚 Напиши название книги - добавлю в список.",
    }
    return MessageSpec(text=prompts.get(kind, "Напиши элемент - добавлю в список."))


def list_added(kind, item):
    icons = {"country": "🧳", "artist": "🎤", "book": "📚"}
    return MessageSpec(text=f"✅ {icons.get(kind, '')} «{esc(item)}» добавлено.", parse_mode="HTML")


def style_custom_input():
    return MessageSpec(
        text=(
            "🎨 Опиши свой стиль — как хочешь выглядеть, что нравится, что нет.\n\n"
            "<i>Например: «Люблю тёмные оттенки, оверсайз-силуэты, минимум принтов. "
            "Стараюсь избегать костюмов.»</i>"
        ),
        parse_mode="HTML",
    )


def body_input():
    return MessageSpec(
        text=(
            "🎚️ <b>Параметры тела</b>\n\n"
            "Напиши свободным текстом — рост, размер одежды, размер обуви и брюк.\n\n"
            "<i>Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32</i>"
        ),
        parse_mode="HTML",
    )


def style_pick():
    return MessageSpec(
        text="🎨 <b>Стиль одежды</b>\n\nВыбери из предложенных или опиши своими словами — бот учтёт при подборе образа:",
        parse_mode="HTML",
    )


def settings_home():
    return MessageSpec(
        text="🎚️ <b>Настройки</b>\n\nНастройте бота под себя и управляйте личными данными.",
        parse_mode="HTML",
    )


def leisure_settings():
    return MessageSpec(
        text="🍿 <b>Настройки досуга</b>\n\nКино, страны, артисты и книги для рекомендаций.",
        parse_mode="HTML",
    )


def list_section(title, items, empty_hint="Пока пусто — добавь первый элемент 👇"):
    text = title if items else f"{title}\n\n{empty_hint}"
    return MessageSpec(text=text, parse_mode="HTML")


def wardrobe_home():
    return MessageSpec(
        text="👕 <b>Мой гардероб</b>\n\nБаза вещей и параметры для подбора одежды.",
        parse_mode="HTML",
    )


def countries_home():
    return MessageSpec(text="🗺️ <b>Мои страны</b>", parse_mode="HTML")


def artists_home(items):
    return list_section("🎤 <b>Мои музыканты</b>", items)


def books_home(items):
    return list_section("📚 <b>Мои книги</b>", items)


def lagom_home(items):
    intro = (
        "☕️ <b>Лагом</b>\n\n"
        "Лагом (швед. <i>lagom</i> — «в самый раз») — твой личный свод принципов: "
        "что важно, как хочешь жить, что даёт энергию, а что забирает.\n\n"
        "Бот использует их в ☕️ Мотивация — "
        "чтобы советы звучали именно про тебя, а не общими словами.\n\n"
        "<b>Примеры:</b> «Меньше, но лучше» · «Физическая активность каждый день» · "
        "«Не сравниваю себя с другими»"
    )
    return list_section(intro, items, empty_hint="Пока пусто — добавь первый принцип 👇")


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
    header = f"⭐ <b>{esc(source)}</b>" + (f" · {esc(date)}" if date else "")
    return MessageSpec(text=header + "\n\n" + text, parse_mode="HTML")


def trips_empty():
    return MessageSpec(text="🧳 <b>Поездки</b>\n\nПока пусто.", parse_mode="HTML")


def trips_home():
    return MessageSpec(
        text="🧳 <b>Мои поездки</b>\n\nСохранённые планы поездок.\n\nВыбери план 👇",
        parse_mode="HTML",
    )


def later_home_empty():
    return MessageSpec(
        text=(
            "⏳ <b>Позже</b>\n\n"
            "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
            "поездки, еда, гардероб и всё прочее.\n\n"
            "Пока пусто — сохраняй интересное кнопкой «⏳ Позже» под ответами."
        ),
        parse_mode="HTML",
    )


def later_home():
    return MessageSpec(
        text=(
            "⏳ <b>Позже</b>\n\n"
            "Сюда попадают временные закладки из ответов: кино, книги, музыка, "
            "поездки, еда, гардероб и всё прочее.\n\n"
            "Открой категорию, чтобы посмотреть и почистить её."
        ),
        parse_mode="HTML",
    )


def later_group(label, desc):
    return MessageSpec(
        text=(
            f"⭐️ <b>Позже · {esc(label)}</b>\n\n"
            f"Здесь лежат временные закладки: {esc(desc)}.\n"
            "Открой карточку, чтобы увидеть её в исходном виде или удалить."
        ),
        parse_mode="HTML",
    )


def favorites_home():
    return MessageSpec(
        text="❤️ <b>Любимые</b>\n\nТвои топ-категории.\n\nВыбери раздел 👇",
        parse_mode="HTML",
    )


def favorite_section(title, items):
    body = "\n".join(f"• {esc(str(it))}" for it in items[:50]) if items else "<i>пусто</i>"
    return MessageSpec(text=f"<b>{esc(title)}</b>\n\n{body}", parse_mode="HTML")


def favorite_add_prompt(name):
    return MessageSpec(text=f"Напиши {name} — добавлю в любимые.")


def favorite_added():
    return MessageSpec(text="Добавлено.")


def admin_only():
    return MessageSpec(text="⛔ Только для администратора.")


def admin_home():
    return MessageSpec(
        text="🔐 <b>Администратор</b>\n\nСервисный раздел. Только для владельца.",
        parse_mode="HTML",
    )


def admin_users(entries, pending_count=0):
    lines = ["👥 <b>Пользователи</b>", ""]
    for uid, name, is_owner in entries:
        name_part = f" · {esc(name)}" if name else ""
        if is_owner:
            lines.append(f"👑 Owner{name_part}")
        else:
            lines.append(f"👤 {esc(uid)}{name_part}")
    if pending_count:
        lines.extend(["", f"⏳ Активных инвайтов: {pending_count}"])
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def admin_cost_empty():
    return MessageSpec(text="💸 <b>Расходы за 7 дней</b>\n\nДанных пока нет.", parse_mode="HTML")


def admin_cost_summary(call_count, total_tokens, providers, modules):
    lines = [
        "💸 <b>Расходы за 7 дней</b>",
        "",
        f"Вызовов: {call_count}",
        f"Токенов: ~{total_tokens:,}",
        "",
        "<b>По провайдерам:</b>",
    ]
    for label, configured, tokens, percent in providers:
        safe_label = esc(label)
        if not configured:
            lines.append(f"  {safe_label}: —")
        elif tokens:
            lines.append(f"  {safe_label}: {tokens:,} tok ({percent})")
        else:
            lines.append(f"  {safe_label}: 0 tok")
    if modules:
        lines.extend(["", "<b>Где тратится:</b>"])
        for label, tokens, percent in modules:
            lines.append(f"  {esc(label)}: {tokens:,} tok ({percent})")
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def admin_health(required, optional, state_lines):
    lines = ["<b>Статус сервисов</b>", "", "<b>Обязательные ключи</b>"]
    for key, ok in required:
        lines.append(f"  {'✅' if ok else '❌'} <code>{esc(key)}</code>")
    lines.extend(["", "<b>Опциональные ключи</b>"])
    for key, ok in optional:
        lines.append(f"  {'✅' if ok else '⚪'} <code>{esc(key)}</code>")
    lines.extend(["", "<b>Состояние</b>"])
    lines.extend(esc(line) for line in state_lines)
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def admin_llm_check(results):
    lines = ["<b>LLM check</b>", "", "Проверяю провайдеров по очереди…", ""]
    for label, ok, detail in results:
        if ok:
            lines.append(f"✅ {esc(label)}: Хорошо")
        else:
            lines.append(f"❌ {esc(label)}: {esc(detail)}")
    lines += ["", "<i>Проверка идёт последовательно, чтобы увидеть реальный ответ каждого провайдера.</i>"]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def admin_run_notifications():
    return MessageSpec(
        text=(
            f"<b>{ADMIN_RUN_NOTIF_TITLE}</b>\n\n"
            "Выбери уведомление — оно придёт тебе прямо сейчас.\n"
            "Время в кнопках показывает обычное расписание."
        ),
        parse_mode="HTML",
    )


def admin_invite(link):
    return MessageSpec(
        text=f"🔗 <b>Подарочный инвайт:</b>\n<a href=\"{esc(link)}\">{esc(link)}</a>",
        parse_mode="HTML",
    )
