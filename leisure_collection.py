import ai
import config
import recommendation_stoplist
import research
import store

def _ensure_books(cid):
    """Возвращает список книг пользователя (без авто-сида)."""
    return store.get_list(config.FAVORITE_BOOKS_KEY, cid)

def _norm(x):
    """Нормализованное имя элемента (строка или {name}) для сравнения без учёта регистра."""
    s = x.get("name", "") if isinstance(x, dict) else str(x)
    return s.strip().lower()

def dedupe_lists():
    """Разовая чистка: убирает повторы в личных коллекциях."""
    keys = [config.FAVORITE_BOOKS_KEY, config.FAVORITE_ARTISTS_KEY, config.FAVORITE_MOVIES_KEY,
            config.SAVED_COUNTRIES_KEY]
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

def content_recommend(kind, cid):
    if kind == "movie":
        loved = store.get_list(config.FAVORITE_MOVIES_KEY, cid)
        blocked = recommendation_stoplist.values(cid, "movie")
        what = "фильмов или сериалов"
        loved_titles = [s if isinstance(s, str) else str(s) for s in loved]
        skip = loved_titles + blocked
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

    # Книги: референсы вкуса берём только из личного списка пользователя.
    my_books = _ensure_books(cid)
    my_books_titles = [b if isinstance(b, str) else str(b) for b in my_books]
    blocked = recommendation_stoplist.values(cid, "book")
    refs = my_books_titles
    anchors = ", ".join(refs[:25])
    skip = my_books_titles + blocked
    avoid = ("\nНЕ рекомендуй уже прочитанное, любимое или отклонённое: " + ", ".join(skip[:80])) if skip else ""
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
 "author": "автор", "desc": "одно законченное конкретное предложение: жанр, герой или среда и главный конфликт; не пиши обрывок, набор эпитетов или рекламный слоган",
 "why": ["одна конкретная причина читать", "ещё одна конкретная причина, только если она не повторяет первую"],
 "plot": "одно-два законченных предложения о завязке и ставке героя; не пересказывай описание мира и не обрывай фразу",
 "hook": "1 короткий редакторский итог без общих слов"}}]}}"""
    return ai.llm_json(prompt, 1300, tier="leisure")
