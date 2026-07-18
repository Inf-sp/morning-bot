"""Ежедневная разговорная фраза для главной карточки обучения."""

from datetime import datetime

import config


_DAILY_PHRASES = {
    "nl": (
        {
            "text": "Dat is de druppel!",
            "translation": "Это последняя капля",
            "meaning": "Когда мелкие неприятности копятся, и очередная окончательно добивает.",
        },
        {
            "text": "Geen probleem.",
            "translation": "Без проблем",
            "meaning": "Когда спокойно соглашаются помочь или показывают, что всё нормально.",
        },
        {
            "text": "Komt goed.",
            "translation": "Всё будет нормально",
            "meaning": "Когда хотят коротко успокоить или показать, что вопрос решится.",
        },
        {
            "text": "Doe maar rustig aan.",
            "translation": "Не торопись",
            "meaning": "Когда человеку предлагают не спешить и действовать спокойнее.",
        },
        {
            "text": "Ik zie wel.",
            "translation": "Посмотрим",
            "meaning": "Когда пока не принимают решение и оставляют всё открытым.",
        },
        {
            "text": "Laat maar.",
            "translation": "Ладно, забудь / забей",
            "meaning": "Когда больше не хотят объяснять, спорить или продолжать тему.",
        },
        {
            "text": "Het valt mee.",
            "translation": "Всё не так плохо",
            "meaning": "Когда ситуация оказалась легче или приятнее, чем ожидалось.",
        },
        {
            "text": "Ik ben er klaar mee.",
            "translation": "С меня хватит",
            "meaning": "Когда устали от ситуации и больше не хотят с ней мириться.",
        },
        {
            "text": "Dat komt goed uit.",
            "translation": "Это как раз кстати",
            "meaning": "Когда что-то удобно совпало с планами или ситуацией.",
        },
        {
            "text": "Daar heb ik geen zin in.",
            "translation": "Мне совсем не хочется",
            "meaning": "Когда прямо говорят, что нет желания что-то делать.",
        },
    ),
    "en": (
        {
            "text": "No worries.",
            "translation": "Не переживай",
            "meaning": "Когда хотят показать, что всё нормально и проблемы нет.",
        },
        {
            "text": "That makes sense.",
            "translation": "Логично",
            "meaning": "Когда объяснение звучит понятно и разумно.",
        },
        {
            "text": "I'm in.",
            "translation": "Я с вами",
            "meaning": "Когда соглашаются участвовать в предложенном плане.",
        },
        {
            "text": "Fair enough.",
            "translation": "Справедливо",
            "meaning": "Когда принимают чужой аргумент и не хотят спорить дальше.",
        },
        {
            "text": "It slipped my mind.",
            "translation": "Я совсем забыл",
            "meaning": "Когда что-то забыли не специально.",
        },
        {
            "text": "Give me a sec.",
            "translation": "Дай секунду",
            "meaning": "Когда просят немного подождать.",
        },
        {
            "text": "That was close.",
            "translation": "Чуть не случилось",
            "meaning": "Когда неприятность почти произошла, но её удалось избежать.",
        },
        {
            "text": "I'm not feeling it.",
            "translation": "Мне не заходит",
            "meaning": "Когда что-то не нравится или не подходит по настроению.",
        },
        {
            "text": "Let's call it a day.",
            "translation": "Давай на сегодня закончим",
            "meaning": "Когда предлагают закончить работу или дело на сегодня.",
        },
        {
            "text": "I'm running late.",
            "translation": "Я опаздываю",
            "meaning": "Когда сообщают, что не успевают прийти вовремя.",
        },
    ),
}


def daily_phrase(language="nl") -> dict:
    """Одна проверенная фраза на календарный день без AI и сетевых запросов."""
    code = language if language in _DAILY_PHRASES else "nl"
    phrases = _DAILY_PHRASES[code]
    index = datetime.now(config.TZ).date().toordinal() % len(phrases)
    return dict(phrases[index])
