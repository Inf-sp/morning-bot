"""Ежедневная разговорная фраза для главной карточки обучения."""

from datetime import datetime

import config


_DAILY_PHRASES = {
    # Фраза, пример с ней, одно правило из этого примера и одно действие (B1).
    # В rule слова в _подчёркиваниях_ — на изучаемом языке, UI выделяет их курсивом.
    "nl": (
        {
            "text": 'Dat is de druppel!',
            "translation": 'Это последняя капля.',
            "example": 'Eerst was mijn trein te laat, toen morste ik koffie. Dat is de druppel!',
            "rule": 'После _eerst_ и _toen_ в начале подлежащее и глагол меняются местами: _Toen morste ik koffie._',
            "tip": 'придумай своё предложение с «eerst… toen…» и скажи его вслух.',
        },
        {
            "text": 'Geen probleem.',
            "translation": 'Без проблем.',
            "example": 'Geen probleem, ik bel je terug zodra ik thuis ben.',
            "rule": 'После _zodra_ глагол уходит в конец придаточного: _zodra ik thuis ben._',
            "tip": 'закончи вслух три фразы «Ik bel je terug zodra…».',
        },
        {
            "text": 'Komt goed.',
            "translation": 'Всё будет нормально.',
            "example": 'Maak je geen zorgen, het komt goed.',
            "rule": 'В _zich zorgen maken_ возвратное местоимение меняется по лицу: _ik maak me zorgen_, _maak je geen zorgen_.',
            "tip": 'проспрягай вслух «zich zorgen maken» для ik, jij, hij и wij.',
        },
        {
            "text": 'Doe maar rustig aan.',
            "translation": 'Не торопись.',
            "example": 'Doe maar rustig aan, we hoeven pas om acht uur te vertrekken.',
            "rule": '_Hoeven_ работает с _te_ и словами вроде _niet_ или _pas_: _we hoeven pas om acht uur te vertrekken._',
            "tip": 'скажи вслух три фразы «Ik hoef vandaag niet… te…» о своих делах.',
        },
        {
            "text": 'Ik zie wel.',
            "translation": 'Посмотрим.',
            "example": 'Ik weet nog niet of ik meega naar het feest, ik zie wel.',
            "rule": 'В придаточном с _of_ разделяемый глагол снова пишется слитно и стоит в конце: _of ik meega._',
            "tip": 'начни три предложения с «Ik weet nog niet of…» и закончи разделяемым глаголом.',
        },
        {
            "text": 'Laat maar.',
            "translation": 'Ладно, забудь.',
            "example": 'Laat maar, ik had het je gisteren al moeten vertellen.',
            "rule": '«Надо было» — _had … moeten_ + инфинитив в самом конце: _ik had het moeten vertellen._',
            "tip": 'скажи вслух две вещи, которые ты «had moeten doen» на этой неделе.',
        },
        {
            "text": 'Het valt mee.',
            "translation": 'Всё не так плохо.',
            "example": 'Ik dacht dat het hier druk zou zijn, maar het valt mee.',
            "rule": '_Zou_ + инфинитив передаёт ожидание в прошлом: _ik dacht dat het druk zou zijn._',
            "tip": 'расскажи вслух о своём дне по схеме «Ik dacht dat… zou…, maar het valt mee».',
        },
        {
            "text": 'Ik ben er klaar mee.',
            "translation": 'С меня хватит.',
            "example": 'Elke dag weer die vertraging — ik ben er klaar mee.',
            "rule": '_Er … mee_ заменяет «с этим»: _klaar met de vertraging_ → _ik ben er klaar mee._',
            "tip": 'скажи вслух три фразы с «er … mee»: blij, tevreden и klaar.',
        },
        {
            "text": 'Dat komt goed uit.',
            "translation": 'Это как раз кстати.',
            "example": 'Kom je zaterdag langs? Dat komt goed uit, want dan ben ik thuis.',
            "rule": '_Want_ не меняет порядок слов, а _dan_ в начале меняет: _want dan ben ik thuis._',
            "tip": 'составь два предложения с «want dan…» о планах на выходные.',
        },
        {
            "text": 'Daar heb ik geen zin in.',
            "translation": 'Мне совсем не хочется.',
            "example": 'Om zes uur opstaan? Daar heb ik geen zin in.',
            "rule": 'В _zin hebben in_ слово _daar_ встаёт в начало, а _in_ уходит в конец: _Daar heb ik geen zin in._',
            "tip": 'скажи вслух, на что у тебя сегодня есть и нет zin, начиная каждую фразу с «Daar…».',
        },
    ),
    "en": (
        {
            "text": 'No worries.',
            "translation": 'Не переживай.',
            "example": "No worries — I'll send you the file as soon as I get home.",
            "rule": 'После _as soon as_ о будущем говорят в Present Simple: _as soon as I get home_, без _will_.',
            "tip": "закончи вслух три фразы «I'll call you as soon as…».",
        },
        {
            "text": 'That makes sense.',
            "translation": 'Логично.',
            "example": "The shop closes at six? That makes sense — if we go now, we'll make it.",
            "rule": "В реальном условии после _if_ стоит Present Simple, а _will_ — только в главной части: _if we go now, we'll make it._",
            "tip": "составь вслух два предложения «If…, I'll…» о своих планах на сегодня.",
        },
        {
            "text": "I'm in.",
            "translation": 'Я с вами.',
            "example": "You're going hiking on Saturday? I'm in.",
            "rule": "Present Continuous описывает уже договорённые планы: _You're going hiking on Saturday?_",
            "tip": 'расскажи вслух о трёх планах на неделю в Present Continuous.',
        },
        {
            "text": 'Fair enough.',
            "translation": 'Справедливо.',
            "example": "You'd rather stay in tonight? Fair enough.",
            "rule": "После _would rather_ идёт инфинитив без _to_: _You'd rather stay in._",
            "tip": "скажи вслух три предпочтения по схеме «I'd rather… than…».",
        },
        {
            "text": 'It slipped my mind.',
            "translation": 'Совсем вылетело из головы.',
            "example": 'Sorry, I was supposed to call you yesterday, but it slipped my mind.',
            "rule": '_Was supposed to_ + инфинитив — «должен был, но не сделал»: _I was supposed to call you._',
            "tip": 'назови вслух два дела, которые ты «was supposed to» сделать на этой неделе.',
        },
        {
            "text": 'Give me a sec.',
            "translation": 'Дай секунду.',
            "example": "Give me a sec — I've almost finished this email.",
            "rule": "Present Perfect с _almost_, _just_ и _already_ показывает результат к моменту речи: _I've almost finished._",
            "tip": "скажи вслух три фразы «I've just…» о том, что ты сделал за последний час.",
        },
        {
            "text": 'That was close.',
            "translation": 'Чуть не случилось.',
            "example": "That was close — if I'd left a minute later, I would have missed the train.",
            "rule": "Об упущенном прошлом: _if I'd left…, I would have missed…_ — третий тип условных.",
            "tip": 'составь фразу «If I had…, I would have…» о сегодняшнем дне.',
        },
        {
            "text": "I'm not feeling it.",
            "translation": 'Мне не заходит.',
            "example": "I've been watching this series for a week, but I'm not feeling it.",
            "rule": "Present Perfect Continuous — действие началось в прошлом и идёт до сих пор: _I've been watching… for a week._",
            "tip": "скажи вслух три фразы «I've been… for…» о своих привычках.",
        },
        {
            "text": "Let's call it a day.",
            "translation": 'Давай на сегодня закончим.',
            "example": "It's getting late, so let's call it a day.",
            "rule": "_Get_ + прилагательное значит «становиться»: _It's getting late._",
            "tip": "опиши вслух три перемены вокруг тебя по схеме «It's getting…».",
        },
        {
            "text": "I'm running late.",
            "translation": 'Я опаздываю.',
            "example": "I'm running late — I should have left ten minutes earlier.",
            "rule": '_Should have_ + третья форма — сожаление о прошлом: _I should have left earlier._',
            "tip": 'скажи вслух две вещи, которые ты «should have done» вчера.',
        },
    ),
}


def daily_phrase(language="nl", variant=0) -> dict:
    """Одна проверенная фраза на календарный день без AI и сетевых запросов."""
    code = language if language in _DAILY_PHRASES else "nl"
    phrases = _DAILY_PHRASES[code]
    index = (datetime.now(config.TZ).date().toordinal() + max(0, int(variant))) % len(phrases)
    return dict(phrases[index])
