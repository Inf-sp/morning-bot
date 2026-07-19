from .builder import MessageBuilder, MessageSpec


def prompt_screen():
    b = MessageBuilder()
    b.title("👩🏻‍⚕️ Врач")
    b.line("Опиши, что беспокоит, или задай вопрос о здоровье.")
    b.spacer()
    b.line("Я разберу ситуацию, объясню возможные причины и подскажу, что делать дальше.")
    return b.build_stripped()


def answer_card(data):
    b = MessageBuilder()
    b.title("👩🏻‍⚕️ Разбор")
    direct = str(data.get("direct") or "").strip()
    if direct:
        b.line(direct)
    likely = str(data.get("likely") or "").strip()
    if likely:
        b.spacer()
        b.labeled_line("Похоже на", likely, lowercase=False)
    actions = [str(x).strip() for x in (data.get("actions") or []) if str(x).strip()][:3]
    if actions:
        b.spacer()
        b.bold("Что делать сейчас:")
        b.newline()
        for action in actions:
            b.bullet(action)
    help_if = str(data.get("help_if") or "").strip()
    if help_if:
        b.spacer()
        b.line(f"💡 Обратись за помощью, если: {help_if}")
    questions = [str(x).strip() for x in (data.get("questions") or []) if str(x).strip()][:2]
    if questions:
        b.spacer()
        b.bold("Уточни:")
        b.newline()
        for question in questions:
            b.line(question)
    return b.build_stripped()


def emergency_card(netherlands=True):
    if netherlands:
        text = ("🚨 Нужна срочная оценка. При непосредственной угрозе жизни звони 112. "
                "Если состояние срочное, но угрозы жизни нет, свяжись с huisarts или huisartsenpost вне рабочих часов.")
    else:
        text = ("🚨 Нужна срочная медицинская оценка. При непосредственной угрозе жизни "
                "позвони в местную экстренную службу прямо сейчас.")
    return MessageSpec(text=text)
