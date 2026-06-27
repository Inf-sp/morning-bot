"""Память пользователя («бот учится на тебе»): фокус дня, фидбек гардероба, наблюдения, Лагом, предпочтения.

Тонкий доменный слой поверх профиля в store (config.PROFILE_KEY). Без LLM и сети.
Профиль - dict на пользователя: {"focus": {...}, "wardrobe_fb": [...], "observations": [...], "lagom": [...], "prefs": [...]}.
"""
from datetime import date, datetime
import json
import re
from pathlib import Path
import config
import store

_HERE = Path(__file__).parent

_OBS_CAP = 30
_FB_CAP = 20

# коды вердиктов фидбека гардероба -> человеческие ярлыки
WARDROBE_VERDICTS = {
    "worn": "надел",
    "nostyle": "не нравится",
}


def _today():
    return datetime.now(config.TZ).date().isoformat()


# ---------- наблюдения ----------
def add_observation(cid, tag, text):
    """Лента наблюдений о пользователе (что сработало/что игнорирует). Cap _OBS_CAP."""
    text = (text or "").strip()
    if not text:
        return
    prof = store.get_profile(cid)
    obs = prof.get("observations", [])
    obs.append({"date": _today(), "tag": tag, "text": text})
    prof["observations"] = obs[-_OBS_CAP:]
    store.set_profile(cid, prof)


def observations(cid, tag=None):
    obs = store.get_profile(cid).get("observations", [])
    return [o for o in obs if tag is None or o.get("tag") == tag]


# ---------- фокус дня ----------
def set_focus(cid, text):
    """Сохранить фокус на завтра (с датой). Пустой текст - очистка."""
    prof = store.get_profile(cid)
    text = (text or "").strip()
    if text:
        prof["focus"] = {"date": _today(), "text": text}
    else:
        prof.pop("focus", None)
    store.set_profile(cid, prof)


def get_focus(cid):
    """Сырой фокус {"date","text"} или {}."""
    return store.get_profile(cid).get("focus", {}) or {}


def fresh_focus(cid, max_age_days=1):
    """Текст фокуса, если он свежий (<= max_age_days от сегодня), иначе ''."""
    f = get_focus(cid)
    txt = (f.get("text") or "").strip()
    if not txt:
        return ""
    try:
        age = (date.fromisoformat(_today()) - date.fromisoformat(f["date"])).days
    except Exception:
        return txt
    return txt if 0 <= age <= max_age_days else ""


# ---------- фидбек гардероба ----------
def add_wardrobe_feedback(cid, look, verdict):
    """Записать реакцию на образ. verdict - код из WARDROBE_VERDICTS. Cap _FB_CAP."""
    if verdict not in WARDROBE_VERDICTS:
        return
    prof = store.get_profile(cid)
    fb = prof.get("wardrobe_fb", [])
    fb.append({"date": _today(), "look": (look or "").strip()[:120], "verdict": verdict})
    prof["wardrobe_fb"] = fb[-_FB_CAP:]
    store.set_profile(cid, prof)


def wardrobe_hints(cid, recent=10):
    """Компактная сводка последнего фидбека для подмешивания в промпт. '' если пусто."""
    fb = store.get_profile(cid).get("wardrobe_fb", [])[-recent:]
    if not fb:
        return ""
    counts = {}
    nostyle_looks = []
    for f in fb:
        v = f.get("verdict")
        counts[v] = counts.get(v, 0) + 1
        if v == "nostyle" and f.get("look"):
            nostyle_looks.append(f["look"])
    parts = []
    if counts.get("cold"):
        parts.append(f"часто мёрзнет в образах (×{counts['cold']}) - не одевай слишком легко")
    if counts.get("hot"):
        parts.append(f"часто жарко (×{counts['hot']}) - не перегружай слоями")
    if nostyle_looks:
        parts.append("не его стиль: " + "; ".join(nostyle_looks[-2:]))
    if counts.get("worn"):
        parts.append(f"носит охотно похожие образы (×{counts['worn']})")
    return "; ".join(parts)


# ---------- Лагом (ценности/установки пользователя) ----------
def get_lagom(cid) -> list:
    """Список Лагом-принципов пользователя."""
    prof = store.get_profile(cid)
    if "lagom" not in prof:
        # миграция из старого отдельного ключа (без подгрузки файла-сида)
        old = store.get_list(config.LAGOM_KEY, cid)
        prof["lagom"] = list(old)
        store.set_profile(cid, prof)
    return prof.get("lagom", [])


def set_lagom(cid, items: list):
    prof = store.get_profile(cid)
    prof["lagom"] = list(items)
    store.set_profile(cid, prof)


def add_lagom(cid, item: str):
    item = (item or "").strip()
    if not item:
        return
    items = get_lagom(cid)
    items.append(item)
    set_lagom(cid, items)


def del_lagom(cid, i: int):
    items = get_lagom(cid)
    if 0 <= i < len(items):
        items.pop(i)
        set_lagom(cid, items)


def _split_items(text: str) -> list:
    """Разбивает ввод на отдельные принципы: сначала по строкам, затем по предложениям."""
    lines = [re.sub(r'^[\s*\-•·→\d]+[\.\)\:]?\s*', '', l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return []
    # Одна строка без переносов → разбить по предложениям
    if len(lines) == 1:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', lines[0]) if s.strip()]
        return sentences if len(sentences) > 1 else lines
    return lines


def add_lagom_batch(cid, text: str) -> list:
    """Парсит текст, добавляет каждый принцип отдельно. Возвращает список добавленных."""
    parts = _split_items(text)
    existing = set(get_lagom(cid))
    added = []
    for it in parts:
        if it and it not in existing:
            add_lagom(cid, it)
            existing.add(it)
            added.append(it)
    return added


# ---------- Предпочтения пользователя (Memory Agent) ----------
_PREFS_CAP = 50


def add_preference(cid, text: str):
    """Сохранить факт/предпочтение о пользователе (строка). Дубликаты отсекаются."""
    text = (text or "").strip()
    if not text:
        return
    prof = store.get_profile(cid)
    prefs = prof.get("prefs", [])
    if text not in prefs:
        prefs.append(text)
    prof["prefs"] = prefs[-_PREFS_CAP:]
    store.set_profile(cid, prof)


def get_preferences(cid) -> list:
    """Список сохранённых фактов о пользователе."""
    return store.get_profile(cid).get("prefs", [])


def del_preference(cid, i: int):
    """Удалить предпочтение по индексу."""
    prefs = get_preferences(cid)
    if 0 <= i < len(prefs):
        prefs.pop(i)
        prof = store.get_profile(cid)
        prof["prefs"] = prefs
        store.set_profile(cid, prof)


def profile_hints(cid) -> str:
    """Компактная строка предпочтений для подмешивания в LLM-промпты. '' если пусто."""
    prefs = get_preferences(cid)
    if not prefs:
        return ""
    return "Знаешь о пользователе: " + "; ".join(prefs[:20]) + "."
