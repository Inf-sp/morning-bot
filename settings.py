from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import learning
from util import esc

SETTINGS_KEY = "user_settings.json"
NOTIF_TYPES = [
    ("morning_brief",  "☀️ Утренний бриф (08:30)"),
    ("weather_warn",   "🌧 Погодное предупреждение (08:45)"),
    ("lagom_daily",    "☕️ Лагом дня (09:00)"),
    ("recipe_daily",   "🍽️ Рецепт дня (12:30)"),
    ("checkin_day",    "🫣 Дневная разгрузка (14:00)"),
    ("evening_weather","🌆 Вечерняя погода (19:00)"),
    ("weekly_events",  "🎵 Афиша недели (вс 10:00)"),
    ("weekly_forecast","🌍 Недельный прогноз (вс 19:00)"),
    ("grammar",        "📝 Слово/фраза дня (11:00)"),
    ("live_lang",      "💬 Живой язык — пословицы"),
    ("vocab_review",   "📖 Повтор словаря (21:00)"),
    ("checkin_eve",    "🥸 Вечерний разбор (22:00)"),
]

# Группировка для отображения в send_notif
_NOTIF_GROUPS = [
    ("🗓 День", ["morning_brief", "weather_warn", "lagom_daily", "recipe_daily",
                 "checkin_day", "evening_weather"]),
    ("📅 Неделя", ["weekly_events", "weekly_forecast"]),
    ("📚 Обучение", ["grammar", "live_lang", "vocab_review"]),
    ("🧠 Самозабота", ["checkin_eve"]),
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
    return get(cid, f"notif_{kind}", False)

def study_lang(cid):
    return get(cid, "study_lang", "нидерландский")


def home_kb(cid):
    rows = [
        [InlineKeyboardButton("🌍 Сменить город", callback_data="set_city")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif")],
    ]
    if config.CHAT_ID and str(cid) == str(config.CHAT_ID):
        rows.append([InlineKeyboardButton("🔐 Администратор", callback_data="set_admin")])
    return InlineKeyboardMarkup(rows)

async def send_home(bot, cid):
    await bot.send_message(chat_id=cid,
        text="⚙️ <b>Настройки</b>\n\nЯзык, уведомления, город и параметры стиля.\n\nВыбери раздел 👇",
        parse_mode="HTML", reply_markup=home_kb(cid))

async def _run_notif_test(bot, cid, kind):
    """Предпросмотр уведомления: вызывает реальную send-функцию для kind."""
    import verify
    try:
        if kind == "morning_brief":
            import weather as _w
            await _w.send_weather(bot, cid, "tomorrow_plain")
            await learning.send_morning_word(bot, cid)
        elif kind == "weather_warn":
            import weather as _w
            s = store.get_settings(cid)
            data = _w.fetch_weather(s["lat"], s["lon"], 2)
            d = data["daily"]
            wind = d["windspeed_10m_max"][0] or 0
            code = d["weathercode"][0]
            rain = d["precipitation_probability_max"][0] or 0
            rain_mm = (d.get("precipitation_sum") or [None])[0]
            text = _w.storm_alert(wind, code, rain, rain_mm, cc=s.get("cc", ""))
            if not text:
                parts = []
                if wind > 10:
                    parts.append(f"💨 ветер до {wind:.0f} м/с")
                if rain > 70:
                    parts.append(f"🌧 дождь {rain:.0f}%")
                if code in {95, 96, 99}:
                    parts.append("⛈ возможна гроза")
                if not parts:
                    parts.append("Сейчас без экстремальных условий")
                text = "⚠️ <b>Погодное предупреждение</b>\n\n" + " • ".join(parts)
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
        elif kind == "lagom_daily":
            import balance as _b
            await _b.send_motiv_push(bot, cid)
        elif kind == "grammar":
            await learning.send_morning_word(bot, cid)
        elif kind == "recipe_daily":
            import balance as _b
            await _b.send_recipe_push(bot, cid)
        elif kind == "checkin_day":
            await bot.send_message(chat_id=cid, parse_mode="HTML",
                text="🫣 <b>Дневная разгрузка</b>\n\nСейчас не анализируй, просто выгрузи мысли.\n\n"
                     "Каждая тревога - с новой строки.\n\nВечером проверим, что было фактами, а что шумом…")
        elif kind == "vocab_review":
            await learning.send_vocab_review(bot, cid)
        elif kind == "checkin_eve":
            import balance as _b
            await _b.send_evening_review(bot, cid)
        elif kind == "weekly_forecast":
            import weather as _w
            await _w.send_weather(bot, cid, "week")
        elif kind == "weekly_events":
            import leisure as _l
            await _l.send_weekly_events(bot, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e, skill="notif_test")


async def send_notif(bot, cid, q=None):
    kind_to_label = dict(NOTIF_TYPES)
    rows = []
    for group_title, kinds in _NOTIF_GROUPS:
        rows.append([InlineKeyboardButton(f"— {group_title} —", callback_data="noop")])
        for kind in kinds:
            label = kind_to_label.get(kind, kind)
            on = notif_on(cid, kind)
            mark = "🟢" if on else "⚪"
            rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"set_notiftgl_{kind}")])
    any_on = any(notif_on(cid, k) for k, _ in NOTIF_TYPES)
    if any_on:
        rows.append([InlineKeyboardButton("🔕 Отключить все", callback_data="set_notif_off_all")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="set_home")])
    text = "🔔 <b>Уведомления</b>\n\nНажми для включения/выключения. 🟢 — включено."
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

async def toggle_notif(bot, cid, kind, q=None):
    set_(cid, f"notif_{kind}", not notif_on(cid, kind))
    await send_notif(bot, cid, q)

async def notif_off_all(bot, cid, q=None):
    for kind, _ in NOTIF_TYPES:
        set_(cid, f"notif_{kind}", False)
    await send_notif(bot, cid, q)

async def send_lang(bot, cid):
    cur = study_lang(cid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if cur == "нидерландский" else "") + "🇳🇱 Нидерландский", callback_data="set_lang_nl")],
        [InlineKeyboardButton(("✅ " if cur == "английский" else "") + "🇬🇧 Английский", callback_data="set_lang_en")],
        [InlineKeyboardButton("◀️ Назад", callback_data="set_home")],
    ])
    await bot.send_message(chat_id=cid, text="🗣 <b>Язык для утренней грамматики/слова дня</b>",
                           parse_mode="HTML", reply_markup=kb)

async def set_lang(bot, cid, lang):
    set_(cid, "study_lang", lang)
    await bot.send_message(chat_id=cid, text=f"Готово. Язык уведомлений по обучению: {lang}.")
    await send_home(bot, cid)

_BODY_PLACEHOLDER = "не указано"

async def send_body(bot, cid):
    body = get(cid, "body", "")
    style = get(cid, "style", "минимализм")
    body_line = esc(body) if body else "<i>не задано</i>"
    txt = (
        "📐 <b>Мои параметры </b>\n\n"
        "Бот использует эти данные при подборе образа и оценке покупок — "
        "чтобы советы по размеру и силуэту подходили именно тебе.\n\n"
        f"<b>Параметры тела:</b> {body_line}\n"
        f"<b>Стиль:</b> {esc(style)}\n\n"
        "<i>Пример параметров: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Параметры тела", callback_data="set_bodyinput")],
        [InlineKeyboardButton("🎨 Стиль", callback_data="set_stylepick")],
        [InlineKeyboardButton("◀️ Назад", callback_data="set_wardrobe")],
    ])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)

async def send_style_pick(bot, cid):
    cur = get(cid, "style", "минимализм")
    rows = [[InlineKeyboardButton(("✅ " if cur == s else "") + s, callback_data=f"set_style_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("✏️ Описать своими словами", callback_data="set_stylecustom")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="set_body")])
    await bot.send_message(chat_id=cid,
        text="🎨 <b>Стиль одежды</b>\n\nВыбери из предложенных или опиши своими словами — бот учтёт при подборе образа:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def set_style(bot, cid, i):
    if 0 <= i < len(STYLES):
        set_(cid, "style", STYLES[i])
    await send_body(bot, cid)


# ===== Списки в настройках: страны, артисты, книги, шкаф =====
def _item_label(it):
    return it if isinstance(it, str) else (it.get("name") or it.get("word") or str(it))

def _list_kb(items, del_prefix, add_cb, back="set_home"):
    rows = [[InlineKeyboardButton(f"❌ {_item_label(it)[:35]}", callback_data=f"{del_prefix}{i}")]
            for i, it in enumerate(items[-40:])]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data=add_cb)])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(rows)

async def _send_list(bot, cid, title, items, del_prefix, add_cb, back="set_home"):
    """Лагом и аналогичные экраны с intro-текстом: элементы только в кнопках."""
    txt = title if items else f"{title}\n\nПока пусто — добавь первый элемент 👇"
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=_list_kb(items, del_prefix, add_cb, back))

# --- Шкаф ---
async def send_wardrobe(bot, cid, back="m_notes"):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Добавить", callback_data="set_ward_add")],
        [InlineKeyboardButton("❌ Убрать", callback_data="set_ward_del")],
        [InlineKeyboardButton("📐 Параметры", callback_data="set_body")],
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ])
    await bot.send_message(chat_id=cid, text="👕 <b>Мой шкаф</b>\n\nБаза вещей и параметры для подбора одежды.",
                           parse_mode="HTML", reply_markup=kb)

# --- Страны ---
async def send_countries(bot, cid):
    from util import country_flag
    items = store.get_list(config.COUNTRIES_KEY, cid)
    rows = [[InlineKeyboardButton(f"❌ {country_flag(it)} {_item_label(it)[:33]}", callback_data=f"setdel_country_{i}")]
            for i, it in enumerate(items[-40:])]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data="setadd_country")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid, text="🗺️ <b>Мои страны</b>", parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

# --- Артисты ---
async def send_artists(bot, cid):
    items = store.get_list(config.ARTISTS_KEY, cid)
    await bot.send_message(chat_id=cid, text="🎤 <b>Мои артисты</b>", parse_mode="HTML",
                           reply_markup=_list_kb(items, "setdel_artist_", "setadd_artist"))

# --- Книги ---

# --- Лагом ---
_LAGOM_INTRO = (
    "☕️ <b>Лагом — твои установки и ценности</b>\n\n"
    "Лагом (швед. <i>lagom</i> — «в самый раз») — твой личный свод принципов: "
    "что важно, как хочешь жить, что даёт энергию, а что забирает.\n\n"
    "Бот использует их в ☕️ Личная мотивация — "
    "чтобы советы звучали именно про тебя, а не общими словами.\n\n"
    "<b>Примеры:</b> «Меньше, но лучше» · «Физическая активность каждый день» · "
    "«Не сравниваю себя с другими»\n\n"
)

async def send_lagom(bot, cid, back="m_notes"):
    import memory
    items = memory.get_lagom(cid)
    txt = _LAGOM_INTRO.rstrip() if items else f"{_LAGOM_INTRO.rstrip()}\n\nПока пусто — добавь первый принцип 👇"
    rows = []
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data="setadd_lagom")])
    if items:
        rows.append([InlineKeyboardButton("❌ Убрать", callback_data="set_lagom_clean")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_books(bot, cid):
    items = store.get_list(config.BOOKS_KEY, cid)
    await bot.send_message(chat_id=cid, text="📚 <b>Мои книги</b>", parse_mode="HTML",
                           reply_markup=_list_kb(items, "setdel_book_", "setadd_book"))

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
    from util import esc
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    icons = {"country": "🧳", "artist": "🎤", "book": "📚"}
    item = text.strip()
    store.add_to_list(keymap[kind], cid, item)
    await bot.send_message(chat_id=cid,
        text=f"✅ {icons.get(kind, '')} «{esc(item)}» добавлено.", parse_mode="HTML")
    if kind == "country":
        await send_countries(bot, cid)
    elif kind == "artist":
        await send_artists(bot, cid)
    else:
        await send_books(bot, cid)


async def handle_callback(bot, cid, data, q=None):
    if data == "set_home":
        await send_home(bot, cid)
    elif data == "set_love":
        await send_love_home(bot, cid, back="m_notes")
    elif data == "set_dict":
        import learning
        await learning.send_dict(bot, cid, back="m_notes")
    elif data == "set_dict_g":
        import learning
        await learning.send_dict(bot, cid, back="m_learn")
    elif data == "set_fridge":
        import balance
        await balance.send_fridge(bot, cid, back="m_notes")
    elif data == "set_fridge_g":
        import balance
        await balance.send_fridge(bot, cid, back="m_food")
    elif data == "set_notif":
        await send_notif(bot, cid, q)
    elif data.startswith("set_notiftgl_"):
        await toggle_notif(bot, cid, data[len("set_notiftgl_"):], q)
    elif data.startswith("set_notiftest_"):
        await _run_notif_test(bot, cid, data[len("set_notiftest_"):])
    elif data == "set_notif_off_all":
        await notif_off_all(bot, cid, q)
    elif data == "set_lang":
        await send_lang(bot, cid)
    elif data == "set_lang_nl":
        await set_lang(bot, cid, "нидерландский")
    elif data == "set_lang_en":
        await set_lang(bot, cid, "английский")
    elif data == "set_levels":
        await learning.send_levels(bot, cid, q=q, back="set_home")
    elif data == "set_city":
        store.pending_input[cid] = "setcity"
        await bot.send_message(chat_id=cid, text="🌍 Напиши город - переключу.")
    elif data == "set_body":
        await send_body(bot, cid)
    elif data == "set_wardrobe":
        await send_wardrobe(bot, cid, back="m_notes")
    elif data == "set_wardrobe_g":
        await send_wardrobe(bot, cid, back="m_wardrobe")
    elif data == "set_ward_add":
        store.pending_input[cid] = "wardrobe_add_set"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="set_wardrobe")]])
        await bot.send_message(chat_id=cid,
            text="🏷 Напиши вещь: тип + цвет + детали/бренд.\n"
                 "<i>Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.</i>",
            parse_mode="HTML", reply_markup=kb)
    elif data == "set_ward_del":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "kast_s")
    elif data == "set_lagom":
        await send_lagom(bot, cid, back="m_notes")
    elif data == "setadd_lagom":
        store.pending_input[cid] = "setadd_lagom"
        await bot.send_message(chat_id=cid,
            text="☕️ Напиши установку или принцип — добавлю в Лагом.\n\n"
                 "<i>Например: «Меньше экрана, больше природы»</i>",
            parse_mode="HTML")
    elif data.startswith("setdel_lagom_"):
        import memory
        memory.del_lagom(cid, int(data.split("_")[-1]))
        await send_lagom(bot, cid)
    elif data == "set_lagom_clean":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "lagom")
    elif data == "set_countries":
        await send_countries(bot, cid)
    elif data == "set_artists":
        await send_artists(bot, cid)
    elif data == "set_books":
        await send_books(bot, cid)
    elif data == "setadd_country":
        store.pending_input[cid] = "setadd_country"
        await bot.send_message(chat_id=cid, text="🧳 Напиши страну - добавлю в список.")
    elif data == "setadd_artist":
        store.pending_input[cid] = "setadd_artist"
        await bot.send_message(chat_id=cid, text="🎤 Напиши имя артиста - добавлю в список.")
    elif data == "setadd_book":
        store.pending_input[cid] = "setadd_book"
        await bot.send_message(chat_id=cid, text="📚 Напиши название книги - добавлю в список.")
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
        await bot.send_message(chat_id=cid,
            text="🎨 Опиши свой стиль — как хочешь выглядеть, что нравится, что нет.\n\n"
                 "<i>Например: «Люблю тёмные оттенки, оверсайз-силуэты, минимум принтов. "
                 "Стараюсь избегать костюмов.»</i>",
            parse_mode="HTML")
    elif data.startswith("set_style_"):
        await set_style(bot, cid, int(data.split("_")[-1]))
    elif data == "set_bodyinput":
        store.pending_input[cid] = "bodyinput"
        await bot.send_message(chat_id=cid,
            text="✏️ <b>Параметры тела</b>\n\nНапиши свободным текстом — рост, размер одежды, размер обуви и брюк.\n\n"
                 "<i>Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32</i>",
            parse_mode="HTML")
    elif data == "set_admin":
        await _admin_guard(bot, cid, send_admin)
    elif data == "set_admin_users":
        await _admin_guard(bot, cid, send_admin_users)
    elif data == "set_admin_invite":
        async def _do_invite(b, c):
            import access as _acc
            import secrets as _sec
            code = _acc.create_invite()
            me = await b.get_me()
            link = f"https://t.me/{me.username}?start={code}"
            await b.send_message(chat_id=c,
                text=f"🔗 <b>Подарочный инвайт :</b>\n<a href=\"{link}\">{link}</a>",
                parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Пользователи", callback_data="set_admin_users")]]))
        await _admin_guard(bot, cid, _do_invite)
    elif data.startswith("set_admin_revoke_"):
        target = data[len("set_admin_revoke_"):]
        async def _do_revoke(b, c):
            import access as _acc
            _acc.revoke_user(target)
            store.purge_user(target)
            await send_admin_users(b, c)
        await _admin_guard(bot, cid, _do_revoke)
    elif data == "set_admin_cost":
        await _admin_guard(bot, cid, send_admin_cost)
    elif data == "set_admin_health":
        await _admin_guard(bot, cid, send_admin_health)
    elif data == "set_admin_run_notif":
        await _admin_guard(bot, cid, send_admin_run_notif)
    elif data.startswith("set_admin_runjob_"):
        kind = data[len("set_admin_runjob_"):]
        async def _do_job(b, c):
            await b.send_message(chat_id=c, text="▶️ Запускаю…")
            await _run_notif_test(b, c, kind)
        await _admin_guard(bot, cid, _do_job)


# ===== СОХРАНЕНИЯ / ЛЮБИМЫЕ (notes.py) =====

async def save_fav(bot, cid, q=None):
    # Берём оригинальный текст сообщения (с HTML-форматированием) прямо из callback
    txt = ""
    if q is not None and q.message:
        txt = q.message.text_html or q.message.caption_html or ""
    if not txt:
        txt = store.last_answer.get(str(cid), "")
    if not txt:
        await bot.send_message(chat_id=cid, text="Нечего сохранять."); return
    source = store.last_source.get(str(cid), "Прочее")
    store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(config.TZ).strftime("%d.%m"),
                                              "text": txt, "source": source, "bucket": "fav"})
    await bot.send_message(chat_id=cid, text="⏳ Сохранено во временные закладки.")

def _top_cat(source):
    return (source or "Прочее").split(" · ")[0]

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

async def note_delete_menu(bot, cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list):
        await send_notes(bot, cid); return
    n = notes_list[i]
    t = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
    typ, _, _, _ = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    rows = []
    if typ:
        rows.append([InlineKeyboardButton("🚫 В чёрный список", callback_data=f"as_noteblack_{i}")])
        rows.append([InlineKeyboardButton("❤️ В любимые", callback_data=f"as_notelove_{i}")])
    rows.append([InlineKeyboardButton("🗑 Просто удалить", callback_data=f"as_notedrop_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Отмена", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=f"Что сделать с «{t[:60]}»?",
                           reply_markup=InlineKeyboardMarkup(rows))

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
        await bot.send_message(chat_id=cid, text=f"🚫 «{t[:50]}» - в чёрный список «{cat}». Больше не порекомендую.")
    else:
        await bot.send_message(chat_id=cid, text="Удалил из закладок.")
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
        await bot.send_message(chat_id=cid, text=f"❤️ «{t[:50]}» - в любимые, раздел «{cat}».")
    else:
        await bot.send_message(chat_id=cid, text="Удалил из закладок.")
    await send_bucket(bot, cid, "fav")

async def note_drop(bot, cid, i):
    n = _pop_note(cid, i)
    bucket = _note_bucket(n) if n else "fav"
    await bot.send_message(chat_id=cid, text="🗑 Удалил.")
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
    lines.append("🧳 ПЛАНЫ ПОЕЗДОК")
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
        ("Мои артисты", store.get_list(config.ARTISTS_KEY, cid)),
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
    notes_list = store.get_list(config.NOTES_KEY, cid)
    n_fav = sum(1 for n in notes_list if _note_bucket(n) == "fav")
    rows = [
        [InlineKeyboardButton(f"⏳ Позже ({n_fav})", callback_data="as_bucket_fav")],
        [InlineKeyboardButton("🎚️ Шкаф", callback_data="set_wardrobe"),
         InlineKeyboardButton("🎚️ Холодильник", callback_data="set_fridge")],
        [InlineKeyboardButton("🎚️ Словарь", callback_data="set_dict"),
         InlineKeyboardButton("🎚️ Лагом", callback_data="set_lagom")],
        [InlineKeyboardButton("🎚️ Кино", callback_data="as_love_movies"),
         InlineKeyboardButton("🎚️ Страны", callback_data="as_love_countries")],
        [InlineKeyboardButton("🎚️ Артисты", callback_data="as_love_artists"),
         InlineKeyboardButton("🎚️ Книги", callback_data="as_love_books")],
        [InlineKeyboardButton("📤 Экспорт", callback_data="as_export")],
    ]
    await bot.send_message(chat_id=cid, parse_mode="HTML",
        text="🗂️ <b>Моя база</b>\n\nПланы, гардероб, холодильник, словарь и любимое — всё в одном месте.\n\nВыбери раздел 👇",
        reply_markup=InlineKeyboardMarkup(rows))

async def send_plans(bot, cid):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "plan"]
    if not items:
        await bot.send_message(chat_id=cid, text="🧳 <b> Поездки</b>\n\nпусто", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="as_bucket_fav")]]))
        return
    rows = []
    for i, n in items:
        country = (n.get("country") or "Поездка") if isinstance(n, dict) else "Поездка"
        d = n.get("date", "") if isinstance(n, dict) else ""
        rows.append([InlineKeyboardButton(f"🧳 {d} · {country}"[:40], callback_data=f"as_planview_{i}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_bucket_fav")])
    await bot.send_message(chat_id=cid, parse_mode="HTML",
        text="🧳 <b>Мои поездки</b>\n\nСохранённые планы поездок.\n\nВыбери план 👇",
        reply_markup=InlineKeyboardMarkup(rows))

async def plan_view(bot, cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "plan":
        await send_plans(bot, cid); return
    n = notes_list[i]
    text = n.get("text", "") if isinstance(n, dict) else str(n)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить план", callback_data=f"as_plandel_{i}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="as_bucket_plan")],
    ])
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

async def fav_view(bot, cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "fav":
        await send_bucket(bot, cid, "fav"); return
    n = notes_list[i]
    text = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
    src = n.get("source", "") if isinstance(n, dict) else ""
    d = n.get("date", "") if isinstance(n, dict) else ""
    header = f"⭐ <b>{esc(src)}</b>" + (f" · {esc(d)}" if d else "")
    full = header + "\n\n" + text
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"fav_del_{i}")],
        [InlineKeyboardButton("⬅️ К закладкам", callback_data="as_bucket_fav")],
    ])
    chunks = [full[j:j + 4000] for j in range(0, len(full), 4000)]
    for idx, chunk in enumerate(chunks):
        markup = kb if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=chunk, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk, reply_markup=markup)


async def fav_del(bot, cid, i):
    _pop_note(cid, i)
    await send_bucket(bot, cid, "fav")


async def send_bucket(bot, cid, bucket):
    if bucket == "love":
        await send_love_home(bot, cid); return
    if bucket == "plan":
        await send_plans(bot, cid); return
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "fav"]
    count = len(items)
    if not count:
        txt = ("⏳ <b>Позже </b>\n\n"
               "Пусто — сохраняй интересное кнопкой «⏳ Позже» под ответами.")
        rows = [
            [InlineKeyboardButton("🧳 Мои поездки", callback_data="as_bucket_plan")],
            [InlineKeyboardButton("◀️ Назад", callback_data="as_notes")],
        ]
        await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(rows)); return
    import re as _re
    _strip_html = lambda s: _re.sub(r"<[^>]+>", "", s)
    txt = f"⏳ <b>Позже </b> · {count}"
    rows = []
    for i, n in items:
        src = (n.get("source", "Прочее") if isinstance(n, dict) else "Прочее") or "Прочее"
        raw = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
        preview = _strip_html(raw)
        short = preview[:28] + ("…" if len(preview) > 28 else "")
        label = f"{src} · {short}"
        rows.append([InlineKeyboardButton(label, callback_data=f"fav_view_{i}")])
    rows.append([InlineKeyboardButton("🧳 Мои поездки", callback_data="as_bucket_plan")])
    rows.append([InlineKeyboardButton("❌ Удалить", callback_data="as_clean_fav")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


LOVE_SECTIONS = [
    ("🎬 Кино", "movies"),
    ("🗺️ Мои страны", "countries"),
    ("🎸 Мои артисты", "artists"),
    ("📖 Мои книги", "books"),
]

async def send_love_home(bot, cid, back="m_notes"):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])
    await bot.send_message(chat_id=cid, text="❤️ <b>Любимые</b>\n\nТвои топ-категории.\n\nВыбери раздел 👇",
                           parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

def _love_items(cid, key):
    if key == "movies":
        return list(store.get_list(config.WATCHLIST_KEY, cid))
    if key == "countries":
        cur = store.get_list(config.COUNTRIES_KEY, cid)
        return [c if isinstance(c, str) else c.get("name", "") for c in cur]
    if key == "artists":
        return list(store.get_list(config.ARTISTS_KEY, cid))
    if key == "books":
        return list(store.get_list(config.BOOKS_KEY, cid))
    return []

def _love_title(key):
    return {"movies": "🎬 Мое кино", "countries": "🗺️ Мои страны",
            "artists": "🎸 Мои артисты", "books": "📖 Мои книги"}.get(key, "Любимые")

async def send_love_section(bot, cid, key):
    if key == "recipes":
        import balance
        await balance.send_my_recipes(bot, cid)
        return
    items = _love_items(cid, key)
    title = _love_title(key)
    if items:
        preview = "\n".join(f"• {esc(str(it))}" for it in items[:50])
        body = preview
    else:
        body = "<i>пусто</i>"
    lines = [f"<b>{title}</b>", "", body]
    rows = [[InlineKeyboardButton("📝 Добавить", callback_data=f"as_loveadd_{key}")]]
    if items:
        rows.append([InlineKeyboardButton("🗑 Выбрать для удаления", callback_data=f"as_loveclean_{key}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

def _love_key_of(key):
    return {"movies": config.WATCHLIST_KEY, "countries": config.COUNTRIES_KEY,
            "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)

async def love_delete(bot, cid, key, i):
    store_key = _love_key_of(key)
    if not store_key:
        await send_love_section(bot, cid, key); return
    items = store.get_list(store_key, cid)
    if i < len(items):
        items.pop(i)
        store.set_list(store_key, cid, items)
    await send_love_section(bot, cid, key)

async def love_add_start(bot, cid, key):
    store.pending_input[str(cid)] = f"loveadd_{key}"
    name = {"movies": "фильм или сериал", "countries": "страну",
            "artists": "артиста", "books": "книгу"}.get(key, "элемент")
    await bot.send_message(chat_id=cid, text=f"Напиши {name} — добавлю в любимые.")

async def love_add_done(bot, cid, key, text):
    store_key = _love_key_of(key)
    if store_key:
        store.add_to_list(store_key, cid, text.strip())
    import cleanup as _cl
    await bot.send_message(chat_id=cid, text="Добавлено.")
    await _cl.open_cleanup(bot, cid, f"lv_{key}")


async def handle_notes_callback(bot, cid, q, data):
    """Роутер для callback'ов закладок/любимого (as_* и fav_*)."""
    if data == "as_fav":
        await save_fav(bot, cid, q); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_bucket_fav":
        await send_bucket(bot, cid, "fav"); return
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
    if data.startswith("as_notedel_"):
        await note_delete_menu(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_noteblack_"):
        await note_to_blacklist(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notelove_"):
        await note_to_love(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notedrop_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_view_"):
        await fav_view(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_del_"):
        await fav_del(bot, cid, int(data.split("_")[-1])); return
    if data == "as_clean_fav":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "nb"); return
    if data.startswith("as_loveclean_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"lv_{data[len('as_loveclean_'):]}"); return
    if data.startswith("as_lovedel_"):
        parts = data[len("as_lovedel_"):].rsplit("_", 1)
        await love_delete(bot, cid, parts[0], int(parts[1])); return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):]); return
    if data.startswith("as_love_"):
        key = data[len("as_love_"):]
        import cleanup as _cl
        await _cl.open_cleanup(bot, cid, f"lv_{key}"); return


# ===== АДМИНИСТРАТОР =====

def _is_admin(cid) -> bool:
    return bool(config.CHAT_ID) and str(cid) == str(config.CHAT_ID)


async def _admin_guard(bot, cid, fn):
    """Выполнить fn(bot, cid) только если cid — администратор."""
    if not _is_admin(cid):
        await bot.send_message(chat_id=cid, text="⛔ Только для администратора.")
        return
    await fn(bot, cid)


async def send_admin(bot, cid):
    """Главный экран администратора."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🩺 Статус сервисов", callback_data="set_admin_health")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="set_admin_users")],
        [InlineKeyboardButton("💸 Расходы на LLM", callback_data="set_admin_cost")],
        [InlineKeyboardButton("📩 Запустить рассылку", callback_data="set_admin_run_notif")],
        [InlineKeyboardButton("◀️ Назад", callback_data="set_home")],
    ])
    await bot.send_message(
        chat_id=cid,
        text="🔐 <b>Администратор</b>\n\nСервисный раздел. Только для владельца.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def send_admin_users(bot, cid):
    """Список пользователей с инвайтами и кнопками отзыва."""
    import access as _acc
    allowed = _acc.get_allowed_cids()
    pending = _acc.pending_invites()

    lines = ["👥 <b>Пользователи</b>", ""]
    rows = []
    for uid in allowed:
        prof = store.get_profile(uid)
        name = prof.get("name", "")
        name_part = f" · {esc(name)}" if name else ""
        if _acc.is_owner(uid):
            lines.append(f"👑 Owner{name_part}")
        else:
            lines.append(f"👤 {uid}{name_part}")
            rows.append([InlineKeyboardButton(f"🗑 Удалить {uid}{name_part}", callback_data=f"set_admin_revoke_{uid}")])

    if pending:
        lines.append("")
        lines.append(f"⏳ Активных инвайтов: {len(pending)}")

    rows.append([InlineKeyboardButton("🔗 Создать инвайт", callback_data="set_admin_invite")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="set_admin")])

    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


async def send_admin_cost(bot, cid):
    """Сводка расходов на LLM за последние 7 дней."""
    import ai as _ai
    import time as _time
    log = _ai.get_cost_log()
    week_ago = _time.time() - 7 * 86400
    recent = [e for e in log if e.get("ts", 0) >= week_ago]
    if not recent:
        text = "💸 <b>Расходы за 7 дней</b>\n\nДанных пока нет."
    else:
        by_mod: dict = {}
        by_prov: dict = {}
        total_tokens = 0
        for e in recent:
            mod = e.get("module") or "?"
            prov = e.get("provider") or "?"
            tok = e.get("tokens", 0)
            by_mod[mod] = by_mod.get(mod, 0) + tok
            by_prov[prov] = by_prov.get(prov, 0) + tok
            total_tokens += tok

        # грубая оценка в USD
        haiku_tok = sum(e.get("tokens", 0) for e in recent if "haiku" in e.get("model", "").lower())
        other_tok = total_tokens - haiku_tok
        usd_est = haiku_tok / 1_000_000 * 0.75 + other_tok / 1_000_000 * 3.0

        def _pct(t):
            return f"{round(t / total_tokens * 100)}%" if total_tokens else "0%"

        # все провайдеры в порядке приоритета + проверка настроен ли ключ
        _PROV_ORDER = [
            ("claude",      "Claude (Anthropic)", bool(config.ANTHROPIC_API_KEY)),
            ("openai",      "OpenAI",             bool(config.OPENAI_API_KEY)),
            ("gemini",      "Gemini (бесплатно)", True),
            ("openrouter",  "OpenRouter",          bool(config.OPENROUTER_API_KEY)),
            ("groq",        "Groq (бесплатно)",   bool(config.GROQ_API_KEY)),
            ("cf",          "Cloudflare",          bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
        ]

        # человекочитаемые имена функций
        _mod_names = {"wardrobe": "👗 Гардероб", "balance": "🥗 Баланс/еда",
                      "weather": "🌤 Погода", "learning": "📚 Обучение",
                      "leisure": "🎬 Досуг", "myday": "☀️ Мой день",
                      "travel": "✈️ Путешествия", "assistant": "💬 Ассистент",
                      "content": "🎵 Контент", "notes": "📝 Заметки"}

        lines = ["💸 <b>Расходы за 7 дней</b>", "",
                 f"Вызовов: {len(recent)}",
                 f"Токенов: ~{total_tokens:,}",
                 f"Оценка: ~${usd_est:.3f}", ""]

        # все провайдеры в порядке приоритета
        lines.append("<b>По провайдерам:</b>")
        for key, label, configured in _PROV_ORDER:
            tok = by_prov.get(key, 0)
            if not configured:
                lines.append(f"  {esc(label)}: — (нет ключа)")
            elif tok:
                lines.append(f"  {esc(label)}: {tok:,} tok ({_pct(tok)})")
            else:
                lines.append(f"  {esc(label)}: 0 tok")

        # по функциям — только заполненные модули
        known_mods = [(m, t) for m, t in by_mod.items() if m and m != "?"]
        if known_mods:
            top_mods = sorted(known_mods, key=lambda x: -x[1])[:5]
            lines.append("")
            lines.append("<b>Где тратится:</b>")
            for m, t in top_mods:
                label = _mod_names.get(m, m)
                lines.append(f"  {esc(label)}: {t:,} tok ({_pct(t)})")

        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="set_admin")]])
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)


async def send_admin_health(bot, cid):
    """Inline-статус сервисов: API-ключи, DB, Weather, LLM."""
    import store as _st
    import weather as _w

    lines = ["📡 <b>Статус сервисов</b>"]

    required = [
        ("TELEGRAM_TOKEN", bool(config.TELEGRAM_TOKEN)),
        ("GEMINI_API_KEY",  bool(config.GEMINI_API_KEY)),
        ("DATABASE_URL",    bool(config.DATABASE_URL)),
        ("CHAT_ID",         bool(config.CHAT_ID)),
    ]
    lines.extend(["", "<b>Обязательные ключи</b>"])
    for k, ok in required:
        lines.append(f"  {'✅' if ok else '❌'} <code>{k}</code>")

    optional = [
        ("ANTHROPIC_API_KEY",   bool(config.ANTHROPIC_API_KEY)),
        ("GROQ_API_KEY",        bool(config.GROQ_API_KEY)),
        ("OPENAI_API_KEY",      bool(config.OPENAI_API_KEY)),
        ("OPENROUTER_API_KEY",  bool(config.OPENROUTER_API_KEY)),
        ("CLOUDFLARE",          bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
        ("TAVILY_API_KEY",      bool(config.TAVILY_API_KEY)),
        ("TMDB_API_KEY",        bool(config.TMDB_API_KEY)),
        ("TICKETMASTER_API_KEY",bool(config.TICKETMASTER_API_KEY)),
    ]
    lines.extend(["", "<b>Опциональные ключи</b>"])
    for k, ok in optional:
        lines.append(f"  {'✅' if ok else '⚪'} <code>{k}</code>")

    lines.extend(["", "<b>Состояние</b>"])
    try:
        _st._load("__health__")
        lines.append("  ✅ DB: OK")
    except Exception as e:
        lines.append(f"  ❌ DB: {str(e)[:60]}")

    try:
        s = store.get_settings(cid)
        _w.fetch_weather(s["lat"], s["lon"], 1)
        lines.append("  ✅ Weather API: OK")
    except Exception:
        lines.append("  ❌ Weather API: недоступна")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="set_admin")]])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def send_admin_run_notif(bot, cid):
    """Подменю: запустить любое уведомление прямо сейчас."""
    rows = [
        [InlineKeyboardButton(label, callback_data=f"set_admin_runjob_{kind}")]
        for kind, label in NOTIF_TYPES
    ]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="set_admin")])
    kb = InlineKeyboardMarkup(rows)
    await bot.send_message(
        chat_id=cid,
        text="📩 <b>Запустить рассылку</b>\n\nВыбери уведомление — оно придёт тебе прямо сейчас:",
        parse_mode="HTML",
        reply_markup=kb,
    )
