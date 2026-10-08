"""UI админ-панели — единый визуальный язык (§1 docs/admin.md).

Каждый экран: заголовок → ключевой статус → блок метрик (b.metric)
→ inline-кнопки. Метрики, которые пока держатся на новом трекинге, честно помечаем
маркером ⚠️, если данных ещё нет (значение 0/пусто) — но не скрываем строку.

Здесь только сборка текста. Логика/данные — в settings.py (send_admin_*).
"""
from .builder import MessageBuilder
from . import rich
from .constants import UI_EMOJI, ui_label


def deploy_report(version, title, release_notes):
    notes = [str(note).strip() for note in (release_notes or []) if str(note).strip()]
    if not notes:
        notes = ["Бот получил небольшие внутренние улучшения."]

    b = MessageBuilder()
    b.text_line(f"{UI_EMOJI['version']} ")
    b.bold(f"v{version}")
    b.text_line(f" · {title or 'Обновление'}")
    b.newline()
    b.bold("Что изменено:")
    b.newline()
    for note in notes[:4]:
        b.line(f"• {note}")
    b.spacer()
    b.text_line("Бот развёрнут и работает ✅")
    return b.build_stripped()


# ================= ДОМ =================

def home(status_dot=None, status_text=None, updated_at=None, stale=False,
         *, system_dot=None, system_text=None, system_line=None,
         notif_line=None, users_line=None, data_line=None, logs_line=None,
         system_rows=None, error_rows=None, version_line=None):
    """Render the admin home screen.

    The compact metric form is used by the current screen.  The older
    ``status_*`` form remains supported for callers that only have a health
    status available.
    """
    b = MessageBuilder()
    b.bold(ui_label("admin", "Админ"))
    b.newline()
    b.spacer()
    head = [line for line in (version_line,) if line]
    for line in head:
        b.line(line)
    if head:
        b.spacer()
    if system_rows is not None:
        rows = [str(row or "").strip() for row in system_rows if str(row or "").strip()]
        for row in rows:
            if row in ("AI", "Данные"):
                if row == "Данные":
                    b.spacer()
                b.bold("Мозг:" if row == "AI" else "Данные:")
                b.newline()
            else:
                b.line(row)
        errors = [
            str(row or "").strip() for row in error_rows or []
            if str(row or "").strip()
        ]
        if errors:
            b.spacer()
            b.bold("Ошибки:")
            b.newline()
            for row in errors:
                b.line(row)
        return b.build_stripped()
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


def card_refresh_menu(status=""):
    b = MessageBuilder()
    b.bold("🔄 Обновить карточки")
    b.newline()
    b.spacer()
    if status:
        b.line(status)
        b.spacer()
    b.line("Выбери карточку для принудительного обновления кэша.")
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


# ================= ПРОВЕРКА API =================

_API_CHECK_MARKS = {"ok": "✅", "fail": "❌", "limit": "⚠️"}


def api_check_row(result):
    parts = [f"{_API_CHECK_MARKS.get(result.get('status'), '❌')} {result.get('label') or '—'}"]
    seconds = result.get("seconds")
    if seconds is not None:
        parts.append(f"{float(seconds):.1f} с".replace(".", ","))
    if result.get("detail"):
        parts.append(str(result["detail"]))
    return " · ".join(parts)


def api_check(results):
    b = MessageBuilder()
    b.bold("🩺 Проверка API")
    b.newline()
    b.spacer()
    for result in results:
        b.line(api_check_row(result))
    return b.build_stripped()
