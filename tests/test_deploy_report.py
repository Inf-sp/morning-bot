import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import deploy_report


def test_deploy_report_filters_duplicate_status_lines():
    message = deploy_report.build_deploy_report_message(
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


def test_repository_version_does_not_require_release_notes():
    notes, source = deploy_report.load_release_notes()

    assert deploy_report.get_app_version() == "1.16.243"
    assert source == "disabled"
    assert notes == []


def _fake_commit(monkeypatch, sha="a1b2c3d", subject="fix: починить погоду"):
    from datetime import datetime

    committed = datetime(2026, 10, 6, 15, 42, tzinfo=deploy_report.config.TZ)
    started = datetime(2026, 10, 6, 15, 45, tzinfo=deploy_report.config.TZ)
    monkeypatch.setattr(deploy_report, "_COMMIT", {"sha": sha, "committed_at": committed, "subject": subject})
    monkeypatch.setattr(deploy_report, "_STARTED_AT", started)


def test_version_line_shows_commit_and_start_time(monkeypatch):
    _fake_commit(monkeypatch)

    assert deploy_report.version_line() == (
        "🚀 v1.16.243 · a1b2c3d · коммит 06.10 15:42 · запущен 06.10 15:45"
    )


def test_version_line_without_git_still_shows_version(monkeypatch):
    monkeypatch.setattr(deploy_report, "_COMMIT", {})

    assert deploy_report.version_line().startswith("🚀 v1.16.243 · запущен ")


def test_deploy_report_is_sent_once_per_commit(monkeypatch):
    import asyncio

    sent, state = [], {}
    monkeypatch.setattr(deploy_report.config, "ADMIN_CHAT_ID", 1)
    monkeypatch.setattr(deploy_report.store, "get_last_admin_deploy_notified_version", lambda: state.get("key"))
    monkeypatch.setattr(
        deploy_report.store, "set_last_admin_deploy_notified_version",
        lambda key, _at: state.__setitem__("key", key),
    )

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    _fake_commit(monkeypatch, sha="a1b2c3d")
    asyncio.run(deploy_report.maybe_send_admin_deploy_notification(Bot()))
    asyncio.run(deploy_report.maybe_send_admin_deploy_notification(Bot()))
    _fake_commit(monkeypatch, sha="e4f5a6b", subject="feat: новое")
    asyncio.run(deploy_report.maybe_send_admin_deploy_notification(Bot()))

    assert len(sent) == 2
    assert "v1.16.243 · a1b2c3d · 06.10 15:42" in sent[0]
    assert "• fix: починить погоду" in sent[0]
    assert "e4f5a6b" in sent[1]
