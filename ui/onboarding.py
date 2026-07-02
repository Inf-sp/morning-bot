from .builder import MessageBuilder, MessageSpec


def _firstvisit_wardrobe():
    b = MessageBuilder()
    b.bold("👕 Настроим гардероб")
    b.blank()
    b.text_line(
        "Напиши в свободном виде:\n"
        "• Твой стиль одежды (минимализм, casual, streetwear…)\n"
        "• Любимые вещи или бренды\n"
        "• Размеры: одежда, обувь, брюки"
    )
    b.blank()
    b.italic("Пример: Люблю минимализм и оверсайз. Uniqlo, Nike. Размер M, обувь EU 43, брюки W32 L32")
    return b.build()


def _firstvisit_learn():
    b = MessageBuilder()
    b.bold("📚 Настроим обучение")
    b.blank()
    b.text_line("Какие языки изучаешь и какой у тебя уровень?")
    b.blank()
    b.italic("Пример: нидерландский A2, английский B1")
    return b.build()


def _firstvisit_leisure():
    b = MessageBuilder()
    b.bold("🍿 Расскажи о своих предпочтениях")
    b.blank()
    b.text_line(
        "Напиши в любом виде:\n"
        "• Любимые фильмы и сериалы\n"
        "• Любимые исполнители\n"
        "• Любимые книги"
    )
    b.blank()
    b.italic(
        "Пример:\n"
        "Фильмы: Паразиты, Эйфория, Настоящий детектив\n"
        "Музыка: The xx, Massive Attack, Portishead\n"
        "Книги: Дюна, Мастер и Маргарита"
    )
    return b.build()


def _firstvisit_balance():
    b = MessageBuilder()
    b.bold("🧠 Немного о тебе")
    b.blank()
    b.text_line(
        "Расскажи о предпочтениях в еде и здоровье:\n"
        "• Диета или ограничения (без мяса, без глютена…)\n"
        "• Цели (энергия, здоровый вес, лучший сон…)\n"
        "• Что любишь или не ешь"
    )
    b.blank()
    b.italic("Пример: не ем мясо, хочу больше энергии, люблю азиатскую кухню, аллергия на орехи")
    return b.build()


_FIRSTVISIT_BUILDERS = {
    "wardrobe": _firstvisit_wardrobe,
    "learn": _firstvisit_learn,
    "leisure": _firstvisit_leisure,
    "balance": _firstvisit_balance,
}


def firstvisit_prompt(section):
    return _FIRSTVISIT_BUILDERS[section]()


def firstvisit_saved(saved_items):
    b = MessageBuilder()
    b.bold("✅ Сохранено")
    b.blank()
    b.text_line("\n".join(f"• {item}" for item in saved_items))
    return b.build()


def onboard_start():
    b = MessageBuilder()
    b.bold("👋 Добро пожаловать!")
    b.blank()
    b.text_line("Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.")
    b.blank()
    b.text_line("Как тебя зовут?")
    return b.build()


def onboard_name_saved(name):
    b = MessageBuilder()
    b.text_line("Приятно познакомиться, ")
    b.bold(str(name))
    b.text_line("! 🙌")
    b.blank()
    b.text_line("🌍 Из какого ты города? Напиши текстом — настрою погоду и контекст для советов.")
    return b.build()


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
