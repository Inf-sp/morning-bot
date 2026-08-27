"""Единая короткая новостная строка для главных экранов."""

import re


def _one_line(value, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip(" ,;·")


def append_weekly_news(builder, news):
    """Render an already selected cached item; no store, network or decisions."""
    news = news if isinstance(news, dict) else {}
    text = _one_line(news.get("text_ru"), 150)
    if not text:
        return
    source = _one_line(news.get("source_name"), 40)
    source_url = str(news.get("source_url") or "").strip()
    builder.spacer()
    builder.text_line("📰 ")
    builder.bold("На неделе:")
    builder.text_line(f" {text}")
    if source and source_url.startswith("https://"):
        builder.text_line(" · ")
        builder.link(source, source_url)
    builder.newline()
