from .builder import MessageSpec
from util import esc


FIRSTVISIT_PROMPTS = {
    "wardrobe": (
        "👕 <b>Настроим гардероб</b>\n\n"
        "Напиши в свободном виде:\n"
        "• Твой стиль одежды (минимализм, casual, streetwear…)\n"
        "• Любимые вещи или бренды\n"
        "• Размеры: одежда, обувь, брюки\n\n"
        "<i>Пример: Люблю минимализм и оверсайз. Uniqlo, Nike. "
        "Размер M, обувь EU 43, брюки W32 L32</i>"
    ),
    "learn": (
        "📚 <b>Настроим обучение</b>\n\n"
        "Какие языки изучаешь и какой у тебя уровень?\n\n"
        "<i>Пример: нидерландский A2, английский B1</i>"
    ),
    "leisure": (
        "🍿 <b>Расскажи о своих предпочтениях</b>\n\n"
        "Напиши в любом виде:\n"
        "• Любимые фильмы и сериалы\n"
        "• Любимые исполнители\n"
        "• Любимые книги\n\n"
        "<i>Пример:\n"
        "Фильмы: Паразиты, Эйфория, Настоящий детектив\n"
        "Музыка: The xx, Massive Attack, Portishead\n"
        "Книги: Дюна, Мастер и Маргарита</i>"
    ),
    "balance": (
        "🧠 <b>Немного о тебе</b>\n\n"
        "Расскажи о предпочтениях в еде и здоровье:\n"
        "• Диета или ограничения (без мяса, без глютена…)\n"
        "• Цели (энергия, здоровый вес, лучший сон…)\n"
        "• Что любишь или не ешь\n\n"
        "<i>Пример: не ем мясо, хочу больше энергии, "
        "люблю азиатскую кухню, аллергия на орехи</i>"
    ),
}


def firstvisit_prompt(section):
    return MessageSpec(text=FIRSTVISIT_PROMPTS[section], parse_mode="HTML")


def firstvisit_saved(saved_items):
    lines = "\n".join(f"• {esc(item)}" for item in saved_items)
    return MessageSpec(text=f"✅ <b>Сохранено</b>\n\n{lines}", parse_mode="HTML")


def onboard_start():
    return MessageSpec(
        text=(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.\n\n"
            "Как тебя зовут?"
        ),
        parse_mode="HTML",
    )


def onboard_name_saved(name):
    return MessageSpec(
        text=(
            f"Приятно познакомиться, <b>{esc(name)}</b>! 🙌\n\n"
            "🌍 Из какого ты города? Напиши текстом — настрою погоду и контекст для советов."
        ),
        parse_mode="HTML",
    )


def onboard_language_question():
    return MessageSpec(text="🌐 Какие языки изучаешь? Настрою тренажёр и словарь.")


def onboard_level_question(code):
    flag = "🇳🇱" if code == "nl" else "🇬🇧"
    lang = "нидерландского" if code == "nl" else "английского"
    return MessageSpec(text=f"{flag} Какой у тебя уровень {lang}?")


def onboard_priorities_question():
    return MessageSpec(
        text=(
            "🎯 Что для тебя сейчас важнее?\n\n"
            "Можно выбрать несколько пунктов. Я буду учитывать это в брифе, советах и рекомендациях."
        )
    )
