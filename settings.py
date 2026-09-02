import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import dictionary_morning
import learning_settings as learning_preferences
from ui import settings as settings_ui
from ui import weather as weather_ui
from ui.constants import cuisine_label, ui_label

_log = logging.getLogger(__name__)


SETTINGS_KEY = "user_settings.json"
EVENING_WEATHER_TIME = "20:00"
NOTIF_TYPES = [
    ("weather_warn",     "Погодное предупреждение"),
    ("weekend_events",  "Ближайшие события"),
    ("daily_words",     "Обучение языку"),
    ("evening_weather", "Погода на завтра"),
]

CUISINE_OPTIONS = [
    ("asian", cuisine_label("asian", "Азиатская")),
    ("italian", cuisine_label("italian", "Итальянская")),
    ("mediterranean", cuisine_label("mediterranean", "Средиземноморская")),
    ("french", cuisine_label("french", "Французская")),
    ("mexican", cuisine_label("mexican", "Мексиканская")),
    ("indian", cuisine_label("indian", "Индийская")),
    ("eastern_european", cuisine_label("eastern_european", "Восточноевропейская")),
]

STYLES = [
    "Минимализм",
    "Скандинавский",
    "Повседневный",
    "Городской",
    "Классический",
    "Спортивный",
]

FIT_OPTIONS = [
    "свободная",
    "прямая",
    "приталенная",
]

PALETTE_OPTIONS = ["тёмные", "светлые", "яркие"]
PALETTE_ALIASES = {"цветные": "яркие"}
STYLE_AVOID_OPTIONS = ["крупные принты", "узкий крой"]
STYLE_AVOID_LABELS = {
    "крупные принты": "Без крупных принтов",
    "узкий крой": "Без узкого кроя",
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
    "daily_words": ("daily_words_nl", "daily_words_en", "grammar_nl", "grammar_en"),
    "weekend_events": ("weekly_events", "favorite_artists"),
}

def notif_on(cid, kind):
    value = get(cid, f"notif_{kind}", None)
    if value is not None:
        return bool(value)
    # До появления переключателя погодные предупреждения приходили всем.
    # Сохраняем это поведение для старых профилей, пока пользователь сам их
    # не отключит.
    if kind == "weather_warn":
        return True
    for legacy_kind in _LEGACY_NOTIF_KINDS.get(kind, ()):
        legacy_value = get(cid, f"notif_{legacy_kind}", None)
        if legacy_value is not None:
            return bool(legacy_value)
    if kind == "daily_words":
        return bool(get(cid, "notif_grammar", False))
    return False

def study_lang(cid):
    if not store.learning_is_enabled(cid):
        return "не изучаю"
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
    migrated = ["asian" if key == "japanese" else key for key in saved]
    return list(dict.fromkeys(key for key in migrated if key in valid))


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
    if kind == "weather_warn":
        return f"{label} (ежедневно в 08:00, если есть повод)"
    if kind == "weekend_events":
        return f"{label} (по пятницам в 10:00)"
    times = {
        "daily_words": "11:00",
        "evening_weather": EVENING_WEATHER_TIME,
    }
    if kind in times:
        return f"{label} (ежедневно в {times[kind]})"
    return label


def notification_markup(kind: str, rows, *, enabled: bool = True) -> InlineKeyboardMarkup:
    """Единая вертикальная навигация плановых уведомлений."""
    toggle_label = "🔕 Отключить уведомления" if enabled else "✅ Включить уведомления"
    actions, home = [], []
    for row in rows:
        for button in row:
            callback = str(getattr(button, "callback_data", "") or "")
            if callback.startswith("set_notifpush_"):
                continue
            (home if callback == "m_menu" else actions).append(button)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"set_notifpush_{kind}")],
        *[[button] for button in actions],
        *[[button] for button in home],
    ])

async def send_home(bot, cid, q=None):
    rows = [
        [InlineKeyboardButton("📍 Город", callback_data="set_city")],
        [InlineKeyboardButton(ui_label("broadcasts", "Уведомления"), callback_data="set_notif")],
        [InlineKeyboardButton("📤 Экспорт данных", callback_data="as_export")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ]
    city = store.get_settings(cid).get("city") or ""
    notification_kinds = [item.key for item in get_notification_options()]
    notifications_on = any(notif_on(cid, kind) for kind in notification_kinds)
    language = "Не изучаю"
    if store.learning_is_enabled(cid):
        language = "Английский" if store.get_learning_language(cid) == "en" else "Нидерландский"
    msg = settings_ui.settings_home(city, notifications_on, language)
    markup = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


async def send_preferences(bot, cid, q=None):
    rows = [
        [InlineKeyboardButton("🧠 Обучение", callback_data="set_pref_learning")],
        [InlineKeyboardButton("🥣 Кухни", callback_data="set_pref_cuisines")],
        [InlineKeyboardButton("🧵 Стиль", callback_data="set_pref_style")],
        [InlineKeyboardButton("🎧 Музыка", callback_data="set_pref_music")],
        [InlineKeyboardButton("🎬 Кино", callback_data="set_pref_movie")],
        [InlineKeyboardButton("📚 Книги", callback_data="set_pref_books")],
        [InlineKeyboardButton("👾 Игры", callback_data="set_pref_games")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ]
    msg = settings_ui.preferences_home()
    markup = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


async def send_lifehacks(bot, cid, q=None):
    import myday

    records = myday.lifehack_records(include_disabled=False)
    page = 0
    total_pages = max(1, (len(records) + 4) // 5)
    chunk = records[page * 5:page * 5 + 5]
    msg = settings_ui.lifehacks_home(len(records), chunk, page, total_pages)
    rows = [
        [InlineKeyboardButton("🆕 Добавить", callback_data="set_lh_add")],
        [InlineKeyboardButton("❌ Удалить", callback_data="set_lh_delete")],
    ]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"set_lh_page_{total_pages - 1}"),
            InlineKeyboardButton("▶️", callback_data="set_lh_page_1"),
        ])
    rows.extend([
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    markup = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


async def send_lifehack_page(bot, cid, page, q=None):
    import myday

    records = myday.lifehack_records(include_disabled=False)
    total_pages = max(1, (len(records) + 4) // 5)
    page = max(0, min(int(page), total_pages - 1))
    chunk = records[page * 5:page * 5 + 5]
    msg = settings_ui.lifehacks_home(len(records), chunk, page, total_pages)
    rows = [
        [InlineKeyboardButton("🆕 Добавить", callback_data="set_lh_add")],
        [InlineKeyboardButton("❌ Удалить", callback_data=f"set_lh_delete_page_{page}")],
    ]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"set_lh_page_{(page - 1) % total_pages}"),
            InlineKeyboardButton("▶️", callback_data=f"set_lh_page_{(page + 1) % total_pages}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    markup = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


def _lifehack_selection_rows(records, prefix, page=0, total_pages=1, action="delete"):
    rows = []
    for item in records[:50]:
        record_id = str(item.get("id") or "")
        if not record_id:
            continue
        text = " ".join(str(item.get("text") or "").split())
        label = text[:42] + ("…" if len(text) > 42 else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}{record_id}")])
    if total_pages > 1:
        callback_prefix = "set_lh_edit_page_" if action == "edit" else "set_lh_delete_page_"
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"{callback_prefix}{(page - 1) % total_pages}"),
            InlineKeyboardButton("▶️", callback_data=f"{callback_prefix}{(page + 1) % total_pages}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="set_lifehacks"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return rows


async def send_lifehack_edit_list(bot, cid, q=None, page=0):
    import myday

    records = myday.lifehack_records(include_disabled=False)
    total_pages = max(1, (len(records) + 4) // 5)
    page = max(0, min(int(page), total_pages - 1))
    chunk = records[page * 5:page * 5 + 5]
    msg = settings_ui.lifehacks_list("✏️ Изменить", chunk, page, total_pages)
    markup = InlineKeyboardMarkup(_lifehack_selection_rows(chunk, "set_lh_edit_", page, total_pages, "edit"))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


async def send_lifehack_delete_list(bot, cid, q=None, page=0):
    import myday

    records = myday.lifehack_records(include_disabled=False)
    total_pages = max(1, (len(records) + 4) // 5)
    page = max(0, min(int(page), total_pages - 1))
    chunk = records[page * 5:page * 5 + 5]
    msg = settings_ui.lifehacks_list("❌ Удалить", chunk, page, total_pages)
    markup = InlineKeyboardMarkup(_lifehack_selection_rows(chunk, "set_lh_delete_", page, total_pages, "delete"))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, transient=True)


async def start_lifehack_add(bot, cid):
    store.pending_input[str(cid)] = "lifehack_add"
    await bot.send_message(chat_id=cid, text="Напиши текст лайфхака одним сообщением.")


async def start_lifehack_edit(bot, cid, record_id):
    import myday

    record = next((item for item in myday.lifehack_records() if item.get("id") == record_id), None)
    if record is None:
        await send_lifehacks(bot, cid)
        return
    store.pending_input[str(cid)] = f"lifehack_edit_{record_id}"
    msg = settings_ui.lifehack_edit_input(record.get("text", ""))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)


async def confirm_lifehack_delete(bot, cid, record_id):
    import myday

    record = next((item for item in myday.lifehack_records() if item.get("id") == record_id), None)
    if record is None:
        await send_lifehacks(bot, cid)
        return
    msg = settings_ui.lifehack_delete_confirm(record.get("text", ""))
    rows = [[
        InlineKeyboardButton("❌ Удалить", callback_data=f"set_lh_delete_yes_{record_id}"),
        InlineKeyboardButton("Отмена", callback_data="set_lifehacks"),
    ]]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def delete_lifehack(bot, cid, record_id):
    import myday

    myday.delete_lifehack(record_id)
    await send_lifehacks(bot, cid)


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


class _NotificationTrackingBot:
    """Count actual notification sends and failed delivery attempts."""

    _SEND_METHODS = {
        "send_message", "send_photo", "send_document", "send_animation",
        "send_audio", "send_video", "send_voice", "send_poll", "send_location",
        "send_media_group",
    }

    def __init__(self, bot, kind):
        self._bot = bot
        self._kind = kind
        self.failed = False

    def _record_failure(self, error):
        if self.failed:
            return
        self.failed = True
        import api_usage
        import tracking
        api_usage.record_request(
            "telegram", ok=False, units={"requests": 0, "failures": 1},
            error=type(error).__name__,
        )
        tracking.log_error(
            "broadcast", str(error), kind=f"notif:{self._kind}", exc=error,
            section="Уведомления", action="не отправлено уведомление",
            service="Telegram",
        )

    def __getattr__(self, name):
        original = getattr(self._bot, name)
        if name not in self._SEND_METHODS:
            return original

        async def tracked_send(*args, **kwargs):
            try:
                result = await original(*args, **kwargs)
            except Exception as error:
                self._record_failure(error)
                raise
            import api_usage
            amount = len(result) if name == "send_media_group" and isinstance(result, (list, tuple)) else 1
            api_usage.record_request(
                "telegram", ok=True, units={"requests": 0, "messages": amount},
            )
            return result

        return tracked_send


async def _send_scheduled_notification(bot, cid, kind):
    if kind == "weather_warn":
        import asyncio
        import weather as _w
        import weather_warn as _ww
        s = store.get_settings(cid)
        data = await asyncio.to_thread(_w.fetch_weather, s["lat"], s["lon"], 2)
        msg = _ww.build_warning(data, cid)
        # Тихий день без значимых погодных факторов — ничего не отправляем.
        if msg is not None:
            kb = notification_markup("weather_warn", [[
                InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
            ]])
            await bot.send_message(
                chat_id=cid,
                text=msg.text,
                entities=msg.entities,
                reply_markup=kb,
            )
    elif kind == "daily_words":
        kb = notification_markup("daily_words", [[
            InlineKeyboardButton("🧠 Обучение", callback_data="notify_learning"),
            InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        ]])
        await dictionary_morning.send_daily_practice(bot, cid, reply_markup=kb)
    elif kind == "weekend_events":
        import leisure_concerts
        await leisure_concerts.send_weekend_events(bot, cid)
    elif kind == "evening_weather":
        import weather as _w
        kb = notification_markup("evening_weather", [[
            InlineKeyboardButton(weather_ui.WEEK_FORECAST_BUTTON, callback_data="a_w_week"),
            InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        ]])
        await _w.send_weather(bot, cid, "tomorrow_plain", reply_markup=kb)


async def send_scheduled_notification(bot, cid, kind):
    """Send a scheduled notification and record only real sends/failures."""
    tracked_bot = _NotificationTrackingBot(bot, kind)
    try:
        return await _send_scheduled_notification(tracked_bot, cid, kind)
    except Exception as error:
        # Generation may fail before the first Telegram method is reached. It is
        # still one notification that the user did not receive.
        tracked_bot._record_failure(error)
        raise


async def _run_notif_test(bot, cid, kind) -> bool:
    """Предпросмотр уведомления: вызывает тот же код, что и плановое уведомление.
    Возвращает True/False — вызывающий сам решает, что показать администратору."""
    try:
        await send_scheduled_notification(bot, cid, kind)
        return True
    except Exception as e:
        _log.error("notif test failed for kind=%s: %r", kind, e, exc_info=True)
        import tracking
        tracking.log_error(
            "app", str(e), kind=f"notif_test:{kind}", exc=e,
            section="Мой день", action="не отправлено уведомление",
        )
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
    "weather_warn":    ("08:00, если есть повод", "Погодное предупреждение"),
    "weekend_events":  ("пт 10:00", "Ближайшие события"),
    "daily_words":     ("11:00", "Обучение языку"),
    "evening_weather": (EVENING_WEATHER_TIME, "Погода на завтра"),
}


def _time_sort_key(value: str) -> int:
    """Извлекает HH:MM из произвольного места строки (не только 'HH:MM' целиком) —
    time_label теперь может быть 'пт 10:00' или '08:00, если есть повод'."""
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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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


async def toggle_notification_from_message(cid, kind, q):
    """Меняет один тип рассылки, сохраняя полезное сообщение и его навигацию."""
    if kind not in dict(NOTIF_TYPES) or q is None:
        return
    enabled = not notif_on(cid, kind)
    set_(cid, f"notif_{kind}", enabled)
    current = getattr(getattr(q, "message", None), "reply_markup", None)
    rows = []
    for row in getattr(current, "inline_keyboard", []) or []:
        kept = [
            button for button in row
            if not str(getattr(button, "callback_data", "") or "").startswith("set_notifpush_")
        ]
        if kept:
            rows.append(kept)
    markup = notification_markup(kind, rows, enabled=enabled)
    try:
        await q.edit_message_reply_markup(reply_markup=markup)
    except Exception:
        try:
            await q.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            pass


async def notif_off_all(bot, cid, q=None):
    for kind, _ in NOTIF_TYPES:
        set_(cid, f"notif_{kind}", False)
    await send_notif(bot, cid, q)


async def send_personalization(bot, cid, q=None):
    """Безопасный редирект для уже отправленных кнопок старой персонализации."""
    rows = [
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
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


def _cuisines_kb(cid, back="as_fridge_home"):
    selected = set(cuisines(cid))
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "") + label,
            callback_data=f"set_cuisine_{key}",
        )
        for key, label in CUISINE_OPTIONS
    ]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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
            "минимализм": "Минимализм",
            "скандинавский": "Скандинавский",
            "скандинавский стиль": "Скандинавский",
            "smart casual": "Повседневный",
            "повседневный": "Повседневный",
            "streetwear": "Городской",
            "streetwear / городской": "Городской",
            "городской": "Городской",
            "классика": "Классический",
            "классический": "Классический",
            "спортивный": "Спортивный",
        }
        return [aliases.get(s, s) for s in cur if aliases.get(s, s) in STYLES]
    return []


def wardrobe_styles(cid):
    return _normalize_wardrobe_styles(get(cid, "style", []))


STYLE_LIMIT = 3


def _invalidate_wardrobe_recommendations(cid):
    """Следующий образ и совет по покупке должны учитывать новые параметры стиля."""
    store.clear_wardrobe_daylook(cid)
    store.clear_wardrobe_purchase_recommendation(cid)


async def set_style(bot, cid, i, q=None):
    if 0 <= i < len(STYLES):
        chosen = STYLES[i]
        selected = wardrobe_styles(cid)
        if chosen in selected:
            selected = [s for s in selected if s != chosen]
            set_(cid, "style", selected)
            _invalidate_wardrobe_recommendations(cid)
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
            _invalidate_wardrobe_recommendations(cid)
    await send_wardrobe_style(bot, cid, q=q)


async def set_fit(bot, cid, i, q=None):
    if 0 <= i < len(FIT_OPTIONS):
        set_(cid, "wardrobe_fit", FIT_OPTIONS[i])
        _invalidate_wardrobe_recommendations(cid)
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
    _invalidate_wardrobe_recommendations(cid)


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
    _invalidate_wardrobe_recommendations(cid)


def _multi_pick_kb(selected, options, prefix, back):
    buttons = [InlineKeyboardButton(("✅ " if v in selected else "") + v, callback_data=f"{prefix}_{i}")
               for i, v in enumerate(options)]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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
    emojis = {"Минимализм": "👕", "Скандинавский": "🧥", "Повседневный": "👖",
              "Городской": "🧢", "Классический": "👔", "Спортивный": "👟"}
    style_buttons = [InlineKeyboardButton(("✅ " if s in selected_styles else "") + f"{emojis[s]} {s}", callback_data=f"set_style_{i}")
                     for i, s in enumerate(STYLES)]
    rows = [[button] for button in style_buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="w_closet"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_wardrobe_style(bot, cid, q=None):
    """Стиль гардероба — один экран, все переключатели нажимаются сразу (стиль и
    посадка — toggle с галочкой на месте, без перехода на отдельный подэкран)."""
    state = _wardrobe_style_state(cid)
    msg = settings_ui.wardrobe_style(state["styles"], "", [], [])
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
    """Совместимость со старыми сообщениями: открываем актуальный экран стиля."""
    await send_wardrobe_style(bot, cid, q=q)


async def handle_callback(bot, cid, data, q=None):
    if data == "set_home":
        await send_home(bot, cid)
    elif data == "set_preferences":
        await send_preferences(bot, cid, q)
    elif data == "set_pref_learning":
        await learning_preferences.send_learning_settings(bot, cid, q=q, back="set_preferences")
    elif data == "set_pref_cuisines":
        await send_cuisines(bot, cid, q)
    elif data == "set_pref_style":
        await send_wardrobe_style(bot, cid, q)
    elif data == "set_pref_music":
        import leisure_music
        await leisure_music.send_music_preferences(bot, cid, q)
    elif data == "set_pref_movie":
        import leisure_movies
        await leisure_movies.send_movie_prefs(bot, cid, q)
    elif data == "set_pref_books":
        import leisure_books
        await leisure_books.send_book_preferences(bot, cid, q)
    elif data == "set_pref_games":
        import leisure_games
        await leisure_games.send_game_preferences(bot, cid, q)
    elif data.startswith("set_game_platform_"):
        import leisure_games
        await leisure_games.toggle_game_platform(
            bot, cid, data[len("set_game_platform_"):], q,
        )
    elif data.startswith("set_game_recency_"):
        import leisure_games
        await leisure_games.toggle_game_recency(
            bot, cid, data[len("set_game_recency_"):], q,
        )
    elif data.startswith("set_game_rating_"):
        import leisure_games
        await leisure_games.toggle_game_rating(
            bot, cid, data[len("set_game_rating_"):], q,
        )
    elif data == "set_lifehacks":
        await send_lifehacks(bot, cid, q)
    elif data.startswith("set_lh_page_"):
        await send_lifehack_page(bot, cid, data[len("set_lh_page_"):], q)
    elif data == "set_lh_add":
        await start_lifehack_add(bot, cid)
    elif data == "set_lh_edit":
        await send_lifehack_edit_list(bot, cid, q)
    elif data.startswith("set_lh_edit_page_"):
        await send_lifehack_edit_list(bot, cid, q, data[len("set_lh_edit_page_"):])
    elif data == "set_lh_delete":
        await send_lifehack_delete_list(bot, cid, q)
    elif data.startswith("set_lh_delete_page_"):
        await send_lifehack_delete_list(bot, cid, q, data[len("set_lh_delete_page_"):])
    elif data.startswith("set_lh_edit_"):
        await start_lifehack_edit(bot, cid, data[len("set_lh_edit_"):])
    elif data.startswith("set_lh_delete_yes_"):
        await delete_lifehack(bot, cid, data[len("set_lh_delete_yes_"):])
    elif data.startswith("set_lh_delete_"):
        await confirm_lifehack_delete(bot, cid, data[len("set_lh_delete_"):])
    elif data in {"set_mydata_leisure", "set_mydata_leisure_p", "set_mydata_cinema", "set_mydata_books", "set_mydata_music"}:
        # Кнопки из старых сообщений: общая страница «Досуг» больше не существует.
        await send_home(bot, cid)
    elif data == "set_food":
        import menu
        await menu.send_food_menu(bot, cid, q=q)
    elif data == "set_travel":
        import travel
        await travel.send_home(bot, cid, q=q)
    elif data == "set_fridge":
        import fridge
        await fridge.send_fridge(bot, cid, back="set_food")
    elif data == "set_fridge_g":
        import menu
        await menu.send_food_menu(bot, cid)
    elif data == "set_notif":
        await send_notif(bot, cid, q)
    elif data in (
        "set_refresh_data",
        "set_refresh_review", "set_refresh_review_apply",
        "set_refresh_review_delete", "set_refresh_review_skip",
    ):
        # Кнопки ручного обновления удалены. Старые сообщения безопасно
        # возвращают к актуальным настройкам и не запускают сетевую обработку.
        await send_home(bot, cid, q=q)
    elif data == "set_priorities":
        await send_personalization(bot, cid, q)
    elif data.startswith("set_prio_"):
        # Compat-редирект для старых сообщений: раздел "Приоритеты" стал "Персонализацией".
        await send_personalization(bot, cid, q)
    elif data == "set_wardrobe_settings":
        import wardrobe
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
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
    elif data.startswith("set_notifpush_"):
        await toggle_notification_from_message(cid, data[len("set_notifpush_"):], q)
    elif data == "set_notif_off_all":
        await notif_off_all(bot, cid, q)
    elif data == "set_learning_global":
        await learning_preferences.send_learning_settings(bot, cid, q=q, back="m_settings")
    elif data == "set_learning_mydata":
        await learning_preferences.send_learning_settings(bot, cid, q=q, back="set_priorities")
    elif (data == "set_learning" or data == "toggle_learning_language"
          or data.startswith("set_learning_language_") or data.startswith("set_learning_level_")):
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe_g"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])
        msg = settings_ui.wardrobe_item_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    elif data == "adm_home":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data in ("adm_system", "adm_api_ai"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "adm_refresh_cards":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_card_refresh_menu(b, c, q))
    elif data.startswith("adm_refresh_card_"):
        import admin as _adm
        card = data[len("adm_refresh_card_"):]
        await _admin_guard(bot, cid, lambda b, c, key=card: _adm.refresh_card(b, c, key, q))
    elif data == "adm_logs":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_logs(b, c, q))
    elif data == "adm_logs_clear":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.clear_logs(b, c, q))
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
    elif data in ("adm_welcome_preview", "adm_welcome_edit"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_welcome(b, c, q))


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
