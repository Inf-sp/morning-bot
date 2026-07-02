from .builder import MessageBuilder, MessageSpec
from util import esc


def food_card(data, label="Рецепт дня"):
    """HTML-текст карточки: рендерится через util.send_html (там же оживают LLM-теги в поле 'full')."""
    name = esc(str(data.get("name", "")).strip())
    ingredients = esc(str(data.get("ingredients", "")).strip())
    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    lines = [f"🥣 <b>{esc(label)}</b>"]
    if name:
        lines += ["", f"<b>{name}</b>"]
    if ingredients:
        lines += ["", "<b>Ингредиенты:</b>", ingredients]
    if steps:
        lines += ["", "<b>Приготовление:</b>"]
        for step in steps:
            lines.append(f"• {esc(str(step).strip())}")
    lines += ["", "<b>😋 Приятного аппетита!</b>"]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


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
