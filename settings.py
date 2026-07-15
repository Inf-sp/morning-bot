import logging
import re
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import learning_dictionary as dictionary
import dictionary_morning
import learning_settings as learning_preferences
import saved_items
import cooking
import util
from ui import settings as settings_ui
from ui.constants import cuisine_label, delete_label, ui_label

_log = logging.getLogger(__name__)


SETTINGS_KEY = "user_settings.json"
NOTIF_TYPES = [
    ("morning_brief",   "Утро"),
    ("weekend_events",  "Куда сходить"),
    ("daily_words",     "Практика языка"),
    ("checkin_day",     "Дневная разгрузка"),
    ("evening_weather", "Погода на завтра"),
    ("checkin_eve",     "Вечерний разбор"),
    ("weather_warn",    "Погодное предупреждение"),
]

CUISINE_OPTIONS = [
    ("asian", cuisine_label("asian", "Азиатская")),
    ("russian", cuisine_label("russian", "Русская")),
    ("italian", cuisine_label("italian", "Итальянская")),
    ("mediterranean", cuisine_label("mediterranean", "Средиземноморская")),
    ("mexican", cuisine_label("mexican", "Мексиканская")),
    ("french", cuisine_label("french", "Французская")),
    ("japanese", cuisine_label("japanese", "Японская")),
    ("korean", cuisine_label("korean", "Корейская")),
    ("chinese", cuisine_label("chinese", "Китайская")),
    ("thai", cuisine_label("thai", "Тайская")),
    ("vietnamese", cuisine_label("vietnamese", "Вьетнамская")),
    ("indian", cuisine_label("indian", "Индийская")),
    ("turkish", cuisine_label("turkish", "Турецкая")),
    ("greek", cuisine_label("greek", "Греческая")),
    ("spanish", cuisine_label("spanish", "Испанская")),
    ("german", cuisine_label("german", "Немецкая")),
    ("american", cuisine_label("american", "Американская")),
    ("georgian", cuisine_label("georgian", "Грузинская")),
]

STYLES = [
    "минимализм",
    "скандинавский",
    "smart casual",
    "streetwear",
    "классический",
    "спортивный",
]

FIT_OPTIONS = [
    "свободная",
    "прямая",
    "приталенная",
]

PALETTE_OPTIONS = ["тёмные", "светлые", "яркие"]
PALETTE_ALIASES = {"цветные": "яркие"}
STYLE_AVOID_OPTIONS = ["крупные принты", "узкий крой", "слишком спортивное"]
STYLE_AVOID_LABELS = {
    "крупные принты": "Без крупных принтов",
    "узкий крой": "Без узкого кроя",
    "слишком спортивное": "Меньше спортивного",
}

COLOR_OPTIONS = [
    "белый", "чёрный", "серый", "бежевый", "синий", "зелёный",
    "красный", "жёлтый", "оливковый", "розовый", "коричневый", "бордовый",
]

CONSTRAINT_OPTIONS = [
    "не предлагать облегающий верх",
    "не предлагать облегающий низ",
    "визуально вытягивать силуэт",
    "без узких штанин",
    "без коротких рукавов",
    "без ярких принтов",
    "закрывать плечи",
    "свободная посадка везде",
]

LAYERS_OPTIONS = [
    ("1", "1 слой"),
    ("2", "2 слоя"),
    ("3", "3 слоя и больше"),
]

def _all():
    return store._load(SETTINGS_KEY)

def get(cid, key, default=None):
    return _all().get(str(cid), {}).get(key, default)

def set_(cid, key, value):
    d = _all()
    d.setdefault(str(cid), {})[key] = value
    store._save(SETTINGS_KEY, d)

_LEGACY_NOTIF_KINDS = {
    "daily_words": ("daily_words_nl", "daily_words_en", "live_lang", "grammar_nl", "grammar_en"),
    "weekend_events": ("weekly_events", "favorite_artists"),
}

def notif_on(cid, kind):
    value = get(cid, f"notif_{kind}", None)
    if value is not None:
        return bool(value)
    for legacy_kind in _LEGACY_NOTIF_KINDS.get(kind, ()):
        legacy_value = get(cid, f"notif_{legacy_kind}", None)
        if legacy_value is not None:
            return bool(legacy_value)
    if kind == "daily_words":
        return bool(get(cid, "notif_grammar", False))
    return False

def study_lang(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return "нидерландский" if code == "nl" else "английский"
    legacy = get(cid, "study_lang", "нидерландский")
    code = "en" if legacy == "английский" else "nl"
    store.set_learning_language(cid, code)
    return "нидерландский" if code == "nl" else "английский"


def cuisines(cid):
    saved = get(cid, "cuisines", [])
    if not isinstance(saved, list):
        return []
    valid = {key for key, _ in CUISINE_OPTIONS}
    return [key for key in saved if key in valid]


def cuisine_labels(cid):
    selected = set(cuisines(cid))
    return [label for key, label in CUISINE_OPTIONS if key in selected]


def cuisine_context(cid):
    labels = cuisine_labels(cid)
    if not labels:
        return ""
    return "Предпочитаемые кухни пользователя: " + ", ".join(labels) + "."


def _mark_transient_edit(bot, cid, message):
    marker = getattr(bot, "mark_transient_message", None)
    if marker is not None:
        marker(cid, getattr(message, "message_id", None))


def _notif_label(kind: str, label: str) -> str:
    if kind == "weekend_events":
        return f"{label} (по пятницам в 10:00)"
    times = {
        "morning_brief": "08:30",
        "weather_warn": "08:45",
        "daily_words": "11:00",
        "checkin_day": "14:00",
        "evening_weather": "19:00",
        "checkin_eve": "21:30",
    }
    if kind in times:
        return f"{label} (ежедневно в {times[kind]})"
    return label

async def send_home(bot, cid):
    await saved_items.send_notes(bot, cid)


async def refresh_database(bot, cid, q=None):
    import data_refresh

    if q is not None:
        try:
            await q.message.edit_text("🔄 Обновляю базу и физические свойства вещей…")
        except Exception:
            pass
    result = await data_refresh.refresh_user_database(cid)
    msg = settings_ui.database_refresh_result(result)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="set_home"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ]])
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


class _NoKbBot:
    """Обёртка для push-уведомлений: убирает кнопки, как в плановых уведомлениях."""
    def __init__(self, bot):
        self._bot = bot

    def __getattr__(self, name):
        orig = getattr(self._bot, name)
        if name in ("send_message", "send_photo", "send_document", "send_animation", "send_chat_action"):
            async def _w(*a, **kw):
                kw.pop("reply_markup", None)
                return await orig(*a, **kw)
            return _w
        return orig


async def send_scheduled_notification(bot, cid, kind):
    """Отправить ровно то уведомление, которое уходит из планового уведомления."""
    if kind == "morning_brief":
        import myday as _m
        # force=False: если пользователь уже открывал «Мой день» сегодня, уведомление
        # переиспользует готовый дневной кэш вместо повторной сборки (экономит AI/API).
        await _m.send_plany(_NoKbBot(bot), cid, force=False, show_loading=False)
    elif kind == "weather_warn":
        import asyncio
        import weather as _w
        import weather_warn as _ww
        s = store.get_settings(cid)
        data = await asyncio.to_thread(_w.fetch_weather, s["lat"], s["lon"], 2)
        msg = _ww.build_warning(data, cid)
        # Тихий день без значимых погодных факторов — ничего не отправляем.
        if msg is not None:
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif kind == "daily_words":
        await dictionary_morning.send_daily_practice(_NoKbBot(bot), cid)
    elif kind == "checkin_day":
        store.pending_input[str(cid)] = "worry"
        set_(cid, "_worry_prompt_ts", datetime.now(config.TZ).timestamp())
        _log.info("checkin_day: pending_input=worry set for cid=%s", cid)
        await bot.send_message(chat_id=cid, parse_mode="HTML",
            text="🫣 <b>Дневная разгрузка</b>\n\nСейчас не анализируй, просто выгрузи мысли.\n\n"
                 "Каждая тревога - с новой строки.\n\nВечером проверим, что было фактами, а что шумом…")
    elif kind == "checkin_eve":
        import balance as _b
        await _b.send_evening_review(bot, cid)
    elif kind == "weekend_events":
        import leisure_concerts
        await leisure_concerts.send_weekend_events(_NoKbBot(bot), cid)
    elif kind == "evening_weather":
        import weather as _w
        await _w.send_weather(_NoKbBot(bot), cid, "tomorrow_plain")


async def _run_notif_test(bot, cid, kind) -> bool:
    """Предпросмотр уведомления: вызывает тот же код, что и плановое уведомление.
    Возвращает True/False — вызывающий сам решает, что показать администратору."""
    try:
        await send_scheduled_notification(bot, cid, kind)
        return True
    except Exception as e:
        _log.error("notif test failed for kind=%s: %r", kind, e, exc_info=True)
        import tracking
        tracking.log_error("app", str(e), kind=f"notif_test:{kind}")
        return False


class NotificationOption:
    """Одно тестируемое уведомление для админ-панели: ключ + заголовок + расписание.

    button_title — ровно тот заголовок, который видно в реальном пришедшем
    сообщении (см. send_scheduled_notification), не служебный ярлык — чтобы
    пользователь на экране «Уведомления» узнавал кнопку по тому, что ему
    приходит, а не гадал по короткому названию."""
    __slots__ = ("key", "title", "schedule_label", "time_label", "button_title", "button_label", "sort_key")

    def __init__(self, key: str, title: str, schedule_label: str, time_label: str = "",
                 button_title: str = "", sort_key: int = 9999):
        self.key = key
        self.title = title
        self.schedule_label = schedule_label
        self.time_label = time_label
        self.button_title = button_title or title
        self.button_label = f"{self.button_title} · {time_label}".strip(" ·") if time_label else self.button_title
        self.sort_key = sort_key


_ADMIN_NOTIFICATION_META = {
    "morning_brief":   ("08:30", "☀️ Мой день"),
    "weekend_events":  ("пт 10:00", "🎧 Ближайшие события"),
    "daily_words":     ("11:00", "📚 Слова и фразы дня"),
    "checkin_day":     ("14:00", "🫣 Дневная разгрузка"),
    "evening_weather": ("19:00", "🌦️ Погода на завтра"),
    "checkin_eve":     ("21:30", "🌙 Вечерний разбор"),
    "weather_warn":    ("08:45, если есть повод", "⚠️ Погодное предупреждение"),
}


def _time_sort_key(value: str) -> int:
    """Извлекает HH:MM из произвольного места строки (не только 'HH:MM' целиком) —
    time_label теперь может быть 'пт 10:00' или '08:45, если есть повод'."""
    import re
    m = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not m:
        return 9999
    return int(m.group(1)) * 60 + int(m.group(2))


def get_notification_options() -> list:
    """Все реально существующие уведомления с короткими универсальными названиями.
    Берём из NOTIF_TYPES (тот же список, что видит пользователь в своих настройках),
    т.к. каждый kind оттуда обрабатывается в send_scheduled_notification."""
    options = []
    for order, (kind, label) in enumerate(NOTIF_TYPES):
        time_label, button_title = _ADMIN_NOTIFICATION_META.get(kind, ("", label))
        options.append(NotificationOption(
            key=kind,
            title=label,
            schedule_label=_notif_schedule(kind),
            time_label=time_label,
            button_title=button_title,
            sort_key=_time_sort_key(time_label) * 100 + order,
        ))
    return sorted(options, key=lambda opt: opt.sort_key)


def get_admin_notification_options() -> list:
    """Compatibility wrapper: админка использует тот же список, что и пользовательское меню."""
    return get_notification_options()


def _notif_schedule(kind: str) -> str:
    """Короткое человекочитаемое расписание уведомления для пикера в админке."""
    labelled = _notif_label(kind, "")
    # _notif_label возвращает "<label> (<когда>)" или просто label, если расписания нет —
    # достаём только скобочную часть с расписанием.
    if "(" in labelled and labelled.endswith(")"):
        return labelled[labelled.index("(") + 1:-1].capitalize()
    return "По расписанию"


async def send_notif(bot, cid, q=None):
    rows = []
    for opt in get_notification_options():
        on = notif_on(cid, opt.key)
        mark = "✅" if on else "□"
        rows.append([InlineKeyboardButton(f"{mark} {opt.button_label}", callback_data=f"set_notiftgl_{opt.key}")])
    if any(notif_on(cid, kind) for kind, _ in NOTIF_TYPES):
        rows.append([InlineKeyboardButton("🔕 Отключить все", callback_data="set_notif_off_all")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = settings_ui.notifications()
    text = msg.text
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=msg.entities,
                           reply_markup=kb, transient=True)

async def toggle_notif(bot, cid, kind, q=None):
    if kind not in dict(NOTIF_TYPES):
        await send_notif(bot, cid, q)
        return
    set_(cid, f"notif_{kind}", not notif_on(cid, kind))
    await send_notif(bot, cid, q)


async def notif_off_all(bot, cid, q=None):
    for kind, _ in NOTIF_TYPES:
        set_(cid, f"notif_{kind}", False)
    await send_notif(bot, cid, q)


async def send_personalization(bot, cid, q=None):
    """Персонализация сейчас пустует по содержанию — Гардероб, Обучение, Кино/музыка
    и Кухни переехали в свои разделы («Гардероб» → «Настройки гардероба», «Обучение»
    → «Настройки обучения», «Досуг» → «Настройки досуга», «Готовка» → «Настройки
    готовки»). Экран оставлен как compat-редирект на главные Настройки."""
    rows = [
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.personalization()
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _cuisines_kb(cid):
    selected = set(cuisines(cid))
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "") + label,
            callback_data=f"set_cuisine_{key}",
        )
        for key, label in CUISINE_OPTIONS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_fridge_g"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_cuisines(bot, cid, q=None):
    labels = cuisine_labels(cid)
    current = ", ".join(labels) if labels else "не выбраны"
    msg = settings_ui.cuisines(current)
    text = msg.text
    kb = _cuisines_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=msg.entities,
                           reply_markup=kb, transient=True)


async def toggle_cuisine(bot, cid, key, q=None):
    valid = {k for k, _ in CUISINE_OPTIONS}
    if key not in valid:
        await send_cuisines(bot, cid, q)
        return
    selected = cuisines(cid)
    if key in selected:
        selected = [k for k in selected if k != key]
    else:
        selected.append(key)
    set_(cid, "cuisines", selected)
    await send_cuisines(bot, cid, q)


def _normalize_wardrobe_styles(cur):
    if isinstance(cur, str):
        cur = [cur] if cur else []
    if isinstance(cur, list):
        aliases = {
            "скандинавский стиль": "скандинавский",
            "streetwear / городской": "streetwear",
            "классика": "классический",
        }
        return [aliases.get(s, s) for s in cur if aliases.get(s, s) in STYLES]
    return []


def wardrobe_styles(cid):
    return _normalize_wardrobe_styles(get(cid, "style", []))


STYLE_LIMIT = 3

async def set_style(bot, cid, i, q=None):
    if 0 <= i < len(STYLES):
        chosen = STYLES[i]
        selected = wardrobe_styles(cid)
        if chosen in selected:
            selected = [s for s in selected if s != chosen]
            set_(cid, "style", selected)
        elif len(selected) >= STYLE_LIMIT:
            if q is not None:
                try:
                    await q.answer(f"Можно выбрать максимум {STYLE_LIMIT} стиля.", show_alert=False)
                except Exception:
                    pass
            await send_wardrobe_style(bot, cid, q=q)
            return
        else:
            selected.append(chosen)
            set_(cid, "style", selected)
    await send_wardrobe_style(bot, cid, q=q)


async def set_fit(bot, cid, i, q=None):
    if 0 <= i < len(FIT_OPTIONS):
        set_(cid, "wardrobe_fit", FIT_OPTIONS[i])
    await send_wardrobe_style(bot, cid, q=q)


def _multi_selected(cid, key, options):
    """Список выбранных значений поля key, только те что входят в options (защита от
    устаревших/свободных значений старого текстового формата)."""
    cur = get(cid, key, [])
    if isinstance(cur, list):
        return [v for v in cur if v in options]
    return []


def wardrobe_colors_love(cid):
    return _multi_selected(cid, "wardrobe_colors_love", COLOR_OPTIONS)


def wardrobe_colors_avoid(cid):
    return _multi_selected(cid, "wardrobe_colors_avoid", COLOR_OPTIONS)


def wardrobe_constraints_list(cid):
    return _multi_selected(cid, "wardrobe_constraints", CONSTRAINT_OPTIONS)


def wardrobe_palette(cid):
    return _normalize_palette(get(cid, "wardrobe_palette", []))


def wardrobe_style_avoid(cid):
    return _multi_selected(cid, "wardrobe_style_avoid", STYLE_AVOID_OPTIONS)


def _normalize_palette(values):
    if not isinstance(values, list):
        return []
    normalized = [PALETTE_ALIASES.get(value, value) for value in values]
    return list(dict.fromkeys(value for value in normalized if value in PALETTE_OPTIONS))


def _toggle_palette(cid, idx):
    if not (0 <= idx < len(PALETTE_OPTIONS)):
        return
    chosen = PALETTE_OPTIONS[idx]
    selected = wardrobe_palette(cid)
    selected = [value for value in selected if value != chosen] if chosen in selected else [*selected, chosen]
    set_(cid, "wardrobe_palette", selected)


def _toggle_multi(cid, key, options, idx):
    if not (0 <= idx < len(options)):
        return
    chosen = options[idx]
    selected = _multi_selected(cid, key, options)
    if chosen in selected:
        selected = [v for v in selected if v != chosen]
    else:
        selected.append(chosen)
    set_(cid, key, selected)


def _multi_pick_kb(selected, options, prefix, back):
    buttons = [InlineKeyboardButton(("✅ " if v in selected else "") + v, callback_data=f"{prefix}_{i}")
               for i, v in enumerate(options)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_colors_love(bot, cid, q=None):
    msg = settings_ui.mydata_section("Любимые цвета", "Отметь, какие цвета предпочитаешь в образах.")
    kb = _multi_pick_kb(wardrobe_colors_love(cid), COLOR_OPTIONS, "set_colorlove", "set_wardrobe_style")
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


async def set_colors_love_toggle(bot, cid, i, q=None):
    _toggle_multi(cid, "wardrobe_colors_love", COLOR_OPTIONS, i)
    await send_colors_love(bot, cid, q=q)


async def send_colors_avoid(bot, cid, q=None):
    msg = settings_ui.mydata_section("Не предлагать цвета", "Отметь цвета, которые не стоит предлагать.")
    kb = _multi_pick_kb(wardrobe_colors_avoid(cid), COLOR_OPTIONS, "set_coloravoid", "set_wardrobe_style")
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


async def set_colors_avoid_toggle(bot, cid, i, q=None):
    _toggle_multi(cid, "wardrobe_colors_avoid", COLOR_OPTIONS, i)
    await send_colors_avoid(bot, cid, q=q)


async def send_constraints(bot, cid, q=None):
    """Ограничения: практические правила подбора (не факты тела) — напр. «не предлагать
    облегающий верх», «визуально вытягивать силуэт»."""
    msg = settings_ui.mydata_section("Ограничения", "Отметь, что учитывать при подборе образа.")
    kb = _multi_pick_kb(wardrobe_constraints_list(cid), CONSTRAINT_OPTIONS, "set_constraint", "set_wardrobe_style")
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def set_constraint_toggle(bot, cid, i, q=None):
    _toggle_multi(cid, "wardrobe_constraints", CONSTRAINT_OPTIONS, i)
    await send_constraints(bot, cid, q=q)


def wardrobe_prefs_context(cid):
    """Собирает все предпочтения гардероба одной строкой для промпта подбора образа.
    Формулировки — явные инструкции, а не общее пожелание (§ Персонализация в CLAUDE.md)."""
    parts = []
    styles = wardrobe_styles(cid)
    if styles:
        if len(styles) == 1:
            parts.append(f"Стиль пользователя: {styles[0]}.")
        else:
            extra = ", ".join(styles[1:])
            parts.append(f"Основной стиль пользователя: {styles[0]} (дополнительные ориентиры: {extra}).")
    style_custom = get(cid, "wardrobe_style_custom", "")
    if style_custom:
        parts.append(f"Стиль своими словами: {style_custom}.")
    fit = get(cid, "wardrobe_fit", "")
    if fit:
        parts.append(f"Предпочитаемая посадка одежды: {fit}.")
    palette = wardrobe_palette(cid)
    if palette:
        parts.append(f"Предпочитаемая палитра: {', '.join(palette)}.")
    style_avoid = wardrobe_style_avoid(cid)
    if style_avoid:
        parts.append(f"Не предлагать: {', '.join(style_avoid)}.")
    colors_love = wardrobe_colors_love(cid)
    if colors_love:
        parts.append(f"Любимые цвета — предпочитай их в подборе: {', '.join(colors_love)}.")
    colors_avoid = wardrobe_colors_avoid(cid)
    if colors_avoid:
        parts.append(f"Нежелательные цвета — не предлагать: {', '.join(colors_avoid)}.")
    constraints = wardrobe_constraints_list(cid)
    if constraints:
        parts.append(f"Ограничения — обязательно учитывай: {', '.join(constraints)}.")
    layers = get(cid, "wardrobe_layers", "")
    if layers:
        layers_label = dict(LAYERS_OPTIONS).get(layers, "")
        parts.append(f"Слои: {layers_label}.")
    return "\n".join(parts)


# ===== Настройки гардероба (живут в разделе «Гардероб», не в Персонализации) =====
async def send_wardrobe_settings_hub(bot, cid, q=None):
    """«Настройки гардероба»: Стиль (предпочтения подбора) и Мой гардероб (сами вещи)."""
    msg = settings_ui.mydata_section(
        "Настройки гардероба",
        "Стиль влияет на подбор образа. Мой гардероб — управление вещами.",
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Стиль", callback_data="set_wardrobe_style")],
        [InlineKeyboardButton("👕 Мой гардероб", callback_data="set_wardrobe_g")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


def _wardrobe_style_state(cid):
    raw = _all().get(str(cid), {})
    palette = raw.get("wardrobe_palette", [])
    avoid = raw.get("wardrobe_style_avoid", [])
    return {
        "styles": _normalize_wardrobe_styles(raw.get("style", [])),
        "fit": raw.get("wardrobe_fit", ""),
        "palette": _normalize_palette(palette),
        "avoid": [value for value in avoid if value in STYLE_AVOID_OPTIONS] if isinstance(avoid, list) else [],
    }


def _wardrobe_style_kb(cid, state=None):
    state = state or _wardrobe_style_state(cid)
    selected_styles = set(state["styles"])
    style_buttons = [InlineKeyboardButton(("✅ " if s in selected_styles else "") + s.capitalize(), callback_data=f"set_style_{i}")
                     for i, s in enumerate(STYLES)]
    fit = state["fit"]
    fit_buttons = [InlineKeyboardButton(("✅ " if fit == f else "") + f.capitalize(), callback_data=f"set_fit_{i}")
                   for i, f in enumerate(FIT_OPTIONS)]
    palette = set(state["palette"])
    avoid = set(state["avoid"])
    rows = [style_buttons[i:i + 2] for i in range(0, len(style_buttons), 2)]
    rows.extend(fit_buttons[i:i + 3] for i in range(0, len(fit_buttons), 3))
    palette_buttons = [InlineKeyboardButton(("✅ " if value in palette else "") + value.capitalize(), callback_data=f"set_palette_{i}")
                       for i, value in enumerate(PALETTE_OPTIONS)]
    rows.append(palette_buttons)
    avoid_buttons = [InlineKeyboardButton(
        ("✅ " if value in avoid else "") + STYLE_AVOID_LABELS[value],
        callback_data=f"set_stylelimit_{i}",
    )
                     for i, value in enumerate(STYLE_AVOID_OPTIONS)]
    rows.extend(avoid_buttons[i:i + 2] for i in range(0, len(avoid_buttons), 2))
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_wardrobe"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_wardrobe_style(bot, cid, q=None):
    """Стиль гардероба — один экран, все переключатели нажимаются сразу (стиль и
    посадка — toggle с галочкой на месте, без перехода на отдельный подэкран)."""
    state = _wardrobe_style_state(cid)
    msg = settings_ui.wardrobe_style(state["styles"], state["fit"], state["palette"], state["avoid"])
    kb = _wardrobe_style_kb(cid, state)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


async def send_wardrobe_prefs(bot, cid, back="set_priorities", q=None):
    """Совместимость со старыми сообщениями: раздел переехал в «Гардероб» → «Настройки
    гардероба» → «Стиль»."""
    await send_wardrobe_style(bot, cid, q=q)


# --- Страны ---
async def send_lagom(bot, cid, back="m_balance"):
    import memory
    items = memory.get_lagom(cid)
    rows = [[InlineKeyboardButton("🆕 Добавить принцип", callback_data="setadd_lagom")]]
    if items:
        rows.append([InlineKeyboardButton(delete_label("Удалить принципы"), callback_data="set_lagom_clean")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = settings_ui.lagom_home(items)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

async def handle_callback(bot, cid, data, q=None):
    if data == "set_home":
        await send_home(bot, cid)
    elif data == "set_mydata_leisure":
        await saved_items.send_mydata_leisure(bot, cid)
    elif data == "set_mydata_leisure_p":
        await saved_items.send_mydata_leisure(bot, cid, back="set_priorities")
    elif data == "set_mydata_cinema":
        await saved_items.send_mydata_cinema(bot, cid)
    elif data == "set_mydata_books":
        await saved_items.send_mydata_books(bot, cid)
    elif data == "set_mydata_music":
        await saved_items.send_mydata_music(bot, cid)
    elif data == "set_food":
        await saved_items.send_food(bot, cid, q)
    elif data == "set_travel":
        await saved_items.send_travel(bot, cid)
    elif data == "set_fridge":
        import balance
        import fridge
        await fridge.send_fridge(bot, cid, back="set_food")
    elif data == "set_myrecipes":
        import balance
        import saved_recipes
        await saved_recipes.send_my_recipes(bot, cid)
    elif data == "set_fridge_g":
        await saved_items.send_food(bot, cid, back="m_food")
    elif data == "set_notif":
        await send_notif(bot, cid, q)
    elif data == "set_refresh_data":
        await refresh_database(bot, cid, q)
    elif data == "set_priorities":
        await send_personalization(bot, cid, q)
    elif data.startswith("set_prio_"):
        # Compat-редирект для старых сообщений: раздел "Приоритеты" стал "Персонализацией".
        await send_personalization(bot, cid, q)
    elif data == "set_wardrobe_settings":
        await send_wardrobe_settings_hub(bot, cid, q)
    elif data == "set_wardrobe_style":
        await send_wardrobe_style(bot, cid, q)
    elif data in ("set_wardrobe_prefs", "set_stylepick", "set_fitpick", "set_layerspick"):
        # Compat-редирект: настройки гардероба переехали из Персонализации в раздел
        # «Гардероб» → «Настройки гардероба» → «Стиль», слои убраны из UI.
        await send_wardrobe_style(bot, cid, q)
    elif data.startswith("set_style_"):
        await set_style(bot, cid, int(data[len("set_style_"):]), q)
    elif data.startswith("set_fit_"):
        await set_fit(bot, cid, int(data[len("set_fit_"):]), q)
    elif data.startswith("set_palette_"):
        _toggle_palette(cid, int(data[len("set_palette_"):]))
        await send_wardrobe_style(bot, cid, q)
    elif data.startswith("set_stylelimit_"):
        _toggle_multi(cid, "wardrobe_style_avoid", STYLE_AVOID_OPTIONS, int(data[len("set_stylelimit_"):]))
        await send_wardrobe_style(bot, cid, q)
    elif data.startswith("set_styleavoid_"):
        # Старые кнопки содержат другой порядок вариантов: только открываем
        # актуальный экран, чтобы не включить неверное ограничение по индексу.
        await send_wardrobe_style(bot, cid, q)
    elif data.startswith("set_layers_"):
        # Compat: кнопки слоёв в старых сообщениях больше никуда не ведут отдельно.
        await send_wardrobe_style(bot, cid, q)
    elif data == "set_colors_love":
        await send_colors_love(bot, cid, q)
    elif data.startswith("set_colorlove_"):
        await set_colors_love_toggle(bot, cid, int(data[len("set_colorlove_"):]), q)
    elif data == "set_colors_avoid":
        await send_colors_avoid(bot, cid, q)
    elif data.startswith("set_coloravoid_"):
        await set_colors_avoid_toggle(bot, cid, int(data[len("set_coloravoid_"):]), q)
    elif data == "set_constraints":
        await send_constraints(bot, cid, q)
    elif data.startswith("set_constraint_"):
        await set_constraint_toggle(bot, cid, int(data[len("set_constraint_"):]), q)
    elif data == "set_cuisines":
        await send_cuisines(bot, cid, q)
    elif data.startswith("set_cuisine_"):
        await toggle_cuisine(bot, cid, data[len("set_cuisine_"):], q)
    elif data.startswith("set_notiftgl_"):
        await toggle_notif(bot, cid, data[len("set_notiftgl_"):], q)
    elif data == "set_notif_off_all":
        await notif_off_all(bot, cid, q)
    elif data == "set_learning_mydata":
        await learning_preferences.send_learning_settings(bot, cid, q=q, back="set_priorities")
    elif data == "set_learning" or data == "toggle_learning_language" or data.startswith("set_learning_level_"):
        await learning_preferences.handle_learning_settings_callback(bot, cid, q, data)
    elif data == "set_city":
        store.pending_input[cid] = "setcity"
        msg = settings_ui.city_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "set_body":
        # Compat-редирект для старых сообщений: поле переехало в "Ограничения".
        await send_constraints(bot, cid)
    elif data == "set_wardrobe_g":
        import wardrobe
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
    elif data == "set_ward_add":
        store.pending_input[cid] = "wardrobe_add_set"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe_g"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
        msg = settings_ui.wardrobe_item_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    elif data == "set_lagom":
        await send_lagom(bot, cid, back="m_balance")
    elif data == "setadd_lagom":
        store.pending_input[cid] = "setadd_lagom"
        msg = settings_ui.lagom_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "set_lagom_clean":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "lagom")
    elif data == "set_countries":
        _log.info("legacy callback used: %s", data)
        await saved_items.send_love_section(bot, cid, "countries")
    elif data == "set_artists":
        _log.info("legacy callback used: %s", data)
        await saved_items.send_love_section(bot, cid, "artists")
    elif data == "set_books":
        _log.info("legacy callback used: %s", data)
        await saved_items.send_love_section(bot, cid, "books")
    elif data == "set_stylecustom":
        store.pending_input[cid] = "styleinput"
        msg = settings_ui.style_custom_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "adm_home":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data in ("adm_check_all", "adm_system", "adm_system_check", "adm_diag", "adm_diag_api",
                  "adm_diag_llm", "adm_diag_news", "adm_api_ai", "adm_api_ai_check"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "adm_logs":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_logs(b, c, q))
    elif data in ("adm_notif", "adm_notif_check"):
        # Compat-редирект: раздел "Уведомления" в админке удалён (ручные тесты не нужны).
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "adm_users":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_users(b, c, q))
    elif data == "adm_user_del":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_user_delete_list(b, c, q))
    elif data.startswith("adm_user_delconfirm_"):
        target = data[len("adm_user_delconfirm_"):]
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c, t=target: _adm.send_user_delete_confirm(b, c, t, q))
    elif data.startswith("adm_user_delok_"):
        target = data[len("adm_user_delok_"):]
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c, t=target: _adm.do_user_delete(b, c, t, q))
    elif data == "adm_invite":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_invite(b, c, q))
    elif data == "adm_invite_create":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.create_invite(b, c, q))
    elif data in ("adm_welcome", "adm_welcome_preview", "adm_welcome_edit"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_welcome(b, c, q))
    elif data == "adm_tests" or data.startswith("adm_test_"):
        # Compat-редирект: ручные тесты уведомлений удалены.
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "set_admin":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "set_admin_users":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_users(b, c, q))
    elif data in ("set_admin_llm", "set_admin_news", "set_admin_llmcheck", "set_admin_llmhistory"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data in ("set_admin_broadcast", "set_admin_broadcast_test_pick") or data.startswith("set_admin_broadcast_test_"):
        # Compat-редирект: ручные тесты уведомлений удалены.
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data in ("set_admin_issues", "set_admin_check_all") or data.startswith("set_admin_issue_"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "set_admin_api_diagnostics":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "set_admin_cache_clear":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.clear_cache(b, c, q))
    elif data == "set_admin_invite":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_invite(b, c, q))
    elif data.startswith("set_admin_revoke_"):
        target = data[len("set_admin_revoke_"):]
        async def _do_revoke(b, c):
            import access as _acc
            _acc.revoke_user(target)
            store.purge_user(target)
            import admin as _adm
            await _adm.send_users(b, c, q)
        await _admin_guard(bot, cid, _do_revoke)
    elif data.startswith("set_admin_"):
        # устаревшие или неизвестные callback-и из уже отправленных сообщений —
        # безопасный fallback вместо silent fail или traceback; авторизация уже
        # проверена _admin_guard-ом до захода в эту ветку.
        async def _do_fallback(b, c):
            _log.warning("unknown/legacy admin callback: %s", data)
            await b.send_message(chat_id=c, text="Панель обновлена. Открываю актуальное меню.")
            await send_admin(b, c)
        await _admin_guard(bot, cid, _do_fallback)


# ===== АДМИНИСТРАТОР =====

def _is_admin(cid) -> bool:
    return bool(config.CHAT_ID) and str(cid) == str(config.CHAT_ID)


async def _admin_guard(bot, cid, fn):
    """Выполнить fn(bot, cid) только если cid — администратор."""
    if not _is_admin(cid):
        msg = settings_ui.admin_only()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return
    await fn(bot, cid)


async def send_admin(bot, cid):
    """Главный экран администратора (Дом). Делегирует в модуль admin."""
    if not _is_admin(cid):
        msg = settings_ui.admin_only()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return
    import admin as _admin
    await _admin.send_home(bot, cid)
