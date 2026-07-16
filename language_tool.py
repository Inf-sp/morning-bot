"""LanguageTool client and conservative correction helpers for learning data."""

from __future__ import annotations

import asyncio
import hashlib
import time
import unicodedata
from urllib.parse import urlparse

import requests

import api_usage
import config
import util


_CACHE_TTL = 24 * 60 * 60
_MAX_TEXT_CHARS = 5000
_STYLE_TYPES = {"style", "locale-violation"}
_SAFE_RULE_PARTS = (
    "WHITESPACE", "SPACE", "DOUBLE_PUNCTUATION", "UPPERCASE_SENTENCE_START",
    "LOWERCASE_SENTENCE_START", "UNPAIRED_BRACKETS",
)


def _check_url() -> str:
    base = str(config.LANGUAGETOOL_API_URL or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{base}/check"


def _cache_key(text: str, language: str) -> str:
    raw = f"{language}\0{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _issue(match: dict, text: str) -> dict:
    offset = max(0, int(match.get("offset") or 0))
    length = max(0, int(match.get("length") or 0))
    rule = match.get("rule") if isinstance(match.get("rule"), dict) else {}
    category = rule.get("category") if isinstance(rule.get("category"), dict) else {}
    replacements = [
        " ".join(str(item.get("value") or "").split())
        for item in match.get("replacements") or []
        if isinstance(item, dict) and str(item.get("value") or "").strip()
    ]
    return {
        "offset": offset,
        "length": length,
        "original": text[offset:offset + length],
        "message": " ".join(str(match.get("message") or "").split()),
        "short_message": " ".join(str(match.get("shortMessage") or "").split()),
        "replacements": replacements[:5],
        "rule_id": str(rule.get("id") or ""),
        "issue_type": str(rule.get("issueType") or ""),
        "category": str(category.get("name") or ""),
    }


def apply_first_replacements(text: str, issues) -> str:
    corrected = str(text or "")
    edits = []
    for issue in issues or []:
        replacements = issue.get("replacements") or []
        if replacements:
            edits.append((int(issue.get("offset") or 0), int(issue.get("length") or 0), replacements[0]))
    for offset, length, replacement in sorted(edits, reverse=True):
        corrected = corrected[:offset] + replacement + corrected[offset + length:]
    return corrected


def is_style_issue(issue: dict) -> bool:
    issue_type = str(issue.get("issue_type") or "").strip().lower()
    category = str(issue.get("category") or "").strip().lower()
    return issue_type in _STYLE_TYPES or "style" in category


def _edit_distance(left: str, right: str) -> int:
    left, right = left.casefold(), right.casefold()
    previous = list(range(len(right) + 1))
    for row, char_left in enumerate(left, 1):
        current = [row]
        for column, char_right in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (char_left != char_right),
            ))
        previous = current
    return previous[-1]


def is_safe_issue(issue: dict, *, allow_spelling=True) -> bool:
    """True only for deterministic formatting or an obvious one-word typo."""
    if is_style_issue(issue):
        return False
    rule_id = str(issue.get("rule_id") or "").upper()
    if any(part in rule_id for part in _SAFE_RULE_PARTS):
        return bool(issue.get("replacements"))
    issue_type = str(issue.get("issue_type") or "").lower()
    replacements = issue.get("replacements") or []
    original = str(issue.get("original") or "").strip()
    if not allow_spelling or issue_type not in ("misspelling", "typographical"):
        return False
    if len(replacements) != 1 or not original or original.casefold() in {
        "de", "het", "een", "the", "a", "an",
    }:
        return False
    replacement = str(replacements[0] or "").strip()
    if not replacement or " " in original or " " in replacement:
        return False
    distance = _edit_distance(original, replacement)
    return distance <= (1 if max(len(original), len(replacement)) < 9 else 2)


def meaningful_issues(report: dict) -> list[dict]:
    return [issue for issue in (report.get("issues") or []) if not is_style_issue(issue)]


def apply_safe_replacements(text: str, issues, *, allow_spelling=True) -> str:
    safe = [issue for issue in issues or [] if is_safe_issue(issue, allow_spelling=allow_spelling)]
    return unicodedata.normalize("NFC", apply_first_replacements(text, safe))


async def check_text_retry(text: str, language="nl-NL", *, retries=1,
                           delay=0.4, semaphore=None) -> dict:
    """Async wrapper with bounded concurrency and one short retry on outage."""
    async def run_once():
        if semaphore is None:
            return await asyncio.to_thread(check_text, text, language)
        async with semaphore:
            return await asyncio.to_thread(check_text, text, language)

    report = await run_once()
    for attempt in range(max(0, int(retries))):
        if report.get("available"):
            break
        await asyncio.sleep(float(delay) * (attempt + 1))
        report = await run_once()
    return report


def check_text(text: str, language="nl-NL") -> dict:
    text = str(text or "").strip()[:_MAX_TEXT_CHARS]
    if not text:
        return {"ok": False, "available": True, "text": "", "issues": [], "corrected_text": ""}
    url = _check_url()
    if not url:
        return {"ok": False, "available": False, "text": text, "issues": [], "corrected_text": text}
    cache_key = _cache_key(text, language)
    cached = util.ttl_get("languagetool", cache_key, _CACHE_TTL)
    if isinstance(cached, dict):
        api_usage.record_cache_hit("languagetool")
        return cached

    started = time.time()
    try:
        response = requests.post(
            url,
            data={"text": text, "language": language, "motherTongue": "ru"},
            timeout=8,
        )
    except requests.exceptions.Timeout:
        api_usage.record_request("languagetool", ok=False, error="timeout")
        return {"ok": False, "available": False, "text": text, "issues": [], "corrected_text": text}
    except requests.exceptions.RequestException:
        api_usage.record_request("languagetool", ok=False, error="network_error")
        return {"ok": False, "available": False, "text": text, "issues": [], "corrected_text": text}
    latency_ms = int((time.time() - started) * 1000)
    if response.status_code != 200:
        api_usage.record_request(
            "languagetool", ok=False, status_code=response.status_code,
            error=f"HTTP {response.status_code}", latency_ms=latency_ms,
            headers=response.headers,
        )
        return {"ok": False, "available": False, "text": text, "issues": [], "corrected_text": text}
    try:
        payload = response.json()
        matches = payload.get("matches") or []
    except (AttributeError, TypeError, ValueError):
        api_usage.record_request(
            "languagetool", ok=False, error="invalid_json", latency_ms=latency_ms,
            headers=response.headers,
        )
        return {"ok": False, "available": False, "text": text, "issues": [], "corrected_text": text}

    issues = [_issue(match, text) for match in matches if isinstance(match, dict)]
    result = {
        "ok": not issues,
        "available": True,
        "text": text,
        "issues": issues,
        "corrected_text": apply_first_replacements(text, issues),
        "language": str(((payload.get("language") or {}).get("code") or language)),
    }
    api_usage.record_request(
        "languagetool", ok=True, units={"characters": len(text)},
        latency_ms=latency_ms, headers=response.headers,
    )
    util.ttl_set("languagetool", cache_key, result)
    return result
