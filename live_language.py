"""Ежедневная разговорная фраза для главной карточки обучения."""

from datetime import datetime

import config


_DAILY_PHRASES = {
    # Фраза дня, грамматика из неё («слово — значение. правило: пример») и ещё пример
    # с переводом (B1). В rule слова в _подчёркиваниях_ — на изучаемом языке, UI выделяет их курсивом.
    "nl": (
        {"text": "Dat is de druppel!", "translation": "Это последняя капля.",
         "rule": "_Eerst … toen_ — сначала … потом. После _toen_ в начале подлежащее и глагол меняются местами: _toen morste ik koffie_.",
         "more": "Eerst regende het, toen ging de zon schijnen.",
         "more_translation": "Сначала шёл дождь, потом выглянуло солнце."},
        {"text": "Geen probleem.", "translation": "Без проблем.",
         "rule": "_Zodra_ — как только. В придаточном предложении глагол стоит в конце: _zodra ik thuis ben_.",
         "more": "Ik bel je terug zodra ik klaar ben.",
         "more_translation": "Я перезвоню тебе, как только закончу."},
        {"text": "Komt goed.", "translation": "Всё будет нормально.",
         "rule": "_Zich zorgen maken_ — волноваться. Возвратное местоимение меняется по лицу: _ik maak me zorgen_, _maak je geen zorgen_.",
         "more": "Ze maakt zich zorgen om haar examen.",
         "more_translation": "Она волнуется из-за экзамена."},
        {"text": "Doe maar rustig aan.", "translation": "Не торопись.",
         "rule": "_Hoeven … te_ — нужно, обычно с _niet_ или _pas_. Глагол с _te_ стоит в конце: _we hoeven pas om acht uur te vertrekken_.",
         "more": "Je hoeft niet te koken, we eten buiten.",
         "more_translation": "Тебе не нужно готовить, мы едим вне дома."},
        {"text": "Ik zie wel.", "translation": "Посмотрим.",
         "rule": "_Of_ — ли. В придаточном с _of_ разделяемый глагол пишется слитно и стоит в конце: _of ik meega_.",
         "more": "Ik weet niet of hij vanavond meekomt.",
         "more_translation": "Я не знаю, пойдёт ли он с нами сегодня вечером."},
        {"text": "Laat maar.", "translation": "Ладно, забудь.",
         "rule": "_Had … moeten_ — надо было. Инфинитив стоит в самом конце: _ik had het moeten vertellen_.",
         "more": "Ik had eerder moeten vertrekken.",
         "more_translation": "Мне надо было выйти раньше."},
        {"text": "Het valt mee.", "translation": "Всё не так плохо.",
         "rule": "_Zou_ + инфинитив — ожидание в прошлом: _ik dacht dat het druk zou zijn_.",
         "more": "Ik dacht dat de toets moeilijk zou zijn, maar het viel mee.",
         "more_translation": "Я думал, что тест будет трудным, но всё оказалось не так плохо."},
        {"text": "Ik ben er klaar mee.", "translation": "С меня хватит.",
         "rule": "_Er … mee_ — с этим, вместо «met + это»: _klaar met de vertraging_ → _ik ben er klaar mee_.",
         "more": "Mijn nieuwe fiets? Ik ben er heel blij mee.",
         "more_translation": "Мой новый велосипед? Я им очень доволен."},
        {"text": "Dat komt goed uit.", "translation": "Это как раз кстати.",
         "rule": "_Want_ — потому что, порядок слов не меняет; _dan_ в начале меняет: _want dan ben ik thuis_.",
         "more": "Ik neem de trein, want dan kan ik lezen.",
         "more_translation": "Я поеду на поезде, потому что тогда смогу почитать."},
        {"text": "Daar heb ik geen zin in.", "translation": "Мне совсем не хочется.",
         "rule": "_Zin hebben in_ — хотеть, быть в настроении. _Daar_ встаёт в начало, а _in_ уходит в конец: _daar heb ik geen zin in_.",
         "more": "Heb je zin in koffie? Daar heb ik wel zin in!",
         "more_translation": "Хочешь кофе? А вот этого мне хочется!"},
    ),
    "en": (
        {"text": "No worries.", "translation": "Не переживай.",
         "rule": "_As soon as_ — как только. О будущем после него — Present Simple, без _will_: _as soon as I get home_.",
         "more": "I'll text you as soon as the meeting ends.",
         "more_translation": "Я напишу тебе, как только закончится встреча."},
        {"text": "That makes sense.", "translation": "Логично.",
         "rule": "_If_ — если. В реальном условии после _if_ Present Simple, а _will_ — только в главной части: _if we go now, we'll make it_.",
         "more": "If it rains, we'll stay home.",
         "more_translation": "Если пойдёт дождь, мы останемся дома."},
        {"text": "I'm in.", "translation": "Я с вами.",
         "rule": "Present Continuous — уже договорённые планы: _you're going hiking on Saturday_.",
         "more": "We're meeting Anna for lunch tomorrow.",
         "more_translation": "Завтра мы обедаем с Анной."},
        {"text": "Fair enough.", "translation": "Справедливо.",
         "rule": "_Would rather_ — предпочёл бы. После него инфинитив без _to_: _you'd rather stay in_.",
         "more": "I'd rather walk than take the bus.",
         "more_translation": "Я лучше пройдусь пешком, чем поеду на автобусе."},
        {"text": "It slipped my mind.", "translation": "Совсем вылетело из головы.",
         "rule": "_Was supposed to_ — должен был, но не сделал. Дальше инфинитив: _I was supposed to call you_.",
         "more": "The parcel was supposed to arrive on Monday.",
         "more_translation": "Посылка должна была прийти в понедельник."},
        {"text": "Give me a sec.", "translation": "Дай секунду.",
         "rule": "_Almost_, _just_, _already_ + Present Perfect — результат к моменту речи: _I've almost finished_.",
         "more": "I've just made some tea.",
         "more_translation": "Я только что заварил чай."},
        {"text": "That was close.", "translation": "Чуть не случилось.",
         "rule": "Третий тип условных — об упущенном прошлом: _if I'd left later, I would have missed the train_.",
         "more": "If I had checked the map, I wouldn't have got lost.",
         "more_translation": "Если бы я посмотрел карту, я бы не заблудился."},
        {"text": "I'm not feeling it.", "translation": "Мне не заходит.",
         "rule": "Present Perfect Continuous — началось в прошлом и идёт до сих пор: _I've been watching it for a week_.",
         "more": "She's been learning Dutch for two years.",
         "more_translation": "Она учит нидерландский уже два года."},
        {"text": "Let's call it a day.", "translation": "Давай на сегодня закончим.",
         "rule": "_Get_ + прилагательное — становиться: _it's getting late_.",
         "more": "It's getting cold, take a jacket.",
         "more_translation": "Становится холодно, возьми куртку."},
        {"text": "I'm running late.", "translation": "Я опаздываю.",
         "rule": "_Should have_ + третья форма — сожаление о прошлом: _I should have left earlier_.",
         "more": "I should have charged my phone.",
         "more_translation": "Надо было зарядить телефон."},
    ),
}


def daily_phrase(language="nl", variant=0) -> dict:
    """Одна проверенная фраза на календарный день без AI и сетевых запросов."""
    code = language if language in _DAILY_PHRASES else "nl"
    phrases = _DAILY_PHRASES[code]
    index = (datetime.now(config.TZ).date().toordinal() + max(0, int(variant))) % len(phrases)
    return dict(phrases[index])
