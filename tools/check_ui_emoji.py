from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]

# These are the old UI duplicates explicitly banned by the UI emoji spec.
# Domain-specific emoji such as weather phenomena or country flags are audited
# in docs/ui-emoji-inventory.md and should be migrated separately when their
# screen is touched.
BANNED_UI_EMOJI = {
    "🧳": "use ✈️ for travel or 🗺 for routes",
    "🍳": "use 🍽 for recipes",
    "🎵": "use 🎧 for music",
    "💾": "use ⭐️ for save",
    "➕": "use ✏️ for add or 🔗 for invite",
    "🗑": "use ❌ for delete",
    "⚙️": "use 🎚️ for settings",
    "🎤": "use 🎧 for music/artists or 🎸 for concerts",
}

ALLOWED_FILES = {
    "ui/constants.py",
    "docs/ui-emoji-inventory.md",
    "tools/check_ui_emoji.py",
}


def iter_python_lines():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or rel in ALLOWED_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            yield rel, lineno, line


def main():
    failures = []
    for rel, lineno, line in iter_python_lines():
        for emoji, reason in BANNED_UI_EMOJI.items():
            if emoji in line:
                failures.append((rel, lineno, emoji, reason, line.strip()))

    if failures:
        for rel, lineno, emoji, reason, line in failures:
            print(f"{rel}:{lineno}: banned {emoji}: {reason}: {line}")
        return 1

    bad_back = re.compile(r"Назад в|Назад к|⬅️ Система|⬅️ Меню|◀️ Назад|◀ Назад")
    for rel, lineno, line in iter_python_lines():
        if bad_back.search(line):
            print(f"{rel}:{lineno}: back button must be exactly ⬅️ Назад: {line.strip()}")
            return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
