#!/usr/bin/env bash
# Отчёт о состоянии morning-bot на VM — чтобы прислать его в чат разработки.
# Запуск: sudo bash /opt/morning-bot/tools/vm_report.sh
# Секретов в отчёте нет: ключи — только «задан / пусто» и длина, в логах токены и ключи маскируются.
set -uo pipefail

REPO=/opt/morning-bot
VENV=/opt/morning-bot-venv
ENV_FILE=/etc/morning-bot.env
OUT=/tmp/morning-bot-report.txt

mask() {
    sed -E \
        -e 's/(sk-[A-Za-z0-9_-]{2})[A-Za-z0-9_-]{8,}/\1***/g' \
        -e 's/[0-9]{6,}:[A-Za-z0-9_-]{25,}/***telegram-token***/g' \
        -e 's/AIza[0-9A-Za-z_-]{20,}/AIza***/g' \
        -e 's/((api[_-]?key|apikey|token|key|secret|password|Bearer)["=: ]+)[A-Za-z0-9_.:\/+-]{8,}/\1***/Ig' \
        -e 's/(chat_id[=: ]+)-?[0-9]{5,}/\1***/Ig'
}

section() { printf '\n===== %s =====\n' "$1"; }

{
    section "Версия"
    runuser -u bot -- git -C "$REPO" log -1 --format='%h · %cd · %s' --date=format:'%Y-%m-%d %H:%M' 2>&1
    runuser -u bot -- git -C "$REPO" fetch --quiet origin 2>/dev/null
    printf 'отстаёт от origin: %s коммит(ов)\n' "$(runuser -u bot -- git -C "$REPO" rev-list --count 'HEAD..@{u}' 2>/dev/null || echo '?')"
    printf 'локальные изменения: %s файл(ов)\n' "$(runuser -u bot -- git -C "$REPO" status --porcelain 2>/dev/null | wc -l)"
    "$VENV/bin/python" --version 2>&1

    section "Сервисы"
    printf 'morning-bot: %s (с %s)\n' "$(systemctl is-active morning-bot)" \
        "$(systemctl show -p ActiveEnterTimestamp --value morning-bot)"
    printf 'перезапусков: %s\n' "$(systemctl show -p NRestarts --value morning-bot)"
    printf 'автообновление: %s, следующее — %s\n' "$(systemctl is-active morning-bot-update.timer)" \
        "$(systemctl show -p NextElapseUSecRealtime --value morning-bot-update.timer)"
    journalctl -u morning-bot-update --since "3 days ago" --no-pager -o cat 2>/dev/null | grep -iE "updated|error|fail" | tail -5

    section "Ресурсы"
    uptime
    free -m | head -2
    df -h / | tail -1
    DB=$(grep -E '^DATABASE_PATH=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"'"'")
    [ -n "$DB" ] && [ -f "$DB" ] && printf 'база: %s\n' "$(du -h "$DB" | cut -f1)"
    printf 'последняя копия базы: %s\n' "$(ls -t /var/backups/morning-bot/ 2>/dev/null | head -1)"

    section "Ключи в $ENV_FILE (без значений)"
    grep -vE '^\s*(#|$)' "$ENV_FILE" 2>/dev/null | while IFS='=' read -r name value; do
        value=${value%\"}; value=${value#\"}; value=${value%\'}; value=${value#\'}
        if [ -z "$value" ]; then state="ПУСТО"; else state="задан, ${#value} симв."; fi
        case "$value" in *" "*|*$'\r'*) state="$state, ЕСТЬ ПРОБЕЛ/CR";; esac
        printf '%-28s %s\n' "$name" "$state"
    done
    dups=$(grep -vE '^\s*(#|$)' "$ENV_FILE" 2>/dev/null | cut -d= -f1 | sort | uniq -d)
    [ -n "$dups" ] && printf 'ПОВТОРЯЮТСЯ: %s\n' "$dups"

    section "Проверка API и лимиты (как «🩺 Проверить API» в админке)"
    set -a; . "$ENV_FILE"; set +a
    cd "$REPO" && runuser -p -u bot -- "$VENV/bin/python" - <<'PY' 2>&1 | mask
import asyncio, logging
logging.disable(logging.CRITICAL)
import api_usage, config, service_monitor
for row in asyncio.run(service_monitor.live_check_all()):
    seconds = f"{row['seconds']:.1f} с" if row.get("seconds") is not None else ""
    print(f"{row['status']:<6} {row['label']:<16} {seconds:<7} {row.get('detail') or ''}")
print()
for service in config.API_FREE_LIMITS:
    state = api_usage.limit_state(service)
    if state:
        print(f"лимит {service:<13} {state['used']} из {state['limit']} ({state['period']})")
try:
    budget = api_usage.tavily_budget()
    print(f"лимит tavily        {budget.get('used')} из {budget.get('total')} (month)")
except Exception:
    pass
print()
for line in service_monitor.attention() or ["Нужно внимание: ничего"]:
    print("⚠️ " + line)
PY

    section "Ошибки и предупреждения за 24 ч (топ повторов)"
    journalctl -u morning-bot --since "24 hours ago" -p warning --no-pager -o cat 2>/dev/null \
        | mask | sed -E 's/[0-9]{2,}/N/g' | cut -c1-160 | sort | uniq -c | sort -rn | head -40

    section "Последние 30 строк лога"
    journalctl -u morning-bot -n 30 --no-pager -o cat 2>/dev/null | mask | cut -c1-200
} > "$OUT" 2>&1

cat "$OUT"
printf '\nОтчёт сохранён: %s — пришли его в чат целиком.\n' "$OUT"
