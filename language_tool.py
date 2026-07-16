"""Narrow LanguageTool integration for Dutch writing practice."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlparse

import requests

import api_usage
import config
import util


_CACHE_TTL = 24 * 60 * 60
_MAX_TEXT_CHARS = 5000


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
