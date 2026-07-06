from .builder import MessageBuilder, MessageSpec

# Эмодзи категории приёма пищи (§7 спеки) — используется в заголовке карточки.
MEAL_EMOJI = {
    "breakfast": "🍳",
    "lunch": "🥗",
    "dinner": "🍽️",
    "fridge": "🧊",
}
MEAL_LABEL = {
    "breakfast": "Завтрак",
    "lunch": "Обед",
    "dinner": "Ужин",
    "fridge": "Из холодильника",
}

DEFAULT_CUISINE_EMOJI = "🍽️"

# Русское название кухни по машиночитаемому коду (balance.RECIPE_CUISINE_CODES) —
# модель возвращает код, а не готовую подпись, чтобы не плодить разнобой в языке/падежах.
CUISINE_RU = {
    "asian": "Азиатская кухня",
    "russian": "Русская кухня",
    "italian": "Итальянская кухня",
    "mediterranean": "Средиземноморская кухня",
    "mexican": "Мексиканская кухня",
    "french": "Французская кухня",
    "japanese": "Японская кухня",
    "korean": "Корейская кухня",
    "chinese": "Китайская кухня",
    "thai": "Тайская кухня",
    "vietnamese": "Вьетнамская кухня",
    "indian": "Индийская кухня",
    "turkish": "Турецкая кухня",
    "greek": "Греческая кухня",
    "spanish": "Испанская кухня",
    "german": "Немецкая кухня",
    "american": "Американская кухня",
    "georgian": "Грузинская кухня",
}


def _step_line(step) -> str:
    """Рендерит один шаг приготовления: строка или {"text":..., "minutes":...} (§7)."""
    if isinstance(step, dict):
        text = str(step.get("text", "")).strip()
        minutes = step.get("minutes")
        if text and minutes:
            return f"{text} — {minutes} мин."
        return text
    return str(step).strip()


def food_card(data, label="Рецепт дня", meal=None, cuisine_emoji_fallback=None):
    """Карточка рецепта. Не пишется в БД как HTML: живёт в store.last_recipe/last_answer
    только до рестарта, а в заметки (NOTES_KEY) попадает через save_fav, который берёт
    entities напрямую из уже отправленного сообщения — MessageBuilder тут ничем не хуже HTML.

    meal — код категории ("breakfast"/"lunch"/"dinner"/"fridge") для эмодзи в заголовке
    (§7); если не передан, используется общий 🥣 + label, как раньше.
    cuisine_emoji_fallback — словарь {cuisine_code: emoji} для случая, когда модель не
    вернула cuisine_emoji (§7 — обязателен fallback на случай пустого/нераспознанного значения)."""
    name = str(data.get("name", "")).strip()
    ingredients = str(data.get("ingredients", "")).strip()
    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    cuisine_code = str(data.get("cuisine") or "").strip().lower()
    cuisine_label = str(data.get("cuisine_label") or CUISINE_RU.get(cuisine_code) or data.get("cuisine") or "").strip()
    cuisine_emoji = str(data.get("cuisine_emoji") or "").strip()
    if not cuisine_emoji and cuisine_emoji_fallback:
        cuisine_emoji = cuisine_emoji_fallback.get(cuisine_code, "")
    if not cuisine_emoji and cuisine_label:
        cuisine_emoji = DEFAULT_CUISINE_EMOJI
    chef_tip = str(data.get("chef_tip") or "").strip()

    b = MessageBuilder()
    meal_emoji = MEAL_EMOJI.get(meal, "🥣")
    header = f"{meal_emoji} {label}"
    if cuisine_label:
        header += f" • {cuisine_emoji} {cuisine_label}".rstrip()
    b.section(header)
    if name:
        b.spacer()
        b.bold(name)
    if ingredients:
        b.spacer()
        b.bold("Ингредиенты:")
        b.newline()
        b.line(ingredients)
    if steps:
        b.spacer()
        b.bold("Приготовление:")
        b.newline()
        for step in steps:
            line = _step_line(step)
            if line:
                b.bullet(line)
    if chef_tip:
        b.spacer()
        b.bold("Совет шефа:")
        b.newline()
        b.line(chef_tip)
    b.spacer()
    b.bold("😋 Приятного аппетита!")
    return b.build_stripped()


TELEGRAM_CAPTION_LIMIT = 1024


def fit_caption(msg: MessageSpec) -> MessageSpec:
    """Обрезает MessageSpec под лимит caption у send_photo (§7 спеки).

    Telegram ограничивает caption 1024 символами (в UTF-16 code units — как и entities,
    см. builder.u16_len). Если карточка не влезает, обрезаем текст по границе строки,
    сохраняя структуру (не разрывая слово на середине), а не переходим на два сообщения.
    entities, выходящие за обрезанную длину, отбрасываются/укорачиваются вместе с текстом."""
    from .builder import u16_len, MessageEntity

    text = msg.text
    if u16_len(text) <= TELEGRAM_CAPTION_LIMIT:
        return msg

    # Обрезаем по UTF-16 длине, по границе последнего переноса строки в пределах лимита,
    # чтобы не рвать структуру карточки. Но если это съедает больше ~15% лимита (длинная
    # строка без переносов — например совет шефа), откатываемся к границе слова, чтобы
    # не выбрасывать блок целиком.
    encoded = text.encode("utf-16-le")
    cut_units = TELEGRAM_CAPTION_LIMIT - 1  # запас на многоточие
    truncated = encoded[: cut_units * 2].decode("utf-16-le", errors="ignore")
    last_newline = truncated.rfind("\n")
    if last_newline > 0 and (len(truncated) - last_newline) <= cut_units * 0.15:
        truncated = truncated[:last_newline]
    else:
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
    truncated = truncated.rstrip() + "…"

    new_len = u16_len(truncated)
    kept_entities = []
    for e in (msg.entities or []):
        if e.offset >= new_len:
            continue
        length = min(e.length, new_len - e.offset)
        if length <= 0:
            continue
        kept_entities.append(MessageEntity(e.type, e.offset, length, url=getattr(e, "url", None)))
    return MessageSpec(text=truncated, entities=kept_entities, reply_markup=msg.reply_markup, parse_mode=msg.parse_mode)


def fridge_home_empty():
    b = MessageBuilder()
    b.section("🧊 Мой холодильник")
    b.spacer()
    b.line("Пусто — добавь продукты, которые обычно есть дома.")
    return b.build_stripped()


def fridge_home(count, available):
    b = MessageBuilder()
    b.bold("🧊 Мой холодильник")
    b.text_line(f" · {count} продуктов · {available} в наличии")
    b.spacer()
    b.line("Выбери категорию:")
    return b.build_stripped()


def fridge_category(emoji, label, total, available):
    b = MessageBuilder()
    b.text_line(f"{emoji} ")
    b.bold(label)
    b.text_line(
        f" · {total} продуктов · {available} в наличии\n\n"
        "🟢 — есть в наличии  ⚪ — закончилось\n"
        "Нажми продукт, чтобы изменить статус."
    )
    return b.build()


def fridge_updated(added_by_cat, added, duplicates, rejected, cat_order, cat_emoji, cat_labels):
    b = MessageBuilder()
    b.section("🧊 Холодильник обновлён")
    if added:
        b.spacer()
        b.bold("Добавил:")
        for cat in cat_order:
            names = sorted(set(added_by_cat.get(cat, [])))
            if names:
                emoji = cat_emoji.get(cat, "📦")
                label = cat_labels.get(cat, cat.capitalize())
                b.newline()
                b.text_line(f"{emoji} ")
                b.bold(f"{label}:")
                b.text_line(f" {', '.join(names)}")
    else:
        b.spacer()
        b.text_line("Новых продуктов не нашёл.")
    if duplicates:
        b.spacer()
        b.bold("Уже было:")
        b.newline()
        b.text_line(", ".join(sorted(set(duplicates))[:20]))
    if rejected:
        b.spacer()
        b.bold("Не добавил:")
        for name, reason in rejected[:12]:
            b.newline()
            b.text_line(f"• {name} — {reason}")
    return b.build_stripped()


def fridge_empty_for_recipe():
    return MessageBuilder().text_line(
        "🧊 Холодильник пуст или все продукты отмечены как отсутствующие.\n\n"
        "Отметь 🟢, что есть сейчас, и попробуй снова."
    ).build()


def my_recipes_empty():
    b = MessageBuilder()
    b.section("🍳 Мои рецепты")
    b.spacer()
    b.line("Пусто. Сохраняй рецепты кнопкой «❤️ Сохранить рецепт» под любым рецептом.")
    return b.build_stripped()


def my_recipes_list(recipes):
    b = MessageBuilder()
    b.bold("🍳 Мои рецепты")
    b.text_line(f" — {len(recipes)}")
    b.spacer()
    for recipe in recipes:
        b.bullet(recipe.get("name", "?"))
    return b.build_stripped()
