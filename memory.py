"""Память пользователя («бот учится на тебе»): фокус дня, фидбек гардероба, наблюдения.

Тонкий доменный слой поверх профиля в store (config.PROFILE_KEY). Без LLM и сети.
Профиль - dict на пользователя: {"focus": {...}, "wardrobe_fb": [...], "observations": [...]}.
"""
from datetime import date, datetime
import config
import store

_OBS_CAP = 30
_FB_CAP = 20

# коды вердиктов фидбека гардероба -> человеческие ярлыки
WARDROBE_VERDICTS = {
    "worn": "надел",
    "cold": "было холодно",
    "hot": "было жарко",
    "nostyle": "не мой стиль",
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
