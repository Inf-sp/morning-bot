"""Доп. настройки рассылок: что именно присылать, чтобы уведомление было полезным, а не шумным.

Каждая рассылка — список опций (`OPTIONS`): одиночный выбор («one») или набор переключателей («multi»). Значения —
в настройках пользователя под `notif_opt_<рассылка>_<опция>`; не выбрано — значение по умолчанию (старые профили
работают как раньше). Экран выбора — `settings.send_notif_option`, callback-и — по номерам рассылки, опции и
значения (`set_no_<k>_<o>`, `set_nov_<k>_<o>_<v>`): коротко и стабильно.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    key: str
    title: str
    kind: str  # "one" — одно значение; "multi" — набор
    choices: tuple  # ((значение, подпись), …)
    default: object
    hint: str = ""


_WEATHER_KINDS = (("storm", "гроза"), ("ice", "гололёд, снег и мороз"), ("wind", "штормовой ветер"),
                  ("rain", "сильный дождь"), ("heat", "жара и UV"), ("fog", "туман и влажность"))
_PLAN_BLOCKS = (("bike", "велосипед"), ("take", "что взять"), ("waste", "вывоз мусора"),
                ("events", "события и концерты"), ("premieres", "премьеры и релизы"), ("works", "работы на ЖД"))

OPTIONS = {
    "weather_warn": (
        Option("kinds", "Предупреждать о", "multi", _WEATHER_KINDS, tuple(key for key, _ in _WEATHER_KINDS),
               "Официальные предупреждения KNMI приходят всегда — выключить можно только свой расчёт по прогнозу."),
    ),
    "weekend_events": (
        Option("scope", "Какие концерты", "one", (("all", "мои и популярные"), ("mine", "только мои артисты")), "all"),
    ),
    "daily_words": (
        Option("quiz", "Вечерняя викторина", "one", (("on", "присылать в 19:30"), ("off", "не присылать")), "on",
               "Вечером — викторина по утреннему слову дня, чтобы закрепить."),
    ),
    "evening_weather": (
        Option("blocks", "Что показывать", "multi", _PLAN_BLOCKS, tuple(key for key, _ in _PLAN_BLOCKS),
               "Погода, предупреждения KNMI и праздники в плане есть всегда."),
    ),
    "ns_disruptions": (
        Option("level", "Какие сбои", "one", (("all", "все сбои"), ("severe", "только когда поездов нет")), "all"),
        Option("hours", "Когда", "one", (("day", "весь день, 06–23"), ("commute", "утром 06–10 и вечером 15–20")),
               "day"),
    ),
    "bus_disruptions": (
        Option("delay", "Опоздание от", "one",
               (("10", "10 мин"), ("15", "15 мин"), ("20", "20 мин"), ("off", "только отмены")), "10"),
    ),
    "week_holidays": (
        Option("free", "Какие события", "one", (("all", "все"), ("free", "только бесплатные")), "all"),
    ),
}
KINDS = tuple(OPTIONS)  # порядок — для коротких callback-ов по номеру


def _setting_key(kind, key) -> str:
    return f"notif_opt_{kind}_{key}"


def option(kind, key) -> Option | None:
    return next((item for item in OPTIONS.get(kind, ()) if item.key == key), None)


def get(cid, kind, key):
    """Значение опции: строка («one») или кортеж («multi»); не выбрано или устарело — по умолчанию."""
    import settings as user_settings
    item = option(kind, key)
    if item is None:
        return None
    saved = user_settings.get(cid, _setting_key(kind, key), None)
    allowed = {value for value, _ in item.choices}
    if item.kind == "multi":
        return tuple(value for value in saved if value in allowed) if isinstance(saved, list) else tuple(item.default)
    return saved if saved in allowed else item.default


def choose(cid, kind, key, value) -> None:
    """«one» — выбрать значение; «multi» — переключить (хотя бы одно остаётся)."""
    import settings as user_settings
    item = option(kind, key)
    if item is None or value not in {choice for choice, _ in item.choices}:
        return
    if item.kind == "multi":
        current = list(get(cid, kind, key))
        current = [entry for entry in current if entry != value] if value in current else [*current, value]
        user_settings.set_(cid, _setting_key(kind, key), current or [value])
    else:
        user_settings.set_(cid, _setting_key(kind, key), value)


def summary(cid, kind, key) -> str:
    """Коротко для кнопки: «только мои артисты», «4 из 6», «все»."""
    item = option(kind, key)
    labels = dict(item.choices)
    value = get(cid, kind, key)
    if item.kind == "multi":
        return "все" if len(value) == len(item.choices) else f"{len(value)} из {len(item.choices)}"
    return labels.get(value, "")
