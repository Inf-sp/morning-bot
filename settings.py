import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import learning
import util
from util import esc
from ui import settings as settings_ui
from ui.constants import cuisine_label, ui_label
import onboarding_status as obs

_log = logging.getLogger(__name__)


SETTINGS_KEY = "user_settings.json"
NOTIF_TYPES = [
    ("morning_brief",  "Утренний бриф"),
    ("weather_warn",   "Погодное предупреждение"),
    ("lagom_daily",    "Мотивация дня"),
    ("recipe_daily",   "Рецепт дня"),
    ("checkin_day",    "Дневная разгрузка"),
    ("evening_weather","Вечерняя погода"),
    ("weekly_events",  "Афиша недели"),
    ("favorite_artists","Новые концерты любимых артистов"),
    ("personal_news", "Новости для тебя"),
    ("weekly_forecast","Недельный прогноз"),
    ("daily_words_nl", "Нидерландский"),
    ("daily_words_en", "Английский"),
    ("live_lang",      "Живой язык"),
    ("checkin_eve",    "Вечерний разбор"),
]

PRIORITY_OPTIONS = [
    ("health", "Здоровье"),
    ("learning", "Учёба"),
    ("food", "Еда"),
    ("wardrobe", "Гардероб"),
    ("leisure", "Досуг"),
    ("quiet", "Минимум уведомлений"),
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
    "скандинавский стиль",
    "smart casual",
    "casual / повседневный",
    "классика",
    "streetwear / городской",
    "натуральный / бохо",
    "спортивный",
]

def _all():
    return store._load(SETTINGS_KEY)

def get(cid, key, default=None):
    return _all().get(str(cid), {}).get(key, default)

def set_(cid, key, value):
    d = _all()
    d.setdefault(str(cid), {})[key] = value
    store._save(SETTINGS_KEY, d)

def notif_on(cid, kind):
    value = get(cid, f"notif_{kind}", None)
    if value is None and kind in ("daily_words_nl", "daily_words_en"):
        legacy_kind = "grammar_nl" if kind.endswith("_nl") else "grammar_en"
        legacy_value = get(cid, f"notif_{legacy_kind}", None)
        return get(cid, "notif_grammar", False) if legacy_value is None else bool(legacy_value)
    return bool(value)

def study_lang(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return "нидерландский" if code == "nl" else "английский"
    legacy = get(cid, "study_lang", "нидерландский")
    code = "en" if legacy == "английский" else "nl"
    store.set_learning_language(cid, code)
    return "нидерландский" if code == "nl" else "английский"


def priorities(cid):
    saved = get(cid, "priorities", [])
    if not isinstance(saved, list):
        return []
    valid = {key for key, _ in PRIORITY_OPTIONS}
    return [key for key in saved if key in valid]


def priority_labels(cid):
    selected = set(priorities(cid))
    return [label for key, label in PRIORITY_OPTIONS if key in selected]


def priority_context(cid):
    labels = priority_labels(cid)
    if not labels:
        return ""
    return "Приоритеты пользователя: " + ", ".join(labels) + "."


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


def _notif_label(kind: str, label: str) -> str:
    if kind == "favorite_artists":
        return f"{label} (проверка по ВС, только если есть новое)"
    if kind in ("weekly_events", "weekly_forecast"):
        return f"{label} (1 раз в ВС в {'10:00' if kind == 'weekly_events' else '19:00'})"
    if kind in ("live_lang",):
        return f"{label} (ежедневно в 16:30)"
    if kind in ("daily_words_nl", "daily_words_en", "morning_brief", "weather_warn",
                "lagom_daily", "recipe_daily", "checkin_day", "evening_weather",
                "checkin_eve", "personal_news"):
        times = {
            "morning_brief": "08:30",
            "weather_warn": "08:45",
            "lagom_daily": "09:30",
            "recipe_daily": "12:30",
            "checkin_day": "14:00",
            "evening_weather": "21:30",
            "daily_words_nl": "11:00",
            "daily_words_en": "11:00",
            "checkin_eve": "22:00",
            "personal_news": "09:00",
        }
        return f"{label} (ежедневно в {times[kind]})"
    return label

async def send_home(bot, cid):
    await send_notes(bot, cid)


class _NoKbBot:
    """Обёртка для push-уведомлений: убирает кнопки, как в плановых рассылках."""
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
    """Отправить ровно то уведомление, которое уходит из плановой рассылки."""
    if kind == "morning_brief":
        import myday as _m
        # force=False: если пользователь уже открывал «Мой день» сегодня, рассылка
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
    elif kind == "lagom_daily":
        import balance as _b
        await _b.send_motiv_push(_NoKbBot(bot), cid)
    elif kind in ("daily_words_nl", "daily_words_en"):
        await learning.send_morning_word(bot, cid, language=study_lang(cid), with_kb=False)
    elif kind == "live_lang":
        await learning.send_proverb_both(bot, cid, with_kb=False)
    elif kind == "recipe_daily":
        import balance as _b
        await _b.send_recipe_push(_NoKbBot(bot), cid)
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
    elif kind == "weekly_forecast":
        import weather as _w
        await _w.send_weather(_NoKbBot(bot), cid, "week_plain")
    elif kind == "weekly_events":
        import leisure as _l
        await _l.send_weekly_events(_NoKbBot(bot), cid)
    elif kind == "favorite_artists":
        import leisure as _l
        await _l.send_new_concerts_notif(_NoKbBot(bot), cid)
    elif kind == "personal_news":
        import personal_news as _pn
        await _pn.send_scheduled(bot, cid)
    elif kind == "evening_weather":
        import weather as _w
        await _w.send_weather(_NoKbBot(bot), cid, "tomorrow_plain")


async def _run_notif_test(bot, cid, kind) -> bool:
    """Предпросмотр уведомления: вызывает тот же код, что и плановая рассылка.
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
    """Одно тестируемое уведомление для админ-панели: ключ + заголовок + расписание."""
    __slots__ = ("key", "title", "schedule_label", "time_label", "button_title", "button_label", "sort_key")

    def __init__(self, key: str, title: str, schedule_label: str, time_label: str = "",
                 button_title: str = "", sort_key: int = 9999):
        self.key = key
        self.title = title
        self.schedule_label = schedule_label
        self.time_label = time_label
        self.button_title = button_title or title
        self.button_label = f"{time_label} {self.button_title}".strip()
        self.sort_key = sort_key


_ADMIN_NOTIFICATION_META = {
    "morning_brief": ("08:30", "Мой день"),
    "weather_warn": ("08:45", "Погода"),
    "lagom_daily": ("09:30", "Мотивация"),
    "personal_news": ("09:00", "Новости"),
    "weekly_events": ("10:00", "Афиша"),
    "favorite_artists": ("10:05", "Концерты"),
    "daily_words_nl": ("11:00", "Слова NL"),
    "daily_words_en": ("11:00", "Слова EN"),
    "recipe_daily": ("12:30", "Еда"),
    "checkin_day": ("14:00", "Разгрузка"),
    "live_lang": ("16:30", "Живой язык"),
    "weekly_forecast": ("19:00", "Неделя"),
    "evening_weather": ("21:30", "Вечер"),
    "checkin_eve": ("22:00", "Разбор"),
}


def _time_sort_key(value: str) -> int:
    try:
        hh, mm = str(value).split(":", 1)
        return int(hh) * 60 + int(mm)
    except Exception:
        return 9999


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
    buttons = []
    for opt in get_notification_options():
        on = notif_on(cid, opt.key)
        mark = "✅" if on else "□"
        buttons.append(InlineKeyboardButton(f"{mark} {opt.button_label}", callback_data=f"set_notiftgl_{opt.key}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if any(notif_on(cid, kind) for kind, _ in NOTIF_TYPES):
        rows.append([InlineKeyboardButton("🔕 Отключить все", callback_data="set_notif_off_all")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    msg = settings_ui.notifications()
    text = msg.text
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=msg.entities, reply_markup=kb)

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


def _priorities_kb(cid):
    selected = set(priorities(cid))
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "") + label,
            callback_data=f"set_prio_{key}",
        )
        for key, label in PRIORITY_OPTIONS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_profile")])
    return InlineKeyboardMarkup(rows)


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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_food")])
    return InlineKeyboardMarkup(rows)


async def send_priorities(bot, cid, q=None):
    labels = priority_labels(cid)
    current = ", ".join(labels) if labels else "не выбраны"
    msg = settings_ui.priorities(current)
    text = msg.text
    kb = _priorities_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=msg.entities, reply_markup=kb)


async def toggle_priority(bot, cid, key, q=None):
    valid = {k for k, _ in PRIORITY_OPTIONS}
    if key not in valid:
        await send_priorities(bot, cid, q)
        return
    selected = priorities(cid)
    if key in selected:
        selected = [k for k in selected if k != key]
    else:
        selected.append(key)
    set_(cid, "priorities", selected)
    if key == "quiet" and key in selected:
        for kind, _ in NOTIF_TYPES:
            if kind not in ("morning_brief", "weather_warn"):
                set_(cid, f"notif_{kind}", False)
    await send_priorities(bot, cid, q)


async def send_cuisines(bot, cid, q=None):
    labels = cuisine_labels(cid)
    current = ", ".join(labels) if labels else "не выбраны"
    msg = settings_ui.cuisines(current)
    text = msg.text
    kb = _cuisines_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=msg.entities, reply_markup=kb)


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


_BODY_PLACEHOLDER = "не указано"

async def send_profile(bot, cid):
    rows = [
        [InlineKeyboardButton("🌍 Город", callback_data="set_city")],
        [InlineKeyboardButton(ui_label("priorities", "Приоритеты"), callback_data="set_priorities")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ]
    msg = settings_ui.profile()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def send_body(bot, cid, back="set_wardrobe_mydata"):
    """Особенности телосложения (используется в подборе образа в wardrobe.py)."""
    store.pending_input[str(cid)] = "wardrobe_profile_input"
    profile = get(cid, "wardrobe_profile", "")
    body = get(cid, "body", "")
    profile_line = esc(profile or body) if (profile or body) else "<i>не задано</i>"
    msg = settings_ui.body_profile(profile_line)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

async def send_style_pick(bot, cid):
    cur = get(cid, "style", "минимализм")
    rows = [[InlineKeyboardButton(("✅ " if cur == s else "") + s, callback_data=f"set_style_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("✏️ Описать своими словами", callback_data="set_stylecustom")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_body")])
    msg = settings_ui.style_pick()
    await bot.send_message(chat_id=cid,
        text=msg.text,
        entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

async def set_style(bot, cid, i):
    if 0 <= i < len(STYLES):
        set_(cid, "style", STYLES[i])
    await send_body(bot, cid)


# ===== Списки в настройках: страны, артисты, книги, шкаф =====
def _item_label(it):
    return it if isinstance(it, str) else (it.get("name") or it.get("word") or str(it))

def _list_kb(items, del_prefix, add_cb, back="set_home", clean_cb=None):
    rows = []
    if clean_cb and items:
        rows.append([
            InlineKeyboardButton("✏️ Добавить", callback_data=add_cb),
            InlineKeyboardButton("❌ Удалить", callback_data=clean_cb),
        ])
    else:
        rows.append([InlineKeyboardButton("✏️ Добавить", callback_data=add_cb)])
    rows.extend([[InlineKeyboardButton(_item_label(it)[:40], callback_data="noop")]
                 for it in items[-40:]])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(rows)

# --- Шкаф ---
async def send_wardrobe_hub(bot, cid, back="set_home"):
    """Гардероб в Настройках: вещи + стиль + особенности телосложения - всё, что
    реально использует wardrobe.py при подборе образа, в одном месте."""
    rows = [
        [InlineKeyboardButton(ui_label("clothes", "Вещи"), callback_data="set_wardrobe")],
        [InlineKeyboardButton(ui_label("clothing_style", "Стиль"), callback_data="set_stylepick")],
        [InlineKeyboardButton("Особенности телосложения", callback_data="set_body")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('wardrobe', 'Гардероб')}",
        "Влияет на подбор образа и разбор шкафа.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_wardrobe(bot, cid, back="set_wardrobe_mydata"):
    if store.pending_input.get(str(cid)) == "wardrobe_profile_input":
        store.pending_input.pop(str(cid), None)
    rows = [[
        InlineKeyboardButton("✏️ Добавить", callback_data="set_ward_add"),
        InlineKeyboardButton("❌ Удалить", callback_data="set_ward_del"),
    ]]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    kb = InlineKeyboardMarkup(rows)
    msg = settings_ui.wardrobe_home()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

# --- Страны ---
async def send_countries(bot, cid):
    from util import country_flag
    items = store.get_list(config.COUNTRIES_KEY, cid)
    rows = []
    if items:
        rows.append([
            InlineKeyboardButton("✏️ Добавить", callback_data="setadd_country"),
            InlineKeyboardButton("❌ Удалить", callback_data="set_clean_countries"),
        ])
    else:
        rows.append([InlineKeyboardButton("✏️ Добавить", callback_data="setadd_country")])
    rows.extend([[InlineKeyboardButton(f"{country_flag(it)} {_item_label(it)[:36]}", callback_data="noop")]
                 for it in items[-40:]])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    msg = settings_ui.countries_home()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

# --- Артисты ---
async def send_artists(bot, cid):
    items = store.get_list(config.ARTISTS_KEY, cid)
    msg = settings_ui.artists_home(items)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=_list_kb(items, "setdel_artist_", "setadd_artist",
                                                 clean_cb="set_clean_artists"))

# --- Книги ---

async def send_lagom(bot, cid, back="m_notes"):
    import memory
    items = memory.get_lagom(cid)
    rows = []
    if items:
        rows.append([
            InlineKeyboardButton("✏️ Добавить", callback_data="setadd_lagom"),
            InlineKeyboardButton("❌ Удалить", callback_data="set_lagom_clean"),
        ])
    else:
        rows.append([InlineKeyboardButton("✏️ Добавить", callback_data="setadd_lagom")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    msg = settings_ui.lagom_home(items)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_books(bot, cid):
    items = store.get_list(config.BOOKS_KEY, cid)
    msg = settings_ui.books_home(items)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=_list_kb(items, "setdel_book_", "setadd_book",
                                                 clean_cb="set_clean_books"))

async def list_delete(bot, cid, kind, i):
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    key = keymap.get(kind)
    items = store.get_list(key, cid)
    if i < len(items):
        items.pop(i)
        store.set_list(key, cid, items)
    if kind == "country":
        await send_countries(bot, cid)
    elif kind == "artist":
        await send_artists(bot, cid)
    else:
        await send_books(bot, cid)

async def list_add_done(bot, cid, kind, text):
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    item = text.strip()
    store.add_to_list(keymap[kind], cid, item)
    msg = settings_ui.list_added(kind, item)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if kind == "country":
        await send_countries(bot, cid)
    elif kind == "artist":
        await send_artists(bot, cid)
    else:
        await send_books(bot, cid)


async def handle_callback(bot, cid, data, q=None):
    if data == "set_home":
        await send_home(bot, cid)
    elif data == "set_profile":
        await send_profile(bot, cid)
    elif data == "set_mydata_leisure":
        await send_mydata_leisure(bot, cid)
    elif data == "set_mydata_cinema":
        await send_mydata_cinema(bot, cid)
    elif data == "set_mydata_books":
        await send_mydata_books(bot, cid)
    elif data == "set_mydata_music":
        await send_mydata_music(bot, cid)
    elif data == "set_food":
        await send_food(bot, cid, q)
    elif data == "set_travel":
        await send_travel(bot, cid)
    elif data == "set_health":
        await send_lagom(bot, cid, back="set_home")
    elif data == "set_dict":
        await learning.send_dict(bot, cid, back="m_notes")
    elif data == "set_dict_g":
        await learning.send_dict(bot, cid, back="m_learn")
    elif data == "set_leisure_settings":
        await send_leisure_settings(bot, cid)
    elif data == "set_fridge":
        import balance
        await balance.send_fridge(bot, cid, back="m_notes")
    elif data == "set_fridge_g":
        await send_food(bot, cid, back="m_food")
    elif data == "set_notif":
        await send_notif(bot, cid, q)
    elif data == "set_priorities":
        await send_priorities(bot, cid, q)
    elif data.startswith("set_prio_"):
        await toggle_priority(bot, cid, data[len("set_prio_"):], q)
    elif data == "set_cuisines":
        await send_cuisines(bot, cid, q)
    elif data.startswith("set_cuisine_"):
        await toggle_cuisine(bot, cid, data[len("set_cuisine_"):], q)
    elif data.startswith("set_notiftgl_"):
        await toggle_notif(bot, cid, data[len("set_notiftgl_"):], q)
    elif data == "set_notif_off_all":
        await notif_off_all(bot, cid, q)
    elif data == "set_levels":
        await learning.send_learning_settings(bot, cid, q=q, back="set_home")
    elif data == "set_learning_hub":
        await send_learning_hub(bot, cid)
    elif data == "set_learning_mydata":
        await learning.send_learning_settings(bot, cid, q=q, back="set_learning_hub")
    elif data == "set_learning" or data == "toggle_learning_language" or data.startswith("set_learning_level_"):
        await learning.handle_learning_settings_callback(bot, cid, q, data)
    elif data == "set_city":
        store.pending_input[cid] = "setcity"
        msg = settings_ui.city_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "set_body":
        await send_body(bot, cid, back="set_wardrobe_mydata")
    elif data == "set_wardrobe":
        await send_wardrobe(bot, cid, back="set_wardrobe_mydata")
    elif data == "set_wardrobe_mydata":
        await send_wardrobe_hub(bot, cid, back="set_home")
    elif data == "set_wardrobe_g":
        await send_wardrobe_hub(bot, cid, back="m_wardrobe")
    elif data == "set_ward_add":
        store.pending_input[cid] = "wardrobe_add_set"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe")]])
        msg = settings_ui.wardrobe_item_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    elif data == "set_ward_del":
        import wardrobe
        await wardrobe.send_del_zones(bot, cid, origin="s")
    elif data == "set_lagom":
        await send_lagom(bot, cid, back="set_home")
    elif data == "setadd_lagom":
        store.pending_input[cid] = "setadd_lagom"
        msg = settings_ui.lagom_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "set_lagom_clean":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "lagom")
    elif data == "set_countries":
        _log.info("legacy callback used: %s", data)
        await send_love_section(bot, cid, "countries")
    elif data == "set_artists":
        _log.info("legacy callback used: %s", data)
        await send_love_section(bot, cid, "artists")
    elif data == "set_books":
        _log.info("legacy callback used: %s", data)
        await send_love_section(bot, cid, "books")
    elif data == "setadd_country":
        store.pending_input[cid] = "setadd_country"
        msg = settings_ui.list_add_prompt("country")
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "setadd_artist":
        store.pending_input[cid] = "setadd_artist"
        msg = settings_ui.list_add_prompt("artist")
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "setadd_book":
        store.pending_input[cid] = "setadd_book"
        msg = settings_ui.list_add_prompt("book")
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "set_clean_countries":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "cfg_countries")
    elif data == "set_clean_artists":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "cfg_artists")
    elif data == "set_clean_books":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "cfg_books")
    elif data.startswith("setdel_country_"):
        await list_delete(bot, cid, "country", int(data.split("_")[-1]))
    elif data.startswith("setdel_artist_"):
        await list_delete(bot, cid, "artist", int(data.split("_")[-1]))
    elif data.startswith("setdel_book_"):
        await list_delete(bot, cid, "book", int(data.split("_")[-1]))
    elif data == "set_stylepick":
        await send_style_pick(bot, cid)
    elif data == "set_stylecustom":
        store.pending_input[cid] = "styleinput"
        msg = settings_ui.style_custom_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data.startswith("set_style_"):
        await set_style(bot, cid, int(data.split("_")[-1]))
    elif data == "set_bodyinput":
        store.pending_input[cid] = "bodyinput"
        msg = settings_ui.body_input()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    elif data == "adm_home":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "adm_check_all":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.check_system(b, c, q))
    elif data == "adm_system":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_system(b, c, q))
    elif data == "adm_system_check":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.check_system(b, c, q))
    elif data in ("adm_diag", "adm_diag_api", "adm_diag_llm", "adm_diag_news", "adm_api_ai"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "adm_api_ai_check":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.check_api_ai(b, c, q))
    elif data == "adm_logs":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_logs(b, c, q))
    elif data == "adm_notif":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_notifications(b, c, q))
    elif data == "adm_notif_check":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.check_notifications(b, c, q))
    elif data == "adm_users":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_users(b, c, q))
    elif data == "adm_invite":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_invite(b, c, q))
    elif data == "adm_invite_create":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.create_invite(b, c, q))
    elif data in ("adm_welcome", "adm_welcome_preview", "adm_welcome_edit"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_welcome(b, c, q))
    elif data == "adm_tests":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_notifications(b, c, q))
    elif data.startswith("adm_test_"):
        kind = data[len("adm_test_"):]
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c, kind=kind: _adm.run_test(b, c, kind))
    elif data == "set_admin":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_home(b, c, q))
    elif data == "set_admin_users":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_users(b, c, q))
    elif data in ("set_admin_llm", "set_admin_news", "set_admin_llmcheck", "set_admin_llmhistory"):
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "set_admin_broadcast":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_notifications(b, c, q))
    elif data == "set_admin_broadcast_test_pick":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_notifications(b, c, q))
    elif data.startswith("set_admin_broadcast_test_"):
        kind = data[len("set_admin_broadcast_test_"):]
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c, kind=kind: _adm.run_test(b, c, kind))
    elif data == "set_admin_issues":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_logs(b, c, q))
    elif data == "set_admin_check_all":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.check_system(b, c, q))
    elif data == "set_admin_api_diagnostics":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_api_ai(b, c, q))
    elif data == "set_admin_cache_clear":
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.clear_cache(b, c, q))
    elif data.startswith("set_admin_issue_"):
        key = data[len("set_admin_issue_"):]
        import admin as _adm
        await _admin_guard(bot, cid, lambda b, c: _adm.send_logs(b, c, q))
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


# ===== СОХРАНЕНИЯ / ЛЮБИМЫЕ (notes.py) =====

async def save_fav(bot, cid, q=None):
    # Берём оригинальный текст сообщения прямо из callback — entities уже структурированы
    # Telegram-ом (Message.entities/caption_entities), без похода через HTML-строку.
    txt, txt_entities = "", []
    if q is not None and q.message:
        txt = q.message.text or q.message.caption or ""
        txt_entities = list(q.message.entities or q.message.caption_entities or [])
    if not txt:
        txt = store.last_answer.get(str(cid), "")
        txt_entities = []
    if not txt:
        msg = settings_ui.nothing_to_save()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities); return
    source = store.last_source.get(str(cid), "Прочее")
    store.add_to_list(config.NOTES_KEY, cid, {
        "date": datetime.now(config.TZ).strftime("%d.%m"),
        "text": txt, "entities": util.entities_to_json(txt_entities),
        "source": source, "bucket": "fav",
    })
    msg = settings_ui.saved_to_later()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

def _note_type(source):
    s = (source or "").lower()
    if "фильм" in s or "сериал" in s or "кино" in s:
        return ("movie", config.MOVIE_BLACKLIST_KEY, config.WATCHLIST_KEY, "Кино")
    if "книг" in s:
        return ("book", config.BOOK_BLACKLIST_KEY, config.BOOKS_KEY, "Книги")
    if "музык" in s or "концерт" in s:
        return ("music", config.MUSIC_DISLIKE_KEY, config.ARTISTS_KEY, "Артисты")
    if "путешеств" in s or "стран" in s:
        return ("travel", config.TRAVEL_DISLIKE_KEY, config.FAVCOUNTRIES_KEY, "Страны")
    return (None, None, None, None)

def _note_bucket(n):
    return n.get("bucket", "fav") if isinstance(n, dict) else "fav"

def _fav_group(source: str) -> str:
    s = (source or "").lower()
    if "фильм" in s or "сериал" in s or "кино" in s:
        return "movies"
    if "книг" in s:
        return "books"
    if "музык" in s or "концерт" in s:
        return "music"
    if "путешеств" in s or "стран" in s:
        return "travel"
    if "гардероб" in s or "образ" in s or "покупк" in s:
        return "wardrobe"
    if "питан" in s or "рецепт" in s or "ед" in s or "холодиль" in s:
        return "food"
    if "здоров" in s or "мотивац" in s or "врач" in s or "тревог" in s or "баланс" in s:
        return "health"
    return "other"

def _fav_group_meta():
    return [
        ("movies", ui_label("cinema", "Кино"), "фильмы и сериалы"),
        ("books", ui_label("books", "Книги"), "книги и списки к прочтению"),
        ("music", ui_label("music", "Музыка"), "музыка, артисты и концерты"),
        ("travel", ui_label("travel", "Поездки"), "страны и поездки"),
        ("food", ui_label("recipes", "Еда"), "рецепты и питание"),
        ("wardrobe", ui_label("wardrobe", "Гардероб"), "образы и покупки"),
        ("health", ui_label("health", "Здоровье"), "здоровье и мотивация"),
        ("other", "Прочее", "всё, что не попало в отдельную категорию"),
    ]

def _fav_group_info(key: str):
    for group_key, label, desc in _fav_group_meta():
        if group_key == key:
            return label, desc
    return "Прочее", "всё, что не попало в отдельную категорию"

def _pop_note(cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list):
        return None
    n = notes_list.pop(i)
    store.set_list(config.NOTES_KEY, cid, notes_list)
    return n

def _note_text(n):
    return (n.get("text", "") if isinstance(n, dict) else str(n)).strip()

async def note_to_blacklist(bot, cid, i):
    n = _pop_note(cid, i)
    if not n:
        await send_notes(bot, cid); return
    typ, black_key, _, cat = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    t = _note_text(n)
    if black_key:
        store.add_to_list(black_key, cid, t)
        msg = settings_ui.note_blacklisted(t, cat)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        msg = settings_ui.note_removed_from_later()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await send_bucket(bot, cid, "fav")

async def note_to_love(bot, cid, i):
    n = _pop_note(cid, i)
    if not n:
        await send_notes(bot, cid); return
    typ, _, fav_key, cat = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    t = _note_text(n)
    if fav_key:
        if typ == "travel":
            from util import country_flag
            store.add_to_list(fav_key, cid, {"name": t, "flag": country_flag(t)})
        else:
            store.add_to_list(fav_key, cid, t)
        msg = settings_ui.note_moved_to_favorites(t, cat)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        msg = settings_ui.note_removed_from_later()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await send_bucket(bot, cid, "fav")

async def note_drop(bot, cid, i):
    n = _pop_note(cid, i)
    bucket = _note_bucket(n) if n else "fav"
    msg = settings_ui.note_deleted()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await send_bucket(bot, cid, bucket)

async def export_notes(bot, cid):
    import io, re as _re2
    _plain = lambda s: _re2.sub(r"<[^>]+>", "", s).strip()
    lines = ["Мои сохранения (DM)", ""]

    notes_list = store.get_list(config.NOTES_KEY, cid)
    fav = [n for n in notes_list if _note_bucket(n) == "fav"]
    lines.append("⏳ ВРЕМЕННЫЕ ЗАКЛАДКИ")
    if fav:
        for n in fav:
            t = _plain(n.get("text", "") if isinstance(n, dict) else str(n))
            d = n.get("date", "") if isinstance(n, dict) else ""
            src_full = n.get("source", "") if isinstance(n, dict) else ""
            src = src_full.split(" · ", 1)[1] if " · " in src_full else src_full
            tag = f" [{src}]" if src and src != "Прочее" else ""
            lines.append(f"- [{d}]{tag} {t}")
    else:
        lines.append("- пусто")
    lines.append("")

    plans = [n for n in notes_list if _note_bucket(n) == "plan"]
    lines.append(f"{ui_label('travel', '')} ПЛАНЫ ПОЕЗДОК")
    if plans:
        for n in plans:
            d = n.get("date", "") if isinstance(n, dict) else ""
            country = (n.get("country") or "") if isinstance(n, dict) else ""
            lines.append(f"- [{d}] {country}")
    else:
        lines.append("- пусто")
    lines.append("")

    lines.append("❤️ ЛЮБИМЫЕ")
    sections = [
        ("Мои страны", store.get_list(config.COUNTRIES_KEY, cid)),
        ("Мои музыканты", store.get_list(config.ARTISTS_KEY, cid)),
        ("Мои книги", store.get_list(config.BOOKS_KEY, cid)),
    ]
    any_love = False
    for name, items in sections:
        names = [i if isinstance(i, str) else i.get("name", "") for i in items]
        names = [x for x in names if x]
        if names:
            any_love = True
            lines.append(f"  {name}:")
            for x in names:
                lines.append(f"  - {x}")
    if not any_love:
        lines.append("- пусто")
    lines.append("")

    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buf.name = "moi_sohraneniya.txt"
    await bot.send_document(chat_id=cid, document=buf, filename="moi_sohraneniya.txt",
                            caption="📤 Готово. Текст можно сохранить на ваше устройство.")

async def send_notes(bot, cid):
    rows = [
        [InlineKeyboardButton(ui_label("profile", "Профиль"), callback_data="set_profile"),
         InlineKeyboardButton(ui_label("broadcasts", "Рассылки"), callback_data="set_notif")],
        [InlineKeyboardButton(ui_label("wardrobe", "Гардероб"), callback_data="set_wardrobe_mydata"),
         InlineKeyboardButton(ui_label("food", "Готовка"), callback_data="set_food")],
        [InlineKeyboardButton(ui_label("learning", "Обучение"), callback_data="set_learning_hub"),
         InlineKeyboardButton(ui_label("health", "Здоровье"), callback_data="set_health")],
        [InlineKeyboardButton(ui_label("travel", "Путешествия"), callback_data="set_travel"),
         InlineKeyboardButton(ui_label("leisure", "Досуг"), callback_data="set_mydata_leisure")],
        [InlineKeyboardButton("📤 Экспорт данных", callback_data="as_export")],
    ]
    msg = settings_ui.settings_home()
    await bot.send_message(chat_id=cid, entities=msg.entities,
        text=msg.text,
        reply_markup=InlineKeyboardMarkup(rows))


async def send_mydata_leisure(bot, cid):
    rows = [
        [InlineKeyboardButton(ui_label("cinema", "Кино"), callback_data="set_mydata_cinema")],
        [InlineKeyboardButton(ui_label("books", "Книги"), callback_data="set_mydata_books")],
        [InlineKeyboardButton(ui_label("music", "Музыка"), callback_data="set_mydata_music")],
        [InlineKeyboardButton(ui_label("concerts", "Концерты"), callback_data="a_concerts_find")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('leisure', 'Досуг')}",
        "Наполни любимое — рекомендации станут точнее.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_learning_hub(bot, cid):
    """Обучение в Настройках: язык/уровень + единственная кнопка на словарь
    (было две дублирующих - "Словарь" и "Фразы" - обе вели на один экран)."""
    rows = [
        [InlineKeyboardButton(ui_label("dictionary", "Словарь"), callback_data="a_dict_mydata")],
        [InlineKeyboardButton("Язык и уровень", callback_data="set_learning_mydata")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('learning', 'Обучение')}",
        "Слова из словаря сами попадают в тренажёры.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_mydata_cinema(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимое", callback_data="colr:cinema_favorites:set_mydata_leisure")],
        [InlineKeyboardButton("Сохранённое", callback_data="colr:cinema_saved:set_mydata_leisure")],
        [InlineKeyboardButton("Смотрел", callback_data="colr:cinema_watched:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытое", callback_data="colr:cinema_hidden:set_mydata_leisure")],
        [InlineKeyboardButton("Предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('cinema', 'Кино')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_mydata_books(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимое", callback_data="colr:books_favorites:set_mydata_leisure")],
        [InlineKeyboardButton("Сохранённое", callback_data="colr:books_saved:set_mydata_leisure")],
        [InlineKeyboardButton("Прочитано", callback_data="colr:books_read:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытое", callback_data="colr:books_hidden:set_mydata_leisure")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('books', 'Книги')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_mydata_music(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимые артисты", callback_data="colr:music_favorite_artists:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытые артисты", callback_data="colr:music_hidden_artists:set_mydata_leisure")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('music', 'Музыка')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_food(bot, cid, q=None, back="set_home"):
    cuisine_mark = " ✅" if cuisines(cid) else ""
    rows = [
        [InlineKeyboardButton(ui_label("products", "Продукты"), callback_data="colr:fridge_items:set_food")],
        [InlineKeyboardButton(ui_label("recipes", "Рецепты"), callback_data="colr:recipes_saved:set_food")],
        [InlineKeyboardButton(f"{ui_label('cuisines', 'Кухни')}{cuisine_mark}", callback_data="set_cuisines")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('food', 'Готовка')}",
        "Кухни влияют на рецепт дня и подбор из холодильника.",
    )
    text, entities = msg.text, msg.entities
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)


async def send_travel(bot, cid):
    rows = [
        [InlineKeyboardButton(ui_label("countries", "Любимые страны"), callback_data="colr:travel_favorite_countries:set_travel")],
        [InlineKeyboardButton(ui_label("routes", "Сохранённые места"), callback_data="colr:travel_saved_places:set_travel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('travel', 'Путешествия')}",
        "Страны — для идей поездок. Места — то, что уже сохранил.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_leisure_settings(bot, cid):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton(ui_label("cinema", "Предпочтения кино"), callback_data="movie_prefs")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    msg = settings_ui.leisure_settings()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows),
    )

async def send_plans(bot, cid):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "plan"]
    if not items:
        msg = settings_ui.trips_empty()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav")]]))
        return
    rows = []
    for i, n in items:
        country = (n.get("country") or "Поездка") if isinstance(n, dict) else "Поездка"
        d = n.get("date", "") if isinstance(n, dict) else ""
        rows.append([InlineKeyboardButton(f"{ui_label('travel', '').strip()} {d} · {country}"[:40], callback_data=f"as_planview_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav")])
    msg = settings_ui.trips_home()
    await bot.send_message(chat_id=cid, entities=msg.entities,
        text=msg.text,
        reply_markup=InlineKeyboardMarkup(rows))

async def plan_view(bot, cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "plan":
        await send_plans(bot, cid); return
    n = notes_list[i]
    text = n.get("text", "") if isinstance(n, dict) else str(n)
    entities = util.entities_from_json(n.get("entities") if isinstance(n, dict) else None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить план", callback_data=f"as_plandel_{i}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_plan")],
    ])
    chunks = util.chunk_text_with_entities(text, entities, 4000)
    for idx, (chunk_text, chunk_entities) in enumerate(chunks):
        markup = kb if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=chunk_text, entities=chunk_entities, reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk_text, reply_markup=markup)

async def fav_view(bot, cid, i, back="as_bucket_fav", delete_cb=None):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "fav":
        await send_bucket(bot, cid, "fav"); return
    n = notes_list[i]
    text = (n.get("text", "") if isinstance(n, dict) else str(n)).rstrip()
    body_entities = util.entities_from_json(n.get("entities") if isinstance(n, dict) else None)
    src = n.get("source", "") if isinstance(n, dict) else ""
    d = n.get("date", "") if isinstance(n, dict) else ""
    full = settings_ui.favorite_card(src, d, text, body_entities)
    typ, _, _, _ = _note_type(src)
    if typ:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❤️ В любимые", callback_data=f"as_notelove_{i}"),
             InlineKeyboardButton("Скрыть", callback_data=f"as_noteblack_{i}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
        ])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Удалить", callback_data=delete_cb or f"fav_del_{i}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
        ])
    chunks = util.chunk_text_with_entities(full.text, full.entities, 4000)
    for idx, (chunk_text, chunk_entities) in enumerate(chunks):
        markup = kb if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=chunk_text, entities=chunk_entities, reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk_text, reply_markup=markup)


async def fav_del(bot, cid, i):
    _pop_note(cid, i)
    await send_bucket(bot, cid, "fav")


async def fav_del_group(bot, cid, group, i):
    _pop_note(cid, i)
    await send_fav_group(bot, cid, group)


async def send_fav_group(bot, cid, group):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = []
    for i, n in enumerate(notes_list):
        if _note_bucket(n) != "fav":
            continue
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        if _fav_group(src) == group:
            items.append((i, n))

    label, desc = _fav_group_info(group)
    msg = settings_ui.later_group(label, desc)
    rows = []
    import re as _re
    _strip_html = lambda s: _re.sub(r"<[^>]+>", "", s).strip()
    for i, n in items:
        src = (n.get("source", "Прочее") if isinstance(n, dict) else "Прочее") or "Прочее"
        date = (n.get("date", "") if isinstance(n, dict) else "") or ""
        raw = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
        preview = _strip_html(raw)
        short = preview[:34] + ("…" if len(preview) > 34 else "")
        prefix = f"{date} · " if date else ""
        rows.append([InlineKeyboardButton(f"{prefix}{src} · {short}"[:60], callback_data=f"fav_viewg_{group}_{i}")])
    if items:
        rows.append([InlineKeyboardButton("Убрать из сохранённого", callback_data=f"as_clean_favgrp_{group}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_bucket(bot, cid, bucket):
    if bucket == "love":
        await send_love_home(bot, cid); return
    if bucket == "plan":
        await send_plans(bot, cid); return
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "fav"]
    count = len(items)
    if not count:
        msg = settings_ui.later_home_empty()
        rows = [
            [InlineKeyboardButton(ui_label("travel", "Мои поездки"), callback_data="as_bucket_plan")],
            [InlineKeyboardButton(ui_label("cinema", "Кино"), callback_data="as_bucket_favgrp_movies"),
             InlineKeyboardButton(ui_label("books", "Книги"), callback_data="as_bucket_favgrp_books")],
            [InlineKeyboardButton(ui_label("music", "Музыка"), callback_data="as_bucket_favgrp_music"),
             InlineKeyboardButton(ui_label("travel", "Поездки"), callback_data="as_bucket_favgrp_travel")],
            [InlineKeyboardButton(ui_label("recipes", "Еда"), callback_data="as_bucket_favgrp_food"),
             InlineKeyboardButton(ui_label("wardrobe", "Гардероб"), callback_data="as_bucket_favgrp_wardrobe")],
            [InlineKeyboardButton(ui_label("health", "Здоровье"), callback_data="as_bucket_favgrp_health"),
             InlineKeyboardButton("Прочее", callback_data="as_bucket_favgrp_other")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")],
        ]
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                               reply_markup=InlineKeyboardMarkup(rows)); return
    groups = {key: [] for key, _, _ in _fav_group_meta()}
    for idx, n in items:
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        groups[_fav_group(src)].append((idx, n))

    msg = settings_ui.later_home()
    rows = []
    for key, label, desc in _fav_group_meta():
        if groups.get(key):
            rows.append([InlineKeyboardButton(f"{label} ({len(groups[key])})", callback_data=f"as_bucket_favgrp_{key}")])
    rows.append([InlineKeyboardButton(ui_label("travel", "Мои поездки"), callback_data="as_bucket_plan")])
    rows.append([InlineKeyboardButton("Убрать из сохранённого", callback_data="as_clean_fav")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))


LOVE_SECTIONS = [
    (ui_label("cinema", "Кино"), "movies"),
    (ui_label("countries", "Мои страны"), "countries"),
    (ui_label("music", "Мои музыканты"), "artists"),
    (ui_label("books", "Мои книги"), "books"),
]

async def send_love_home(bot, cid, back="m_notes"):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    msg = settings_ui.favorites_home()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

def _love_items(cid, key):
    if key == "movies":
        return list(store.get_list(config.WATCHLIST_KEY, cid))
    if key == "countries":
        cur = store.get_list(config.FAVCOUNTRIES_KEY, cid)
        return [c if isinstance(c, str) else c.get("name", "") for c in cur]
    if key == "artists":
        return list(store.get_list(config.ARTISTS_KEY, cid))
    if key == "books":
        return list(store.get_list(config.BOOKS_KEY, cid))
    return []

def _love_title(key):
    return {
        "movies": ui_label("cinema", "Мое кино"),
        "countries": ui_label("countries", "Мои страны"),
        "artists": ui_label("music", "Мои музыканты"),
        "books": ui_label("books", "Мои книги"),
    }.get(key, "Любимые")

_HIDDEN_SUPPORTED = {"movies", "books", "artists", "countries"}

async def send_love_section(bot, cid, key):
    if key == "recipes":
        import balance
        await balance.send_my_recipes(bot, cid)
        return
    items = _love_items(cid, key)
    title = _love_title(key)
    msg = settings_ui.favorite_section(title, items)
    rows = []
    if items:
        rows.append([
            InlineKeyboardButton("✏️ Добавить", callback_data=f"as_loveadd_{key}"),
            InlineKeyboardButton("Убрать из любимого", callback_data=f"as_loveclean_{key}"),
        ])
    else:
        rows.append([InlineKeyboardButton("✏️ Добавить", callback_data=f"as_loveadd_{key}")])
    if key in _HIDDEN_SUPPORTED:
        rows.append([InlineKeyboardButton("Скрытое", callback_data=f"as_lovehidden_{key}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

def _love_key_of(key):
    return {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
            "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)

async def love_add_start(bot, cid, key, origin="base"):
    prefix = "loveaddls" if origin == "leisure" else "loveadd"
    store.pending_input[str(cid)] = f"{prefix}_{key}"
    name = {"movies": "фильм или сериал", "countries": "страну",
            "artists": "артиста", "books": "книгу"}.get(key, "элемент")
    msg = settings_ui.favorite_add_prompt(name)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

async def love_add_done(bot, cid, key, text, origin="base"):
    store_key = _love_key_of(key)
    if store_key and key == "countries":
        from util import country_flag
        name = text.strip()
        store.add_to_list(store_key, cid, {"name": name, "flag": country_flag(name)})
    elif store_key:
        store.add_to_list(store_key, cid, text.strip())
    import cleanup as _cl
    msg = settings_ui.favorite_added()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    ctx_prefix = "lvls" if origin == "leisure" else "lv"
    await _cl.open_cleanup(bot, cid, f"{ctx_prefix}_{key}",
                           back="m_leisure_settings" if origin == "leisure" else "as_notes")


async def handle_notes_callback(bot, cid, q, data):
    """Роутер для callback'ов закладок/любимого (as_* и fav_*)."""
    if data == "as_fav":
        await save_fav(bot, cid, q); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_bucket_fav":
        await send_bucket(bot, cid, "fav"); return
    if data.startswith("as_bucket_favgrp_"):
        await send_fav_group(bot, cid, data[len("as_bucket_favgrp_"):]); return
    if data.startswith("as_clean_favgrp_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"nb_{data[len('as_clean_favgrp_'):]}")
        return
    if data == "as_bucket_plan":
        await send_bucket(bot, cid, "plan"); return
    if data == "as_bucket_love":
        await send_notes(bot, cid); return
    if data.startswith("as_planview_"):
        await plan_view(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_plandel_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
    if data == "as_export":
        await export_notes(bot, cid); return
    if data.startswith("as_noteblack_"):
        await note_to_blacklist(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notelove_"):
        await note_to_love(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notedrop_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_view_"):
        await fav_view(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_viewg_"):
        group, idx = data[len("fav_viewg_"):].rsplit("_", 1)
        await fav_view(bot, cid, int(idx), back=f"as_bucket_favgrp_{group}", delete_cb=f"fav_delg_{group}_{idx}")
        return
    if data.startswith("fav_del_"):
        await fav_del(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_delg_"):
        group, idx = data[len("fav_delg_"):].rsplit("_", 1)
        await fav_del_group(bot, cid, group, int(idx))
        return
    if data == "as_clean_fav":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "nb"); return
    if data.startswith("ls_loveclean_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"lvls_{data[len('ls_loveclean_'):]}", back="m_leisure_settings"); return
    if data.startswith("ls_loveadd_"):
        await love_add_start(bot, cid, data[len("ls_loveadd_"):], origin="leisure"); return
    if data.startswith("ls_love_"):
        key = data[len("ls_love_"):]
        import cleanup as _cl
        await _cl.open_cleanup(bot, cid, f"lvls_{key}", back="m_leisure_settings"); return
    if data.startswith("as_loveclean_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"lv_{data[len('as_loveclean_'):]}", back="as_notes"); return
    if data.startswith("as_lovehidden_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"hid_{data[len('as_lovehidden_'):]}", back="as_notes"); return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):]); return
    if data.startswith("as_love_"):
        key = data[len("as_love_"):]
        import cleanup as _cl
        await _cl.open_cleanup(bot, cid, f"lv_{key}", back="as_notes"); return


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
