import re

from .builder import MessageBuilder
from .constants import CUISINE_FLAG, ui_label

DEFAULT_CUISINE_EMOJI = ui_label("recipes", "").strip()

# Русское название кухни по машиночитаемому коду (settings.CUISINE_OPTIONS) —
# модель возвращает код, а не готовую подпись, чтобы не плодить разнобой в языке/падежах.
CUISINE_RU = {
    "european": "Европейская кухня",
    "international": "Международная кухня",
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
    "eastern_european": "Восточноевропейская кухня",
    "turkish": "Турецкая кухня",
    "greek": "Греческая кухня",
    "spanish": "Испанская кухня",
    "german": "Немецкая кухня",
    "american": "Американская кухня",
    "canadian": "Канадская кухня",
    "british": "Британская кухня",
    "dutch": "Нидерландская кухня",
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

_YOU_RE = re.compile(r"^(?:ты|вы)\s+", re.IGNORECASE)
_TIP_STOP = {"перед", "чтобы", "после", "когда", "если", "тогда", "будет", "станет", "можно", "лучше", "очень",
             "сразу", "оно", "она", "они", "его", "её"}


def recipe_sentence(text) -> str:
    """Шаг или совет рецепта: без «ты» в начале (модель пишет «ты смешай»), с заглавной буквы и точкой."""
    text = _YOU_RE.sub("", " ".join(str(text or "").split()).strip(" -•"))
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?…" else text + "."


def _stems(text) -> set:
    return {word[:5] for word in re.findall(r"[а-яёa-z]+", str(text).casefold())
            if len(word) >= 4 and word not in _TIP_STOP}


def tip_repeats_steps(tip, steps) -> bool:
    """Совет повторяет шаг: его действие уже есть в приготовлении и большая часть слов совета — тоже
    («Охлади тесто перед раскаткой» при шаге «…замеси тесто и охлади»)."""
    tip_stems = _stems(tip)
    step_stems = set().union(*(_stems(step) for step in steps)) if steps else set()
    first = next(iter(re.findall(r"[а-яёa-z]+", str(tip).casefold())), "")[:5]
    return bool(tip_stems) and first in step_stems and len(tip_stems & step_stems) * 2 >= len(tip_stems)


def recipe_tip(tip, steps) -> str:
    """Финальный совет рецепта: с заглавной буквы; повтор шага — не показываем."""
    tip = recipe_sentence(tip)
    return "" if not tip or tip_repeats_steps(tip, steps) else tip


def _step_text(step) -> str:
    """Оставляет действие шага без отдельного времени и служебной детализации."""
    text = str(step.get("text", "") if isinstance(step, dict) else step).strip()
    text = _STEP_TIME_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -—,.;")
    sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
    text = " ".join(sentences[:2]) if sentences else text
    return recipe_sentence(text)


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


def food_card(data, cuisine_emoji_fallback=None, show_cuisine_emoji=True):
    """Карточка рецепта. Живёт в store.last_recipe/last_answer только до рестарта;
    MessageBuilder сохраняет разметку без промежуточного HTML.

    Заголовок «🇮🇹 Что приготовить • Кухня»: флаг кухни, без него — 🍳 (docs/food.md).
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
    chef_tip = recipe_tip(data.get("chef_tip"), steps)

    b = MessageBuilder()
    header = f"{CUISINE_FLAG.get(cuisine_code, '🍳')} Что приготовить"
    if cuisine_label:
        cuisine_part = f"{cuisine_emoji} {cuisine_label}" if show_cuisine_emoji else cuisine_label
        header = f"{header} • {cuisine_part}"
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
    if chef_tip:
        b.spacer()
        b.italic(chef_tip)  # финальная подсказка экрана — курсивом
        b.newline()
    return b.build_stripped()


def fridge_home(favorites):
    """«🎚️ Мои продукты · 24» и подсказка курсивом."""
    b = MessageBuilder()
    b.bold(f"🎚️ Мои продукты · {favorites}")
    b.newline()
    b.spacer()
    b.italic("Отмечай в категориях, что любишь и обычно покупаешь, — рецепт «Из моих продуктов» соберу из них.")
    b.newline()
    return b.build_stripped()


def fridge_category(label, total, favorites):
    """«Мясо и рыба · 5 из 15» — любимых из всех продуктов категории."""
    b = MessageBuilder()
    b.bold(label)
    b.text_line(f" · {favorites} из {total}")
    return b.build_stripped()


def fridge_updated(added_by_cat, added, duplicates, rejected, cat_order, cat_emoji, cat_labels):
    b = MessageBuilder()
    b.section("Мои продукты обновлены")
    if added:
        b.spacer()
        b.bold("Отметил любимыми:")
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
        b.text_line("Новых любимых не отметил.")
    if duplicates:
        b.spacer()
        b.bold("Уже любимые:")
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
        "Моих продуктов пока нет.\n\n"
        "Отметь в «Любимых продуктах», что любишь и обычно покупаешь, и попробуй снова."
    ).build()
