"""Small, declarative builders for Telegram Rich Message blocks.

The python-telegram-bot version used by the project does not yet expose typed
objects for Bot API 10.1/10.2 Rich Messages.  Keeping the JSON shape here makes
UI renderers independent from delivery details and gives each rich screen a
plain-text fallback through ``MessageSpec``.
"""

from __future__ import annotations


def message(blocks, *, is_rtl=False):
    payload = {"blocks": [block for block in blocks if block]}
    if is_rtl:
        payload["is_rtl"] = True
    return payload


def _text(value):
    """Keep Bot API RichText values (string, object, or list) intact."""
    if isinstance(value, (str, dict, list)):
        return value
    return str(value or "")


def heading(text, *, size=2):
    return {"type": "heading", "text": _text(text), "size": max(1, min(int(size), 6))}


def paragraph(text):
    return {"type": "paragraph", "text": _text(text)}


def divider():
    return {"type": "divider"}


def footer(text):
    return {"type": "footer", "text": _text(text)}


def date_time(text, unix_time, *, date_time_format="t"):
    """RichTextDateTime with a readable literal fallback for unsupported clients."""
    try:
        unix_time = int(unix_time)
    except (TypeError, ValueError):
        return str(text or "")
    if unix_time <= 0:
        return str(text or "")
    return {
        "type": "date_time",
        "text": str(text or ""),
        "unix_time": unix_time,
        "date_time_format": date_time_format,
    }


def table(headers, rows, *, caption=None, bordered=True, striped=True):
    """Build a phone-friendly table: callers should normally use 2–3 columns."""
    header_cells = [
        {"text": "" if value is None else str(value), "is_header": True}
        for value in headers
    ]
    cells = [header_cells]
    for row in rows:
        cells.append([{"text": "" if value is None else str(value)} for value in row])
    block = {
        "type": "table",
        "cells": cells,
        "is_bordered": bool(bordered),
        "is_striped": bool(striped),
    }
    if caption:
        block["caption"] = str(caption)
    return block
