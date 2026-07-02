from .builder import MessageSpec
from util import esc


def food_card(data, label="Рецепт дня"):
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
    return MessageSpec(
        text="🧊 <b>Мой холодильник</b>\n\nПусто — добавь продукты, которые обычно есть дома.",
        parse_mode="HTML",
    )


def fridge_home(count, available):
    return MessageSpec(
        text=f"🧊 <b>Мой холодильник</b> · {count} продуктов · {available} в наличии\n\nВыбери категорию:",
        parse_mode="HTML",
    )


def fridge_category(title, total, available):
    return MessageSpec(
        text=(
            f"{title} · {total} продуктов · {available} в наличии\n\n"
            "🟢 — есть в наличии  ⚪ — закончилось\n"
            "Нажми продукт, чтобы изменить статус."
        ),
        parse_mode="HTML",
    )


def fridge_updated(added_by_cat, added, duplicates, rejected, cat_order, cat_emoji, cat_labels):
    lines = ["🧊 <b>Холодильник обновлён</b>"]
    if added:
        lines += ["", "<b>Добавил:</b>"]
        for cat in cat_order:
            names = sorted(set(added_by_cat.get(cat, [])))
            if names:
                emoji = cat_emoji.get(cat, "📦")
                label = cat_labels.get(cat, cat.capitalize())
                lines.append(f"{emoji} <b>{esc(label)}:</b> {esc(', '.join(names))}")
    else:
        lines += ["", "Новых продуктов не нашёл."]
    if duplicates:
        lines += ["", "<b>Уже было:</b>", esc(", ".join(sorted(set(duplicates))[:20]))]
    if rejected:
        lines += ["", "<b>Не добавил:</b>"]
        for name, reason in rejected[:12]:
            lines.append(f"• {esc(name)} — {esc(reason)}")
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def fridge_empty_for_recipe():
    return MessageSpec(
        text=(
            "🧊 Холодильник пуст или все продукты отмечены как отсутствующие.\n\n"
            "Отметь 🟢, что есть сейчас, и попробуй снова."
        )
    )


def my_recipes_empty():
    return MessageSpec(
        text=(
            "🍳 <b>Мои рецепты</b>\n\nПусто. Сохраняй рецепты кнопкой "
            "«❤️ Сохранить рецепт» под любым рецептом."
        ),
        parse_mode="HTML",
    )


def my_recipes_list(recipes):
    text = "🍳 <b>Мои рецепты</b> — {}\n\n".format(len(recipes))
    text += "\n".join(f"• {esc(r.get('name', '?'))}" for r in recipes)
    return MessageSpec(text=text, parse_mode="HTML")
