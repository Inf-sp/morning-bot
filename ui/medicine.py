from .builder import MessageBuilder, MessageSpec


def prompt_screen():
    b = MessageBuilder()
    b.title("💊 Разбор лекарства")
    b.line("Напиши название препарата и свой вопрос.")
    b.spacer()
    b.line("Если знаешь дозировку и форму — укажи их тоже, например XL или CR.")
    return b.build_stripped()


def medicine_card(data):
    b = MessageBuilder()
    b.title("💊 Разбор лекарства")
    b.labeled_line("Запрос", data.get("query") or "Вопрос о препарате", lowercase=False)
    b.spacer()
    b.labeled_line("Ответ", data.get("answer") or "Недостаточно официальных данных", lowercase=False)
    details = [str(x).strip() for x in (data.get("details") or []) if str(x).strip()][:2]
    important = str(data.get("important") or "").strip()
    disclaimer = str(data.get("disclaimer") or "").strip()
    tail = details + ([f"💡 Важно: {important}"] if important else []) + ([disclaimer] if disclaimer else [])
    if tail:
        b.spacer()
        b.line("\n".join(tail))
    return b.build_stripped()


def emergency_card():
    return MessageSpec(
        text=("🚨 Это может быть экстренная ситуация. Не жди разбора лекарства: "
              "позвони 112 или немедленно обратись за срочной медицинской помощью. "
              "Не принимай дополнительную дозу."),
    )
