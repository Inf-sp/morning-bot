import asyncio
import logging
import re
from datetime import datetime
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config

_log = logging.getLogger(__name__)
import store
import sun
import weather
import learning
import util
from util import _WEEKDAY_SHORT, _MONTHS
import verify
from ui import myday as myday_ui
from ui import weather as weather_ui

TZ = config.TZ


def _item_text(item):
    """Текст элемента списка: элемент может быть строкой или {"id":..., "value": строка}
    (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


# Кураторская база цитат из известных книг — fallback, когда AI недоступен
# или вернул невалидную цитату. Ключ — casefold названия книги.
_BOOK_QUOTES = {
    "мастер и маргарита": ("Рукописи не горят.", "Михаил Булгаков"),
    "1984": ("Война — это мир. Свобода — это рабство. Незнание — сила.", "Джордж Оруэлл"),
    "маленький принц": ("Мы в ответе за тех, кого приручили.", "Антуан де Сент-Экзюпери"),
    "преступление и наказание": ("Тварь ли я дрожащая или право имею?", "Фёдор Достоевский"),
    "идиот": ("Красота спасёт мир.", "Фёдор Достоевский"),
    "братья карамазовы": ("Все виноваты за всех, и я больше всех.", "Фёдор Достоевский"),
    "сто лет одиночества": (
        "Много лет спустя перед лицом расстрела полковник Аурелиано Буэндиа вспомнит тот далёкий день, когда отец взял его на поиски льда.",
        "Габриэль Гарсиа Маркес",
    ),
    "убить пересмешника": ("Заберись в чужую шкуру и походи в ней.", "Харпер Ли"),
    "анна каренина": (
        "Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастлива по-своему.",
        "Лев Толстой",
    ),
    "евгений онегин": ("Привычка свыше нам дана: замена счастию она.", "Александр Пушкин"),
    "герой нашего времени": (
        "Глупец я или злодей, не знаю; но то верно, что я также очень достоин сожаления.",
        "Михаил Лермонтов",
    ),
    "горе от ума": ("Служить бы рад, прислуживаться тошно.", "Александр Грибоедов"),
    "капитанская дочка": ("Береги честь смолоду.", "Александр Пушкин"),
    "лолита": ("Лолита, свет моей жизни, огонь моих чресел.", "Владимир Набоков"),
    "над пропастью во ржи": (
        "Я люблю, когда человек сам становится лучше, а не просто делает мир лучше.",
        "Джером Сэлинджер",
    ),
    "великий гэтсби": (
        "И так мы пытаемся плыть вперёд, борясь с течением, а оно всё сносит и сносит нас назад, в прошлое.",
        "Фрэнсис Фицджеральд",
    ),
    "старик и море": ("Человека можно уничтожить, но нельзя победить.", "Эрнест Хемингуэй"),
    "451 градус по фаренгейту": (
        "Не спрашивайте, что нужно сжечь, спрашивайте, что нужно сохранить.",
        "Рэй Брэдбери",
    ),
    "о дивный новый мир": (
        "Слова могут быть как рентгеновские лучи — они проходят сквозь всё.",
        "Олдос Хаксли",
    ),
    "гордость и предубеждение": (
        "Гордость и предубеждение — два главных препятствия к счастью.",
        "Джейн Остин",
    ),
    "джейн эйр": ("Я не птица и не падаю в силок.", "Шарлотта Бронте"),
    "граф монте-кристо": ("Ждать и надеяться — вот моё счастье.", "Александр Дюма"),
    "три мушкетёра": ("Один за всех, все за одного.", "Александр Дюма"),
    "отверженные": (
        "Любить — это не значит смотреть друг на друга, это значит смотреть в одном направлении.",
        "Виктор Гюго",
    ),
    "приключения шерлока холмса": (
        "Когда исключено всё невозможное, то оставшееся, каким бы невероятным оно ни было, и есть правда.",
        "Артур Конан Дойл",
    ),
    "властелин колец": ("Даже самый маленький человек может изменить ход будущего.", "Джон Толкин"),
    "гарри поттер": (
        "Счастье можно найти даже в самые тёмные времена, если только не забывать включать свет.",
        "Джоан Роулинг",
    ),
    "алиса в стране чудес": (
        "Здесь нужно бежать со всех ног, чтобы только оставаться на месте.",
        "Льюис Кэрролл",
    ),
    "приключения тома сойера": (
        "Работа — это то, что ты обязан делать, а игра — то, что ты не обязан делать.",
        "Марк Твен",
    ),
    "думай медленно решай быстро": (
        "Ничто в жизни не является таким важным, как ты думаешь, пока ты об этом думаешь.",
        "Даниэль Канеман",
    ),
    "атомные привычки": (
        "Ты не поднимаешься до уровня своих целей, ты падаешь до уровня своих систем.",
        "Джеймс Клир",
    ),
    "богатый папа бедный папа": (
        "Богатые не работают за деньги. Они заставляют деньги работать на себя.",
        "Роберт Кийосаки",
    ),
    "как заводить друзей и оказывать влияние на людей": (
        "Улыбка ничего не стоит, но много даёт.",
        "Дейл Карнеги",
    ),
}

# Только мотивирующие цитаты для финального блока «Моего дня». Отдельный пул
# не даёт случайно выбрать мрачную или ироничную строку из общей книжной базы.
_MOTIVATING_BOOK_QUOTE_KEYS = (
    "маленький принц",
    "убить пересмешника",
    "капитанская дочка",
    "над пропастью во ржи",
    "великий гэтсби",
    "старик и море",
    "451 градус по фаренгейту",
    "о дивный новый мир",
    "джейн эйр",
    "граф монте-кристо",
    "три мушкетёра",
    "отверженные",
    "властелин колец",
    "гарри поттер",
    "алиса в стране чудес",
    "приключения тома сойера",
    "думай медленно решай быстро",
    "атомные привычки",
    "как заводить друзей и оказывать влияние на людей",
)


def _motivating_book_quote(cid, shown_ids=None):
    """Неповторяющаяся мотивирующая цитата; любимые книги имеют приоритет."""
    pool = {
        key: _BOOK_QUOTES[key]
        for key in _MOTIVATING_BOOK_QUOTE_KEYS
        if key in _BOOK_QUOTES
    }
    shown = {str(value) for value in (shown_ids or []) if str(value) in pool}
    if len(shown) >= len(pool):
        shown.clear()
    favorite_keys = [
        _item_text(book).casefold()
        for book in store.get_list(config.FAVORITE_BOOKS_KEY, cid)
        if _item_text(book).casefold() in pool and _item_text(book).casefold() not in shown
    ]
    available = favorite_keys or [key for key in pool if key not in shown]
    quote_id = random.choice(available)
    quote, author = pool[quote_id]
    return {
        "id": quote_id,
        "quote": quote,
        "src": author,
        "book": quote_id,
    }, list(shown)


# Цитаты авторов страны из настроек (в отпуске — страны поездки), без AI: только проверенные
# цитаты. Страны нет в списке — общий пул книжных цитат.
_COUNTRY_QUOTES = {
    "NL": (
        ("Как замечательно, что никому не нужно ждать ни минуты, чтобы начать улучшать мир.", "Анна Франк"),
        ("Тот, кто счастлив, сделает счастливыми и других.", "Анна Франк"),
        ("Великие дела совершаются не порывом, а чередой мелочей, сложенных вместе.", "Винсент ван Гог"),
        ("Если внутренний голос говорит тебе «ты не умеешь рисовать» — рисуй во что бы то ни стало, "
         "и этот голос замолчит.", "Винсент ван Гог"),
        ("Не смеяться, не плакать, не проклинать, а понимать.", "Бенедикт Спиноза"),
        ("Всё прекрасное так же трудно, как и редко.", "Бенедикт Спиноза"),
        ("У каждого недостатка есть своё преимущество.", "Йохан Кройф"),
        ("Призвание человека — быть человеком.", "Мультатули"),
    ),
    "BE": (
        ("Таланта не существует. Талант — это желание что-то делать.", "Жак Брель"),
        ("Всё, что мы видим, скрывает что-то другое.", "Рене Магритт"),
    ),
    "DE": (
        ("Недостаточно знать, надо и применять. Недостаточно хотеть, надо и делать.", "Иоганн Вольфганг Гёте"),
        ("Кто не знает иностранных языков, ничего не знает о своём собственном.", "Иоганн Вольфганг Гёте"),
        ("Что меня не убивает, делает меня сильнее.", "Фридрих Ницше"),
        ("У кого есть «зачем» жить, тот выдержит почти любое «как».", "Фридрих Ницше"),
        ("Имей мужество пользоваться собственным умом.", "Иммануил Кант"),
    ),
    "FR": (
        ("Зорко одно лишь сердце. Самого главного глазами не увидишь.", "Антуан де Сент-Экзюпери"),
        ("Надо возделывать свой сад.", "Вольтер"),
        ("Посреди зимы я обнаружил в себе непобедимое лето.", "Альбер Камю"),
        ("Человек — всего лишь тростник, слабейшее из творений природы, но он тростник мыслящий.", "Блез Паскаль"),
        ("Я мыслю, следовательно, существую.", "Рене Декарт"),
    ),
    "GB": (
        ("Весь мир — театр, а люди в нём — актёры.", "Уильям Шекспир"),
        ("Не все, кто странствует, потеряны.", "Джон Толкин"),
        ("Нужно бежать со всех ног, чтобы только оставаться на месте, а чтобы куда-то попасть, "
         "надо бежать как минимум вдвое быстрее.", "Льюис Кэрролл"),
        ("Когда исключено всё невозможное, то, что остаётся, и есть правда, каким бы невероятным оно ни казалось.",
         "Артур Конан Дойл"),
        ("Это было лучшее из всех времён, это было худшее из всех времён.", "Чарльз Диккенс"),
    ),
    "IE": (
        ("Мы все лежим в сточной канаве, но некоторые из нас смотрят на звёзды.", "Оскар Уайльд"),
        ("Разумный человек приспосабливается к миру, неразумный упорно пытается приспособить мир к себе. "
         "Поэтому прогресс зависит от неразумных.", "Бернард Шоу"),
        ("Пробовал. Ошибался. Неважно. Пробуй снова. Ошибайся снова. Ошибайся лучше.", "Сэмюэл Беккет"),
    ),
    "ES": (
        ("Кто много читает и много странствует, тот много видит и много знает.", "Мигель де Сервантес"),
        ("Я — это я и моё обстоятельство.", "Хосе Ортега-и-Гассет"),
        ("Вдохновение существует, но оно должно застать тебя за работой.", "Пабло Пикассо"),
        ("Путник, дороги нет — дорогу прокладывает идущий.", "Антонио Мачадо"),
    ),
    "IT": (
        ("Земную жизнь пройдя до половины, я очутился в сумрачном лесу.", "Данте Алигьери"),
        ("Как хорошо проведённый день дарит спокойный сон, так хорошо прожитая жизнь дарит спокойную смерть.",
         "Леонардо да Винчи"),
        ("Кто не читает, к семидесяти годам проживёт одну жизнь — свою. Кто читает, проживёт пять тысяч лет.",
         "Умберто Эко"),
        ("Классика — это книга, которая никогда не заканчивает говорить то, что должна сказать.", "Итало Кальвино"),
    ),
    "AT": (
        ("Границы моего языка означают границы моего мира.", "Людвиг Витгенштейн"),
        ("О чём невозможно говорить, о том следует молчать.", "Людвиг Витгенштейн"),
        ("Когда мы больше не в силах изменить ситуацию, перед нами стоит задача изменить себя.", "Виктор Франкл"),
    ),
    "CH": (
        ("Человек рождается свободным, но повсюду он в оковах.", "Жан-Жак Руссо"),
        ("Кто смотрит наружу — видит сны, кто смотрит внутрь — пробуждается.", "Карл Густав Юнг"),
        ("Чтобы стало возможным возможное, нужно снова и снова тянуться к невозможному.", "Герман Гессе"),
    ),
    "PL": (
        ("В жизни нечего бояться, есть только то, что нужно понять.", "Мария Склодовская-Кюри"),
        ("Когда я думал, что достиг дна, снизу постучали.", "Станислав Ежи Лец"),
        ("Ничто не бывает дважды.", "Вислава Шимборская"),
    ),
    "SE": (
        ("Я никогда этого не делала, поэтому точно справлюсь!", "Астрид Линдгрен"),
        ("Спокойствие, только спокойствие!", "Астрид Линдгрен"),
        ("Никогда не мерь высоту горы, пока не достиг вершины. Тогда увидишь, какой низкой она была.",
         "Даг Хаммаршёльд"),
    ),
    "DK": (
        ("Жизнь можно понять, только оглядываясь назад, но прожить её можно, только глядя вперёд.", "Сёрен Кьеркегор"),
        ("Просто жить мало, — сказала бабочка. — Нужны солнце, свобода и маленький цветок.",
         "Ханс Кристиан Андерсен"),
        ("Эксперт — это человек, который совершил все возможные ошибки в очень узкой области.", "Нильс Бор"),
    ),
    "NO": (
        ("Самый сильный человек на свете — тот, кто наиболее одинок.", "Генрик Ибсен"),
        ("Трудное — это то, что требует немного времени; невозможное — то, что требует немного больше времени.",
         "Фритьоф Нансен"),
    ),
    "PT": (
        ("Всё стоит того, если душа не мелка.", "Фернандо Пессоа"),
        ("Нужно покинуть остров, чтобы увидеть остров.", "Жозе Сарамаго"),
    ),
    "CZ": (
        ("Книга должна быть топором для замёрзшего моря внутри нас.", "Франц Кафка"),
        ("Надежда — это не уверенность, что всё кончится хорошо, а уверенность, что в происходящем есть смысл.",
         "Вацлав Гавел"),
    ),
    "GR": (
        ("Я ни на что не надеюсь. Я ничего не боюсь. Я свободен.", "Никос Казандзакис"),
        ("Нельзя дважды войти в одну и ту же реку.", "Гераклит"),
        ("Я знаю, что ничего не знаю.", "Сократ"),
    ),
    "US": (
        ("Человека можно уничтожить, но нельзя победить.", "Эрнест Хемингуэй"),
        ("Если человек уверенно идёт к своей мечте, его ждёт успех, какого он не ждал в обычные часы.",
         "Генри Дэвид Торо"),
        ("Если не можешь лететь — беги, не можешь бежать — иди, не можешь идти — ползи, но двигайся вперёд.",
         "Мартин Лютер Кинг"),
        ("Заберись в чужую шкуру и походи в ней.", "Харпер Ли"),
    ),
    "RU": (
        ("Красота спасёт мир.", "Фёдор Достоевский"),
        ("Рукописи не горят.", "Михаил Булгаков"),
        ("В человеке должно быть всё прекрасно: и лицо, и одежда, и душа, и мысли.", "Антон Чехов"),
        ("Береги честь смолоду.", "Александр Пушкин"),
    ),
    "ZA": (
        ("Всё кажется невозможным, пока не сделаешь это.", "Нельсон Мандела"),
        ("Образование — самое мощное оружие, которым можно изменить мир.", "Нельсон Мандела"),
    ),
    "JP": (
        ("Боль неизбежна. Страдание — выбор.", "Харуки Мураками"),
        ("Старый пруд. Прыгнула в воду лягушка. Всплеск в тишине.", "Мацуо Басё"),
    ),
}


def _country_quote(cc, shown_ids=None):
    """Неповторяющаяся цитата автора страны cc (пока не показаны все); история других пулов сохраняется."""
    pool = {f"{cc}:{index}": quote for index, quote in enumerate(_COUNTRY_QUOTES[cc])}
    history = [str(value) for value in (shown_ids or [])]
    seen = [value for value in history if value in pool]
    if len(set(seen)) >= len(pool):
        seen = []
    quote_id = random.choice([key for key in pool if key not in seen])
    quote, author = pool[quote_id]
    return {"id": quote_id, "quote": quote, "src": author}, [value for value in history if value not in pool] + seen


def _cap(s):
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s


_QUOTE_MAX_CHARS = 220  # ограничивает цитату 2-3 строками в Telegram-карточке


def _clip_quote(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= _QUOTE_MAX_CHARS:
        return text
    return text[:_QUOTE_MAX_CHARS - 1].rstrip(" ,.;:") + "…"


def _word_of_day(cid):
    """Фраза дня для строки «Тренажер»: «Doe maar rustig aan → Не торопись.» (перевод — под спойлером).

    Фраза дня живёт в «Моём дне»; экран «Обучение» начинается с грамматики (live_language).
    """
    if not store.learning_is_enabled(cid):
        return "", ""
    import live_language
    lang = learning._active_language_code(cid)
    variant = int((store.get_profile(cid) or {}).get("learning_home_variant") or 0)
    phrase = live_language.daily_phrase(lang, variant=variant)
    text = str(phrase.get("text") or "").strip().rstrip(".")
    translation = str(phrase.get("translation") or "").strip().rstrip(".")
    if not text or not translation:
        return "", lang
    return f"{_cap(text)} → {_cap(translation)}.", lang


def _movie_rebus_of_day(day):
    """Локальная альтернатива языковой строке, когда обучение выключено."""
    import leisure_movies
    return leisure_movies.daily_movie_rebus(day)


_DAY_CACHE_VERSION = 19
_day_cache = {}  # cid -> {"date":..., "version":..., "text":..., "entities":..., "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)
    def change(profile):
        profile.pop("myday_home_cache", None)
        return profile, None

    store.mutate_profile(cid, change)


def _load_day_cache(cid, today):
    cached = _day_cache.get(str(cid))
    if cached and cached.get("date") == today and cached.get("version") == _DAY_CACHE_VERSION:
        return cached
    prof = store.get_profile(cid)
    saved = prof.get("myday_home_cache")
    if (not isinstance(saved, dict) or saved.get("date") != today
            or saved.get("version") != _DAY_CACHE_VERSION or not saved.get("text")):
        return None
    cached = {
        "date": today,
        "version": _DAY_CACHE_VERSION,
        "text": saved["text"],
        "entities": util.entities_from_json(saved.get("entities")),
        "ts": saved.get("ts", 0),
    }
    _day_cache[str(cid)] = cached
    return cached


def _daily_literary_quote(cid):
    """Одна проверенная цитата в день без AI: автор страны из настроек (в отпуске — страны поездки),
    для страны без своего списка — общий пул книжных цитат."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cc = str(store.get_settings(cid).get("cc") or "").upper()
    cc = cc if cc in _COUNTRY_QUOTES else ""
    profile = store.get_profile(cid)
    cached = profile.get("myday_quote_cache") or {}
    if (
        cached.get("date") == today
        and cached.get("kind") == "book"
        and cached.get("version") == 3
        and cached.get("cc", "") == cc
        and isinstance(cached.get("data"), dict)
    ):
        return cached["data"]
    history = profile.get("myday_quote_history") or []
    quote, history = _country_quote(cc, history) if cc else _motivating_book_quote(cid, history)
    history.append(quote["id"])
    store.mutate_profile(cid, lambda profile: (
        {
            **profile,
            "myday_quote_history": history,
            "myday_quote_cache": {
                "date": today, "kind": "book", "version": 3, "cc": cc, "data": quote,
            },
        },
        None,
    ))
    return quote


def _save_day_cache(cid, today, text, entities, ts):
    cached = {"date": today, "version": _DAY_CACHE_VERSION, "text": text, "entities": entities, "ts": ts}
    _day_cache[str(cid)] = cached
    saved = {
        "date": today,
        "version": _DAY_CACHE_VERSION,
        "text": text,
        "entities": util.entities_to_json(entities),
        "ts": ts,
    }
    store.mutate_profile(cid, lambda profile: (
        {**profile, "myday_home_cache": saved}, None,
    ))
    return cached


def _ready_day_sections(cid):
    """Готовые соседние карточки, которые сводка может показать без новой генерации."""
    import wardrobe

    return {
        "outfit": bool(wardrobe.get_cached_outfit_summary(cid).get("items")),
    }


def _cache_misses_ready_sections(cache, cid):
    text = str((cache or {}).get("text") or "")
    ready = _ready_day_sections(cid)
    return ready["outfit"] and " Образ:" not in text

def _day_menu_kb(cid=None):
    import vacation
    trip = [[InlineKeyboardButton("🏠 Вернуться домой", callback_data="set_vachome")]] \
        if cid is not None and vacation.active(cid) else []
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Полный прогноз на сегодня", callback_data="a_w_full")],
        [InlineKeyboardButton(weather_ui.WEEK_FORECAST_BUTTON, callback_data="a_w_week")],
        *trip,
        # «Настроить» — какие блоки показывать; экран приходит новым сообщением.
        [InlineKeyboardButton("🎚️ Настроить мой день", callback_data="set_myday_new"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _day_wind_text(wind_ms):
    """Короткое обозначение ветра для сводки дня."""
    value = float(wind_ms or 0)
    label = "Сильный ветер" if value > 10 else "Ветер"
    return f"{label} до {value:.0f} м/с"

def _holidays(settings_data):
    """Праздники страны из Настроек (Nager.Date); сбой — просто без строки."""
    try:
        import public_holidays
        return public_holidays.holiday_lines(settings_data.get("cc"), datetime.now(config.TZ).date())
    except Exception:
        _log.warning("myday: holidays unavailable", exc_info=True)
        return []


def _rail_works(cid):
    """Работы NS на станциях города; любой сбой NS — просто без строки."""
    try:
        import ns_alerts
        return ns_alerts.todays_works(cid)
    except Exception:
        _log.warning("myday: NS works unavailable cid=%s", cid, exc_info=True)
        return []


class _BlockOff(Exception):
    """Блок выключен пользователем — не ошибка, просто не показываем."""


def _vacation_blocks(cid) -> dict:
    """В отпуске: факты о стране и слово дня на языке страны; дома — {}."""
    import vacation
    if not vacation.active(cid):
        return {}
    from ui import vacation as vacation_ui
    guide = vacation.guide(cid)
    return {
        "country_lines": vacation_ui.country_lines(vacation.country_facts(cid), guide),
        "word": vacation.word_today(cid) if vacation.replaces_learning(cid) else "",
    }


def _build_day_text(cid, *, refresh_current=False):
    import settings as user_settings

    def on(block):
        return user_settings.myday_block_on(cid, block)

    s = store.get_settings(cid)
    try:
        if not on("weather"):
            raise _BlockOff()
        data = weather.fetch_weather(s["lat"], s["lon"], 2)
        if refresh_current:
            current = weather.fetch_current_conditions(s["lat"], s["lon"])
            if current:
                data = {**data, "current": current}
        weather_error = None
    except Exception as e:
        _log.warning("myday: fetch_weather failed: %s", e)
        data = None
        weather_error = e

    current_code = None
    current_precipitation = ""
    if data and (data.get("daily") or {}).get("time"):
        d = data["daily"]
        day_str = d["time"][0]
        code = d["weathercode"][0]
        _tmin, tmax = weather._daytime_temperature_range(
            data, day_str, d["temperature_2m_min"][0], d["temperature_2m_max"][0],
        )
        rain_day = d["precipitation_probability_max"][0] or 0
        rain_mm_day = (d.get("precipitation_sum") or [None])[0] if d.get("precipitation_sum") else None
        wind_ms = d["windspeed_10m_max"][0] or 0
        daytime = weather.daytime_outfit_weather(
            data, day_str, tmax, wind_ms, rain_day, rain_mm_day, code,
        )
        rain = daytime["rain_prob"]
        rain_mm = daytime["rain_mm"]
        display_wind_ms = daytime.get("wind_ms") or wind_ms
        current_code = (data.get("current") or {}).get("weathercode")
        current_precipitation = weather.current_precipitation_text(current_code)
        display_code = current_code if current_precipitation else code
        icon = weather.weather_icon(display_code, tmax, rain, display_wind_ms, rain_mm)
        rain_p = weather._periods(data, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
        if len(rain_p) > 1:
            rain_when = ", ".join(rain_p[:-1]) + " и " + rain_p[-1]
        else:
            rain_when = "".join(rain_p)
        weather_icon = icon
        if current_precipitation:
            rain_part = current_precipitation
            if rain_when:
                rain_part += f", {rain_when}"
        elif weather._rain_real(rain, rain_mm):
            rain_part = f"Дождь {rain_when}".rstrip()
        else:
            rain_part = ""
        wind_part = _day_wind_text(display_wind_ms)
        weather_line = f"до {tmax:+.0f}°C" + (f" · {rain_part}" if rain_part else "") + f" · {wind_part}"
        golden = (sun.evening_golden_range(s.get("lat"), s.get("lon"), datetime.now(config.TZ).date(), config.TZ)
                  if on("golden") else "")
        if golden:
            weather_line += f" · Золотой час {golden}"
    else:
        rain = 0
        rain_mm = None
        tmax = None
        response = getattr(weather_error, "response", None)
        status = getattr(response, "status_code", None)
        weather_icon = "☁️"
        if isinstance(weather_error, _BlockOff):
            weather_line = ""  # блок «Погода» выключен в Настройках
        elif isinstance(weather_error, weather.WeatherDailyLimitExceeded) or status == 429:
            weather_line = f"Погодный лимит исчерпан. {weather.WEATHER_LIMIT_FALLBACK}"
        else:
            weather_line = "Сейчас недоступна — остальная сводка всё равно готова."

    now = datetime.now(TZ)
    weekday_name = _WEEKDAY_SHORT[now.weekday()]
    learning_enabled = store.learning_is_enabled(cid)
    trip = _vacation_blocks(cid)
    if trip.get("word"):  # в отпуске «Тренажер» — слово на языке страны
        word_line, word_lang = (trip["word"], "") if on("word") else ("", "")
    else:
        word_line, word_lang = _word_of_day(cid) if learning_enabled and on("word") else ("", "")
    movie_rebus = {} if learning_enabled or trip.get("word") or not on("word") \
        else _movie_rebus_of_day(now.date())
    import wardrobe
    outfit_summary = wardrobe.get_cached_outfit_summary(cid) if on("outfit") else {"items": [], "emoji": ""}
    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    quote = _daily_literary_quote(cid) if on("quote") else {}
    msg = myday_ui.day_summary(
        header,
        s.get("city", ""),
        country=s.get("country", ""),
        flag=(
            util.flag_from_cc(s.get("cc", ""))
            or util.flag_from_cc(str(s.get("country", "")).upper())
            or util.country_flag(s.get("country", ""))
        ),
        weather_icon=weather_icon,
        weather_line=weather_line,
        word_line=word_line,
        word_lang=word_lang,
        movie_rebus=movie_rebus,
        outfit_items=outfit_summary["items"],
        outfit_emoji=outfit_summary["emoji"],
        quote_text=_clip_quote(quote.get("quote", "")),
        quote_author=quote.get("src", ""),
        rail_works=_rail_works(cid) if on("rail") else [],
        holidays=_holidays(s) if on("holidays") else [],
        vacation=trip,
    )
    text = msg.text
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(
        text,
        weather._rain_real(rain, rain_mm)
        or current_code in weather.RAIN_WEATHER_CODES,
    )
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    return text, msg.entities

async def _prepare_outfit(bot, cid):
    """Молча собирает образ дня для сводки; ошибка гардероба не блокирует «Мой день»."""
    import settings as user_settings
    import wardrobe
    if not user_settings.myday_block_on(cid, "outfit"):
        return  # блок «Образ дня» выключен — образ не готовим
    try:
        if not wardrobe.get_cached_outfit_summary(cid).get("items"):
            await wardrobe.send_looks(bot, cid, silent=True)
    except Exception as e:
        _log.warning("myday: outfit prep failed: %s", e)


async def send_plany(bot, cid, force=False, show_loading=True, status=None):
    """Собирает и отправляет сводку «Мой день». При inline-действии статус
    остаётся кнопкой под исходным экраном, а готовая сводка приходит отдельно."""
    import time as _time
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = None if force else _load_day_cache(cid, today)
    if cache is not None and _cache_misses_ready_sections(cache, cid):
        cache = None
    stale = cache is None
    if stale:
        if status is None:
            try:
                await bot.send_chat_action(chat_id=cid, action="typing")
            except Exception:
                _log.debug("send_plany: ignored error", exc_info=True)
        # Сбой подготовки образа не должен ронять всю сводку дня.
        await _prepare_outfit(bot, cid)
        try:
            text, entities = await asyncio.to_thread(
                _build_day_text, cid, refresh_current=force,
            )
        except Exception as e:
            await verify.safe_error(bot, cid, e, back="m_myday"); return
        cache = _save_day_cache(cid, today, text, entities, _time.time())
    cached = cache
    if status is not None:
        await status.replace(
            cached["text"], entities=cached.get("entities"), reply_markup=_day_menu_kb(cid),
        )
        return
    await bot.send_message(
        chat_id=cid, text=cached["text"], entities=cached.get("entities"),
        reply_markup=_day_menu_kb(cid),
    )


async def warm_day_cache(cid, bot=None):
    """Фоново собирает «Мой день» один раз и сохраняет переживающий рестарт кэш."""
    import time as _time
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cached = _load_day_cache(cid, today)
    if cached is not None and not _cache_misses_ready_sections(cached, cid):
        return True
    if bot:
        await _prepare_outfit(bot, cid)
    text, entities = await asyncio.to_thread(_build_day_text, cid)
    _save_day_cache(cid, today, text, entities, _time.time())
    return True
