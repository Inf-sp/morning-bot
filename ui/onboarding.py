from .builder import MessageBuilder, MessageSpec


def _firstvisit_wardrobe():
    b = MessageBuilder()
    b.section("👕 Настроим гардероб")
    b.line(
        "Напиши в свободном виде:\n"
        "• Твой стиль одежды (минимализм, casual, streetwear…)\n"
        "• Любимые вещи или бренды\n"
        "• Размеры: одежда, обувь, брюки"
    )
    b.spacer()
    b.italic("Пример: Люблю минимализм и оверсайз. Uniqlo, Nike. Размер M, обувь EU 43, брюки W32 L32")
    return b.build()


def _firstvisit_learn():
    b = MessageBuilder()
    b.section("📚 Настроим обучение")
    b.line("Какие языки изучаешь и какой у тебя уровень?")
    b.spacer()
    b.italic("Пример: нидерландский A2, английский B1")
    return b.build()


def _firstvisit_leisure():
    b = MessageBuilder()
    b.section("🍿 Расскажи о своих предпочтениях")
    b.line(
        "Напиши в любом виде:\n"
        "• Любимые фильмы и сериалы\n"
        "• Любимые исполнители\n"
        "• Любимые книги"
    )
    b.spacer()
    b.italic(
        "Пример:\n"
        "Фильмы: Паразиты, Эйфория, Настоящий детектив\n"
        "Музыка: The xx, Massive Attack, Portishead\n"
        "Книги: Дюна, Мастер и Маргарита"
    )
    return b.build()


def _firstvisit_health():
    b = MessageBuilder()
    b.section("🚑 Немного о твоём самочувствии")
    b.line(
        "Что хочешь отслеживать или улучшить? Например:\n"
        "• сон, энергия, тревожность\n"
        "• привычки, спорт, питание\n\n"
        "Отметь галочками кнопками ниже или напиши своими словами."
    )
    b.spacer()
    b.italic("Это просто твои личные цели — без медицинских выводов и диагнозов.")
    return b.build()


def _firstvisit_cooking():
    b = MessageBuilder()
    b.section("🥣 Настроим готовку")
    b.line(
        "Расскажи о предпочтениях в еде:\n"
        "• Диета или ограничения (без мяса, без глютена…)\n"
        "• Что любишь или не ешь\n"
        "• Любимые кухни"
    )
    b.spacer()
    b.italic("Пример: не ем мясо, люблю азиатскую кухню, аллергия на орехи")
    return b.build()


_FIRSTVISIT_BUILDERS = {
    "wardrobe": _firstvisit_wardrobe,
    "learning": _firstvisit_learn,
    "leisure": _firstvisit_leisure,
    "health": _firstvisit_health,
    "cooking": _firstvisit_cooking,
}


def firstvisit_prompt(section):
    return _FIRSTVISIT_BUILDERS[section]()


def firstvisit_leisure_titles_prompt():
    b = MessageBuilder()
    b.section("🍿 Любимые названия")
    b.line(
        "Напиши в любом виде:\n"
        "• Любимые фильмы и сериалы\n"
        "• Любимые исполнители\n"
        "• Любимые книги"
    )
    b.spacer()
    b.italic(
        "Пример:\n"
        "Фильмы: Паразиты, Настоящий детектив\n"
        "Музыка: The xx, Portishead\n"
        "Книги: Дюна, Мастер и Маргарита"
    )
    return b.build()


def firstvisit_saved(saved_items):
    b = MessageBuilder()
    b.section("✅ Сохранено")
    for item in saved_items:
        b.bullet(item)
    return b.build_stripped()


def onboard_start():
    b = MessageBuilder()
    b.section("👋 Добро пожаловать!")
    b.line("Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.")
    b.spacer()
    b.line("Как тебя зовут?")
    return b.build_stripped()


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
