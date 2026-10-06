#!/usr/bin/env bash
# Автообновление morning-bot на VM (запускается таймером morning-bot-update от root):
# fetch → при новых коммитах pip (если изменился requirements.txt) → fast-forward → restart.
# Установлен копией в /usr/local/sbin, чтобы root не исполнял файл, доступный на запись пользователю bot.
set -euo pipefail

REPO=/opt/morning-bot
VENV=/opt/morning-bot-venv
git_bot() { runuser -u bot -- git -C "$REPO" "$@"; }

git_bot fetch --quiet origin
LOCAL=$(git_bot rev-parse HEAD)
REMOTE=$(git_bot rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0

# Зависимости ставим до переключения кода: если pip упадёт, бот останется на старой версии,
# а следующий запуск таймера повторит попытку.
if ! git_bot diff --quiet "$LOCAL" "$REMOTE" -- requirements.txt; then
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT
    git_bot show "$REMOTE:requirements.txt" > "$tmp"
    chmod 644 "$tmp"
    runuser -u bot -- "$VENV/bin/pip" install --quiet -r "$tmp"
fi

git_bot merge --ff-only --quiet "$REMOTE"
systemctl restart morning-bot
echo "morning-bot updated ${LOCAL:0:7} -> ${REMOTE:0:7}"
