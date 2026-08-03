from .builder import MessageBuilder, MessageSpec
from .constants import ui_label


def onboard_start():
    b = MessageBuilder()
    b.section(ui_label("welcome", "Добро пожаловать!"))
    b.line("Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.")
    b.spacer()
    b.line("Как тебя зовут?")
    return b.build_stripped()


def onboard_name_saved(name):
    b = MessageBuilder()
    b.text_line("Приятно познакомиться, ")
    b.bold(str(name))
    b.text_line("!")
    b.blank()
    b.text_line("🌍 Из какого ты города? Напиши текстом — настрою погоду и контекст для советов.")
    return b.build()


def onboard_language_question():
    return MessageSpec(text="Какой язык изучаешь? Настрою практику и словарь.")


def onboard_level_question(code):
    flag = "🇳🇱" if code == "nl" else "🇬🇧"
    lang = "нидерландского" if code == "nl" else "английского"
    return MessageSpec(text=f"{flag} Какой у тебя уровень {lang}?")
