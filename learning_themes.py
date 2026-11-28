"""Темы обучения нидерландскому (B1): 5 категорий, 41 тема — то, что видно в «Настроить словарь → Темы обучения».

Каждая тема собирает подходящие грамматические темы из `grammar_topics.json` (по id); у тем лексики и
коммуникации своих грамматических тем нет — тема дня строится из самой темы (`theme_topic`). Включение и
«Пропустить» — в настройках пользователя: `learn_themes_off` (выключил сам) и `learn_themes_done` (изучена или
пропущена). «Изучаем сегодня» берёт следующую тему из включённых и не изученных.
"""

CATEGORIES = (
    (1, "Грамматика", "Грамматика"),
    (2, "Связь предложений и выражение мыслей", "Связь предложений"),
    (3, "Словообразование и части речи", "Словообразование"),
    (4, "Лексика и жизненные ситуации", "Лексика"),
    (5, "Коммуникация", "Коммуникация"),
)


def _ids(*numbers) -> tuple:
    return tuple(f"nl-{number:03d}" for number in numbers)


# (номер, категория, название, что изучаем, грамматические темы)
THEMES = (
    (1, 1, "Werkwoordstijden", "Настоящее, прошедшее и будущее время",
     _ids(6, 7, 8, 9, 32, 134)),
    # Номер 41 — добавлена позже; стоит сразу за временами: конструкции учат отдельно, а не как время.
    (41, 1, "Werkwoordconstructies", "Глагольные конструкции: blijven, zitten te, aan het + инфинитив",
     _ids(110, 111, 112, 149, 220, 223, 224)),
    (2, 1, "Perfectum", "Voltooid tegenwoordige tijd (hebben / zijn)", _ids(70, 71, 72, 73, 75, 148, 174, 264, 265)),
    (3, 1, "Imperfectum", "Onvoltooid verleden tijd", _ids(76, 77, 241)),
    (4, 1, "Plusquamperfectum", "Предпрошедшее время", _ids(166)),
    (5, 1, "Toekomende tijd", "Zullen, gaan + infinitief", _ids(31, 113, 114, 115, 136, 247)),
    (6, 1, "Modale werkwoorden", "Kunnen, moeten, mogen, willen, zouden",
     _ids(27, 28, 29, 30, 116, 167, 168, 170, 244, 246, 263, 270, 274, 275, 277)),
    (7, 1, "Scheidbare werkwoorden", "Отделяемые глаголы", _ids(74, 258, 259, 260, 261)),
    (8, 1, "Wederkerende werkwoorden", "Возвратные глаголы",
     _ids(104, 105, 106, 107, 108, 190, 192, 243, 257, 267, 268)),
    (9, 1, "Bijzinnen", "Придаточные предложения", _ids(89, 101, 150, 151, 215, 216, 245, 266, 313)),
    (10, 1, "Woordvolgorde", "Порядок слов и инверсия", _ids(10, 11, 12, 13, 14, 45, 46, 64, 147, 213, 214, 252)),
    (11, 1, "Betrekkelijke voornaamwoorden", "Die, dat, waar", _ids(102, 103, 206, 207, 311, 312)),
    (12, 1, "Voornaamwoorden", "Личные, притяжательные, указательные местоимения",
     _ids(5, 15, 16, 23, 40, 41, 42, 60, 61, 65, 68, 69, 124, 125, 126, 127, 128, 131, 132, 133, 146, 208, 209,
          210, 211, 227, 228, 310)),
    (13, 1, "Voorzetsels", "Предлоги места, времени и направления",
     _ids(21, 22, 48, 49, 50, 51, 52, 53, 54, 55, 56, 62, 67, 83, 84, 135, 137, 138, 139, 140, 141, 142, 143, 175,
          176, 177, 178, 212, 248)),
    (14, 1, "Vergelijkingen", "Сравнительная и превосходная степени", _ids(34, 85, 86, 87, 88, 152, 204, 292, 293)),
    (15, 1, "Passieve vorm", "Пассивный залог", _ids(171, 172, 173, 280, 281, 314)),
    (16, 2, "Reden", "Omdat, doordat, want", _ids(90, 249, 251, 288, 289, 299, 300, 305, 307)),
    (17, 2, "Gevolg", "Daarom, daardoor, dus", _ids(197, 198, 219, 309)),
    (18, 2, "Tegenstelling", "Maar, hoewel, toch, echter",
     _ids(47, 79, 98, 200, 229, 283, 284, 294, 295, 297, 298, 301)),
    (19, 2, "Voorwaarde", "Als, tenzij, mits", _ids(91, 169, 276, 278, 279, 285, 286)),
    (20, 2, "Doel", "Om te, zodat", _ids(100, 109, 217, 218, 287)),
    (21, 2, "Tijd", "Voordat, nadat, terwijl, zodra",
     _ids(19, 20, 43, 44, 57, 63, 92, 93, 94, 95, 96, 97, 99, 144, 145, 161, 162, 163, 205, 242, 290, 291)),
    (22, 2, "Aanvulling", "Bovendien, daarnaast, trouwens", _ids(35, 164, 165, 193, 194, 195, 196, 199, 201)),
    (23, 2, "Mening en argumentatie", "Volgens mij, enerzijds, anderzijds",
     _ids(202, 203, 221, 222, 253, 255, 262, 271, 272, 273, 296, 302, 303, 304, 306, 308)),
    (24, 3, "Bijvoeglijke naamwoorden", "Прилагательные и окончания",
     _ids(24, 36, 37, 38, 39, 121, 122, 123, 129, 130, 225, 226)),
    (25, 3, "Bijwoorden", "Наречия", _ids(33, 78, 80, 81, 82, 153, 154, 155, 156, 157)),
    (26, 3, "Zelfstandige naamwoorden", "Существительные, множественное число", _ids(1, 2, 3, 4, 17, 18, 25, 26, 66, 282)),
    (27, 3, "Woordvorming", "Приставки, суффиксы, составные слова",
     _ids(230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240)),
    (28, 3, "Vaste combinaties", "Устойчивые сочетания слов и глаголов",
     _ids(58, 59, 117, 118, 119, 120, 158, 159, 160, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 191,
          250, 254, 256, 269, 315, 316, 317, 318, 319, 320, 321)),
    (29, 4, "Werk en carrière", "Работа, собеседования, профессии", ()),
    (30, 4, "Onderwijs", "Образование и обучение", ()),
    (31, 4, "Gezondheid", "Здоровье, самочувствие, медицина", ()),
    (32, 4, "Wonen", "Жильё, бытовые вопросы", ()),
    (33, 4, "Geld en financiën", "Деньги, платежи, расходы", ()),
    (34, 4, "Maatschappij", "Общество, государство, законы", ()),
    (35, 4, "Technologie", "Технологии, интернет, цифровая жизнь", ()),
    (36, 4, "Milieu en klimaat", "Экология, климат, устойчивое развитие", ()),
    (37, 5, "Meningen uiten", "Выражение мнения", ()),
    (38, 5, "Klachten en verzoeken", "Жалобы, просьбы, обращения", ()),
    (39, 5, "Discussies voeren", "Обсуждение и аргументация", ()),
    (40, 5, "Ervaringen vertellen", "Рассказы о событиях и личном опыте", ()),
)
_BY_NUMBER = {theme[0]: theme for theme in THEMES}
_BY_TOPIC = {topic_id: theme[0] for theme in THEMES for topic_id in theme[4]}
OFF_KEY = "learn_themes_off"
DONE_KEY = "learn_themes_done"
THEME_TOPIC_PREFIX = "nl-theme-"


def theme(number) -> tuple | None:
    return _BY_NUMBER.get(int(number)) if str(number).isdigit() else None


def theme_of(topic_id) -> int | None:
    """Номер темы обучения для грамматической темы («nl-201» → 22) или темы-лексики («nl-theme-29» → 29)."""
    topic_id = str(topic_id or "")
    if topic_id.startswith(THEME_TOPIC_PREFIX):
        number = topic_id[len(THEME_TOPIC_PREFIX):]
        return int(number) if number.isdigit() and int(number) in _BY_NUMBER else None
    return _BY_TOPIC.get(topic_id)


def theme_topic(number) -> dict:
    """Тема без своих грамматических тем (лексика, коммуникация) — как тема дня: ИИ соберёт по ней карточку."""
    _n, _cat, title, about, _ids_ = _BY_NUMBER[number]
    return {"id": f"{THEME_TOPIC_PREFIX}{number}", "level": "B1", "topic": title, "meaning": about.lower(),
            "explain": f"Тема «{title}»: {about.lower()} — ходовые слова и фразы на уровне B1.", "details": [],
            "example": "", "translation": ""}


def label(number) -> str:
    """«Связь предложений (Aanvulling)» — для нижней строки «Обучения»."""
    found = theme(number)
    if not found:
        return ""
    short = next(short for cat, _full, short in CATEGORIES if cat == found[1])
    return f"{short} ({found[2]})"


def _numbers(cid, key) -> set:
    import settings as user_settings
    saved = user_settings.get(cid, key, []) or []
    return {int(value) for value in saved if str(value).isdigit() and int(value) in _BY_NUMBER}


def state(cid) -> dict:
    """{"off": выключенные, "done": изученные или пропущенные}."""
    return {"off": _numbers(cid, OFF_KEY), "done": _numbers(cid, DONE_KEY)}


def enabled(cid) -> list:
    """Включённые и ещё не изученные темы — по порядку списка."""
    current = state(cid)
    return [theme[0] for theme in THEMES if theme[0] not in current["off"] | current["done"]]


def is_on(cid, number) -> bool:
    current = state(cid)
    return int(number) not in current["off"] | current["done"]


def toggle(cid, number) -> None:
    """Включена — выключить; выключена или изучена — снова включить (из «изученных» тоже убирается)."""
    import settings as user_settings
    if int(number) not in _BY_NUMBER:
        return
    current = state(cid)
    if int(number) in current["off"] | current["done"]:
        user_settings.set_(cid, OFF_KEY, sorted(current["off"] - {int(number)}))
        user_settings.set_(cid, DONE_KEY, sorted(current["done"] - {int(number)}))
        _forget_seen(cid, number)  # включил снова — тема проходится заново
    else:
        user_settings.set_(cid, OFF_KEY, sorted(current["off"] | {int(number)}))


def _forget_seen(cid, number) -> None:
    import store
    ids = set(_BY_NUMBER[int(number)][4]) | {f"{THEME_TOPIC_PREFIX}{number}"}

    def mutate(profile):
        progress = dict(profile.get("grammar_progress") or {})
        nl = dict(progress.get("nl") or {})
        nl["seen"] = [topic_id for topic_id in nl.get("seen") or [] if topic_id not in ids]
        progress["nl"] = nl
        return {**profile, "grammar_progress": progress}, None

    store.mutate_profile(cid, mutate)


def mark_done(cid, number) -> None:
    """«Пропустить» или все грамматические темы темы показаны — тема изучена."""
    import settings as user_settings
    if theme(number):
        user_settings.set_(cid, DONE_KEY, sorted(state(cid)["done"] | {int(number)}))


def category_summary(cid, category) -> tuple:
    """(включено, всего) тем в категории."""
    numbers = [theme[0] for theme in THEMES if theme[1] == category]
    return sum(1 for number in numbers if is_on(cid, number)), len(numbers)
