import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import recommendation_stoplist
import research
import store
from ui.constants import ui_label
from ui.navigation import back_menu_keyboard

# ===== КОНТЕНТ (content.py) =====

# --- Инлайн-сбор предпочтений при пустом профиле ---
_COLLECT_HINTS = {
    "artists": (
        f"{ui_label('music', '')} <b>Ещё нет любимых исполнителей</b>\n\n"
        "Чтобы подбирать музыку под твой вкус, мне нужно знать, кого ты слушаешь.\n\n"
        "Пришли список прямо сюда — по одному или через запятую:\n"
        "<i>Например: The xx, Massive Attack, Portishead</i>"
    ),
    "movies": (
        f"{ui_label('cinema', '')} <b>Ещё нет любимых фильмов</b>\n\n"
        "Пришли список фильмов или сериалов, которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Паразиты, Эйфория, Настоящий детектив</i>"
    ),
    "books": (
        f"{ui_label('books', '')} <b>Ещё нет любимых книг</b>\n\n"
        "Пришли список книг, которые ты читал и которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Дюна, Атлант расправил плечи, Идиот</i>"
    ),
}

async def _ask_collect(bot, cid, kind: str):
    """Показывает экран сбора предпочтений и ставит pending_input."""
    store.pending_input[str(cid)] = f"collect_{kind}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data="m_leisure")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    await bot.send_message(chat_id=cid, text=_COLLECT_HINTS[kind], parse_mode="HTML", reply_markup=kb)

async def collect_done(bot, cid, kind: str, text: str):
    """Парсит и сохраняет введённый список; повторно открывает раздел."""
    import secure as _sec
    raw = _sec.clamp(text)
    # Разбиваем по запятым, переносам, точкам с запятой
    items = [x.strip() for x in re.split(r"[,;\n]+", raw) if x.strip()]
    if not items:
        await bot.send_message(
            chat_id=cid, text="Не смог разобрать список — попробуй ещё раз.",
            reply_markup=back_menu_keyboard("m_leisure"))
        return
    key_map = {"artists": config.ARTISTS_KEY, "movies": config.WATCHLIST_KEY, "books": config.BOOKS_KEY}
    key = key_map.get(kind)
    if key:
        existing = {_norm(x) for x in store.get_list(key, cid)}
        added = [it for it in items if _norm(it) not in existing]
        for it in added:
            store.add_to_list(key, cid, it)
        n = len(added)
        label = {"artists": "артист(ов)", "movies": "фильм(ов)", "books": "книг(и)"}[kind]
        await bot.send_message(chat_id=cid,
            text=f"✅ Сохранено {n} {label}.", parse_mode="HTML")
    # Повторно открываем нужный раздел
    if kind == "artists":
        import leisure_music
        await leisure_music.send_listen(bot, cid)
    elif kind == "movies":
        import leisure_movies
        await leisure_movies.send_recos(bot, cid, "movie")
    elif kind == "books":
        import leisure_books
        await leisure_books.send_books_reco(bot, cid)

def _ensure_books(cid):
    """Возвращает список книг пользователя (без авто-сида)."""
    return store.get_list(config.BOOKS_KEY, cid)

def _norm(x):
    """Нормализованное имя элемента (строка или {name}) для сравнения без учёта регистра."""
    s = x.get("name", "") if isinstance(x, dict) else str(x)
    return s.strip().lower()

def _add_unique(key, cid, value):
    """Добавляет в список, только если такого ещё нет (без учёта регистра). True - если добавлено."""
    existing = {_norm(x) for x in store.get_list(key, cid)}
    if _norm(value) in existing:
        return False
    store.add_to_list(key, cid, value)
    return True

def _note_fav_exists(cid, text):
    """Есть ли уже такая закладка (bucket=fav) с тем же текстом."""
    t = (text or "").strip().lower()
    for n in store.get_list(config.NOTES_KEY, cid):
        if isinstance(n, dict) and n.get("bucket", "fav") == "fav" and n.get("text", "").strip().lower() == t:
            return True
    return False

def dedupe_lists():
    """Разовая чистка: убирает повторы (без учёта регистра) в списках любимого/закладок."""
    keys = [config.BOOKS_KEY, config.ARTISTS_KEY, config.WATCHLIST_KEY,
            config.READLIST_KEY, config.COUNTRIES_KEY]
    changed_any = False
    for key in keys:
        data = store._load(key)
        changed = False
        for cid, items in (data or {}).items():
            if not isinstance(items, list):
                continue
            seen, out = set(), []
            for it in items:
                n = _norm(it)
                if n and n in seen:
                    continue
                seen.add(n)
                out.append(it)
            if len(out) != len(items):
                data[cid] = out
                changed = True
        if changed:
            store._save(key, data)
            changed_any = True
    return changed_any

def seed_movies_from_content():
    """Разово: вливает films+series из content.json в watchlist владельца (CHAT_ID).
    Маркер в store не даёт повторить — удалённые фильмы не возвращаются при рестарте."""
    if not config.CHAT_ID:
        return False
    marker = f"movies_{config.CHAT_ID}"
    flags = store._load("_seed_flags") or {}
    if flags.get(marker):
        return False
    try:
        from pathlib import Path
        import json
        raw = json.loads((Path(__file__).parent / "content.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    titles = [t for t in raw.get("films", []) + raw.get("series", []) if isinstance(t, str) and t.strip()]
    for title in titles:
        _add_unique(config.WATCHLIST_KEY, config.CHAT_ID, title.strip())
    flags[marker] = True
    store._save("_seed_flags", flags)
    return True

def content_recommend(kind, cid):
    if kind == "movie":
        loved = store.get_list(config.WATCHLIST_KEY, cid)
        blocked = recommendation_stoplist.values(cid, "movie")
        notes_all = store.get_list(config.NOTES_KEY, cid)
        noted_movies = [n.get("text", "") for n in notes_all
                        if isinstance(n, dict) and "кино" in str(n.get("source", "")).lower()]
        what = "фильмов или сериалов"
        loved_titles = [s if isinstance(s, str) else str(s) for s in loved]
        skip = loved_titles + blocked + noted_movies
        avoid = ("\nНЕ рекомендуй то, что уже отмечено или не понравилось: " + ", ".join(skip[:80])) if skip else ""
        anchors = ", ".join(loved_titles[:25])
        web_block = ""
        web = research.web_snippet(
            f"лучшие фильмы сериалы 2024 2025 драма артхаус триллер похожие {anchors[:80]}",
            max_chars=700,
        )
        if web:
            web_block = f"\nАктуальные новинки и рекомендации из сети (используй как источник реальных названий):\n{web}\n"
        prompt = f"""Ты опытный кинокритик. Порекомендуй фильмы и сериалы под вкус пользователя.
Его любимые работы (референсы вкуса): {anchors}
{web_block}
Порекомендуй РОВНО 5 {what}, максимально точно под этот вкус.
Обязательно дай СМЕСЬ: и фильмы, и сериалы — минимум 2 сериала из 5.{avoid}
JSON: {{"items": [{{"title": "название (год)", "title_en": "оригинальное/английское название", "hook": "1 строка: на что похоже из его референсов и чем зацепит"}}]}}"""
        return ai.llm_json(prompt, 1000, tier="leisure")

    # книги: референсы вкуса берём из "Мои книги" (настройки/БД, авто-загрузка из content.json)
    my_books = _ensure_books(cid)
    my_books_titles = [b if isinstance(b, str) else str(b) for b in my_books]
    read_seen = store.get_list(config.READLIST_KEY, cid)
    blocked = recommendation_stoplist.values(cid, "book")
    read_titles = [s if isinstance(s, str) else str(s) for s in read_seen]
    refs = my_books_titles
    anchors = ", ".join(refs[:25])
    skip = my_books_titles + read_titles + blocked
    avoid = ("\nНЕ рекомендуй уже прочитанное/в закладках/отклонённое: " + ", ".join(skip[:80])) if skip else ""
    web_block = ""
    from datetime import datetime
    current_year = datetime.now(config.TZ).year
    web = research.web_snippet(
        f"лучшие книги {current_year} литература {anchors[:80]}",
        max_chars=700,
    )
    if web:
        web_block = f"\nАктуальные книжные новинки и рейтинги из сети (используй как источник реальных названий):\n{web}\n"
    prompt = f"""Ты профессиональный редактор и логический критик. Порекомендуй книги под вкус пользователя.
Пиши прямо, жестко и емко. Убирай воду и вводные слова: никаких "однако", "более того", "стоит отметить".
Используй короткие предложения, но чередуй длину для естественного ритма. Не используй точки с запятой.
Если сюжет дублирует описание мира - объединяй. Двусмысленные фразы заменяй точными.
Любимые книги пользователя (референсы вкуса): {anchors if anchors else "список пуст, предложи разнообразные жанры"}
{web_block}
Порекомендуй РОВНО 5 действительно сильных КНИГ {current_year} года под этот вкус (без проходных).
Сравнивай ТОЛЬКО с книгами из его списка выше, не с фильмами/сериалами.{avoid}
JSON: {{"items": [{{"title": "название", "title_en": "оригинальное название", "year": "{current_year}",
 "author": "автор", "desc": "вводный абзац: 1-2 емких предложения о мире/конфликте/жанре, без воды",
 "why": ["раздел 1: сильный тезис почему читать, с точным сравнением с книгами пользователя", "раздел 1: второй сильный тезис"],
 "plot": "раздел 2: сюжет и главный конфликт, 2-3 точных предложения; если мир уже описан, не дублируй",
 "quote": "короткая цитата из книги",
 "hook": "1 короткий редакторский итог без общих слов"}}]}}"""
    return ai.llm_json(prompt, 1300, tier="leisure")
