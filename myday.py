import asyncio
from difflib import SequenceMatcher
import json
import logging
import re
from datetime import datetime
from pathlib import Path
import random
import uuid
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config

_HERE = Path(__file__).parent
_log = logging.getLogger(__name__)
import store
import ai
import weather
import learning
import learning_dictionary as dictionary
import dictionary_seed
import research
import secure
import util
from util import esc, _WEEKDAY_SHORT, _MONTHS
import verify
from ui import myday as myday_ui

TZ = config.TZ

def _strip_quotes(s):
    """Убирает внешние кавычки (« » \" \" \" ') с краёв, чтобы не задваивать обёртку."""
    s = (s or "").strip()
    pairs = ('«»', '""', '""', "''", '„“', '‚‘')
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for p in pairs:
            if s[0] == p[0] and s[-1] == p[1]:
                s = s[1:-1].strip()
                changed = True
        # одинаковые прямые кавычки с обеих сторон
        if len(s) >= 2 and s[0] in '"\'' and s[-1] == s[0]:
            s = s[1:-1].strip()
            changed = True
    return s

# --- Недельные AI-пулы (факты о городе, база знаний) ---
# Общий движок: раз в неделю AI генерирует пачку 14-21 элемент, каждый день выдаётся
# следующий непоказанный (shown_at), без повтора, пока пул не исчерпан - тогда генерируем
# новый пул досрочно. Экономит AI-вызовы (§14 CLAUDE.md: 0-1 в день для "Мой день").

_POOL_MIN_ITEMS = 7
_POOL_TARGET_ITEMS = 18
_LIFEHACK_POOL_VERSION = 2

_CONTENT_BLACKLIST = (
    "футбол", "спорт", "voetbal", "match", "wedstrijd", "club", "клуб", "score", "счёт",
    "гол", "матч", "чемпионат", "лига", "politics", "политик", "выбор", "партия",
    "crime", "преступ", "убий", "moord", "oorlog", "война", "теракт", "суд",
)


def _content_blocked(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in _CONTENT_BLACKLIST)


_LIFEHACK_ACTION_RE = re.compile(
    r"\b(?:наб|полож|постав|добав|хран|пров|включ|отключ|сдел|использ|держ|закр|"
    r"перенес|замен|прот|сфотограф|подпиш|оберн|нажм|покат|встан|посмотр|разлож|"
    r"нареж|вымой|остав|сним|удали|проветр|заряд|запиши|отлож|настро|полив|полей|"
    r"перестав|замороз|размороз|убир|купи|надень|возьми|прикреп|открой|создай|"
    r"выбери|смеш|обжар|отвар|запек|накрой|слож|обнов|представ)\w*",
    re.IGNORECASE,
)
_LIFEHACK_RESULT_RE = re.compile(
    r"(?:чтобы|—|помож|быстр|дольше|меньше|сниж|эконом|избав|улучш|сохран|"
    r"предотврат|легче|удобнее|точнее|сократ|защит|останет|получит|избеж|"
    r"не промок|не испорт|не забуд)",
    re.IGNORECASE,
)
_LIFEHACK_GENERIC_RE = re.compile(
    r"\b(?:важно помнить|просто расслабься|постарайся|не забывай|слушай себя|"
    r"будь продуктивн|мысли позитивно)\b",
    re.IGNORECASE,
)


def _lifehack_useful(text: str) -> bool:
    """Отсекает общие мысли: лайфхак обязан содержать действие и понятный результат."""
    text = " ".join(str(text or "").split())
    return (
        45 <= len(text) <= 240
        and not _content_blocked(text)
        and not _LIFEHACK_GENERIC_RE.search(text)
        and bool(_LIFEHACK_ACTION_RE.search(text))
        and bool(_LIFEHACK_RESULT_RE.search(text))
    )


def _iso_week_key(dt=None) -> str:
    dt = dt or datetime.now(TZ)
    year, week, _ = dt.isocalendar()
    return f"{year}-{week:02d}"


def _pool_get(store_key: str, cid: str, pool_id: str) -> dict:
    data = store._load(store_key) or {}
    return (data.get(str(cid)) or {}).get(pool_id) or {}


def _pool_next_unshown(store_key: str, cid: str, pool_id: str) -> dict | None:
    """Помечает первый непоказанный item как shown и возвращает его (атомарно)."""
    cid = str(cid)
    result = {"item": None}

    def mut(data):
        bucket = data.setdefault(cid, {}).setdefault(pool_id, {})
        items = bucket.get("items") or []
        for item in items:
            if not item.get("shown_at"):
                item["shown_at"] = int(datetime.now(TZ).timestamp())
                result["item"] = dict(item)
                break
        return data, True

    store.mutate_kv(store_key, mut)
    return result["item"]


def _pool_save(store_key: str, cid: str, pool_id: str, items: list) -> None:
    cid = str(cid)

    def mut(data):
        data.setdefault(cid, {})[pool_id] = {
            "version": _LIFEHACK_POOL_VERSION,
            "week": _iso_week_key(),
            "generated_at": int(datetime.now(TZ).timestamp()),
            "items": items,
        }
        return data, True

    store.mutate_kv(store_key, mut)


def _pool_ensure_fresh(store_key: str, cid: str, pool_id: str, generate_fn) -> None:
    """Если пула нет, он не за эту неделю, или все элементы показаны - генерирует новый."""
    bucket = _pool_get(store_key, cid, pool_id)
    items = bucket.get("items") or []
    stale_week = bucket.get("week") != _iso_week_key()
    stale_format = bucket.get("version") != _LIFEHACK_POOL_VERSION
    exhausted = bool(items) and all(i.get("shown_at") for i in items)
    if items and not stale_week and not stale_format and not exhausted:
        return
    raw_items = generate_fn()
    filtered = [
        {"id": idx, "text": text, **extra, "shown_at": None}
        for idx, (text, extra) in enumerate(raw_items)
        if text and _lifehack_useful(text)
    ]
    if len(filtered) < _POOL_MIN_ITEMS and items and not exhausted:
        # генерация дала слишком мало валидных элементов - лучше донашивать старый пул,
        # чем показывать пользователю пустоту или урезанный набор
        return
    if filtered:
        _pool_save(store_key, cid, pool_id, filtered)


# --- Сводка дня (Мой день) ---


_LIFEHACK_CATEGORIES = (
    "язык", "кухня", "путешествия", "технологии", "продуктивность", "деньги",
    "дом", "растения", "фото", "спорт", "здоровье", "разное",
)

_LIFEHACK_CATEGORY_EMOJI = {
    "язык": "🇳🇱", "кухня": "🍳", "путешествия": "🧳", "технологии": "💻",
    "продуктивность": "🧠", "деньги": "💰", "дом": "🏠", "растения": "🌱",
    "фото": "📸", "спорт": "🎾", "здоровье": "❤️", "разное": "✨",
}

_LIFEHACK_CATEGORY_LABELS = {
    "язык": "🇳🇱 Язык", "кухня": "🍳 Кухня", "путешествия": "🧳 Путешествия",
    "технологии": "💻 Технологии", "продуктивность": "🧠 Продуктивность",
    "деньги": "💰 Деньги", "дом": "🏠 Дом", "растения": "🌱 Растения",
    "фото": "📸 Фото", "спорт": "🎾 Спорт", "здоровье": "❤️ Здоровье",
    "разное": "✨ Разное",
}

_LIFEHACK_CATEGORY_ALIASES = {
    "быт и дом": "дом", "дом": "дом", "еда и кухня": "кухня", "кухня": "кухня",
    "гардероб": "разное", "продуктивность": "продуктивность", "технологии": "технологии",
    "фотография": "фото", "фото": "фото", "жизнь в нидерландах": "путешествия",
    "город": "путешествия", "путешествия": "путешествия", "растения": "растения",
    "домашние животные": "разное", "язык": "язык", "деньги": "деньги",
    "здоровье": "здоровье", "спорт": "спорт", "разное": "разное",
}


_LIFEHACK_CHAT_CATEGORIES = (
    (re.compile(r"\b(?:de|het|артикл|нидерланд|голланд|перевод|язык|слово)\b", re.I), "язык"),
    (re.compile(r"\b(?:готов|рецепт|кухн|продукт|хранен|суп|мяс|овощ|фрукт)\w*", re.I), "кухня"),
    (re.compile(r"\b(?:поезд|путеш|отел|билет|маршрут|границ)\w*", re.I), "путешествия"),
    (re.compile(r"\b(?:телефон|компьютер|парол|приложен|заряд|технолог)\w*", re.I), "технологии"),
    (re.compile(r"\b(?:задач|врем|план|работ|продуктив|уведомлен)\w*", re.I), "продуктивность"),
    (re.compile(r"\b(?:деньг|покуп|подписк|цен|бюджет)\w*", re.I), "деньги"),
    (re.compile(r"\b(?:растен|цвет|полив|фото|сним|спорт|трениров|одежд|обув|дом|уборк)\w*", re.I), "разное"),
)


def _lifehack_category(text):
    for pattern, category in _LIFEHACK_CHAT_CATEGORIES:
        if pattern.search(text or ""):
            return category
    return "разное"


def _canonical_lifehack_category(value):
    value = " ".join(str(value or "").casefold().split())
    for category, label in _LIFEHACK_CATEGORY_LABELS.items():
        if value == label.casefold():
            return category
    return _LIFEHACK_CATEGORY_ALIASES.get(value, value if value in _LIFEHACK_CATEGORIES else "разное")


def _lifehack_category_label(category):
    category = _canonical_lifehack_category(category)
    return _LIFEHACK_CATEGORY_LABELS.get(category, _LIFEHACK_CATEGORY_LABELS["разное"])


def _clean_lifehack_text(text):
    return " ".join(str(text or "").replace("\n", " ").split()).strip(" \t\r•-–—")


def _clean_lifehack_tags(tags, category):
    if isinstance(tags, str):
        tags = re.split(r"[,;]", tags)
    result = []
    for tag in tags or []:
        tag = " ".join(str(tag or "").casefold().split()).strip("# ")
        if tag and len(tag) <= 24 and tag not in result:
            result.append(tag)
    if category and category not in result:
        result.insert(0, category)
    return result[:4]


def _lifehack_record(text, category, tags=None, *, source="user", record_id=None,
                     created_at=None, shown_count=0, last_shown=None,
                     favorite=False, enabled=True):
    category = _canonical_lifehack_category(category)
    return {
        "id": str(record_id or f"lh_{uuid.uuid4().hex}"),
        "text": _clean_lifehack_text(text),
        "category": category,
        "tags": _clean_lifehack_tags(tags, category),
        "source": source if source in {"user", "ai"} else "user",
        "created_at": created_at or datetime.now(TZ).isoformat(),
        "shown_count": max(0, int(shown_count or 0)),
        "last_shown": last_shown,
        "favorite": bool(favorite),
        "enabled": bool(enabled),
    }


def _normalize_lifehack_with_ai(text):
    category = _lifehack_category(text)
    fallback = (_clean_lifehack_text(text), category, _clean_lifehack_tags([], category))
    prompt = (
        "Приведи пользовательский лайфхак к единому стилю базы знаний. "
        "Убери воду, исправь ошибки, сократи до 1–2 коротких предложений, "
        "не добавляй новые факты и не меняй смысл. Выбери одну категорию и 1–3 коротких тега. "
        "Категории: язык, кухня, путешествия, технологии, продуктивность, деньги, дом, "
        "растения, фото, спорт, здоровье, разное. "
        'Верни только JSON: {"text":"...","category":"...","tags":["..."]}.\n'
        f"Исходный текст: {secure.wrap_untrusted(text, 'лайфхак пользователя')}"
    )
    try:
        data = ai.llm_json(prompt, 360, tier="cheap", module="myday_utility")
    except Exception:
        return fallback
    normalized = _clean_lifehack_text(data.get("text") if isinstance(data, dict) else "")
    chosen = _canonical_lifehack_category(data.get("category") if isinstance(data, dict) else "")
    tags = _clean_lifehack_tags(data.get("tags") if isinstance(data, dict) else [], chosen)
    if not normalized or not _lifehack_useful(normalized):
        return fallback
    return normalized, chosen, tags


def _load_lifehack_catalog():
    """Читает новый список записей и прозрачно мигрирует старые группы tips."""
    try:
        with open(_HERE / "lifehacks.json", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("items") or []
    if not isinstance(raw, list):
        return []
    records = []
    for index, item in enumerate(raw):
        if isinstance(item, dict) and "tips" in item:
            category = _canonical_lifehack_category(item.get("cat"))
            for tip_index, tip in enumerate(item.get("tips") or []):
                tip = tip if isinstance(tip, dict) else {"text": tip}
                record = _lifehack_record(
                    tip.get("text", ""), category, tip.get("tags", []),
                    source="user", record_id=f"legacy_{index}_{tip_index}",
                )
                if record["text"]:
                    records.append(record)
            continue
        if not isinstance(item, dict):
            continue
        record = _lifehack_record(
            item.get("text", ""), item.get("category", "разное"), item.get("tags", []),
            source=item.get("source", "user"), record_id=item.get("id") or f"lh_{index}",
            created_at=item.get("created_at"), shown_count=item.get("shown_count", 0),
            last_shown=item.get("last_shown"), favorite=item.get("favorite", False),
            enabled=item.get("enabled", True),
        )
        if record["text"]:
            records.append(record)
    return records


def _save_lifehack_catalog(records):
    (_HERE / "lifehacks.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def lifehack_records(*, include_disabled=True):
    records = _load_lifehack_catalog()
    return records if include_disabled else [item for item in records if item["enabled"]]


def _lifehack_near_duplicate(text, records):
    key = _clean_lifehack_text(text).casefold()
    for item in records:
        other = _clean_lifehack_text(item.get("text", "")).casefold()
        if key == other or SequenceMatcher(None, key, other).ratio() >= 0.86:
            return item
    return None


def _store_ai_lifehacks(items):
    """Сохраняет валидный AI-пул в каталоге, чтобы им можно было управлять в настройках."""
    if not items:
        return
    records = _load_lifehack_catalog()
    changed = False
    for text, extra in items:
        if _lifehack_near_duplicate(text, records):
            continue
        category = _canonical_lifehack_category((extra or {}).get("category"))
        records.append(_lifehack_record(text, category, [category], source="ai"))
        changed = True
    if changed:
        try:
            _save_lifehack_catalog(records)
        except OSError:
            _log.warning("myday: cannot store AI lifehacks", exc_info=True)


def add_lifehack_to_file(text):
    """Нормализует и добавляет пользовательский лайфхак в общий каталог."""
    raw_text = _clean_lifehack_text(text)
    if not _lifehack_useful(raw_text):
        return None
    normalized, category, tags = _normalize_lifehack_with_ai(raw_text)
    if not _lifehack_useful(normalized):
        return None
    records = _load_lifehack_catalog()
    duplicate = _lifehack_near_duplicate(normalized, records)
    if duplicate:
        return {"duplicate": True, "category": _lifehack_category_label(duplicate["category"])}
    records.append(_lifehack_record(normalized, category, tags, source="user"))
    try:
        _save_lifehack_catalog(records)
    except OSError:
        return None
    return {"duplicate": False, "category": _lifehack_category_label(category)}


def update_lifehack(record_id, text):
    """Нормализует и обновляет запись каталога, сохраняя её статистику."""
    raw_text = _clean_lifehack_text(text)
    if not _lifehack_useful(raw_text):
        return None
    normalized, category, tags = _normalize_lifehack_with_ai(raw_text)
    if not _lifehack_useful(normalized):
        return None
    records = _load_lifehack_catalog()
    current = next((item for item in records if item.get("id") == str(record_id)), None)
    if current is None:
        return None
    duplicate = _lifehack_near_duplicate(
        normalized, [item for item in records if item.get("id") != str(record_id)]
    )
    if duplicate:
        return {"duplicate": True, "category": _lifehack_category_label(duplicate["category"])}
    current.update({
        "text": normalized,
        "category": category,
        "tags": tags,
        "source": "user",
        "enabled": True,
    })
    try:
        _save_lifehack_catalog(records)
    except OSError:
        return None
    return {"duplicate": False, "category": _lifehack_category_label(category)}


def delete_lifehack(record_id):
    records = _load_lifehack_catalog()
    remaining = [item for item in records if item.get("id") != str(record_id)]
    if len(remaining) == len(records):
        return False
    try:
        _save_lifehack_catalog(remaining)
    except OSError:
        return False
    return True


def _local_lifehack_candidates(cid, rain=False, hot=False, is_weekend=False):
    """Возвращает ещё не показанные записи из lifehacks.json с погодным приоритетом."""
    all_items = []
    for item in lifehack_records(include_disabled=False):
        category = _canonical_lifehack_category(item.get("category"))
        if category in {"здоровье", "деньги"}:
            continue
        text = str(item.get("text") or "").strip()
        if _lifehack_useful(text):
            all_items.append({
                **item,
                "category": category,
                "emoji": _LIFEHACK_CATEGORY_EMOJI.get(category, "💡"),
            })
    if not all_items:
        return []
    cid = str(cid)
    seen = set(store.get_list(config.LIFEHACK_KEY, cid))
    ctx_tags = (["rain"] if rain else []) + (["hot"] if hot else []) + ([] if is_weekend else ["work"])
    unseen = [item for item in all_items if item["id"] not in seen]
    contextual = [item for item in unseen if any(tag in item["tags"] for tag in ctx_tags)]
    candidates = contextual or unseen
    if not candidates:
        store.set_list(config.LIFEHACK_KEY, cid, [])
        candidates = all_items
    oldest = min(item.get("last_shown") or "" for item in candidates)
    return [item for item in candidates if (item.get("last_shown") or "") == oldest]


def _mark_local_lifehack_seen(cid, item):
    seen = list(store.get_list(config.LIFEHACK_KEY, cid))
    if item["id"] not in seen:
        seen.append(item["id"])
    store.set_list(config.LIFEHACK_KEY, cid, seen)
    records = _load_lifehack_catalog()
    for record in records:
        if record.get("id") == item.get("id"):
            record["shown_count"] = int(record.get("shown_count") or 0) + 1
            record["last_shown"] = datetime.now(TZ).isoformat()
            try:
                _save_lifehack_catalog(records)
            except OSError:
                _log.warning("myday: cannot update lifehack statistics", exc_info=True)
            break


def _lifehack_fallback(cid, rain=False, hot=False, is_weekend=False):
    """Аварийный путь из lifehacks.json, если AI-пул не дал совет."""
    candidates = _local_lifehack_candidates(cid, rain=rain, hot=hot, is_weekend=is_weekend)
    if not candidates:
        return "", ""
    tip = random.choice(candidates)
    _mark_local_lifehack_seen(cid, tip)
    return _lifehack_category_label(tip["category"]), tip["text"]


def _generate_lifehack_pool(cid):
    interests = []
    movies = store.get_list(config.WATCHLIST_KEY, cid)[:4]
    books = store.get_list(config.BOOKS_KEY, cid)[:4]
    if movies:
        interests.append(f"любит фильмы/сериалы: {', '.join(str(m) for m in movies if m)}")
    if books:
        interests.append(f"любит книги: {', '.join(str(b) for b in books if b)}")
    interest_block = ("Интересы пользователя: " + "; ".join(interests) + ".\n") if interests else ""
    cats_str = ", ".join(_LIFEHACK_CATEGORIES)
    nl_snippet = research.firecrawl_snippet("жизнь в Нидерландах советы быт бюрократия велосипед", 900)
    nl_ground_block = (
        f"Для категории 'путешествия' используй как источник этот реальный веб-контент "
        f"(не противоречь ему, не выдумывай факты про NL, если он есть):\n{nl_snippet}\n"
        if nl_snippet else ""
    )
    prompt = (
        f"Составь {_POOL_TARGET_ITEMS} практичных, не банальных советов для персональной "
        f"'Базы знаний' в утреннем уведомлении Telegram-бота.\n"
        f"Категории (используй только их): {cats_str}.\n"
        f"{interest_block}"
        f"{nl_ground_block}"
        "Каждый совет должен быть конкретным и применимым сразу, без общих фраз вроде "
        "'пейте больше воды' или 'высыпайтесь'.\n"
        "Пиши на 'ты' и давай ровно одно действие в одном предложении длиной 80-180 знаков. "
        "Обязательно укажи, что именно сделать, при каком условии или каким способом и какой "
        "практический результат это даст. Совет должен экономить время, предотвращать частую "
        "ошибку, упрощать бытовое действие или заметно улучшать результат. Не выдавай наблюдение, "
        "общеизвестный факт, мотивационную фразу или непроверяемое обещание за лайфхак. "
        "Не давай медицинских, юридических и финансовых рекомендаций.\n"
        "Для категории 'кухня': только практичные лайфхаки — что помогает готовить быстрее, "
        "улучшает вкус, исправляет частую ошибку или продлевает хранение продукта. Не используй "
        "фильмы, книги, знаменитостей и абстрактные идеи. Не предлагай целое блюдо вместо лайфхака. "
        "Один пункт — одно конкретное действие с понятным результатом "
        "(например: 'Чтобы омлет получился пышнее, добавь к яйцам ложку воды и готовь под "
        "крышкой на слабом огне').\n"
        'Верни JSON: {"tips": [{"category": "одна из категорий выше", "text": "совет"}]}'
    )
    try:
        d = ai.llm_json(prompt, 1800, tier="cheap", module="myday_utility")
    except Exception as e:
        _log.warning("myday: lifehack pool generation failed: %s", e)
        return []
    tips = d.get("tips") if isinstance(d, dict) else []
    out = []
    for t in tips or []:
        text = str((t or {}).get("text") or "").strip()
        cat = _canonical_lifehack_category((t or {}).get("category"))
        if _lifehack_useful(text):
            out.append((text, {"category": cat}))
    _store_ai_lifehacks(out)
    return out


def daily_lifehack(cid, rain=False, hot=False, is_weekend=False):
    """Смешанный совет из недельного AI-пула и локального lifehacks.json."""
    cid = str(cid)
    _pool_ensure_fresh(config.LIFEHACK_POOL_KEY, cid, "default", lambda: _generate_lifehack_pool(cid))
    bucket = _pool_get(config.LIFEHACK_POOL_KEY, cid, "default")
    items = bucket.get("items") or []
    local_candidates = _local_lifehack_candidates(
        cid, rain=rain, hot=hot, is_weekend=is_weekend,
    )
    # Контекстный приоритет среди непоказанных: дождь/жара -> гардероб, иначе любой.
    ctx_cat = "дом" if (rain or hot) else ""
    ai_unshown = [i for i in items if not i.get("shown_at") and _lifehack_useful(i.get("text"))]
    ai_preferred = [i for i in ai_unshown if ctx_cat and i.get("category") == ctx_cat]
    ai_candidates = ai_preferred or ai_unshown

    # Локальные записи должны регулярно попадать в выдачу даже при рабочем AI.
    # Доля локальной базы — около трети, а при пустом AI-пуле она становится полной.
    use_local = bool(local_candidates) and (
        not ai_candidates or random.random() < 0.35
    )
    if use_local:
        chosen = random.choice(local_candidates)
        _mark_local_lifehack_seen(cid, chosen)
        return _lifehack_category_label(chosen["category"]), chosen["text"]

    if ai_candidates:
        chosen = random.choice(ai_candidates)
        target_id = chosen["id"]

        def mut(data):
            b = data.setdefault(cid, {}).setdefault("default", {})
            for it in b.get("items") or []:
                if it.get("id") == target_id:
                    it["shown_at"] = int(datetime.now(TZ).timestamp())
                    break
            return data, True

        store.mutate_kv(config.LIFEHACK_POOL_KEY, mut)
        cat = _canonical_lifehack_category(chosen.get("category"))
        return _lifehack_category_label(cat), chosen["text"]

    if local_candidates:
        chosen = random.choice(local_candidates)
        _mark_local_lifehack_seen(cid, chosen)
        return _lifehack_category_label(chosen["category"]), chosen["text"]
    return _lifehack_fallback(cid, rain=rain, hot=hot, is_weekend=is_weekend)


def kitchen_lifehacks(cid, n=3):
    """N кухонных лайфхаков из того же недельного пула, что и «Мой день» (категория
    «кухня») — без отдельного AI-вызова на каждый заход в «Готовку». Помечает выданные
    как показанные, чтобы при следующем входе на этой неделе не повторяться."""
    cid = str(cid)
    _pool_ensure_fresh(config.LIFEHACK_POOL_KEY, cid, "default", lambda: _generate_lifehack_pool(cid))
    bucket = _pool_get(config.LIFEHACK_POOL_KEY, cid, "default")
    items = bucket.get("items") or []
    unshown_kitchen = [
        i for i in items
        if i.get("category") == "кухня" and not i.get("shown_at") and _lifehack_useful(i.get("text"))
    ]
    if len(unshown_kitchen) < n:
        # даже показанные ранее кухонные лучше, чем пустой экран - лучше повторить, чем показать ничего
        any_kitchen = [
            i for i in items
            if i.get("category") == "кухня" and _lifehack_useful(i.get("text"))
        ]
        unshown_kitchen = any_kitchen if len(any_kitchen) >= n else unshown_kitchen
    chosen = unshown_kitchen[:n]
    if chosen:
        ids = {c["id"] for c in chosen}

        def mut(data):
            b = data.setdefault(cid, {}).setdefault("default", {})
            for it in b.get("items") or []:
                if it.get("id") in ids and not it.get("shown_at"):
                    it["shown_at"] = int(datetime.now(TZ).timestamp())
            return data, True

        store.mutate_kv(config.LIFEHACK_POOL_KEY, mut)
        return [c["text"] for c in chosen]
    fallback = []
    for _ in range(n):
        _label, text = _lifehack_fallback(cid)
        if text and text not in fallback:
            fallback.append(text)
    return fallback



_QUOTE_RESET_AFTER = 15  # сбрасываем anti-repeat после N авторов


def _item_text(item):
    """Текст элемента списка: элемент может быть строкой или {"id":..., "value": строка}
    (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _build_quote_context(cid):
    """Собирает контекст пользователя для персонализации цитаты."""
    movies = store.get_list(config.WATCHLIST_KEY, cid)[:6]
    books = store.get_list(config.BOOKS_KEY, cid)[:6]
    artists = store.get_list(config.ARTISTS_KEY, cid)[:6]
    seen_authors = store.get_list(config.QUOTE_AUTHORS_KEY, cid)
    if len(seen_authors) >= _QUOTE_RESET_AFTER:
        store.set_list(config.QUOTE_AUTHORS_KEY, cid, [])
        seen_authors = []
    return {
        "movies": [_item_text(m) for m in movies if _item_text(m)],
        "books": [_item_text(b) for b in books if _item_text(b)],
        "artists": [_item_text(a) for a in artists if _item_text(a)],
        "seen_authors": seen_authors,
    }


def _fetch_quote(cid=None):
    """Персонализированная цитата дня с anti-repeat по авторам."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if cid:
        cached = store.get_profile(cid).get("myday_quote_cache") or {}
        if cached.get("date") == today and isinstance(cached.get("data"), dict):
            return cached["data"]
    ctx = _build_quote_context(cid) if cid else {
        "movies": [], "books": [], "artists": [], "focus": "", "seen_authors": []
    }

    parts = []
    if ctx["movies"]:
        parts.append(f"Любимые фильмы/сериалы: {', '.join(ctx['movies'])}")
    if ctx["books"]:
        parts.append(f"Любимые книги: {', '.join(ctx['books'])}")
    if ctx["artists"]:
        parts.append(f"Любимые исполнители: {', '.join(ctx['artists'])}")

    context_block = ("\n".join(parts) + "\n\n") if parts else ""

    avoid_block = ""
    if ctx["seen_authors"]:
        avoid_block = f"Этих авторов уже показывали — не повторяй: {', '.join(ctx['seen_authors'])}.\n\n"

    if parts:
        author_hint = (
            "Выбери автора, чьё мировоззрение или творчество перекликается с интересами человека выше. "
            "Это может быть режиссёр, писатель, музыкант, философ, предприниматель или учёный — "
            "главное, чтобы цитата резонировала с его вкусами или фокусом дня."
        )
    else:
        author_hint = (
            "Выбери мыслителя или предпринимателя (Сенека, Марк Аврелий, Навал Равикант, "
            "Монтень, Шопенгауэр, Эпиктет, Чарли Мунгер — без банальностей)."
        )

    prompt = (
        f"{context_block}"
        f"{avoid_block}"
        f"Дай одну нестандартную цитату (1-2 предложения). {author_hint} "
        "Цитата должна быть реальной — не выдумывай. "
        'Строго JSON: {"quote": "текст на русском", "src": "Автор"}. '
        "Только кириллица, никаких латинских букв в тексте цитаты."
    )

    d = ai.llm_json(prompt, 200, tier="cheap", module="myday_utility")
    if not isinstance(d, dict):
        return {}

    src = (d.get("src") or "").strip()
    if src and cid:
        seen = store.get_list(config.QUOTE_AUTHORS_KEY, cid)
        if src not in seen:
            store.set_list(config.QUOTE_AUTHORS_KEY, cid, seen + [src])
    if cid:
        profile = store.get_profile(cid)
        profile["myday_quote_cache"] = {"date": today, "data": d}
        store.set_profile(cid, profile)

    return d

def _cap(s):
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s

def _quote_valid(q):
    """Пропускает цитату если LLM вставил латинское слово в кириллический текст."""
    return not re.search(r'[а-яА-ЯЁё][a-zA-Z]|[a-zA-Z][а-яА-ЯЁё]', q or "")


_QUOTE_MAX_CHARS = 220  # ограничивает цитату 2-3 строками в Telegram-карточке


def _clip_quote(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= _QUOTE_MAX_CHARS:
        return text
    return text[:_QUOTE_MAX_CHARS - 1].rstrip(" ,.;:") + "…"

def _word_of_day(cid):
    """Запись дня для карточки 'Мой день' — тот же материал, что показывает
    экран 'Обучение' (см. learning.select_daily_material): выбор и его
    побочные эффекты (last_shown_at) живут в learning.py, здесь только формат."""
    entry = learning.select_daily_material(cid)
    lang = learning._active_language_code(cid)
    if not entry:
        return "", lang
    term = dictionary.entry_term(entry)
    ru = dictionary.entry_translation(entry).replace(";", ",")
    return f"{_cap(term)} → {_cap(ru)}.", lang

_DAY_CACHE_VERSION = 7
_day_cache = {}  # cid -> {"date":..., "version":..., "text":..., "entities":..., "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)
    prof = store.get_profile(cid)
    if prof.pop("myday_home_cache", None) is not None:
        store.set_profile(cid, prof)


def _load_day_cache(cid, today):
    cached = _day_cache.get(str(cid))
    if cached and cached.get("date") == today and cached.get("version") == _DAY_CACHE_VERSION:
        return cached
    prof = store.get_profile(cid)
    saved = prof.get("myday_home_cache")
    if (not isinstance(saved, dict) or saved.get("date") != today
            or saved.get("version") != _DAY_CACHE_VERSION or not saved.get("text")):
        return None
    cached = {
        "date": today,
        "version": _DAY_CACHE_VERSION,
        "text": saved["text"],
        "entities": util.entities_from_json(saved.get("entities")),
        "ts": saved.get("ts", 0),
    }
    _day_cache[str(cid)] = cached
    return cached


def _save_day_cache(cid, today, text, entities, ts):
    cached = {"date": today, "version": _DAY_CACHE_VERSION, "text": text, "entities": entities, "ts": ts}
    _day_cache[str(cid)] = cached
    prof = store.get_profile(cid)
    prof["myday_home_cache"] = {
        "date": today,
        "version": _DAY_CACHE_VERSION,
        "text": text,
        "entities": util.entities_to_json(entities),
        "ts": ts,
    }
    store.set_profile(cid, prof)
    return cached

def _day_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])

def _build_day_text(cid, *, refresh_current=False):
    s = store.get_settings(cid)
    try:
        data = weather.fetch_weather(s["lat"], s["lon"], 2)
        if refresh_current:
            current = weather.fetch_current_conditions(s["lat"], s["lon"])
            if current:
                data = {**data, "current": current}
        weather_error = None
    except Exception as e:
        _log.warning("myday: fetch_weather failed: %s", e)
        data = None
        weather_error = e

    current_code = None
    current_precipitation = ""
    if data and (data.get("daily") or {}).get("time"):
        d = data["daily"]
        day_str = d["time"][0]
        code = d["weathercode"][0]
        tmax = d["temperature_2m_max"][0]
        rain_day = d["precipitation_probability_max"][0] or 0
        rain_mm_day = (d.get("precipitation_sum") or [None])[0] if d.get("precipitation_sum") else None
        wind_ms = d["windspeed_10m_max"][0] or 0
        daytime = weather.daytime_outfit_weather(
            data, day_str, tmax, wind_ms, rain_day, rain_mm_day, code,
        )
        rain = daytime["rain_prob"]
        rain_mm = daytime["rain_mm"]
        current_code = (data.get("current") or {}).get("weathercode")
        current_precipitation = weather.current_precipitation_text(current_code)
        display_code = current_code if current_precipitation else code
        icon = weather.weather_icon(display_code, tmax, rain, wind_ms, rain_mm)
        rain_p = weather._periods(data, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        # ветер: показываем всегда, в одной строке с температурой и дождём, без эмодзи
        _, wword = weather.wind_scale(wind_ms)
        wind_p = weather._periods(data, day_str, "windspeed_10m", 6)
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""
        wind_part = f"{wword} до {wind_ms:.0f} м/с{wind_when}"
        weather_icon = icon
        rain_part = weather.rain_text(rain, rain_mm, rain_when)
        if current_precipitation:
            rain_part = current_precipitation
            if rain and rain_when:
                rain_part += f" · вероятность {rain:.0f}%{rain_when}"
        weather_line = f"до {tmax:+.0f}°C" + (f" · {rain_part}" if rain_part else "") + f" · {wind_part}"
        hum_title, hum_line = weather.humidity_phrase(data, day_str, tmax, s.get("cc", ""))
    else:
        rain = 0
        rain_mm = None
        tmax = None
        response = getattr(weather_error, "response", None)
        status = getattr(response, "status_code", None)
        weather_icon = "☁️"
        if isinstance(weather_error, weather.WeatherDailyLimitExceeded) or status == 429:
            weather_line = f"Погодный лимит исчерпан. {weather.WEATHER_LIMIT_FALLBACK}"
        else:
            weather_line = "Сейчас недоступна — остальная сводка всё равно готова."
        hum_title, hum_line = "", ""

    now = datetime.now(TZ)
    weekday_name = _WEEKDAY_SHORT[now.weekday()]
    is_weekend = now.weekday() >= 5
    word_line, word_lang = _word_of_day(cid)
    import balance
    import wardrobe
    mood = balance.health_focus(cid).get("phrase", "")
    outfit_items = wardrobe.get_cached_outfit_items(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    _hack_cat, hack_text = daily_lifehack(
        cid, rain=(rain >= 40 or bool(current_precipitation)),
        hot=(tmax is not None and tmax >= 24), is_weekend=is_weekend)
    try:
        q_data = _fetch_quote(cid)
    except Exception as e:
        _log.warning("myday: _fetch_quote failed: %s", e)
        q_data = {}
    raw_quote = _clip_quote(_strip_quotes(q_data.get("quote", "")))
    quote_text, quote_author = "", ""
    if raw_quote and _quote_valid(raw_quote):
        quote_text = esc(raw_quote)
        quote_author = esc(q_data.get("src", "")).strip()
    msg = myday_ui.day_summary(
        header,
        s.get("city", ""),
        weather_icon=weather_icon,
        weather_line=weather_line,
        humidity_line=f"{hum_title} · {hum_line}" if hum_title else "",
        word_line=word_line,
        word_lang=word_lang,
        mood=mood,
        outfit_items=outfit_items,
        lifehack=hack_text,
        quote_text=quote_text,
        quote_author=quote_author,
    )
    text = msg.text
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(
        text,
        weather._rain_real(rain, rain_mm)
        or current_code in weather.RAIN_WEATHER_CODES,
    )
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    return text, msg.entities

async def _maybe_prompt_dict_seed(bot, cid):
    """Если словарь на активном языке пуст, а seed ещё не предлагали - предложить
    один раз наполнить словарь (§28 CLAUDE.md: стартовые слова по language/level)."""
    try:
        lang = learning._active_language_code(cid)
        words = dictionary.DictionaryRepository(cid).all()
        has_words = any(
            dictionary.entry_term(w) and dictionary.entry_language(w) == lang
            for w in words
        )
        if has_words:
            return
        prof = store.get_profile(cid)
        if prof.get("_myday_seed_prompted"):
            return
        prof["_myday_seed_prompted"] = True
        store.set_profile(cid, prof)
        await dictionary_seed.send_seed_intro(bot, cid, lang)
    except Exception as e:
        _log.warning("myday: _maybe_prompt_dict_seed failed: %s", e)


async def send_plany(bot, cid, force=False, show_loading=True):
    """Собирает и отправляет сводку «Мой день» без промежуточного «Собираю...» —
    пользователь сразу получает готовый результат одним сообщением. show_loading
    сохранён в сигнатуре для обратной совместимости вызовов, но больше не шлёт
    отдельное сообщение — при холодном кэше показывается только typing-индикатор."""
    import time as _time
    await _maybe_prompt_dict_seed(bot, cid)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = None if force else _load_day_cache(cid, today)
    stale = cache is None
    if stale:
        try:
            await bot.send_chat_action(chat_id=cid, action="typing")
        except Exception:
            pass
        try:
            text, entities = await asyncio.to_thread(
                _build_day_text, cid, refresh_current=force,
            )
        except Exception as e:
            await verify.safe_error(bot, cid, e, back="m_myday"); return
        cache = _save_day_cache(cid, today, text, entities, _time.time())
    cached = cache
    await bot.send_message(
        chat_id=cid, text=cached["text"], entities=cached.get("entities"),
        reply_markup=_day_menu_kb(),
    )


async def warm_day_cache(cid):
    """Фоново собирает «Мой день» один раз и сохраняет переживающий рестарт кэш."""
    import time as _time
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cached = _load_day_cache(cid, today)
    if cached is not None:
        return True
    text, entities = await asyncio.to_thread(_build_day_text, cid)
    _save_day_cache(cid, today, text, entities, _time.time())
    return True
