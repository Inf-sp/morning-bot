"""Local country reference data with a best-effort countries.dev fallback.

Normal product requests read the bundled dataset only. The fallback exists for
an uncommon missing or incomplete record and never prevents Travel from
rendering a card.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

import requests

import util


_log = logging.getLogger(__name__)
_DATA_PATH = Path(__file__).parent / "data" / "countries.json"
_REQUIRED_FIELDS = (
    "country_code", "name", "official_name", "capital", "region", "subregion",
    "languages", "currencies", "calling_code", "timezones", "latitude", "longitude", "flag",
)
_FALLBACK_TIMEOUT_SECONDS = 4


def _normalise(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _load_dataset() -> dict[str, dict]:
    try:
        rows = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.error("country dataset could not be loaded: %s", exc)
        return {}
    return {
        str(row.get("country_code") or "").upper(): dict(row)
        for row in rows if isinstance(row, dict) and row.get("country_code")
    }


_COUNTRIES = _load_dataset()
_ALIASES = {
    _normalise(alias): code
    for code, row in _COUNTRIES.items()
    for alias in [code, row.get("name", ""), row.get("official_name", ""), *(row.get("aliases") or [])]
    if _normalise(alias)
}


def _copy(row: dict | None) -> dict:
    return json.loads(json.dumps(row or {}, ensure_ascii=False))


def country_code(value: str) -> str:
    """Resolve a common country name to stable ISO alpha-2 code without network."""
    raw = str(value or "").strip()
    direct = raw.upper()
    if len(direct) == 2 and direct in _COUNTRIES:
        return direct
    return _ALIASES.get(_normalise(raw), "") or str(util.cc_of(raw) or "").upper()


def local_country(value: str) -> dict:
    return _copy(_COUNTRIES.get(country_code(value)))


def is_complete(row: dict) -> bool:
    return bool(row) and all(row.get(field) not in (None, "", [], {}) for field in _REQUIRED_FIELDS)


def _from_countries_dev(payload: dict, fallback_code="") -> dict:
    if not isinstance(payload, dict):
        return {}
    currencies = payload.get("currencies") or []
    languages = payload.get("languages") or []
    latlng = payload.get("latlng") or []
    return {
        "country_code": str(payload.get("alpha2Code") or fallback_code or "").upper(),
        "name": str(payload.get("name") or ""),
        "official_name": str(payload.get("officialName") or payload.get("name") or ""),
        "capital": str(payload.get("capital") or ""),
        "region": str(payload.get("region") or ""),
        "subregion": str(payload.get("subregion") or ""),
        "languages": [str(item.get("name") if isinstance(item, dict) else item) for item in languages if item],
        "currencies": [
            {"code": str(item.get("code") or ""), "name": str(item.get("name") or "")}
            for item in currencies if isinstance(item, dict)
        ],
        "calling_code": str((payload.get("callingCodes") or [""])[0] or ""),
        "timezones": [str(item) for item in (payload.get("timezones") or []) if item],
        "latitude": latlng[0] if len(latlng) > 0 else None,
        "longitude": latlng[1] if len(latlng) > 1 else None,
        "flag": str(payload.get("flag") or ""),
        "aliases": list(payload.get("altSpellings") or []),
    }


def countries_dev_lookup(code: str) -> dict:
    """Fetch one ISO country only when the local record cannot serve it."""
    code = str(code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return {}
    try:
        response = requests.get(
            f"https://countries.dev/alpha/{quote(code)}", timeout=_FALLBACK_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            _log.warning("country fallback failed code=%s status=%s", code, response.status_code)
            return {}
        return _from_countries_dev(response.json(), code)
    except Exception as exc:
        _log.warning("country fallback failed code=%s error=%s", code, type(exc).__name__)
        return {}


def country_data(value: str, *, allow_fallback=True) -> dict:
    """Return local data immediately, optionally filling a rare incomplete record."""
    code = country_code(value)
    local = local_country(code)
    if is_complete(local) or not allow_fallback:
        return local
    fallback = countries_dev_lookup(code)
    if not fallback:
        return local
    # Local names remain preferred; fallback supplies only absent fields.
    merged = {**fallback, **{key: value for key, value in local.items() if value not in (None, "", [], {})}}
    return merged


def update_country_dataset() -> dict:
    """Manual maintenance hook. It does not run on product requests.

    The repository dataset stays the source of truth at runtime; this returns
    validated remote rows for an explicit maintenance command to review and
    apply, rather than silently changing country facts in production.
    """
    try:
        response = requests.get("https://countries.dev/countries?full=true", timeout=20)
        if response.status_code != 200:
            return {"ok": False, "updated": 0, "reason": f"HTTP {response.status_code}"}
        rows = response.json()
        if not isinstance(rows, list):
            return {"ok": False, "updated": 0, "reason": "invalid response"}
        parsed = [_from_countries_dev(row) for row in rows]
        return {"ok": True, "updated": sum(bool(row.get("country_code")) for row in parsed), "rows": parsed}
    except Exception as exc:
        return {"ok": False, "updated": 0, "reason": type(exc).__name__}
