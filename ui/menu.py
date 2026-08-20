import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from .text import finish_dot
from .builder import MessageBuilder, MessageSpec
from .constants import CUISINE_EMOJI, LANGUAGE_EMOJI, ui_label
from .food import CUISINE_RU

UI_MYDAY = ui_label("myday", "").strip()
UI_WARDROBE = ui_label("wardrobe", "").strip()
UI_FOOD = ui_label("food", "").strip()
UI_LEARNING = ui_label("learning", "").strip()
UI_TRAVEL = ui_label("travel", "").strip()
UI_SETTINGS = ui_label("settings", "").strip()


def _add_footer(b: MessageBuilder):
    """Общий футер для экранов меню: подсказка про Настройки, bold только на слове."""
    b.spacer()
    b.text_line(f"Изменить параметры или посмотреть сохранённую информацию можно в {UI_SETTINGS} ")
    b.bold("Настройках")
    b.text_line(".")
    return b


def _screen_message(emoji: str, title: str, description, rows, show_footer: bool = True) -> MessageSpec:
    """Строит экран меню: 'emoji жирный_заголовок' + описание + общий футер настроек."""
    b = MessageBuilder()
    b.text_line(f"{emoji} ")
    b.bold(title)
    b.newline()
    b.spacer()
    if isinstance(description, (list, tuple)):
        for line in description:
            b.line(line)
    else:
        b.line(description)
    if show_footer:
        _add_footer(b)
    return b.build_stripped(reply_markup=ikb(rows))


def ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])


def main_menu_rows():
    return [
        [(ui_label("myday", "Мой день"), "m_myday")],
        [(ui_label("wardrobe", "Гардероб"), "m_wardrobe"), (ui_label("food", "Готовка"), "m_food")],
        [(ui_label("learning", "Обучение"), "m_learn"), (ui_label("travel", "Поездки"), "m_travel")],
        [(ui_label("cinema", "Кино"), "m_movie"), (ui_label("music", "Музыка"), "m_music")],
        [(ui_label("books", "Книги"), "m_books"), ("👾 Игры", "m_games")],
        [(ui_label("settings", "Настройки"), "m_settings")],
    ]


def main_menu_kb():
    return ikb(main_menu_rows())


def welcome(name: str = ""):
    name = str(name or "").strip()
    greeting = f"👋🏻 Привет, {name}! Я DM — твой помощник на каждый день." if name else "👋🏻 Привет! Я DM — твой помощник на каждый день."
    b = MessageBuilder()
    b.bold(greeting)
    b.newline()
    b.spacer()
    b.line("Подберу образ по погоде, найду рецепт из продуктов дома, помогу с языком или спланирую поездку.")
    b.spacer()
    b.line("Выбирай раздел в меню или просто пиши мне здесь 💬")
    return b.build()


def inactivity_reminder():
    b = MessageBuilder()
    b.section("🫪 Давно не виделись")
    b.spacer()
    b.line("Загляни - подберу образ на сегодня, помогу с планами или предложу что-то полезное.")
    b.spacer()
    b.line("Выбери раздел или просто напиши, что нужно.")
    return b.build_stripped(reply_markup=main_menu_kb())


_SCREENS = {
    "m_myday": (
        UI_MYDAY,
        "Мой день",
        "Соберу короткую сводку: погоду, планы, образ и одну полезную мысль на сегодня.",
        [
            [("☀️ Открыть мой день", "a_plany")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_wardrobe": (
        UI_WARDROBE,
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [("✨ Подобрать другой образ", "w_look")],
            [("🎚️ Мой шкаф", "w_closet")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_food": (
        UI_FOOD,
        "Готовка",
        "Подберу блюдо из того, что есть дома, и покажу короткий понятный рецепт.",
        [
            [("✨ Подобрать рецепт", "m_food_gen")],
            [("🎚️ Мой холодильник", "as_fridge_home")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_travel": (
        UI_TRAVEL,
        "Поездки",
        "Предложу направление с понятным маршрутом и главными причинами поехать именно туда.",
        [
            [("✨ Куда поехать", "a_trav_go")],
            [("🎚️ Мой чемодан", "a_trav_countries_0")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_music": (
        "🎧",
        "Музыка",
        "Подберу артиста под твой вкус, а концерты и любимых исполнителей оставлю рядом.",
        [
            [("✨ Подобрать новую музыку", "music_reco")],
            [("🎫 Концерты", "a_concerts_find")],
            [("🎚️ Мои артисты", "artist_favorites")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_movie": (
        "🎬",
        "Кино",
        "Подберу фильм или сериал под твой вкус, покажу ближайшие премьеры в твоей стране.",
        [
            [("✨ Подобрать новое кино", "movie_reco")],
            [("🎟️ Премьеры", "movie_premieres")],
            [("🎚️ Моё кино", "movie_favorites")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_books": (
        "📚",
        "Книги",
        "Подберу книгу под твой вкус и покажу свежие премьеры месяца.",
        [
            [("✨ Подобрать новую книгу", "book_reco")],
            [("🆕 Премьеры", "book_premieres")],
            [("🎚️ Мои книги", "book_favorites")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
}


def learning_menu(home: dict):
    """Главный экран обучения: материал дня, прогресс и следующий шаг."""
    if home.get("disabled"):
        b = MessageBuilder()
        b.section("🧠 Обучение")
        b.spacer()
        b.line("Изучение языка выключено.")
        b.spacer()
        b.line("Выбери нидерландский или английский в предпочтениях, когда захочешь вернуться к практике.")
        return b.build_stripped(reply_markup=ikb([
            [("📌 Язык обучения", "set_learning")],
            [("#️⃣ Главная", "m_menu")],
        ]))
    code = home.get("lang_code", "nl")
    flag = LANGUAGE_EMOJI.get(code, LANGUAGE_EMOJI["nl"])
    title = "Английский" if code == "en" else "Нидерландский"

    b = MessageBuilder()
    if not home.get("has_material"):
        b.section("🧠 Обучение")
        b.spacer()
        b.line("Добавляй сюда слова и фразы, которые хочешь запомнить.")
        b.spacer()
        b.line("Можно просто написать мне в чате:")
        b.line("«добавь в словарь wennen aan»")
        b.spacer()
        b.line("Я буду использовать их в практике и повторении.")
        return b.build_stripped(reply_markup=ikb([
            [("🆕 Добавить слова", f"a_dictadd_smart_{code}")],
            [("✨ Подобрать новые слова", f"a_dictseed_start_{code}")],
        ]))

    b.bold(f"{flag} Изучаем сегодня · {title}")
    b.newline()

    phrase = home.get("live_language") or {}
    if phrase.get("text") and phrase.get("translation"):
        b.spacer()
        b.bold("Живой язык:")
        b.text_line(f" {phrase['text']} → ")
        b.add(finish_dot(phrase["translation"]), MessageEntity.SPOILER)
        b.newline()

    progress = home.get("progress") or {}
    b.spacer()
    b.bold("Прогресс:")
    b.newline()
    b.bullet(f"В изучении {progress.get('total', 0)} слов и фраз")
    b.bullet(f"Повторить сегодня — {progress.get('due_count', 0)}")
    b.bullet(f"Без подсказок — {progress.get('no_hint_pct', 0)}%")
    b.spacer()
    focus = home.get("focus") or "добавить первые слова для практики."
    b.text_line("💡 ")
    b.label("Полезно", focus)

    return b.build_stripped(reply_markup=ikb([
        [(ui_label("word_trainer", "Практика изучения языка"), f"a_train_{code}")],
        [(ui_label("game", "Угадай персонажа"), "a_game")],
        [("🎚️ Мой словарь", f"a_dictlang_{code}_from_menu")],
        [("#️⃣ Главная", "m_menu")],
    ]))


def menu_screen(key):
    if key not in _SCREENS:
        return MessageSpec(text="Выбери раздел через /menu.")
    screen = _SCREENS[key]
    if len(screen) == 4:
        emoji, title, description, rows = screen
        show_footer = True
    else:
        emoji, title, description, rows, show_footer = screen
    return _screen_message(emoji, title, description, rows, show_footer=show_footer)


_COOKING_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\u2190-\u21FF\u2300-\u23FF\u2B00-\u2BFF\ufe0f\u200d\U0001F3FB-\U0001F3FF]+"
)


def _cooking_text(value) -> str:
    value = _COOKING_EMOJI_RE.sub("", str(value or ""))
    return " ".join(value.split()).strip(" -•")


def _cooking_sentence(value) -> str:
    value = _cooking_text(value)
    if value and value[-1] not in ".!?…":
        value += "."
    return value


def food_menu(idea=None):
    """Главный экран Готовки: один полный рецепт из холодильника."""
    idea = idea or {}
    b = MessageBuilder()
    cuisine_code = str(idea.get("cuisine") or "").strip().lower()
    cuisine_flag = CUISINE_EMOJI.get(cuisine_code, CUISINE_EMOJI["international"])
    cuisine_name = CUISINE_RU.get(cuisine_code, CUISINE_RU["international"])
    b.section(f"{cuisine_flag} Готовим сегодня · {cuisine_name}")

    name = _cooking_text(idea.get("name"))
    if name:
        b.spacer()
        b.bold(name)
        b.newline()

    ingredients = [_cooking_text(item) for item in (idea.get("ingredients") or [])]
    ingredients = [item for item in ingredients if item]
    if ingredients:
        b.spacer()
        b.labeled_line("Ингредиенты", ", ".join(ingredients))
    missing = idea.get("missing_ingredients") or idea.get("missing") or []
    if isinstance(missing, str):
        missing = [missing]
    missing = [str(item).strip() for item in missing if str(item).strip()]
    if missing:
        b.spacer()
        b.bold("Не хватает:")
        b.newline()
        b.line(", ".join(missing))

    steps = []
    for raw_step in (idea.get("steps") or [])[:3]:
        step = raw_step if isinstance(raw_step, dict) else {"text": raw_step}
        text = _cooking_text(step.get("text"))
        minutes = step.get("minutes")
        if text and minutes and not re.search(r"\d+(?:\s*[–-]\s*\d+)?\s*мин", text, re.I):
            text = f"{text.rstrip('.!?…')} — {int(minutes)} мин"
        text = _cooking_sentence(text)
        if text:
            steps.append(text)
    if steps:
        b.spacer()
        b.bold("Приготовление:")
        b.newline()
        for step in steps:
            b.bullet(step)
    tip = _cooking_sentence(idea.get("tip"))
    if tip:
        b.spacer()
        b.text_line("💡 ")
        b.labeled_line("Полезно", tip)

    rows = [
        [("✨ Подобрать другой рецепт", "m_food_next")],
        [(ui_label("breakfast", "Завтрак"), "a_recipe_breakfast"), (ui_label("lunch", "Обед"), "a_recipe_lunch"), (ui_label("dinner", "Ужин"), "a_recipe_dinner")],
        [("🎚️ Мой холодильник", "as_fridge_home")],
        [("#️⃣ Главная", "m_menu")],
    ]
    return b.build_stripped(reply_markup=ikb(rows))


def food_empty_menu():
    b = MessageBuilder()
    b.section("🥣 Готовка")
    b.spacer()
    b.line("Добавь продукты, которые обычно есть дома.")
    b.spacer()
    b.line("Я буду подбирать простые рецепты из них и показывать, чего не хватает.")
    return b.build_stripped(reply_markup=ikb([
        [("🧊 Заполнить холодильник", "as_fridge_add")],
        [("#️⃣ Главная", "m_menu")],
    ]))
