"""Фоновый сбор и выбор значимых новостей для главных экранов."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import ai
import config
import research
import secure
import store


_log = logging.getLogger(__name__)
_SCHEMA_VERSION = 1
_CATEGORIES = ("wardrobe", "food", "movie", "travel")
_MAX_AGE_DAYS = 10
_MIN_IMPORTANCE = 75
_MIN_CONFIDENCE = 70
_MAX_ITEMS = 3
_EVENT_STOPWORDS = {
    "about", "after", "against", "been", "from", "into", "more", "most",
    "news", "over", "said", "says", "than", "that", "their", "there", "these",
    "they", "this", "those", "under", "when", "where", "which", "with", "would",
}

_POLICIES = {
    "wardrobe": {
        "query": (
            "most important fashion textile industry regulation sustainable material "
            "innovation major trend news this week"
        ),
        "focus": (
            "regulation, product safety, material innovation, a major corroborated industry "
            "shift or a trend with broad practical relevance; reject celebrity looks, sales "
            "and routine collection promotion"
        ),
        "primary_domains": (
            "ec.europa.eu", "eur-lex.europa.eu", "textileexchange.org",
            "copenhagenfashionweek.com", "britishfashioncouncil.co.uk", "cfda.com",
        ),
    },
    "food": {
        "query": (
            "most important food science safety nutrition culinary technology innovation "
            "peer reviewed news this week"
        ),
        "focus": (
            "food safety, regulation, a consequential peer-reviewed discovery or major food "
            "technology; reject recipes, small product launches and unsupported health claims"
        ),
        "primary_domains": (
            "who.int", "efsa.europa.eu", "fda.gov", "ec.europa.eu", "nature.com",
            "science.org", "cell.com", "thelancet.com", "nejm.org", "wur.nl",
        ),
    },
    "movie": {
        "query": (
            "most important film cinema industry festival distribution major release news "
            "this week"
        ),
        "focus": (
            "a major festival decision, confirmed release or consequential industry change; "
            "reject casting rumours, gossip and minor promotional teasers"
        ),
        "primary_domains": (
            "festival-cannes.com", "berlinale.de", "labiennale.org", "oscars.org",
            "sundance.org", "bafta.org",
        ),
    },
    "travel": {
        "query": (
            "most important Netherlands Europe travel visa transport new route conservation "
            "destination discovery news this week"
        ),
        "focus": (
            "entry rules, a useful new route, major infrastructure, safety, conservation or "
            "a verified scientific discovery tied to a destination; reject destination listicles"
        ),
        "primary_domains": (
            "government.nl", "europa.eu", "consilium.europa.eu", "iata.org",
            "eurostar.com", "ns.nl", "schiphol.nl", "klm.com",
        ),
    },
}

_REJECT_RE = re.compile(
    r"\b(?:opinion|sponsored|advertorial|sale|discount|coupon|top\s*\d+|best\s+\d+|"
    r"celebrity\s+(?:look|style)|rumou?r|unconfirmed|wishlist)\b",
    re.I,
)
_SOURCE_NAMES = {
    "reuters.com": "Reuters", "apnews.com": "AP", "bbc.com": "BBC",
    "bbc.co.uk": "BBC", "theguardian.com": "The Guardian", "nature.com": "Nature",
    "science.org": "Science", "who.int": "WHO", "efsa.europa.eu": "EFSA",
    "ec.europa.eu": "European Commission", "eur-lex.europa.eu": "EUR-Lex",
    "festival-cannes.com": "Festival de Cannes", "berlinale.de": "Berlinale",
    "labiennale.org": "La Biennale di Venezia", "eurostar.com": "Eurostar",
    "schiphol.nl": "Schiphol", "government.nl": "Government of the Netherlands",
}


def _now(value=None):
    return value if isinstance(value, datetime) else datetime.now(config.TZ)


def _host(url):
    try:
        return (urlparse(str(url or "")).hostname or "").casefold().removeprefix("www.")
    except Exception:
        return ""


def _domain_matches(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _published_at(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=config.TZ)
    return parsed.astimezone(config.TZ)


def _source_name(host):
    for domain, name in _SOURCE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    parts = host.split(".")
    label = parts[-2] if len(parts) > 1 else host
    return label.replace("-", " ").title()[:40]


def _clean_rows(rows, now):
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    cleaned, seen_urls, seen_content = [], set(), set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        host = _host(url)
        title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
        content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
        published = _published_at(row.get("published_date") or row.get("publishedAt"))
        fingerprint = hashlib.sha256(
            f"{title.casefold()}|{content.casefold()}".encode()
        ).hexdigest()
        if (
            not url.startswith("https://") or not host or not title or not content or not published
            or published < cutoff or published > now + timedelta(days=1)
            or _REJECT_RE.search(f"{title} {content}") or url in seen_urls
            or fingerprint in seen_content
        ):
            continue
        seen_urls.add(url)
        seen_content.add(fingerprint)
        cleaned.append({
            "url": url,
            "domain": host,
            "title": title[:220],
            "content": content[:700],
            "published_at": published.isoformat(),
        })
    return cleaned[:10]


def _discover(category, now):
    policy = _POLICIES[category]
    rows = research.web_search(
        policy["query"], max_results=10, scenario="category_news",
        allow_tavily=True, search_priority="tavily", topic="news", time_range="week",
        require_published_date=True,
    )
    return _clean_rows(rows, now)


def _editor_prompt(rows_by_category):
    payload = {
        category: [
            {"id": f"{category}:{index}", **row}
            for index, row in enumerate(rows)
        ]
        for category, rows in rows_by_category.items() if rows
    }
    policies = {category: _POLICIES[category]["focus"] for category in payload}
    return f"""Ты редактор короткого еженедельного дайджеста. Выбери до трёх действительно
значимых событий на каждую категорию. Популярность не равна значимости.

Правила:
- факт подтверждён двумя независимыми доменами или одним официальным первоисточником;
- не выводи причинность, если источники её не утверждают;
- не бери слухи, рекламу, мнения, листиклы и обычные мелкие анонсы;
- text_ru: одна русская строка без заголовка и переносов, 70–150 символов;
- имена людей, брендов, фильмов, мест и мероприятий сохраняй в оригинале;
- importance оценивает реальное влияние 0–100, confidence — доказательства 0–100;
- evidence_ids содержит только id источников, прямо подтверждающих этот факт;
- если важной новости нет, верни пустой список.

Фокус категорий:
{secure.wrap_untrusted(json.dumps(policies, ensure_ascii=False), "редакционные политики")}

Кандидаты:
{secure.wrap_untrusted(json.dumps(payload, ensure_ascii=False), "новостные сниппеты")}

Верни JSON:
{{"categories":{{"wardrobe":[{{"text_ru":"...","importance":0,"confidence":0,
"evidence_ids":["wardrobe:0","wardrobe:1"]}}],"food":[],"movie":[],"travel":[]}}}}
"""


def _selected_items(category, decisions, rows, now):
    policy = _POLICIES[category]
    by_id = {f"{category}:{index}": row for index, row in enumerate(rows)}
    accepted = []
    for decision in decisions if isinstance(decisions, list) else []:
        if not isinstance(decision, dict):
            continue
        text = re.sub(r"\s+", " ", str(decision.get("text_ru") or "")).strip()
        try:
            importance = int(decision.get("importance") or 0)
            confidence = int(decision.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        evidence = []
        for source_id in decision.get("evidence_ids") or []:
            row = by_id.get(str(source_id))
            if row and row not in evidence:
                evidence.append(row)
        domains = {row["domain"] for row in evidence}
        has_primary = any(
            _domain_matches(domain, policy["primary_domains"]) for domain in domains
        )
        event_tokens = []
        for row in evidence:
            tokens = {
                token for token in re.findall(
                    r"[a-z0-9][a-z0-9'-]{3,}",
                    f"{row.get('title', '')} {row.get('content', '')}".casefold(),
                )
                if token not in _EVENT_STOPWORDS
            }
            event_tokens.append(tokens)
        common_event_tokens = (
            set.intersection(*event_tokens) if len(event_tokens) > 1 else set()
        )
        if (
            not re.search(r"[а-яё]", text, re.I) or "\n" in text or len(text) > 150
            or len(text) < 45 or importance < _MIN_IMPORTANCE
            or confidence < _MIN_CONFIDENCE or not evidence
            or (len(domains) < 2 and not has_primary)
            or (len(domains) >= 2 and len(common_event_tokens) < 2)
        ):
            continue
        evidence.sort(
            key=lambda row: (
                not _domain_matches(row["domain"], policy["primary_domains"]),
                -_published_at(row["published_at"]).timestamp(),
            ),
        )
        source = evidence[0]
        evidence_urls = [row["url"] for row in evidence]
        item_id = hashlib.sha256(
            f"{category}|{'|'.join(sorted(evidence_urls))}|{text.casefold()}".encode()
        ).hexdigest()[:24]
        accepted.append({
            "id": item_id,
            "category": category,
            "text_ru": text,
            "source_name": _source_name(source["domain"]),
            "source_url": source["url"],
            "published_at": max(
                _published_at(row["published_at"]) for row in evidence
            ).isoformat(),
            "verified_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "importance": importance,
            "confidence": confidence,
            "evidence_urls": evidence_urls,
        })
    accepted.sort(
        key=lambda item: (
            item["importance"], item["confidence"], item["published_at"],
        ),
        reverse=True,
    )
    return accepted[:_MAX_ITEMS]


def _valid_cached_items(items, now):
    valid = []
    for item in items if isinstance(items, list) else []:
        expires = (
            _published_at((item or {}).get("expires_at"))
            if isinstance(item, dict) else None
        )
        if (
            expires and expires >= now
            and str(item.get("source_url") or "").startswith("https://")
            and str(item.get("text_ru") or "").strip()
        ):
            valid.append(dict(item))
    return valid


def cached_line(category, *, now=None):
    """Return a ready line from KV only; never performs search or AI work."""
    if category not in _CATEGORIES:
        return None
    current = _now(now)
    data = store._load(config.CATEGORY_NEWS_CACHE_KEY) or {}
    rows = ((data.get("categories") or {}).get(category) or {}).get("items") or []
    valid = _valid_cached_items(rows, current)
    return valid[0] if valid else None


def refresh_pool(*, categories=None, now=None, force=False):
    """Discover, edit and atomically store one shared pool for all users."""
    current = _now(now)
    requested = tuple(
        category for category in (categories or _CATEGORIES)
        if category in _CATEGORIES
    )
    cached = store._load(config.CATEGORY_NEWS_CACHE_KEY) or {}
    old_categories = (
        cached.get("categories")
        if isinstance(cached.get("categories"), dict) else {}
    )
    if not force:
        today = current.date().isoformat()
        requested = tuple(
            category for category in requested
            if (old_categories.get(category) or {}).get("refreshed_on") != today
        )
    if not requested:
        return {"updated": (), "retained": (), "missing": ()}

    rows_by_category = {}
    for category in requested:
        try:
            rows_by_category[category] = _discover(category, current)
        except Exception as exc:
            _log.warning("Category news discovery failed for %s: %s", category, exc)
    rows_by_category = {
        category: rows for category, rows in rows_by_category.items() if rows
    }
    decisions = {}
    if rows_by_category:
        try:
            result = ai.llm_json(
                _editor_prompt(rows_by_category), 1800, module="category_news",
                tier="smart", fallback_allowed=True, privacy_level="public",
                budget_seconds=30,
            )
            decisions = (result or {}).get("categories") or {}
        except Exception:
            decisions = {}

    next_categories = dict(old_categories)
    updated, retained, missing = [], [], []
    used_urls = set()
    for category in requested:
        selected = _selected_items(
            category, decisions.get(category),
            rows_by_category.get(category, []), current,
        )
        selected = [
            item for item in selected
            if not used_urls.intersection(item.get("evidence_urls") or [])
        ]
        if selected:
            next_categories[category] = {
                "items": selected,
                "refreshed_on": current.date().isoformat(),
                "attempted_at": current.isoformat(),
            }
            used_urls.update(
                url for item in selected for url in item.get("evidence_urls") or []
            )
            updated.append(category)
            continue
        previous = _valid_cached_items(
            ((old_categories.get(category) or {}).get("items") or []), current,
        )
        if previous:
            next_categories[category] = {
                **(old_categories.get(category) or {}),
                "items": previous,
                "attempted_at": current.isoformat(),
            }
            used_urls.update(
                url for item in previous for url in item.get("evidence_urls") or []
            )
            retained.append(category)
        else:
            next_categories[category] = {
                **(old_categories.get(category) or {}),
                "items": [],
                "attempted_at": current.isoformat(),
            }
            missing.append(category)

    def save(current_data):
        current_data = current_data if isinstance(current_data, dict) else {}
        current_categories = (
            current_data.get("categories")
            if isinstance(current_data.get("categories"), dict) else {}
        )
        for category in requested:
            current_categories[category] = next_categories[category]
        current_data.update({
            "schema": _SCHEMA_VERSION,
            "date": current.date().isoformat(),
            "updated_at": current.isoformat(),
            "categories": current_categories,
        })
        return current_data, None

    store.mutate_kv(config.CATEGORY_NEWS_CACHE_KEY, save)
    return {
        "updated": tuple(updated),
        "retained": tuple(retained),
        "missing": tuple(missing),
    }
