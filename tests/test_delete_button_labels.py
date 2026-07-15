import os
import re
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cleanup


ROOT = Path(__file__).resolve().parents[1]


def test_dynamic_cleanup_delete_actions_use_cross_emoji():
    assert cleanup._button_action_label("Удалить вещи") == "❌ Удалить вещи"
    assert cleanup._button_action_label("Убрать из сохранённого") == "❌ Убрать из сохранённого"
    assert cleanup._button_action_label("Вернуть в рекомендации", "restore") == "Вернуть в рекомендации"


def test_no_literal_delete_button_is_missing_cross_emoji():
    pattern = re.compile(
        r"InlineKeyboardButton\(\s*f?[\"'](?:Удалить|Убрать)|"
        r"\(\s*f?[\"'](?:Удалить|Убрать)[^\n]+(?:delete|del|remove)",
    )
    violations = []
    for path in ROOT.glob("*.py"):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "delete_label(" not in line:
                violations.append(f"{path.name}:{line_no}")

    assert violations == []
