import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot


def test_deploy_report_filters_duplicate_status_lines():
    message = bot.build_deploy_report_message(
        "1.16.45",
        [
            "Исправлено добавление слов.",
            "Готово к развёртыванию ✅",
            "*Бот развёрнут и работает ✅*",
        ],
    )

    assert "• Исправлено добавление слов." in message.text
    assert "• Готово к развёртыванию ✅" not in message.text
    assert message.text.count("Бот развёрнут и работает ✅") == 1
