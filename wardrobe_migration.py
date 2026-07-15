"""Миграция сохранённых вещей к актуальной физической схеме."""

import logging
import re

import ai
import store
from wardrobe_model import (
    ATTRIBUTE_SCHEMA_VERSION,
    WARMTH_TEMP_RANGE,
    ZONE_COMPAT,
    clean_physical_name,
    flat_items,
    normalize_warmth,
    strip_internal_tags,
)

_log = logging.getLogger(__name__)
_BATCH_SIZE = 15
_BATCH_MAX_TOKENS = 2000
_WIND_MARKERS = ("ветровк", "ветрозащит", "windproof", "анорак")
_RAIN_MARKERS = ("дождевик", "непромокаем", "waterproof", "raincoat", "плащ")
_ATTR_DEFAULTS = {
    "last_used": None, "use_count": 0, "accepted_count": 0, "rejected_count": 0,
    "season": [], "temp_range": None, "compatible_categories": [], "colors": [],
    "formality": None, "fit": None, "occasions": [], "material": None,
    "length": None, "rain_ok": False, "wind_ok": False,
}


def needs_migration(item):
    try:
        version = int(item.get("attribute_schema_version") or 0)
    except (AttributeError, TypeError, ValueError):
        version = 0
    return version < ATTRIBUTE_SCHEMA_VERSION


def migration_count(wardrobe):
    return sum(1 for _zone, _subcategory, item in flat_items(wardrobe) if needs_migration(item))


def _ensure_defaults(item):
    for key, value in _ATTR_DEFAULTS.items():
        item.setdefault(key, list(value) if isinstance(value, list) else value)


def _safe_clean_name(source_name, candidate):
    """Разрешает перестановку слов, но не добавление новых деталей в название."""
    source_clean = strip_internal_tags(clean_physical_name(source_name) or source_name)
    candidate_clean = strip_internal_tags(clean_physical_name(candidate))
    if not candidate_clean:
        return source_clean
    source_tokens = set(re.findall(r"[a-zа-яё0-9]+", source_clean.casefold()))
    candidate_tokens = set(re.findall(r"[a-zа-яё0-9]+", candidate_clean.casefold()))
    return candidate_clean if candidate_tokens <= source_tokens else source_clean


def _facts_text(item, source_name):
    values = [source_name]
    values.extend(str(item.get(key) or "") for key in (
        "color", "color_secondary", "material", "length", "fit", "formality",
    ))
    for key in ("colors", "season", "occasions"):
        values.extend(str(value) for value in (item.get(key) or []))
    return " ".join(values).casefold().replace("ё", "е")


def _grounded(value, facts):
    tokens = re.findall(r"[a-zа-я0-9]+", str(value or "").casefold().replace("ё", "е"))
    fact_tokens = re.findall(r"[a-zа-я0-9]+", facts)
    if not tokens:
        return False
    for token in tokens:
        stem = token[:4] if len(token) > 3 else token
        if not any((fact[:4] if len(fact) > 3 else fact) == stem for fact in fact_tokens):
            # Частая русская пара: «лён» в поле и «льняной» в названии.
            if not (token == "лен" and any(fact.startswith("льня") for fact in fact_tokens)):
                return False
    return True


def _grounded_list(candidate, existing, facts):
    if existing:
        return [str(value).strip() for value in existing if str(value).strip()]
    return [str(value).strip() for value in (candidate or []) if _grounded(value, facts)]


def _grounded_field(candidate, existing, facts):
    if existing:
        return str(existing).strip() or None
    return str(candidate).strip() if candidate and _grounded(candidate, facts) else None


def _safe_temp_range(existing, warmth):
    return existing or list(WARMTH_TEMP_RANGE[warmth])


async def migrate_item_attrs(cid, wardrobe=None):
    """Пакетно обновляет старые вещи; тепло и имя имеют локальный fallback."""
    wardrobe = wardrobe or store.load_wardrobe(cid)
    todo = [(zone, subcategory, item) for zone, subcategory, item in flat_items(wardrobe) if needs_migration(item)]
    if not todo:
        return wardrobe

    prompt_template = """Обнови физические атрибуты сохранённых вещей. Для КАЖДОЙ верни:
clean_name — естественное русское название с цветом перед типом вещи и без слов о тепле,
плотности и сезоне (например «Тёмно-оливковые брюки с карманами»);
warmth — строго одно из: лёгкие, обычные, тёплые (толстые/плотные/утеплённые = тёплые);
colors (1-2 цвета), season (лето/деми/зима), material или пусто, length или пусто,
formality, fit, occasions, rain_ok и wind_ok.
Не придумывай свойства: если они не следуют из названия, используй warmth=обычные,
а material/length оставь пустыми.
Вещи:
{listing}
JSON: {{"items":[{{"i":0,"clean_name":"","warmth":"обычные","colors":[],"season":[],"material":"","length":"","formality":"","fit":"","occasions":[],"rain_ok":false,"wind_ok":false}}]}}"""
    by_index = {}
    completed_indexes = set()
    for start in range(0, len(todo), _BATCH_SIZE):
        batch = todo[start:start + _BATCH_SIZE]
        listing = "\n".join(
            f"{start + offset}: {item.get('name', '')} ({zone}/{subcategory})"
            for offset, (zone, subcategory, item) in enumerate(batch)
        )
        try:
            data = await ai.allm_json(
                prompt_template.format(listing=listing),
                _BATCH_MAX_TOKENS,
                tier="cheap",
                module="wardrobe",
            )
            parsed_batch = {}
            for parsed_item in data.get("items") or []:
                try:
                    parsed_index = int(parsed_item.get("i"))
                except (AttributeError, TypeError, ValueError):
                    continue
                if start <= parsed_index < start + len(batch):
                    parsed_batch[parsed_index] = parsed_item
            by_index.update(parsed_batch)
            completed_indexes.update(parsed_batch)
        except Exception as error:
            _log.warning("wardrobe schema migration batch uses local fallback: %r", error, exc_info=True)

    def _mut(current):
        current_todo = [entry for entry in flat_items(current) if needs_migration(entry[2])]
        for index, (zone, subcategory, item) in enumerate(current_todo):
            parsed = by_index.get(index, {})
            source_name = str(item.get("name") or "")
            facts = _facts_text(item, source_name)
            item["name"] = _safe_clean_name(source_name, parsed.get("clean_name"))
            item["zone"] = item.get("zone") or zone
            item["subcategory"] = item.get("subcategory") or subcategory
            item["warmth"] = normalize_warmth(parsed.get("warmth") or item.get("warmth"), source_name)
            item["colors"] = _grounded_list(parsed.get("colors"), item.get("colors"), facts)
            if not item["colors"] and item.get("color"):
                item["colors"] = [str(item["color"]).strip()]
            item["season"] = _grounded_list(parsed.get("season"), item.get("season"), facts)
            for key in ("material", "length", "formality", "fit"):
                item[key] = _grounded_field(parsed.get(key), item.get(key), facts)
            item["occasions"] = _grounded_list(parsed.get("occasions"), item.get("occasions"), facts)
            item["temp_range"] = _safe_temp_range(item.get("temp_range"), item["warmth"])
            rain_in_name = any(marker in facts for marker in _RAIN_MARKERS)
            wind_in_name = any(marker in facts for marker in _WIND_MARKERS)
            item["rain_ok"] = bool(item.get("rain_ok") or (parsed.get("rain_ok") and rain_in_name))
            item["wind_ok"] = bool(item.get("wind_ok") or (parsed.get("wind_ok") and wind_in_name))
            item["compatible_categories"] = ZONE_COMPAT.get(item.get("zone") or zone, [])
            _ensure_defaults(item)
            if index in completed_indexes:
                item["attribute_schema_version"] = ATTRIBUTE_SCHEMA_VERSION

    return store.mutate_wardrobe(cid, _mut)
