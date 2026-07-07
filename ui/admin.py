"""UI админ-панели — единый визуальный язык (§1 docs/admin.md).

Каждый экран: заголовок (эмодзи + раздел) → ключевой статус → блок метрик (b.metric)
→ inline-кнопки. Метрики, которые пока держатся на новом трекинге, честно помечаем
маркером ⚠, если данных ещё нет (значение 0/пусто) — но не скрываем строку.

Здесь только сборка текста. Логика/данные — в settings.py (send_admin_*).
"""
from .builder import MessageBuilder, MessageSpec

# --- статус-точки (единственные допустимые «светофоры») ---
OK, WARN, BAD, OFF = "🟢", "🟡", "🔴", "⚪"


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
    return MessageSpec(text="⛔ Только для администратора.")


# ================= ДОМ =================

def home(system_dot, system_text, total_users, active_7d, llm_calls_today, llm_tokens_today,
         next_broadcast_title, next_broadcast_when,
         issues_count, top_issue):
    b = MessageBuilder()
    b.bold("🛠 Администратор")
    b.newline()
    b.spacer()
    b.line(f"{system_dot} {system_text}")
    b.line(f"{total_users} пользователей · {active_7d} активных за 7 дней")
    b.spacer()
    b.line(f"🤖 {_num(llm_calls_today)} LLM-запросов сегодня · ~{_num(llm_tokens_today)} токенов")
    if next_broadcast_title:
        b.line(f"🔔 {next_broadcast_title} — {next_broadcast_when}")
    b.spacer()
    if issues_count:
        b.line(f"⚠️ {issues_count} открытые проблемы")
        if top_issue:
            b.line(top_issue)
    else:
        b.line("🟢 Открытых проблем нет")
    return b.build_stripped()


# ================= ПОЛЬЗОВАТЕЛИ =================

def users(stats, last_user):
    """stats: dict метрик. last_user: (dot, name, city, action, when) | None."""
    b = MessageBuilder()
    b.bold("👥 Пользователи")
    b.newline()
    b.spacer()
    b.metric("Всего", stats.get("total", 0))
    b.metric("Новых сегодня", f"+{stats.get('new_today', 0)}")
    b.metric("Активны сегодня", stats.get("active_1d", 0))
    b.metric("Активны 7 дней", stats.get("active_7d", 0))
    b.metric("Онбординг пройден", stats.get("onboarded", 0))
    b.metric("Не завершили", stats.get("not_onboarded", 0))
    b.metric("Все рассылки off", stats.get("all_off", 0))
    b.metric("Сообщений / юзер", stats.get("avg_msgs", 0))
    if last_user:
        dot, name, city, action, when = last_user
        b.spacer()
        b.bold("Последняя активность")
        b.newline()
        city_part = f" · {city}" if city else ""
        b.line(f"{dot} {name}{city_part}")
        if action:
            b.line(f"   {action}")
        b.line(f"   {when}")
    return b.build_stripped()


def user_card(name, city, cid, onboarded, last_seen, active_days, total_msgs, notif_on, notif_total):
    b = MessageBuilder()
    city_part = f" · {city}" if city else ""
    b.bold(f"👤 {name}{city_part}")
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


# ================= LLM =================

def llm(status_dot, status_text, last_req, avg_ms, errors_today, calls_today, tokens_today, providers):
    """providers: [(label, pct)] доля токенов за сегодня."""
    b = MessageBuilder()
    b.bold("🤖 LLM")
    b.newline()
    b.spacer()
    b.metric("Статус", f"{status_dot} {status_text}")
    b.metric("Последний запрос", last_req)
    b.metric("Запросов сегодня", calls_today)
    b.metric("Токенов сегодня", f"~{_num(tokens_today)}")
    b.metric("Ср. ответ", f"{avg_ms} мс")
    b.metric("Ошибок сегодня", errors_today)
    if providers:
        b.spacer()
        b.line(" · ".join(f"{label} {pct}%" for label, pct in providers))
    return b.build_stripped()


def llm_check(results):
    b = MessageBuilder()
    b.bold("🔍 Проверка провайдеров")
    b.newline()
    for label, ok, detail in results:
        b.spacer()
        b.line(f"{OK} {label}" if ok else f"{BAD} {label}: {detail}")
    return b.build_stripped()


def api_check(results):
    b = MessageBuilder()
    b.bold("🔍 Проверка API")
    b.newline()
    for label, ok, detail in results:
        b.spacer()
        if ok:
            b.line(f"{OK} {label}")
        elif detail:
            b.line(f"{BAD} {label}: {detail}")
        else:
            b.line(f"{BAD} {label}")
    return b.build_stripped()


def llm_history(rows):
    """rows: [(when, provider, module, ok)]."""
    b = MessageBuilder()
    b.bold("🕘 История запросов")
    b.newline()
    b.spacer()
    if not rows:
        b.line("Пока пусто.")
    for when, provider, module, ok in rows:
        dot = OK if ok else BAD
        mod = f" · {module}" if module else ""
        b.line(f"{dot} {when} · {provider}{mod}")
    return b.build_stripped()


# ================= УВЕДОМЛЕНИЯ =================

def broadcast(next_title, next_when):
    """Экран «Уведомления»: только ближайшее автоматическое уведомление, без охвата."""
    b = MessageBuilder()
    b.bold("🔔 Уведомления")
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
    b.bold("🧪 Выберите уведомление")
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
    b.bold("🔎 Подробнее")
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
    b.text_line("🔗 ")
    b.bold("Подарочный инвайт:")
    b.newline()
    b.link(link, link)
    return b.build()
