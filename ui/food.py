import re

from .builder import MessageBuilder
from .constants import ui_label

# Эмодзи категории приёма пищи (§7 спеки) — используется в заголовке карточки.
MEAL_EMOJI = {
    "breakfast": ui_label("breakfast", "").strip(),
    "lunch": ui_label("lunch", "").strip(),
    "dinner": ui_label("dinner", "").strip(),
    "fridge": ui_label("cook_from", "").strip(),
}
MEAL_LABEL = {
    "breakfast": "Завтрак",
    "lunch": "Обед",
    "dinner": "Ужин",
    "fridge": "Из холодильника",
}

DEFAULT_CUISINE_EMOJI = ui_label("recipes", "").strip()

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


# Национальные прилагательные по коду кухни — используются для подстраховки:
# если модель всё же вставила прилагательное кухни в name (например «Итальянские
# тосты»), срезаем его перед показом, чтобы не дублировать кухню с заголовком
# карточки («Завтрак • 🇮🇹 Итальянская кухня\nИтальянские тосты» выглядит как
# повтор). Формы во всех родах/числах, т.к. согласование с существительным заранее
# неизвестно.
_CUISINE_ADJECTIVES = {
    "asian": ["азиатск"],
    "russian": ["русск"],
    "italian": ["итальянск"],
    "mediterranean": ["средиземноморск"],
    "mexican": ["мексиканск"],
    "french": ["французск"],
    "japanese": ["японск"],
    "korean": ["корейск"],
    "chinese": ["китайск"],
    "thai": ["тайск"],
    "vietnamese": ["вьетнамск"],
    "indian": ["индийск"],
    "turkish": ["турецк"],
    "greek": ["греческ"],
    "spanish": ["испанск"],
    "german": ["немецк"],
    "american": ["американск"],
    "georgian": ["грузинск"],
}


def _strip_cuisine_from_name(name: str, cuisine_code: str) -> str:
    """Убирает национальное прилагательное кухни из начала названия блюда (см. выше)."""
    stems = _CUISINE_ADJECTIVES.get(cuisine_code)
    if not stems or not name:
        return name
    words = name.split(" ", 1)
    if not words:
        return name
    first_word_lower = words[0].lower()
    if any(first_word_lower.startswith(stem) for stem in stems):
        rest = words[1] if len(words) > 1 else ""
        return rest[:1].upper() + rest[1:] if rest else name
    return name


_STEP_TIME_RE = re.compile(
    r"\s*(?:—|-|,)?\s*(?:около\s+)?\d+(?:\s*[–-]\s*\d+)?\s*"
    r"(?:мин(?:ут(?:а|ы|у)?|\.)?)",
    re.IGNORECASE,
)

_PAIRING_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\ufe0f\u200d]+"
)


def _step_text(step) -> str:
    """Оставляет действие шага без отдельного времени и служебной детализации."""
    text = str(step.get("text", "") if isinstance(step, dict) else step).strip()
    text = _STEP_TIME_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -—,.;")
    sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
    text = " ".join(sentences[:2]) if sentences else text
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def compact_step_lines(steps) -> list[str]:
    """Сжимает приготовление до 2–3 шагов, оставляя максимум 4 для сложного блюда."""
    if isinstance(steps, str):
        steps = [steps]
    lines = [_step_text(step) for step in (steps or [])]
    lines = [line for line in lines if line]
    while len(lines) > 4:
        pair_index = min(
            range(len(lines) - 1),
            key=lambda index: len((lines[index] + " " + lines[index + 1]).split()),
        )
        lines[pair_index:pair_index + 2] = [f"{lines[pair_index]} {lines[pair_index + 1]}"]
    if len(lines) == 4:
        pairs = [
            (len((lines[index] + " " + lines[index + 1]).split()), index)
            for index in range(3)
        ]
        word_count, pair_index = min(pairs)
        if word_count <= 24:
            lines[pair_index:pair_index + 2] = [f"{lines[pair_index]} {lines[pair_index + 1]}"]
    return lines


def pairing_text(data) -> str:
    """Объединяет все подходящие напитки в одну строку без подзаголовков."""
    values = []
    seen = set()
    for key in ("pairing_wine", "pairing_drink"):
        value = " ".join(str((data or {}).get(key) or "").split())
        value = " ".join(_PAIRING_EMOJI_RE.sub("", value).split())
        folded = value.casefold()
        if value and folded not in seen:
            values.append(value)
            seen.add(folded)
    return "; ".join(values)


def food_card(
    data, label="Рецепт дня", meal=None, cuisine_emoji_fallback=None,
    show_leading_emoji=True,
):
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
    steps = compact_step_lines(steps)
    cuisine_code = str(data.get("cuisine") or "").strip().lower()
    cuisine_label = str(data.get("cuisine_label") or CUISINE_RU.get(cuisine_code) or data.get("cuisine") or "").strip()
    cuisine_emoji = str(data.get("cuisine_emoji") or "").strip()
    if not cuisine_emoji and cuisine_emoji_fallback:
        cuisine_emoji = cuisine_emoji_fallback.get(cuisine_code, "")
    if not cuisine_emoji and cuisine_label:
        cuisine_emoji = DEFAULT_CUISINE_EMOJI
    if cuisine_label:
        # кухня уже показана в заголовке — не дублируем её прилагательным в name
        name = _strip_cuisine_from_name(name, cuisine_code)
    chef_tip = str(data.get("chef_tip") or "").strip()
    if chef_tip and chef_tip[-1] not in ".!?…":
        chef_tip += "."

    b = MessageBuilder()
    meal_emoji = (
        MEAL_EMOJI.get(meal, ui_label("food", "").strip())
        if show_leading_emoji else ""
    )
    header = f"{meal_emoji} {label}".strip()
    if cuisine_label:
        header += f" · {cuisine_emoji} {cuisine_label}".rstrip()
    b.section(header)
    if name:
        b.spacer()
        b.bold(name)
    if ingredients:
        b.spacer()
        b.labeled_line("Ингредиенты", ingredients)
    missing = data.get("missing_ingredients") or []
    if isinstance(missing, str):
        missing = [missing]
    missing = [" ".join(str(item).split()) for item in missing if str(item).strip()]
    if missing:
        b.spacer()
        b.bold("Не хватает:")
        b.newline()
        b.line(", ".join(missing))
    if steps:
        b.spacer()
        b.bold("Приготовление:")
        b.newline()
        for step in steps:
            b.bullet(step)
    pairing = pairing_text(data)
    if pairing:
        b.spacer()
        b.labeled_line("К блюду подойдёт", pairing, lowercase=False)
    if chef_tip:
        b.spacer()
        b.bold("Совет шефа:")
        b.newline()
        b.line(chef_tip)
    b.spacer()
    b.bold("😋 Приятного аппетита!")
    return b.build_stripped()


def _products_label(count):
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return "продукт"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "продукта"
    return "продуктов"


def fridge_home(available):
    b = MessageBuilder()
    b.bold(ui_label("products", "Мой холодильник"))
    b.text_line(f" · {available} {_products_label(available)} в наличии")
    b.spacer()
    b.labeled_line("Выбери категорию")
    return b.build_stripped()


def fridge_category(label, total, available):
    b = MessageBuilder()
    b.bold(label)
    b.text_line(
        f" · {total} {_products_label(total)} · {available} в наличии\n\n"
        "🟢 — есть в наличии  🔴 — закончилось\n"
        "Нажми продукт, чтобы изменить статус."
    )
    return b.build()


def fridge_category_choice(name):
    b = MessageBuilder()
    b.section("🧊 Выбери категорию")
    b.line(f"Не удалось уверенно определить категорию для «{name}».")
    b.line("Куда добавить продукт?")
    return b.build_stripped()


def fridge_updated(added_by_cat, added, duplicates, rejected, cat_order, cat_emoji, cat_labels):
    b = MessageBuilder()
    b.section("🧊 Холодильник обновлён")
    if added:
        b.spacer()
        b.bold("Добавил:")
        for cat in cat_order:
            names = sorted(set(added_by_cat.get(cat, [])))
            if names:
                emoji = cat_emoji.get(cat, "")
                label = cat_labels.get(cat, cat.capitalize())
                b.newline()
                if emoji:
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
        "Отметь продукты, которые есть сейчас, и попробуй снова."
    ).build()


def my_recipes_empty():
    b = MessageBuilder()
    b.section(ui_label("recipes", "Мои рецепты"))
    b.spacer()
    b.line("Пусто. Сохраняй рецепты кнопкой «💾 Сохранить» под любым рецептом.")
    return b.build_stripped()


def my_recipes_list(recipes):
    b = MessageBuilder()
    b.bold(ui_label("recipes", "Мои рецепты"))
    b.text_line(f" — {len(recipes)}")
    b.spacer()
    for recipe in recipes:
        b.bullet(recipe.get("name", "?"))
    return b.build_stripped()
