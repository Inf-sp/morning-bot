"""UI админ-панели — единый визуальный язык (§1 docs/admin.md).

Каждый экран: заголовок → ключевой статус → блок метрик (b.metric)
→ inline-кнопки. Метрики, которые пока держатся на новом трекинге, честно помечаем
маркером ⚠️, если данных ещё нет (значение 0/пусто) — но не скрываем строку.

Здесь только сборка текста. Логика/данные — в settings.py (send_admin_*).
"""
from .builder import MessageBuilder, MessageSpec
from .constants import STATUS_EMOJI, ui_label

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
    b.bold(f"v{version} · {title or 'Обновление'}")
    b.newline()
    b.spacer()
    b.line("Что изменено:")
    for note in change_notes[:4]:
        b.line(f"• {note}")
    b.spacer()
    b.line("Бот развёрнут и работает ✅")
    return b.build_stripped()


# ================= ДОМ =================

def home(system_dot, system_text, system_line, notif_line, users_line, data_line, updated_at):
    b = MessageBuilder()
    b.bold("Админ")
    b.newline()
    b.spacer()
    b.line(f"{system_dot} {system_text}")
    b.spacer()
    b.line(f"{ui_label('system', 'Система')}: {system_line}")
    b.line(f"Уведомления: {notif_line}")
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
    b.bold("Приветствие")
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


def diagnostics():
    b = MessageBuilder()
    b.bold("Диагностика")
    b.newline()
    b.spacer()
    b.line("Выберите раздел:")
    b.spacer()
    b.line("API - лимиты, ошибки, последние ответы")
    b.line("LLM - провайдеры, fallback, токены")
    b.line("Новости - Tavily, фильтрация, кэш")
    b.line("Логи - последние события и ошибки")
    return b.build_stripped()


def api_diagnostics_compact(rows, updated_at):
    b = MessageBuilder()
    b.bold("API · Диагностика")
    b.newline()
    if not rows:
        b.spacer()
        b.line("Пока нет сохранённых реальных API-вызовов.")
    for label, lines in rows:
        b.spacer()
        b.line(f"{label}:")
        for line in lines:
            b.line(line)
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def llm_diagnostics(calls_today, tokens_today, errors_today, providers, fallback_text, problem, updated_at):
    b = MessageBuilder()
    b.bold("LLM · Диагностика")
    b.newline()
    b.spacer()
    b.line("Сегодня:")
    b.line(f"{calls_today} запросов")
    b.line(f"~{_num(tokens_today)} токенов")
    b.line(f"{errors_today} ошибок")
    if problem:
        b.spacer()
        b.line("Проблема:")
        b.line(problem)
    b.spacer()
    b.line("Провайдеры:")
    b.line(providers or "—")
    b.spacer()
    b.line("Fallback:")
    b.line(fallback_text)
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def news_diagnostics(today_credits, daily_limit, month_credits, month_limit,
                     cache_hits, last_build, errors, updated_at):
    b = MessageBuilder()
    b.bold("Новости · Диагностика")
    b.newline()
    b.spacer()
    b.line("Tavily:")
    b.line(f"{today_credits}/{daily_limit} сегодня")
    b.line(f"{month_credits}/{month_limit} месяц")
    b.spacer()
    b.line("Кэш:")
    b.line(f"{cache_hits} попадания")
    b.spacer()
    b.line("Сборка:")
    b.line(f"последняя {last_build}")
    b.spacer()
    b.line("Ошибки:")
    b.line(str(errors))
    b.spacer()
    b.line(f"Обновлено: {updated_at}")
    return b.build_stripped()


def notifications(sent_today, errors_today, active_types, updated_at):
    b = MessageBuilder()
    b.bold("Уведомления")
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
    b.bold("Тесты")
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
    if not rows:
        b.line(f"{OK} Ошибок за 24 часа нет")
    else:
        b.line("Последние ошибки:")
        b.spacer()
        for row in rows:
            b.line(row)
        b.spacer()
        b.line("За 24 часа:")
        b.line(
            f"ошибок {summary.get('errors', errors_24h)}"
            f" · лимитов {summary.get('rate_limits', 0)}"
            f" · fallback {summary.get('fallbacks', 0)}"
        )
        if summary.get("last_429_at"):
            cd = "активен" if summary.get("cooldown_active") else "нет"
            until = f" до {_hm(summary.get('cooldown_until'))}" if summary.get("cooldown_active") else ""
            b.line(f"последний 429 {_hm(summary.get('last_429_at'))} · cooldown {cd}{until}")
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


# ================= LLM =================

def llm(status_dot, status_text, last_req, avg_ms, errors_today, calls_today, tokens_today, providers,
        openrouter_fallback=None):
    """providers: [(label, pct)] доля токенов за сегодня."""
    b = MessageBuilder()
    b.bold("LLM")
    b.newline()
    b.spacer()
    b.metric("Статус", f"{status_dot} {status_text}")
    b.metric("Последний запрос", last_req)
    b.metric("Запросов сегодня", calls_today)
    b.metric("Токенов сегодня", f"~{_num(tokens_today)}")
    b.metric("Ср. ответ", f"{avg_ms} мс")
    b.metric("Ошибок сегодня", errors_today)
    if openrouter_fallback is not None:
        b.spacer()
        b.bold("OpenRouter fallback")
        b.newline()
        b.line(f"• попыток: {openrouter_fallback.get('attempts', 0)}")
        b.line(f"• успешно: {openrouter_fallback.get('success', 0)}")
        b.line(f"• ошибок: {openrouter_fallback.get('errors', 0)}")
    if providers:
        b.spacer()
        b.line(" · ".join(f"{label} {pct}%" for label, pct in providers))
    return b.build_stripped()


def llm_check(results):
    b = MessageBuilder()
    b.bold("🔍 Проверка провайдеров")
    b.newline()
    for result in results:
        label, ok, detail = result[:3]
        dot = result[3] if len(result) > 3 else (OK if ok else BAD)
        b.spacer()
        b.line(f"{dot} {label}" if ok else f"{dot} {label}: {detail}")
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


def _dot(status):
    return {"ok": OK, "warn": WARN, "bad": BAD, "off": OFF}.get(status, OFF)


def _unit_word(unit):
    return {
        "requests": "запросов",
        "credits": "кредитов",
        "tokens": "токенов",
        "messages": "отправок",
    }.get(unit, unit)


def _period_word(period):
    return {
        "minute": "мин",
        "hour": "час",
        "day": "сегодня",
        "month": "месяц",
    }.get(period, period)


def _quota_line(row):
    unit = row.get("unit")
    period = row.get("period")
    used = int(row.get("used") or 0)
    limit = row.get("limit")
    def fmt(n):
        return f"{int(n):,}".replace(",", " ")
    if limit:
        limit = int(limit)
        if period == "day":
            return f"{fmt(used)} / {fmt(limit)} сегодня · осталось {fmt(max(0, limit - used))}"
        if period == "minute":
            return f"{fmt(used)} / {fmt(limit)} за мин"
        if unit == "credits" and period == "month":
            return f"{fmt(used)} / {fmt(limit)} кредитов в этом месяце"
        return f"{fmt(used)} / {fmt(limit)} за {_period_word(period)}"
    return f"{fmt(used)} {_unit_word(unit)} {_period_word(period)}"


def _main_quota_services(services):
    return [s for s in services if s.get("service") in {"openweather", "gemini", "pexels", "tavily"}]


def _local_services(services):
    return [s for s in services if s.get("service") not in {"openweather", "gemini", "pexels", "tavily"}]


def api_check(snapshot):
    b = MessageBuilder()
    b.bold("🔍 Проверка API")
    b.newline()
    b.line(f"Обновлено: {_hm((snapshot or {}).get('updated_at'))}")
    services = (snapshot or {}).get("services") or []
    if not services:
        b.spacer()
        b.line("Пока нет сохранённых реальных API-вызовов.")
        return b.build_stripped()

    main = _main_quota_services(services)
    local = _local_services(services)
    if main:
        b.spacer()
        b.bold(ui_label("system", "Использование"))
        b.newline()
        for svc in main:
            b.spacer()
            b.bold(f"{svc.get('icon')} {svc.get('label')}")
            b.newline()
            if svc.get("service") == "gemini":
                b.line(f"{_num(svc.get('day_requests', 0))} запроса сегодня · лимит 5/мин")
                if int(svc.get("cooldown_until") or 0) > 0 and int(svc.get("cooldown_until") or 0) > __import__("time").time():
                    b.line(f"🟡 cooldown до {_hm(svc.get('cooldown_until'))}")
                elif svc.get("last_ok") is False and svc.get("last_429_at"):
                    b.line("🔴 лимит запросов")
                elif svc.get("last_error_reason") and any(x in str(svc.get("last_error_reason")).lower() for x in ("401", "403", "key", "access")):
                    b.line("🔴 ошибка ключа / доступа")
                else:
                    b.line("🟢 работает")
                continue
            elif svc.get("service") == "tavily":
                quota = next((q for q in svc.get("quotas", []) if q.get("unit") == "credits"), None)
                b.line(_quota_line(quota) if quota else f"{_num(svc.get('month_credits', 0))} кредитов за месяц")
            elif svc.get("service") == "pexels":
                quotas = svc.get("quotas") or []
                if len(quotas) >= 2:
                    b.line(f"{_quota_line(quotas[0])} · {_quota_line(quotas[1])}")
                elif quotas:
                    b.line(_quota_line(quotas[0]))
            else:
                quota = (svc.get("quotas") or [{}])[0]
                b.line(_quota_line(quota))
            b.line(f"{_dot(svc.get('status'))} {svc.get('status_text')}")

    if local:
        b.spacer()
        b.bold("Без общей квоты")
        b.newline()
        for svc in local:
            label = svc.get("label")
            status = _dot(svc.get("status"))
            if svc.get("day_tokens"):
                b.line(f"{status} {label} · {_num(svc.get('day_tokens'))} токенов сегодня")
            elif svc.get("day_messages"):
                b.line(f"{status} {label} · {_num(svc.get('day_messages'))} отправок сегодня")
            elif svc.get("day_requests"):
                b.line(f"{status} {label} · {_num(svc.get('day_requests'))} запросов сегодня")
            else:
                b.line(f"{status} {label}")
    return b.build_stripped()


def api_diagnostics(snapshot):
    b = MessageBuilder()
    b.bold("Диагностика API")
    b.newline()
    for svc in (snapshot or {}).get("services") or []:
        b.spacer()
        b.bold(f"{_dot(svc.get('status'))} {svc.get('label')}")
        b.newline()
        b.line(f"Успешные запросы сегодня: {_num(svc.get('day_requests', 0))}")
        if svc.get("cache_hits"):
            b.line(f"Кэш: {_num(svc.get('cache_hits'))}")
        if svc.get("avg_latency_ms"):
            b.line(f"Средний ответ: {svc.get('avg_latency_ms')} мс")
        b.line(f"Последний API-вызов: {_hm(svc.get('last_request_at'))}")
        if svc.get("last_error_reason"):
            b.line(f"Последняя ошибка: {svc.get('last_error_reason')}")
        if svc.get("rate_limit_errors"):
            b.line(f"Rate-limit ошибок: {svc.get('rate_limit_errors')}")
        errors = svc.get("errors") or []
        if errors:
            b.line("Последние сбои:")
            for err in errors[-3:]:
                code = err.get("status_code") or "n/a"
                b.line(f"{_hm(err.get('ts'))} · HTTP {code} · {err.get('reason') or 'error'}")
    if not ((snapshot or {}).get("services") or []):
        b.spacer()
        b.line("Пока нет сохранённых реальных API-вызовов.")
    return b.build_stripped()

def llm_history(rows):
    """rows: [(when, provider, module, ok)]."""
    b = MessageBuilder()
    b.bold("История запросов")
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
    b.bold("Уведомления")
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
    b.bold("Выберите уведомление")
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
