from .builder import MessageBuilder, MessageSpec


def food_card(data, label="Рецепт дня"):
    """Карточка рецепта. Не пишется в БД как HTML: живёт в store.last_recipe/last_answer
    только до рестарта, а в заметки (NOTES_KEY) попадает через save_fav, который берёт
    entities напрямую из уже отправленного сообщения — MessageBuilder тут ничем не хуже HTML."""
    name = str(data.get("name", "")).strip()
    ingredients = str(data.get("ingredients", "")).strip()
    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    b = MessageBuilder()
    b.section(f"🥣 {label}")
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
            b.bullet(str(step).strip())
    b.spacer()
    b.bold("😋 Приятного аппетита!")
    return b.build_stripped()


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
