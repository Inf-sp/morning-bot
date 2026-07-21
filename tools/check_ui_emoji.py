from pathlib import Path
import ast
import re
import sys


ROOT = Path(__file__).resolve().parents[1]

SKIP_FILES = {
    "ui/constants.py",
    "tools/check_ui_emoji.py",
}

WEATHER_FILES = {
    "weather.py",
    "weather_warn.py",
    "myday.py",
    "ui/weather.py",
    "ui/myday.py",
}

# Explicitly allowed action/status emoji. These are not decorative UI entities.
ACTION_EMOJI = {
    "⬅️",
    "🏠",
    "#️⃣",
    "🆕",
    "✏️",
    "✅",
    "❌",
    "⭐️",
    "❤️",
    "✨",
    "⚠️",
    "🔥",
    "👀",
    "😋",
    "📤",
    "💡",
    "😞",
    "🩹",
    "🫣",
    "🔕",
    "⏳",
    "▶️",
    "◀️",
    "□",
}

# These are old UI duplicates explicitly banned by the UI emoji spec. Mentions in
# this file are the denylist itself, not UI usage.
BANNED_UI_EMOJI = {
    "🔎": "use 🔍",
    "✍": "use ✏️",
    "✍️": "use ✏️",
    "✍🏻": "use ✏️",
    "🗑": "use ❌",
    "💾": "use ⭐️",
    "➕": "use 🆕 or 🔗",
    "⚙️": "use 🎚️",
    "🎵": "use 🎧",
    "🍳": "use 🍽",
    "👇": "remove decorative pointer",
    "🧹": "remove decorative cleanup icon",
    "🟠": "use 🟡",
    "👤": "remove decorative user icon",
    "🏳": "use no fallback flag",
    "🏳️‍🌈": "use text label",
}

EMOJI_RE = re.compile(
    r"(?:"
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[\U0001F300-\U0001FAFF][\ufe0f]?(?:\u200d[\U0001F300-\U0001FAFF][\ufe0f]?)*"
    r"|[\u2300-\u23FF][\ufe0f]?"
    r"|[\u2460-\u24FF][\ufe0f]?"
    r"|[\u25A0-\u25FF][\ufe0f]?"
    r"|[\u2600-\u27BF][\ufe0f]?"
    r"|⬅️|▶️|◀️"
    r")"
)


def _literal_dicts_from_constants():
    path = ROOT / "ui/constants.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dicts = {}
    env = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], "id", "")
        if name not in {
            "UI_EMOJI",
            "CUISINE_EMOJI",
            "LANGUAGE_EMOJI",
            "COUNTRY_EMOJI",
            "WEATHER_EMOJI",
            "STATUS_EMOJI",
        }:
            continue
        try:
            value = eval(compile(ast.Expression(node.value), str(path), "eval"), {}, env)
        except Exception:
            value = ast.literal_eval(node.value)
        dicts[name] = value
        env[name] = value
    return dicts


def _emoji_values(mapping):
    out = set()
    for value in mapping.values():
        if isinstance(value, str):
            out.update(EMOJI_RE.findall(value))
    return out


def iter_python_lines():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or ".claude" in path.parts or rel in SKIP_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            yield rel, lineno, line


def _is_absence_test(rel, line):
    if not rel.startswith("tools/") and not rel.startswith("tests/"):
        return False
    return "not in" in line or "not label.startswith" in line or "BANNED_UI_EMOJI" in line


def main():
    dicts = _literal_dicts_from_constants()
    central = set()
    for name in ("UI_EMOJI", "CUISINE_EMOJI", "LANGUAGE_EMOJI", "COUNTRY_EMOJI", "STATUS_EMOJI"):
        central |= _emoji_values(dicts.get(name, {}))
    weather = _emoji_values(dicts.get("WEATHER_EMOJI", {}))
    allowed_anywhere = central | ACTION_EMOJI

    banned_failures = []
    outside = {}
    bad_weather = []
    bad_back = []
    bad_add = []
    bad_menu = []
    bad_assessment = []

    for rel, lineno, line in iter_python_lines():
        if _is_absence_test(rel, line):
            continue
        for emoji, reason in BANNED_UI_EMOJI.items():
            if emoji in line:
                banned_failures.append((rel, lineno, emoji, reason, line.strip()))

        if "InlineKeyboardButton" in line and "Назад" in line and "⬅️ Назад" not in line:
            bad_back.append((rel, lineno, line.strip()))
        if "🏠 Меню" in line:
            bad_menu.append((rel, lineno, line.strip()))
        is_wardrobe_assessment = (
            "Оценка" in line
            and ("w_check" in line or "b.section" in line)
        )
        if is_wardrobe_assessment and "🧐" not in line and '"assessment"' not in line:
            bad_assessment.append((rel, lineno, line.strip()))

        is_add_button = (
            "Добав" in line
            and "Не добав" not in line
            and (
                "InlineKeyboardButton" in line
                or "add_button=" in line
                or re.search(r"[\[(]\s*\(\s*[f]?[\"'].*Добав", line)
            )
        )
        if is_add_button and "🆕 Добав" not in line:
            bad_add.append((rel, lineno, line.strip()))

        for emoji in EMOJI_RE.findall(line):
            if emoji in weather:
                if rel not in WEATHER_FILES and emoji not in central:
                    bad_weather.append((rel, lineno, emoji, line.strip()))
                continue
            if emoji in allowed_anywhere:
                continue
            outside.setdefault(emoji, []).append((rel, lineno, line.strip()))

    if banned_failures:
        for rel, lineno, emoji, reason, line in banned_failures:
            print(f"{rel}:{lineno}: banned {emoji}: {reason}: {line}")
    if bad_back:
        for rel, lineno, line in bad_back:
            print(f"{rel}:{lineno}: back button must be exactly ⬅️ Назад: {line}")
    if bad_add:
        for rel, lineno, line in bad_add:
            print(f"{rel}:{lineno}: add button must start with 🆕: {line}")
    if bad_menu:
        for rel, lineno, line in bad_menu:
            print(f"{rel}:{lineno}: main menu button must be exactly #️⃣ Главная: {line}")
    if bad_assessment:
        for rel, lineno, line in bad_assessment:
            print(f"{rel}:{lineno}: wardrobe assessment must use 🧐: {line}")
    if bad_weather:
        for rel, lineno, emoji, line in bad_weather:
            print(f"{rel}:{lineno}: weather emoji {emoji} outside weather context: {line}")
    if outside:
        print("Emoji outside dictionaries/allowlist:")
        for emoji, hits in sorted(outside.items()):
            first = hits[0]
            print(f"{emoji}: {len(hits)} first={first[0]}:{first[1]} {first[2]}")

    print(f"Unique emoji sequences outside dictionaries: {len(outside)}")
    if banned_failures or bad_back or bad_add or bad_menu or bad_assessment or bad_weather or outside:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
