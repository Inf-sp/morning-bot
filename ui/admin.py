"""UI админ-панели — единый визуальный язык (§1 DOCS/admin-panel-redesign.md).

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

def home(system_dot, system_text, active, llm_calls, errors):
    b = MessageBuilder()
    b.bold("🛠 Администратор")
    b.newline()
    b.spacer()
    b.line(f"Система {system_dot} {system_text}")
    b.line(f"Сегодня: {active} активны · {llm_calls} LLM · {errors} ошибок")
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


# ================= АНАЛИТИКА =================

def analytics(period_label, m, top_sections):
    b = MessageBuilder()
    b.bold(f"📊 Аналитика · {period_label}")
    b.newline()
    b.spacer()
    b.metric("Активны", f"{m.get('active', 0)}  (7д: {m.get('active_7d', 0)})")
    b.metric("Сообщений", m.get("messages", 0))
    b.metric("Запросов к LLM", m.get("llm", 0))
    b.metric("Ср. ответ", f"{m.get('avg_ms', 0)} мс")
    b.metric("Ошибок", m.get("errors", 0))
    b.metric("Рассылок", m.get("broadcasts", 0))
    if top_sections:
        b.spacer()
        b.bold("Топ разделов")
        b.newline()
        for label, pct in top_sections:
            b.metric(label, f"{pct}%")
    return b.build_stripped()


# ================= LLM =================

def llm(status_dot, status_text, last_req, avg_ms, errors_today, last_provider, models):
    b = MessageBuilder()
    b.bold("🤖 LLM")
    b.newline()
    b.spacer()
    b.metric("Статус", f"{status_dot} {status_text}")
    b.metric("Последний запрос", last_req)
    b.metric("Ср. скорость", f"{avg_ms} мс")
    b.metric("Ошибок сегодня", errors_today)
    b.metric("Провайдер", last_provider or "—")
    if models:
        b.spacer()
        b.bold("Активные модели")
        b.newline()
        b.line(" · ".join(models))
    return b.build_stripped()


def llm_check(results):
    b = MessageBuilder()
    b.bold("🔍 Проверка провайдеров")
    b.newline()
    for label, ok, detail in results:
        b.spacer()
        b.line(f"{OK} {label}" if ok else f"{BAD} {label}: {detail}")
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


# ================= РАСХОДЫ =================

def cost(period_label, call_count, total_tokens, avg_tokens, providers, modules):
    b = MessageBuilder()
    b.bold(f"💰 Расходы · {period_label}")
    b.newline()
    b.spacer()
    if not call_count:
        b.line("Данных пока нет.")
        return b.build_stripped()
    b.metric("Запросов", _num(call_count))
    b.metric("Токенов (оценка)", _num(total_tokens))
    b.metric("Ср. токенов / запрос", avg_tokens)
    b.spacer()
    b.bold("Провайдеры (доля токенов)")
    b.newline()
    for label, configured, pct in providers:
        tag = f"{OK} ключ" if configured else f"{OFF} нет ключа"
        b.metric(label, f"{pct}%  {tag}")
    if modules:
        b.spacer()
        b.bold("По разделам (топ-5)")
        b.newline()
        for label, pct in modules:
            b.metric(label, f"{pct}%")
    return b.build_stripped()


# ================= СЕРВИСЫ =================

def services(rows, checked_when):
    """rows: [(dot, name, detail)]. detail: '340 мс' | '502 · 12 мин назад' | 'не настроен'."""
    b = MessageBuilder()
    b.bold("📡 Сервисы")
    b.newline()
    b.spacer()
    for dot, name, detail in rows:
        b.metric(f"{dot} {name}", detail)
    b.spacer()
    b.line(f"Проверено: {checked_when}")
    return b.build_stripped()


# ================= РАССЫЛКИ =================

def broadcasts(sent, recipients, errors, blocked, next_title, next_when, next_reach):
    b = MessageBuilder()
    b.bold("📢 Рассылки")
    b.newline()
    b.spacer()
    b.metric("Отправлено сегодня", sent)
    b.metric("Получателей", recipients)
    b.metric("Ошибок доставки", errors)
    b.metric("Заблокировали бота", blocked)
    if next_title:
        b.spacer()
        b.bold("Следующая")
        b.newline()
        b.line(next_title)
        b.line(f"{next_when} · ~{next_reach} чел")
    return b.build_stripped()


# ================= ЛОГИ =================

def logs(errors, active_filter="Все"):
    """errors: [(dot, when, source, msg)]."""
    b = MessageBuilder()
    b.bold("⚠️ Логи · последние ошибки")
    b.newline()
    b.spacer()
    if not errors:
        b.line(f"{OK} Ошибок нет.")
    for dot, when, source, msg in errors:
        b.line(f"{dot} {when} {source} · {msg}")
    b.spacer()
    b.line(f"Фильтр: {active_filter}")
    return b.build_stripped()


def log_detail(when, source, kind, msg):
    b = MessageBuilder()
    b.bold("🔎 Подробнее")
    b.newline()
    b.spacer()
    b.metric("Время", when)
    b.metric("Источник", source)
    b.metric("Тип", kind or "—")
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
