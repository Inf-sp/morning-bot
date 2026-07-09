"""UI админ-панели — единый визуальный язык (§1 docs/admin.md).

Каждый экран: заголовок → ключевой статус → блок метрик (b.metric)
→ inline-кнопки. Метрики, которые пока держатся на новом трекинге, честно помечаем
маркером ⚠️, если данных ещё нет (значение 0/пусто) — но не скрываем строку.

Здесь только сборка текста. Логика/данные — в settings.py (send_admin_*).
"""
from .builder import MessageBuilder, MessageSpec
from .constants import STATUS_EMOJI, UI_EMOJI, ui_label

# --- статус-точки (единственные допустимые «светофоры») ---
OK, WARN, BAD, OFF = STATUS_EMOJI["ok"], STATUS_EMOJI["warn"], STATUS_EMOJI["bad"], "□"


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
    b.bold(f"{UI_EMOJI['version']} v{version} · {title or 'Обновление'}")
    b.newline()
    b.spacer()
    b.bold("Что изменено:")
    for note in change_notes[:4]:
        b.line(f"• {note}")
    b.spacer()
    b.italic("Бот развёрнут и работает ✅")
    return b.build_stripped()


# ================= ДОМ =================

def home(system_dot, system_text, system_line, notif_line, users_line, data_line, updated_at):
    b = MessageBuilder()
    b.bold(ui_label("admin", "Админ"))
    b.newline()
    b.spacer()
    b.line(f"{system_dot} {system_text}")
    b.spacer()
    b.line(f"{ui_label('system', 'Система')}: {system_line}")
    b.line(f"{ui_label('notifications', 'Уведомления')}: {notif_line}")
    b.line(f"{ui_label('users', 'Пользователи')}: {users_line}")
    b.line(f"Данные: {data_line}")
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


# ================= ПОЛЬЗОВАТЕЛИ =================

def users(stats, updated_at):
    b = MessageBuilder()
    b.bold(ui_label("users", "Пользователи"))
    b.newline()
    b.spacer()
    b.line(f"Всего: {stats.get('total', 0)}")
    b.line(f"Активны за 7 дней: {stats.get('active_7d', 0)}")
    b.line(f"Новых за 7 дней: {stats.get('new_7d', 0)}")
    b.line(f"С уведомлениями: {stats.get('with_notifications', 0)}")
    b.line(f"Админов: {stats.get('admins', 0)}")
    b.spacer()
    b.line("Инвайты:")
    b.line(f"Активных: {stats.get('active_invites', 0)}")
    b.line(f"Использовано: {stats.get('used_invites', 0)}")
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def invite_prompt():
    b = MessageBuilder()
    b.bold(ui_label("invite", "Инвайт"))
    b.newline()
    b.spacer()
    b.line("Создать ссылку для нового пользователя.")
    b.spacer()
    b.line("Срок: 7 дней")
    b.line("Лимит: 1 пользователь")
    b.spacer()
    b.line("После входа пользователь получит приветствие.")
    return b.build_stripped()


def invite_created(link):
    b = MessageBuilder()
    b.bold("✅ Инвайт создан")
    b.newline()
    b.spacer()
    b.line("Срок: 7 дней")
    b.line("Лимит: 1 пользователь")
    b.spacer()
    b.line("Ссылка:")
    b.line(link)
    b.spacer()
    b.line("Новый пользователь получит приветствие после входа.")
    return b.build_stripped()


def welcome_admin():
    b = MessageBuilder()
    b.bold(ui_label("welcome", "Приветствие"))
    b.newline()
    b.spacer()
    b.line("Текст, который увидит новый пользователь после входа:")
    b.spacer()
    b.line("Привет! Я персональный помощник Дмитрия.")
    b.line("Я помогаю с погодой, одеждой, обучением, рецептами, досугом и важными напоминаниями.")
    b.spacer()
    b.line("Бот работает в тестовом режиме.")
    b.line("Если что-то сломалось или ответ выглядит странно - напишите администратору.")
    return b.build_stripped()


def system(rows, updated_at):
    b = MessageBuilder()
    b.bold(ui_label("system", "Система"))
    b.newline()
    b.spacer()
    for line in rows:
        b.line(line)
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def api_ai(status_ok, fallback_on, problem_line, ai_rows, api_rows, feature_rows,
           last_error_line, updated_at):
    """Единый экран диагностики: § docs/admin.md «API и AI».

    status_ok: bool - общий статус (работает / есть проблемы).
    fallback_on: bool - хоть один provider сейчас работает через fallback.
    problem_line: str|None - короткое описание главной проблемы, если есть.
    ai_rows / api_rows: list[str] - готовые строки "Сервис · статус · деталь".
    feature_rows: list[str] - готовые строки "Раздел · провайдеры · статус".
    last_error_line: str|None.
    """
    b = MessageBuilder()
    b.bold("🔌 API и AI")
    b.newline()
    b.spacer()
    b.line(f"Статус: {'работает' if status_ok else 'есть проблемы'}")
    b.line(f"Fallback: {'включён' if fallback_on else 'выключен'}")
    if problem_line:
        b.line(f"Проблема: {problem_line}")
    b.spacer()
    b.line("AI:")
    for line in ai_rows:
        b.line(line)
    b.spacer()
    b.line("API:")
    for line in api_rows:
        b.line(line)
    b.spacer()
    b.line("Функции:")
    for line in feature_rows:
        b.line(line)
    if last_error_line:
        b.spacer()
        b.line("Последняя ошибка:")
        b.line(last_error_line)
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def notifications(sent_today, errors_today, active_types, updated_at):
    b = MessageBuilder()
    b.bold(ui_label("notifications", "Уведомления"))
    b.newline()
    b.spacer()
    b.line(f"Сегодня: {sent_today} отправлено · {errors_today} ошибок")
    b.line(f"Активных типов: {active_types}")
    b.spacer()
    b.line("Тесты отправляются только вам.")
    b.spacer()
    b.line("Выберите уведомление для теста:")
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def tests(history):
    b = MessageBuilder()
    b.bold(ui_label("tests", "Тесты"))
    b.newline()
    b.spacer()
    b.line("Тесты отправляются только вам.")
    b.spacer()
    b.line("Последние:")
    if history:
        for row in history[:3]:
            b.line(row)
    else:
        b.line("—")
    b.spacer()
    b.line("Выберите тест:")
    return b.build_stripped()


def test_result(ok, when, label, detail):
    b = MessageBuilder()
    b.bold("✅ Тест отправлен" if ok else "🔴 Тест не прошёл")
    b.newline()
    b.spacer()
    b.line(f"{when} · {label} · {detail}")
    b.line("Получатель: только админ")
    return b.build_stripped()


def logs(rows, errors_24h, updated_at, summary=None):
    summary = summary or {"errors": errors_24h}
    b = MessageBuilder()
    b.bold(ui_label("logs", "Логи"))
    b.newline()
    b.spacer()
    cooldown_active = bool(summary.get("cooldown_active"))
    if cooldown_active:
        until = _hm(summary.get("cooldown_until"))
        b.line(f"🔴 Gemini на паузе до {until}")
        b.line("Бот работает через fallback, лимит не превышается повторно.")
    else:
        b.line(f"{OK} Gemini работает")
    b.spacer()
    if not rows:
        b.line("Ошибок за 24 часа нет")
    else:
        b.line("Последние ошибки:")
        b.spacer()
        for row in rows:
            b.line(row)
        b.spacer()
        b.line("За 24 часа:")
        b.line(
            f"лимитов {summary.get('rate_limits', 0)}"
            f" · fallback {summary.get('fallbacks', 0)}"
            f" · записей {summary.get('errors', errors_24h)}"
        )
        if summary.get("last_429_at"):
            b.line(f"последний лимит {_hm(summary.get('last_429_at'))}")
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


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
    b.metric("Рассылки", f"{notif_on} из {notif_total} вкл")
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
    b.line(f"Запросы: {total} / {config.WEATHER_FREE_DAILY_LIMIT:,}".replace(",", " "))
    b.line(f"Успешно: {success} · Ошибки: {failed} · Повторы: {retry}")
    b.line(f"Из кэша: {cache_hits}")
    b.line(f"Осталось бесплатно: {left}")
    b.line(f"Последний запрос: {_weather_ts_hhmm(usage.get('last_request_at'))}")
    if usage.get("last_error_reason"):
        b.line(f"Последняя ошибка: {usage.get('last_error_reason')}")
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
    b.bold(ui_label("notifications", "Выберите уведомление"))
    b.newline()
    b.spacer()
    for opt in options:
        b.line(opt.title)
        b.line(opt.schedule_label)
        b.spacer()
    return b.build_stripped()


# ================= ПРОБЛЕМЫ =================

def issues(rows, checked_when):
    """rows: [(dot, name, detail)]. Пусто -> «проблем нет»."""
    b = MessageBuilder()
    b.bold("⚠️ Проблемы")
    b.newline()
    b.spacer()
    if not rows:
        b.line(f"{OK} Открытых проблем нет")
        b.spacer()
        b.line(f"Последняя проверка · {checked_when}")
        return b.build_stripped()
    for dot, name, detail in rows:
        b.line(f"{dot} {name}")
        b.line(detail)
        b.spacer()
    return b.build_stripped()


def issue_detail(when, source, dot, msg):
    b = MessageBuilder()
    b.bold("🔍 Подробнее")
    b.newline()
    b.spacer()
    b.metric("Время", when)
    b.metric("Источник", f"{dot} {source}")
    b.spacer()
    b.code(msg or "—")
    return b.build_stripped()


# ================= ИНВАЙТ =================

def invite(link):
    b = MessageBuilder()
    b.bold(ui_label("invite", "Подарочный инвайт:"))
    b.newline()
    b.link(link, link)
    return b.build()
