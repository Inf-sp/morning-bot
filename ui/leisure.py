import re

from .builder import MessageSpec, from_html
from util import esc


def clip(text, limit=450):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= int(limit * 0.5):
        return cut[:end + 1].strip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(" ,.;:—-") + "…"


def movie_card(item, tm):
    """Составная карточка (условные блоки + esc()-нутые поля) -> from_html."""
    item = item if isinstance(item, dict) else {"title": str(item)}
    title = (tm.get("name") if tm else "") or item.get("title", "")
    year = f" ({tm.get('year')})" if tm and tm.get("year") else ""
    kind = (tm.get("kind") if tm else "") or ""
    icon = "📺" if kind == "tv" else "🎬"
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")
    lines = [f"{icon} <b>{esc(title)}{year}</b>"]
    en = (tm.get("name_en") if tm else "") or item.get("title_en", "")
    if en and en.lower() != title.lower():
        lines.append(f"<i>{esc(en)}</i>")
    genre_bits = " · ".join(x for x in [type_label, (tm.get("genres") if tm else "")] if x)
    if genre_bits:
        lines += ["", f"🎭 {esc(genre_bits)}"]
    if tm and tm.get("rating"):
        lines.append(f"⭐ {tm.get('rating'):.1f}/10 TMDb")
    if tm and tm.get("overview"):
        lines += ["", esc(clip(tm["overview"]))]
    lines += ["", f"💡 {esc(item.get('hook', ''))}"]
    if tm and tm.get("url"):
        lines += ["", f"🔗 {tm['url']}"]
    return title, from_html("\n".join(lines))


def book_text(item):
    """Составная карточка (условные блоки + esc()-нутые поля) -> from_html."""
    author = esc(item.get("author", ""))
    title = esc(item.get("title", ""))
    en = esc(item.get("title_en", ""))
    year = esc(str(item.get("year", "")))
    head_meta = ", ".join(x for x in [en, year] if x)
    head = f"{author} • «{title}»" if author else f"«{title}»"
    if head_meta:
        head += f" <i>({head_meta})</i>"
    lines = [f"📚 <b>{head}</b>"]
    if item.get("desc"):
        lines += ["", esc(item["desc"])]
    why = item.get("why") or []
    if isinstance(why, list) and why:
        lines += ["", "🎯 <b>Почему стоит читать</b>"] + [f"• {esc(str(w)).lstrip('-–— ')}" for w in why]
    if item.get("plot"):
        lines += ["", "✍🏻 <b>Коротко о сюжете</b>", esc(item["plot"])]
    if item.get("quote"):
        quote = str(item["quote"]).strip().strip("«»\"")
        lines += ["", "💬 <b>Цитата</b>", f"«{esc(quote)}»"]
    return from_html("\n".join(lines))


def artist_card(data):
    """Составная карточка (условные блоки + esc()-нутые поля) -> from_html."""
    artist = data.get("artist", "")
    lines = [f"🎸 <b>{esc(artist)}</b>"]
    if data.get("desc"):
        lines += ["", esc(data["desc"])]
    why = data.get("why") or []
    if isinstance(why, list) and why:
        lines += ["", "🎯 <b>Почему тебе зайдёт:</b>"] + [f"• {esc(str(w))}" for w in why]
    tracks = data.get("tracks") or []
    if isinstance(tracks, list) and tracks:
        lines += ["", "🎧 <b>С чего начать:</b>"] + [f"• {esc(str(t))}" for t in tracks]
    if data.get("fact"):
        lines += ["", "💡 <b>Факт:</b>", esc(data["fact"])]
    return from_html("\n".join(lines))


def country_card(data):
    """Составная карточка (условные блоки + esc()-нутые поля) -> from_html."""
    lines = [f"{data.get('flag','')} <b>{esc(data.get('country',''))}</b>", ""]
    if data.get("about"):
        lines += [esc(data["about"]), ""]
    if data.get("for_what"):
        lines += [f"🎯 <b>Ради чего ехать:</b> {esc(data['for_what'])}", ""]
    if data.get("langs"):
        lines += [f"🗣️ <b>Язык:</b> {esc(data['langs'])}", ""]
    if data.get("note"):
        lines += [f"⚠️ <b>Главный нюанс:</b> {esc(data['note'])}"]
    if data.get("fact"):
        lines += ["", f"🔎 <b>Факт:</b> {esc(data['fact'])}"]
    return from_html("\n".join(lines).strip())


def travel_plan(plan, fallback_country):
    """Текст плана путешествия персистируется как HTML в NOTES_KEY (bucket='plan', full=True)
    и позже режется на chunks по 4000 символов в settings.fav_view — резать entities по offset'ам
    в таком сценарии небезопасно, поэтому держим на HTML, как favorite_card в settings.py."""
    country = plan.get("title", fallback_country)
    lines = [f"{plan.get('flag','')} <b>{esc(country)}</b>"]
    if plan.get("about"):
        lines += ["", esc(plan["about"])]
    if plan.get("why"):
        lines += ["", "🎯 <b>Почему тебе подойдёт</b>"] + [f"• {esc(str(w))}" for w in plan["why"]]
    if plan.get("best_time"):
        lines += ["", "📅 <b>Лучшее время</b>", esc(plan["best_time"])]
    if plan.get("budget"):
        lines += ["", "💰 <b>Бюджет</b>"] + [f"• {esc(str(b))}" for b in plan["budget"]]
    if plan.get("spots"):
        lines += ["", "📸 <b>Не пропусти</b>"] + [f"• {esc(str(sp))}" for sp in plan["spots"]]
    if plan.get("lgbt"):
        lines += ["", "🏳️‍🌈 <b>LGBTQ+</b>", esc(plan["lgbt"])]
    if plan.get("fact"):
        lines += ["", "🍲 <b>Интересный факт</b>", esc(plan["fact"])]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")
