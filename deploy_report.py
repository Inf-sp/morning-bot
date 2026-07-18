"""Сборка и однократная отправка отчёта о новой версии."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import store
from ui import admin as admin_ui


_ROOT = Path(__file__).parent
_DEFAULT_NOTE = "Бот получил небольшие внутренние улучшения."
_DEFAULT_TITLE = "Обновление"


def _normalize_app_version(version: str) -> str:
    version = str(version or "").strip()
    if version.lower().startswith("v") and len(version) > 1:
        return version[1:].strip()
    return version


def get_app_version() -> str:
    return _normalize_app_version(config.APP_VERSION or config._read_text_file("VERSION"))


def _release_heading(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line.startswith("## "):
        return None
    title = line[3:].strip()
    version = title.split()[0] if title else ""
    release_title = ""
    for separator in (" · ", " - ", " — "):
        if separator in title:
            release_title = title.split(separator, 1)[1].strip()
            break
    return _normalize_app_version(version), release_title


def _clean_release_note_line(line: str) -> str:
    line = line.strip()
    if line.startswith("- ") or line.startswith("* "):
        line = line[2:].strip()
    if line.strip("*_ ").casefold() in {
        "бот развёрнут и работает ✅",
        "готово к развёртыванию ✅",
    }:
        return ""
    return line


def load_release_notes() -> tuple[list[str], str]:
    version = get_app_version()
    if not version:
        return [], "empty"
    path = _ROOT / "RELEASE_NOTES.md"
    if not path.exists():
        return [], "missing"

    current_lines = []
    in_current_section = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        heading = _release_heading(raw_line)
        if heading is not None:
            if in_current_section:
                break
            heading_version, _ = heading
            in_current_section = heading_version == version
            continue
        if in_current_section:
            line = _clean_release_note_line(raw_line)
            if line:
                current_lines.append(line)
    if not current_lines:
        return [], "fallback"
    return current_lines, "file"


def load_release_title(version, release_notes) -> str:
    version = _normalize_app_version(version)
    path = _ROOT / "RELEASE_NOTES.md"
    if path.exists() and version:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            heading = _release_heading(raw_line)
            if heading and heading[0] == version and heading[1]:
                return heading[1]

    text = " ".join(str(note) for note in (release_notes or [])).lower()
    if not text:
        return _DEFAULT_TITLE
    if any(word in text for word in ("история", "релиз", "релизов", "обновлен", "обновлений")):
        return "Чистые обновления"
    if "новост" in text:
        return "Умнее новости"
    if "эмодз" in text or "ui-словар" in text or "централизованные значки" in text:
        return "Единый UI-стиль"
    if "рецепт" in text:
        return "Быстрее рецепты"
    if "гардероб" in text:
        return "Аккуратнее гардероб"
    if "уведом" in text:
        return "Тише уведомления"
    if "обуч" in text or "словар" in text:
        return "Лучше обучение"
    return _DEFAULT_TITLE


def build_deploy_report_message(version, release_notes, check_list=None):
    clean_notes = [
        line for note in (release_notes or [])
        if (line := _clean_release_note_line(str(note)))
    ] or [_DEFAULT_NOTE]
    title = load_release_title(version, clean_notes)
    return admin_ui.deploy_report(_normalize_app_version(version), title, clean_notes)


async def maybe_send_admin_deploy_notification(bot):
    version = get_app_version()
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    release_notes, source = load_release_notes()
    log_data = (
        version, version, source, config.RAILWAY_ENVIRONMENT,
        config.RAILWAY_SERVICE_NAME, started_at,
    )
    if not config.ADMIN_CHAT_ID:
        logging.warning(
            "Deploy report skipped: admin chat id is not configured app_version=%s deploy_key=%s release_notes_source=%s railway_environment=%s railway_service=%s started_at=%s result=skipped",
            *log_data,
        )
        return
    if not version:
        logging.warning("Deploy report skipped: APP_VERSION is not configured")
        return
    if store.get_last_admin_deploy_notified_version() == version:
        logging.info("Deploy report skipped: already sent for app_version=%s", version)
        return

    msg = build_deploy_report_message(version, release_notes)
    try:
        await bot.send_message(
            chat_id=config.ADMIN_CHAT_ID, text=msg.text, entities=msg.entities)
        store.set_last_admin_deploy_notified_version(version, sent_at)
        logging.info("Deploy report sent: version=%s result=sent", version)
    except Exception:
        logging.exception(
            "Deploy report failed: version=%s admin_chat_id=%s result=failed",
            version, config.ADMIN_CHAT_ID,
        )
