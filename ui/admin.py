"""UI админ-панели — единый визуальный язык (§1 docs/admin.md).

Каждый экран: заголовок → ключевой статус → блок метрик (b.metric)
→ inline-кнопки. Метрики, которые пока держатся на новом трекинге, честно помечаем
маркером ⚠️, если данных ещё нет (значение 0/пусто) — но не скрываем строку.

Здесь только сборка текста. Логика/данные — в settings.py (send_admin_*).
"""
from .builder import MessageBuilder, MessageSpec, WARNING_EMOJI
from . import rich
from .constants import STATUS_EMOJI, UI_EMOJI, choose_label, ui_label

# --- статус-точки (единственные допустимые «светофоры») ---
OK, WARN, BAD, OFF = STATUS_EMOJI["ok"], STATUS_EMOJI["warn"], STATUS_EMOJI["bad"], "□"
UNKNOWN = STATUS_EMOJI["unknown"]      # давно не было проверки - не значит "всё ок"
WARNING = WARNING_EMOJI                # противоречивые/сломанные данные, а не статус сервиса


def _num(n) -> str:
    """Человекочитаемое число: 1234 -> '1.2k'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def only():
    return MessageSpec(text="❌ Только для администратора.")


def deploy_report(version, title, release_notes):
    notes = [str(note).strip() for note in (release_notes or []) if str(note).strip()]
    if not notes:
        notes = ["Бот получил небольшие внутренние улучшения."]
    change_notes = []
    for note in notes:
        change_notes.append(note)
    if not change_notes:
        change_notes = notes[:1]

    b = MessageBuilder()
    b.text_line(f"{UI_EMOJI['version']} ")
    b.bold(f"v{version}")
    b.text_line(f" · {title or 'Обновление'}")
    b.newline()
    b.bold("Что изменено:")
    b.newline()
    for note in change_notes[:4]:
        b.line(f"• {note}")
    b.spacer()
    b.text_line("Бот развёрнут и работает ✅")
    return b.build_stripped()


# ================= ДОМ =================

def home(status_dot=None, status_text=None, updated_at=None, stale=False,
         *, system_dot=None, system_text=None, system_line=None,
         notif_line=None, users_line=None, data_line=None, logs_line=None,
         error_rows=None):
    """Render the admin home screen.

    The compact metric form is used by the current screen.  The older
    ``status_*`` form remains supported for callers that only have a health
    status available.
    """
    b = MessageBuilder()
    b.bold(ui_label("admin", "Админ"))
    b.newline()
    b.spacer()
    if system_dot is not None or system_text is not None:
        dot = system_dot if system_dot is not None else status_dot
        text = system_text if system_text is not None else status_text
        b.line(f"{dot} {text}")
        b.spacer()
        for emoji, label, value in (
            ("📊", "Система", system_line),
            ("🔔", "Уведомления", notif_line),
            ("👨🏻‍💻", "Пользователи", users_line),
            ("🗄", "Данные", data_line),
            ("⚠️", "Логи", logs_line),
        ):
            if value:
                b.line(f"{emoji} {label} · {value}")
        b.spacer()
        b.line(f"Обновлено в {updated_at}")
        return b.build_stripped()
    if stale:
        stale_text = status_text or "Состояние API неизвестно"
        b.line(f"{status_dot} {stale_text} · данные от {updated_at}")
    else:
        b.line(f"{status_dot} {status_text} · обновлено в {updated_at}")
    rows = [str(row or "").strip() for row in error_rows or [] if str(row or "").strip()]
    if rows:
        b.spacer()
        b.bold("Ошибки:")
        b.newline()
        for row in rows:
            b.line(row)
    return b.build_stripped()


# ================= ПОЛЬЗОВАТЕЛИ =================

def users(stats, users_list, users_total, updated_at):
    b = MessageBuilder()
    b.bold(ui_label("users", "Пользователи"))
    b.newline()
    b.spacer()
    b.labeled_line("Всего", stats.get("total", 0), lowercase=False)
    b.labeled_line("Активны за 7 дней", stats.get("active_7d", 0), lowercase=False)
    b.labeled_line("Новых за 7 дней", stats.get("new_7d", 0), lowercase=False)
    b.labeled_line("С уведомлениями", stats.get("with_notifications", 0), lowercase=False)
    b.labeled_line("Админов", stats.get("admins", 0), lowercase=False)
    b.spacer()
    b.labeled_line("Инвайты")
    b.labeled_line("Активных", stats.get("active_invites", 0), lowercase=False)

    if users_list:
        b.spacer()
        b.section("Список")
        for dot, name, last_seen in users_list:
            b.text_line(f"{dot} ")
            b.bold(name)
            b.text_line(f" · {last_seen}")
            b.newline()
        if users_total > len(users_list):
            b.spacer()
            b.line(f"…и ещё {users_total - len(users_list)}")

    b.spacer()
    b.labeled_line("Обновлено", updated_at, lowercase=False)
    return b.build_stripped()


def user_delete_list(removable):
    b = MessageBuilder()
    b.bold("❌ Удалить пользователя")
    b.newline()
    b.spacer()
    if removable:
        b.line("Выбери, кого удалить из бота.")
    else:
        b.line("Удалять некого — кроме тебя, других пользователей нет.")
    return b.build_stripped()


def user_delete_confirm(name):
    b = MessageBuilder()
    b.bold("❌ Удалить пользователя")
    b.newline()
    b.spacer()
    b.text_line("Удалить ")
    b.bold(name)
    b.text_line("? Он потеряет доступ к боту, пока не получит новый инвайт.")
    return b.build_stripped()


def invite_prompt():
    b = MessageBuilder()
    b.bold(ui_label("invite", "Инвайт"))
    b.newline()
    b.spacer()
    b.line("Создать ссылку для нового пользователя.")
    b.spacer()
    b.labeled_line("Срок", "7 дней", lowercase=False)
    b.labeled_line("Лимит", "1 пользователь", lowercase=False)
    b.spacer()
    b.line("После входа пользователь получит приветствие.")
    return b.build_stripped()


def invite_created(link):
    b = MessageBuilder()
    b.bold("✅ Инвайт создан")
    b.newline()
    b.spacer()
    b.labeled_line("Срок", "7 дней", lowercase=False)
    b.labeled_line("Лимит", "1 пользователь", lowercase=False)
    b.spacer()
    b.labeled_line("Ссылка")
    b.line(link)
    b.spacer()
    b.line("Новый пользователь получит приветствие после входа.")
    return b.build_stripped()


def welcome_admin():
    b = MessageBuilder()
    b.bold(ui_label("welcome", "Приветствие"))
    b.newline()
    b.spacer()
    b.labeled_line("Текст, который увидит новый пользователь после входа")
    b.spacer()
    b.line("Привет! Я персональный помощник Дмитрия.")
    b.line("Я помогаю с погодой, одеждой, обучением, рецептами, досугом и важными напоминаниями.")
    b.spacer()
    b.line("Бот работает в тестовом режиме.")
    b.line("Если что-то сломалось или ответ выглядит странно - напишите администратору.")
    return b.build_stripped()


def _updated_footer(updated_at, updated_unix=None):
    return rich.footer([
        "Обновлено в ",
        rich.date_time(updated_at, updated_unix),
    ])


def _system_table_row(line):
    parts = [part.strip() for part in str(line or "").split(" · ") if part.strip()]
    if not parts:
        return ("—", "—")
    if len(parts) == 1:
        return (parts[0], "—")
    # A phone-width system screen needs one compact status column. Keep the
    # role, model, quota or recovery reason together instead of losing it.
    return (parts[0], " · ".join(parts[1:]))


def _system_rich_message(rows, updated_at, updated_unix=None):
    groups = []
    current_title = ""
    current_rows = []
    for raw in rows:
        line = str(raw or "")
        if line in ("AI", "Данные"):
            if current_title:
                groups.append((current_title, current_rows))
            current_title, current_rows = line, []
        elif line:
            current_rows.append(_system_table_row(line))
    if current_title:
        groups.append((current_title, current_rows))
    if not groups:
        groups = [("Сервисы", [])]

    blocks = [rich.heading("🛠 Система", size=2)]
    for title, table_rows in groups:
        blocks.append(rich.heading(title, size=4))
        if table_rows:
            blocks.append(rich.table(
                ("Сервис", "Состояние"), table_rows,
                striped=True, bordered=True,
            ))
        else:
            blocks.append(rich.paragraph("Пока нет данных"))
    blocks.append(_updated_footer(updated_at, updated_unix))
    return rich.message(blocks)


def api_ai(rows, updated_at, updated_unix=None):
    """Система: native table, with the existing compact text as a fallback."""
    b = MessageBuilder()
    b.bold("🛠 Система")
    b.newline()
    for line in rows:
        if str(line) in ("AI", "Данные"):
            if str(line) == "Данные":
                b.spacer()
            b.bold(str(line))
            b.newline()
        else:
            b.line(str(line))
    b.spacer()
    b.line(f"Обновлено в {updated_at}")
    msg = b.build_stripped()
    msg.rich_message = _system_rich_message(rows, updated_at, updated_unix)
    return msg


def _log_table_row(row):
    row = str(row or "")
    if " · " not in row:
        return None
    parts = row.split(" · ", 2)
    if len(parts) == 3 and len(parts[1]) == 5 and parts[1][2:3] == ":":
        return f"{parts[0]} · {parts[1]}", parts[2]
    at, incident = row.split(" · ", 1)
    if len(at) == 5 and at[2:3] == ":":
        return at, incident
    return None


def _logs_rich_message(rows, updated_at, updated_unix=None):
    table_rows = [parsed for parsed in (_log_table_row(row) for row in rows) if parsed]
    extra_rows = [str(row) for row in rows if _log_table_row(row) is None]
    blocks = [rich.heading("⚠️ Ошибки", size=2)]
    if table_rows:
        blocks.append(rich.table(
            ("Дата и время", "Инцидент"), table_rows,
            striped=True, bordered=True,
        ))
    else:
        blocks.append(rich.paragraph("Ошибок за 24 часа нет"))
    blocks.extend(rich.paragraph(row) for row in extra_rows if row)
    blocks.append(_updated_footer(updated_at, updated_unix))
    return rich.message(blocks)


def logs(rows, errors_24h, updated_at, updated_unix=None):
    b = MessageBuilder()
    b.bold("⚠️ Ошибки")
    b.newline()
    b.spacer()
    if not rows:
        b.line("Ошибок за 24 часа нет")
    else:
        for row in rows:
            b.line(row)
    b.spacer()
    b.line(f"Обновлено в {updated_at}")
    msg = b.build_stripped()
    msg.rich_message = _logs_rich_message(rows, updated_at, updated_unix)
    return msg


def user_card(name, city, cid, onboarded, last_seen, active_days, total_msgs, notif_on, notif_total):
    b = MessageBuilder()
    city_part = f" · {city}" if city else ""
    b.bold(f"{name}{city_part}")
    b.newline()
    ob = "✅" if onboarded else "❌"
    b.line(f"ID {str(cid)[:4]}… · онбординг {ob}")
    b.spacer()
    b.metric("Последний вход", last_seen.split(": ", 1)[-1] if ": " in last_seen else last_seen)
    b.metric("Активных дней", f"{active_days} / 30")
    b.metric("Сообщений всего", total_msgs)
    b.metric("Уведомления", f"{notif_on} из {notif_total} вкл")
    return b.build_stripped()


def user_search_result(dot, name, city, last_seen):
    b = MessageBuilder()
    city_part = f" · {city}" if city else ""
    b.line(f"{dot} {name}{city_part}")
    b.line(last_seen)
    return b.build_stripped()


def _weather_ts_hhmm(value):
    if not value:
        return "—"
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).strftime("%H:%M")
    except Exception:
        return "—"


def _weather_usage_status(total):
    import config
    if total >= config.WEATHER_HARD_DAILY_LIMIT:
        return "🔴 Новые запросы заблокированы до следующего дня"
    if total >= config.WEATHER_CRITICAL_LIMIT:
        return f"{WARN} Почти достигнут бесплатный лимит"
    if total >= config.WEATHER_WARNING_LIMIT:
        return "🟡 Использование растёт"
    return "🟢 Лимит в норме"


def weather_usage_block(usage):
    import config
    total = int(usage.get("requests_total") or 0)
    success = int(usage.get("requests_success") or 0)
    failed = int(usage.get("requests_failed") or 0)
    retry = int(usage.get("requests_retry") or 0)
    cache_hits = int(usage.get("cache_hits") or 0)
    left = max(0, config.WEATHER_FREE_DAILY_LIMIT - total)
    b = MessageBuilder()
    b.bold("OpenWeather · сегодня")
    b.newline()
    b.spacer()
    b.label("Запросы", f"{total} / {config.WEATHER_FREE_DAILY_LIMIT:,}".replace(",", " "), lowercase=False).newline()
    b.label("Успешно", success, lowercase=False)
    b.text_line(" · ")
    b.label("Ошибки", failed, lowercase=False)
    b.text_line(" · ")
    b.labeled_line("Повторы", retry, lowercase=False)
    b.labeled_line("Из кэша", cache_hits, lowercase=False)
    b.labeled_line("Осталось бесплатно", left, lowercase=False)
    b.labeled_line("Последний запрос", _weather_ts_hhmm(usage.get("last_request_at")), lowercase=False)
    if usage.get("last_error_reason"):
        b.labeled_line("Последняя ошибка", usage.get("last_error_reason"))
    b.spacer()
    b.line(_weather_usage_status(total))
    return b.build_stripped().text


def _hm(ts):
    if not ts:
        return "—"
    try:
        from datetime import datetime
        import config
        return datetime.fromtimestamp(int(ts), config.TZ).strftime("%H:%M")
    except Exception:
        return "—"


# ================= УВЕДОМЛЕНИЯ =================

def broadcast(next_title, next_when):
    """Экран «Уведомления»: только ближайшее автоматическое уведомление, без охвата."""
    b = MessageBuilder()
    b.bold(ui_label("notifications", "Уведомления"))
    b.newline()
    b.spacer()
    b.bold("Следующее")
    b.newline()
    b.line(next_title)
    b.line(next_when)
    b.spacer()
    b.line("Тест отправляется только вам.")
    return b.build_stripped()


def notification_picker(options):
    """options: [NotificationOption]. Список для выбора уведомления перед тестом."""
    b = MessageBuilder()
    b.bold(choose_label("Выбрать уведомление"))
    b.newline()
    b.spacer()
    for opt in options:
        b.line(opt.title)
        b.line(opt.schedule_label)
        b.spacer()
    return b.build_stripped()


# ================= ИНВАЙТ =================

def invite(link):
    b = MessageBuilder()
    b.bold(ui_label("invite", "Подарочный инвайт:"))
    b.newline()
    b.link(link, link)
    return b.build()
