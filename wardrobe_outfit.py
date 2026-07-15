"""Локальный движок подбора и оценки образа."""

from datetime import datetime, timedelta

import config
import store
from wardrobe_model import ZONE_ORDER, flat_items as _flat_wardrobe_items

WARDROBE_OUTERWEAR_MAX_TEMP = 20
NEUTRAL_COLORS = ("бел", "чёрн", "черн", "сер", "беж", "сини", "деним", "джинс")

def _day_key():
    return datetime.now(config.TZ).date().isoformat()

# ---------- локальный подбор образа (без AI) ----------
_TEMP_CONFLICT_MARGIN = 10  # °C — насколько диапазон temp_range должен разойтись с погодой, чтобы вещь исключалась


def _temp_conflicts(item, weather_ctx):
    tr = item.get("temp_range")
    tmax = weather_ctx.get("tmax")
    if not tr or tmax is None:
        return False
    lo, hi = tr
    return tmax > hi + _TEMP_CONFLICT_MARGIN or tmax < lo - _TEMP_CONFLICT_MARGIN


def select_outfit_candidates(w, weather_ctx):
    """Жёсткая фильтрация кандидатов по зонам (не скоринг). Возвращает
    {zone: [item, ...]} — зона «Верхняя одежда» опциональна по погоде и может
    вернуть пустой список кандидатов, даже если в шкафу есть куртки."""
    candidates = {}
    for zone in store.ZONE_ORDER:
        if zone == "Другое":
            continue
        items = [it for _s, items in (w.get("zones", {}).get(zone, {}) or {}).items() for it in items]
        items = [it for it in items if not _temp_conflicts(it, weather_ctx)]
        if zone == "Верхняя одежда":
            too_warm_for_outer = (weather_ctx.get("tmax") or 0) > WARDROBE_OUTERWEAR_MAX_TEMP
            outerwear_needed = weather_ctx.get("has_rain") or weather_ctx.get("strong_wind") or not too_warm_for_outer
            if not outerwear_needed:
                candidates[zone] = []
                continue
            if weather_ctx.get("has_rain"):
                items = sorted(items, key=lambda it: not it.get("rain_ok"))
        candidates[zone] = items
    return candidates


def _is_neutral_color(color):
    c = str(color or "").lower()
    return any(p in c for p in NEUTRAL_COLORS)


def _color_penalty(items):
    """Штраф за 2+ ярких (не-нейтральных) цвета одновременно в наборе."""
    bright = []
    for it in items:
        for c in (it.get("colors") or []):
            if not _is_neutral_color(c):
                bright.append(c.lower())
    if len(bright) <= 1:
        return 0
    return -10 * (len(bright) - 1)


def score_outfit(items, weather_ctx, wardrobe_history, prefs_text):
    """Скоринг одной комбинации вещей (одна вещь на зону, максимум 5 вещей).
    Возвращает float — выше лучше."""
    score = 0.0
    tmax = weather_ctx.get("tmax")
    for it in items:
        tr = it.get("temp_range")
        if tr and tmax is not None and tr[0] <= tmax <= tr[1]:
            score += 5
        if it.get("zone") == "Аксессуары":
            # У аксессуаров обычно нет temp_range (не привязаны к погоде) — без
            # небольшого бонуса они никогда не выигрывают у варианта "без аксессуара"
            # при равном score и не попадают в образ вовсе.
            score += 2
    score += _color_penalty(items)
    prefs_low = str(prefs_text or "").lower()
    for it in items:
        colors = [str(color).lower() for color in (it.get("colors") or [])]
        fit = str(it.get("fit") or "").lower()
        style = " ".join((str(it.get("style") or ""), str(it.get("formality") or ""))).lower()
        name = str(it.get("name") or "").lower()
        if fit and f"посадка одежды: {fit}" in prefs_low:
            score += 3
        if "яркие цвета" in prefs_low and any(not _is_neutral_color(color) for color in colors):
            score -= 6
        if "крупные принты" in prefs_low and any(word in name for word in ("крупный принт", "логотип", "график")):
            score -= 7
        if "узкий крой" in prefs_low and any(word in fit + " " + name for word in ("узк", "скинни", "slim")):
            score -= 8
        if "слишком спортивное" in prefs_low and any(word in style + " " + name for word in ("sport", "спортив")):
            score -= 5
        if "тёмные" in prefs_low and any(any(marker in color for marker in ("чёрн", "черн", "тём", "темн", "бордов")) for color in colors):
            score += 1
        if "светлые" in prefs_low and any(any(marker in color for marker in ("бел", "беж", "светл")) for color in colors):
            score += 1
        # Маленький тай-брейкер, не решающий фактор: при прочих равных чуть
        # предпочитаем вещь, которая реже носилась в последнее время — но только
        # после того, как цельность образа (цвет/погода/антиповтор) уже учтена.
        score -= 0.1 * min(int(it.get("use_count", 0)), 20)
    item_ids = {it.get("id") for it in items}
    cutoff_3d = (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_3d:
            continue
        if item_ids & set(entry.get("item_ids") or []):
            score -= 3
    cutoff_7d = (datetime.now(config.TZ) - timedelta(days=7)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_7d:
            continue
        if item_ids and item_ids == set(entry.get("item_ids") or []):
            score -= 100
    if prefs_text:
        avoid_raw = str(prefs_text)
        for it in items:
            for c in (it.get("colors") or []):
                if c and c.lower() in avoid_raw.lower() and "нежелательные цвета" in avoid_raw.lower():
                    score -= 4
    return score


def _top_candidates(items, limit=3):
    """Ограничивает размер зоны до перебора комбинаций (см. _combos) без
    предвзятости по частоте ношения — какая вещь выигрывает, решает score_outfit
    (цельность образа), а не то, что вещь ещё не использовалась."""
    return items[:limit]


def pick_best_outfit(w, weather_ctx, wardrobe_history, prefs_text, previous_item_ids=None):
    """Собирает кандидатов, перебирает ограниченные комбинации (топ-3 на зону),
    возвращает лучший набор вещей (list[item]) или None, если нет кандидатов хотя
    бы на одну обязательную зону (Верх/Низ/Обувь)."""
    candidates = select_outfit_candidates(w, weather_ctx)
    required = ["Верх", "Низ", "Обувь"]
    if any(not candidates.get(z) for z in required):
        return None

    def _combos():
        import itertools
        pools = [_top_candidates(candidates[z]) for z in required]
        optional_zones = [z for z in ("Верхняя одежда", "Аксессуары") if candidates.get(z)]
        for zone in optional_zones:
            pools.append([None] + _top_candidates(candidates[zone], limit=2))
        for combo in itertools.product(*pools):
            yield [it for it in combo if it is not None]

    previous_item_ids = set(previous_item_ids or [])
    combos = list(_combos())
    if previous_item_ids:
        # «Другой образ» должен менять основу комплекта, а не одну случайную вещь.
        # Для полного набора из 4–5 элементов требуем минимум две замены; для
        # маленького шкафа оставляем честную возможность заменить хотя бы одну.
        min_changes = 2 if any(len(combo) >= 4 for combo in combos) else 1
        combos = [combo for combo in combos
                  if len(previous_item_ids - {it.get("id") for it in combo}) >= min_changes]
    scored = [(score_outfit(combo, weather_ctx, wardrobe_history, prefs_text), combo) for combo in combos]
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_combo = scored[0]
    if best_score <= -50:
        # Похоже, единственный приемлемый вариант — это тот самый 7-дневный повтор
        # (guard). Пересчитываем без 7-дневного штрафа — маленький гардероб важнее антиповтора.
        rescored = [(score_outfit(combo, weather_ctx, [
            e for e in wardrobe_history
            if e.get("date", "") >= (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
        ], prefs_text), combo) for _s, combo in scored]
        rescored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_combo = rescored[0]
    return best_combo


# ---------- текст образа: локальный fallback ----------
def build_outfit_reasons(items, weather_ctx, score_details=None):
    """До 3 строк, каждая — про конкретную вещь/погоду, не шаблонная общая фраза.

    Все шаблоны — конструкция "вещь — свойство" (тире, без глагольного
    согласования рода/числа с названием вещи): названия вещей — свободный текст
    от AI, их род/число не гарантированы ("Дождевик" муж., "Шорты" мн.ч.)."""
    reasons = []
    colors = [c for it in items for c in (it.get("colors") or [])]
    bright = [c for c in colors if not _is_neutral_color(c)]
    neutral_anchor = next((it for it in items if any(_is_neutral_color(c) for c in (it.get("colors") or []))), None)
    if neutral_anchor and len(set(bright)) >= 2:
        reasons.append(
            f"{neutral_anchor.get('name')} — нейтральная база, держит {' и '.join(sorted(set(bright))[:2])} в одной палитре."
        )
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("has_rain") and outer.get("rain_ok"):
        reasons.append(f"{outer.get('name')} — защита от дождя.")
    elif weather_ctx.get("has_rain") and not (outer and outer.get("rain_ok")):
        reasons.append("Дождевика или непромокаемой куртки в шкафу нет — сегодня без защиты от дождя.")
    elif outer and weather_ctx.get("warm"):
        reasons.append(f"{outer.get('name')} — пригодится утром, после обеда можно убрать.")
    low = next((it for it in items if it.get("zone") == "Низ"), None)
    if low and weather_ctx.get("hot"):
        reasons.append(f"{low.get('name')} — без перегрева в жару.")
    top = next((it for it in items if it.get("zone") == "Верх"), None)
    shoe = next((it for it in items if it.get("zone") == "Обувь"), None)
    if top and low and not reasons:
        top_fit = str(top.get("fit") or top.get("name") or "").lower()
        low_fit = str(low.get("fit") or low.get("name") or "").lower()
        if any(x in top_fit for x in ("свобод", "оверсайз")) and any(x in low_fit for x in ("широк", "свобод")):
            reasons.append("Свободный верх поддерживает ширину брюк и собирает цельный силуэт.")
        else:
            reasons.append("Пропорции верха и низа сохраняют силуэт собранным.")
    if shoe and len(reasons) < 2:
        shoe_text = str(shoe.get("name") or "").lower()
        if any(x in shoe_text for x in ("бел", "светл")):
            reasons.append("Светлая обувь облегчает нижнюю часть образа.")
        else:
            reasons.append("Обувь добавляет нижней части нужный визуальный вес.")
    return reasons[:3]


_LONG_SLEEVE_MARKERS = ("рубаш", "свитер", "худи")


def build_style_tip(items, weather_ctx=None):
    """Один совет по носке, использующий только вещи из items. Пустая строка,
    если нет подходящего шаблона — не выдумываем совет ради совета."""
    weather_ctx = weather_ctx or {}
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("warm"):
        return "Оставь верхний слой расстёгнутым."
    sleeved = next((it for it in items
                    if it.get("zone") == "Верх" and any(m in str(it.get("name", "")).lower() for m in _LONG_SLEEVE_MARKERS)),
                   None)
    if sleeved:
        return "Подверни рукава, верх оставь навыпуск."
    low = next((it for it in items if it.get("zone") == "Низ"), None)
    if low and any(x in str(low.get("name") or "").lower() for x in ("брюк", "джинс", "чинос")):
        return "Слегка подверни брюки, чтобы открыть обувь."
    return ""


def build_wardrobe_insight(cid, items, wardrobe_history):
    """Один инсайт по фиксированному приоритету правил, первое совпавшее — оно и
    возвращается. None, если ничего не подошло."""
    item_ids = {it.get("id") for it in items}
    if wardrobe_history:
        last = wardrobe_history[-1]
        if item_ids and item_ids == set(last.get("item_ids") or []):
            return "Этот образ был и вчера."
    today = datetime.now(config.TZ).date()
    for it in items:
        last_used = it.get("last_used")
        if last_used:
            try:
                days = (today - datetime.fromisoformat(last_used).date()).days
            except ValueError:
                days = None
            if days is not None and days > 14:
                return f"{it.get('name')} — впервые за {days} дней."
    if len(wardrobe_history) >= 3:
        last3 = wardrobe_history[-3:]
        for it in items:
            if all(it.get("id") in (e.get("item_ids") or []) for e in last3):
                return f"{it.get('name')} — в последних образах подряд."
    return None


def save_outfit_feedback(cid, item_ids, weather_tags):
    """Вызывается только при НОВОЙ генерации образа (не при показе кэша дня):
    use_count/last_used вещей + запись в персистентную историю образов."""
    today = _day_key()
    id_set = set(item_ids)

    def _mut(w):
        for _zone, _subcat, item in _flat_wardrobe_items(w):
            if item.get("id") in id_set:
                item["use_count"] = int(item.get("use_count", 0)) + 1
                item["last_used"] = today

    store.mutate_wardrobe(cid, _mut)
    store.add_wardrobe_history_entry(cid, today, weather_tags, item_ids)
