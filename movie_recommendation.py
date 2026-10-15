"""Personal movie recommendation flow extracted from the movie controller."""

import logging
import asyncio
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import leisure_movies
import movie_engine
import recommendation_stoplist
import settings
import store
import tmdb
import tracking
import verify
from leisure_collection import (
    canonical_movie_label,
    content_recommend,
    movie_title_for_lookup,
    normalize_movie_items,
)
from ui.navigation import nav_row
import rich_delivery

_log = logging.getLogger(__name__)


def _movie_prefs(cid):
    """Предпочтения кино из настроек → dict для движка (приоритеты, не запреты)."""
    # Тип (фильмы/сериалы) и новизна больше не выбираются — старые сохранённые значения не действуют.
    return {
        "type_pref": None,
        "recency": None,
        "min_rating": _as_float(settings.get(cid, "movie_min_rating", None)),
        "regions_off": regions_off(cid),
    }


# С понедельника по четверг — короткий вечер: фильм до ~2 ч 10 мин, серия до ~50 мин; с пятницы — без ограничения.
WEEKDAY_MAX_MINUTES = {"movie": 130, "tv": 50}


def runtime_limit(kind, day=None):
    day = day or datetime.now(config.TZ).date()
    return WEEKDAY_MAX_MINUTES.get(kind) if day.weekday() < 4 else None


def minutes_of(tm) -> int:
    """Длительность фильма или серии в минутах из деталей TMDb; 0 — неизвестна."""
    return int((tm or {}).get("runtime") or (tm or {}).get("episode_runtime") or 0)


def fits_runtime(tm, limit) -> bool:
    """Неизвестная длительность не отсекает: лучше показать, чем остаться без варианта."""
    minutes = minutes_of(tm)
    return not limit or not minutes or minutes <= limit


def _kind_prefs(cid, kind="movie"):
    """Предпочтения подбора строго одного типа: «Фильмы» — только фильмы, «Сериалы» — только сериалы.

    max_runtime — будний вечер (пн–чт): длинный фильм или серия уступают более коротким.
    """
    kind = "tv" if kind == "tv" else "movie"
    return {**_movie_prefs(cid), "type_pref": kind, "only": kind, "max_runtime": runtime_limit(kind)}


def _rec_key(cid, kind="movie"):
    """Ключ last_recos и карточки дня: у сериалов свой, чтобы открытые карточки фильма и сериала не мешали."""
    return f"{cid}|tv" if kind == "tv" else str(cid)


def _of_kind(tm, kind) -> bool:
    return bool(tm) and (tm.get("kind") or "movie") == kind


def regions_off(cid) -> list:
    """Выключенные регионы кино: [asia, africa…]; по умолчанию включены все."""
    import countries
    saved = settings.get(cid, "movie_regions_off", []) or []
    return [key for key, _label in countries.REGIONS if key in saved]


_INCLUSIVE_MOVIE_TITLES = (
    "Moonlight", "Portrait of a Lady on Fire", "Nimona",
    "Heartstopper", "It's a Sin", "Pose",
)


async def _inclusive_movie_pick(cid, prefs):
    """Проверенный ЛГБТ-проект, подходящий типу и минимальному рейтингу."""
    excluded = movie_engine._excluded_norms(cid)
    type_pref = prefs.get("type_pref")
    min_rating = float(prefs.get("min_rating") or 0)
    for title in _INCLUSIVE_MOVIE_TITLES:
        try:
            tm = await asyncio.to_thread(tmdb.lookup_title, title)
        except Exception:
            continue
        if not tm or movie_engine._norm(tm.get("name")) in excluded:
            continue
        if type_pref and tm.get("kind") != type_pref:
            continue
        if float(tm.get("rating") or 0) < min_rating:
            continue
        tm = {**tm, "lgbt": True}
        it = {
            "title": tm.get("name") or title,
            "title_en": tm.get("name_en") or title,
            "hook": "ЛГБТ-история с сильными отзывами и близким тебе настроением.",
        }
        return it, tm
    return None


def _as_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _tmdb_engine_pick(cid, prefs=None, timeout=None):
    """Возвращает (it, tm) из TMDb-движка или (None, None), если данных мало.

    tm — нормализованный TMDb-dict кандидата (совместим с карточкой), дополненный
    деталями и полем because. it — лёгкий dict с title/hook для совместимости.
    """
    if prefs is None:
        prefs = _movie_prefs(cid)
    try:
        # С холодным кэшем движок делает десятки TMDb-запросов; без лимита он
        # съедает весь бюджет действия, и резерву уже нечем подобрать фильм.
        cands, taste = await asyncio.wait_for(
            asyncio.to_thread(movie_engine.recommend, cid, prefs), timeout=timeout,
        )
    except Exception:
        return None, None
    if prefs.get("only"):  # «Фильмы» / «Сериалы» — строго свой тип
        cands = [c for c in cands if (c.get("kind") or "movie") == prefs["only"]]
    if not cands:
        return None, None
    # Детали TMDb — сеть: не в потоке событий бота.
    return await asyncio.to_thread(_first_allowed_card, cid, cands, prefs)


_REGION_CHECKS = 5  # в списках TMDb у фильмов нет страны — проверяем по деталям первых кандидатов


def _first_allowed_card(cid, cands, prefs, reason=None):
    """Первый кандидат, чья страна производства (детали TMDb) не в выключенном регионе
    и длительность подходит будному вечеру (max_runtime)."""
    import countries
    off = (prefs or {}).get("regions_off")
    limit = (prefs or {}).get("max_runtime")
    for c in cands[:_REGION_CHECKS] if off or limit else cands[:1]:
        it, tm = _candidate_to_card(cid, c, reason=reason)
        if countries.allowed(tm, off) and fits_runtime(tm, limit):
            return it, tm
    return None, None


def _candidate_to_card(cid, c, reason=None):
    """Обогащает кандидата деталями и строит (it, tm) для карточки.

    reason — явный источник рекомендации, если не «обычная» (Recommendations/Similar
    по любимому): {"kind": "genre", "label": "Комедия"}.
    Если reason не передан, источник — anchor-поля кандидата (because/via/anchors).

    ВАЖНО: tmdb.detail() отдаёт объект из общего TTL-кэша (по ссылке, не копию) —
    его нельзя мутировать напрямую, иначе персональное поле «because» одного
    пользователя утечёт в карточку другого пользователя/другого запроса для того же
    тайтла (баг: «Потому что понравился Элита» у сериала, никак не связанного с Элитой).
    Поэтому здесь всегда делаем dict(det) перед добавлением полей.
    """
    tm = dict(c)
    try:
        det = tmdb.detail(c.get("id"), c.get("kind"))
        if det:
            det = dict(det)  # копия — не мутируем общий кэш tmdb.detail
            tm = det
    except Exception:
        _log.debug("_candidate_to_card: ignored error", exc_info=True)
    if reason is not None:
        tm["reason"] = reason
    else:
        tm["because"] = c.get("because")
        tm["via"] = c.get("via")
        tm["shared_genres"] = c.get("shared_genres") or []
        tm["anchors"] = c.get("anchors")
    it = {"title": tm.get("name", ""), "title_en": tm.get("name_en", ""),
          "hook": _reason_text(tm)}
    return it, tm


def _reason_text(tm):
    """Причина рекомендации — плоский текст (для it["hook"], фолбэков без карточки-TMDb)."""
    reason = tm.get("reason")
    if reason:
        return _reason_label(reason)
    # Строку «Потому что вам понравился …» в карточке не пишем; «Подходит по жанрам» —
    # тоже: жанры уже в строке рейтинга. Связь с любимым остаётся только в ранжировании.
    return ""


def _reason_label(reason):
    # Подборку по жанру (единственный явный reason) на карточке не подписываем.
    return ""


async def _llm_movie_pick(cid, used):
    """Старый LLM-путь как фолбэк движка."""
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "movie", str(cid))
            items = leisure_movies._normalize_movie_items(data.get("items", []) if isinstance(data, dict) else [])
        except Exception:
            items = []
        if items:
            break
    if not items:
        items = leisure_movies._fallback_movie_items(cid)
    if not items:
        return None, None
    remaining = tracking.remaining_action_seconds()
    timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
    if timeout <= 0.2:
        return items[0], None
    try:
        picked = await asyncio.wait_for(
            asyncio.to_thread(leisure_movies._pick_good_movie, items, used, _movie_prefs(cid)), timeout=timeout)
    except Exception:
        return items[0], None
    if picked[0] is not None:
        return picked
    fallbacks = leisure_movies._fallback_movie_items(cid)
    if fallbacks and fallbacks != items:
        remaining = tracking.remaining_action_seconds()
        timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
        if timeout <= 0.2:
            return fallbacks[0], None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(leisure_movies._pick_good_movie, fallbacks, used, _movie_prefs(cid)), timeout=timeout)
        except Exception:
            return fallbacks[0], None
    return None, None

async def movie_dislike(bot, cid, i, kind="movie"):
    rec = store.last_recos.get(_rec_key(cid, kind))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "movie", title, "hidden")
    await _advance_movie(bot, cid, kind)

async def _advance_movie(bot, cid, kind="movie"):
    """Загрузить следующую рекомендацию кино и показать карточку.

    Если текущая сессия рекомендаций привязана к жанру (last_recos["category"],
    проставлено в _show_discovered), следующая карточка ОБЯЗАНА остаться в той же категории —
    «Другое кино»/«В любимые»/«Уже видел» внутри «Комедии» не должны сбрасывать
    подбор на общий алгоритм. Без category — обычный путь Recommendations/Similar по любимым.
    """
    rec = store.last_recos.get(_rec_key(cid, kind), {"kind": kind, "items": []})
    category = rec.get("category")
    if category:
        it, tm = await _advance_in_category(cid, category, kind)
        if not it:
            label = category["reason"]["label"]
            text = f"В этом жанре «{label}» пока не нашёл нового. Попробуй другой."
            kb = leisure_movies._movie_genre_menu_kb(kind=kind)
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
    else:
        it, tm = await _tmdb_engine_pick(cid, _kind_prefs(cid, kind))
        if it is None:
            used = leisure_movies._movie_used(cid) | {str(x).lower() for x in rec["items"]}
            it, tm = await _llm_movie_pick(cid, used)
            if not _of_kind(tm, kind):  # резерв AI не знает о типе — чужой тип не показываем
                it, tm = None, None
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=leisure_movies._movie_home_only_kb()); return
    disp = leisure_movies._display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec["items"].append(disp)
    store.last_recos[_rec_key(cid, kind)] = rec
    ni = len(rec["items"]) - 1
    await leisure_movies._send_movie_card(bot, cid, it, ni, tm=tm, category=category, kind=kind)


async def _advance_in_category(cid, category, kind="movie"):
    """Следующий кандидат внутри выбранного жанра с тем же обязательным фильтром."""
    genre_id = category["value"]
    return await asyncio.to_thread(
        _discover_pick, cid, [genre_id], _kind_prefs(cid, kind),
        require_genre_ids=[genre_id], reason=category["reason"])

async def send_movie_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу фильм или сериал под твой вкус внутри него."
    await _show_menu_over_card(bot, cid, text, leisure_movies._movie_genre_menu_kb(), q)


async def _show_menu_over_card(bot, cid, text, kb, q):
    """Показывает текстовое меню поверх текущего сообщения.

    Если сообщение текстовое — редактирует его. Если это карточка с постером
    (media), edit_text невозможен: снимаем кнопки у старой карточки (чтобы по ней
    нельзя было случайно нажать) и отправляем меню новым сообщением.
    """
    await rich_delivery.show(bot, cid, text, reply_markup=kb, query=q)


# ---------- экран «Предпочтения кино» ----------
_PREF_RATING = [("6.5", "6.5"), ("7.0", "7.0"), ("7.5", "7.5"), ("8.0", "8.0")]


def _movie_prefs_kb(cid):
    rating = str(settings.get(cid, "movie_min_rating", "") or "")
    rows = []
    rows.extend([[InlineKeyboardButton(("✅ " if rating == value else "□ ") + f"⭐️ {label}",
                                      callback_data=f"mpref_rating_{value}")]
                 for label, value in _PREF_RATING])
    import countries
    off = regions_off(cid)
    # Регионы — как остальные переключатели, по одному в строке: выключенный не попадает в рекомендации.
    rows.extend([[InlineKeyboardButton(("□ " if key in off else "✅ ") + label,
                                       callback_data=f"mpref_region_{key}")]
                 for key, label in countries.REGIONS])
    rows.append(nav_row("movie_favorites"))
    return InlineKeyboardMarkup(rows)


async def send_movie_prefs(bot, cid, q=None):
    text = ("🎬 Кино\n\n"
            "Минимальная оценка — приоритет: я учитываю её при подборе. Регионы — строгий "
            "фильтр: из выключенного региона фильмы и сериалы не предлагаются.")
    kb = _movie_prefs_kb(cid)
    await rich_delivery.show(bot, cid, text, reply_markup=kb, query=q)


async def toggle_movie_pref(bot, cid, data, q=None):
    """Обработка mpref_* переключателей (старые mpref_type_/mpref_recency_ из истории — просто экран)."""
    if data.startswith("mpref_region_"):
        import countries
        key = data[len("mpref_region_"):]
        if key in dict(countries.REGIONS):
            off = regions_off(cid)
            settings.set_(cid, "movie_regions_off", [k for k in off if k != key] if key in off else [*off, key])
    elif data.startswith("mpref_rating_"):
        v = data[len("mpref_rating_"):]
        if v in {value for _label, value in _PREF_RATING}:
            current = str(settings.get(cid, "movie_min_rating", "") or "")
            settings.set_(cid, "movie_min_rating", "" if current == v else v)
    await send_movie_prefs(bot, cid, q)


def _genre_label(genre_id):
    menus = [*leisure_movies._GENRE_MENU, *leisure_movies._TV_GENRE_MENU]
    raw_label = dict((gid, lbl) for lbl, gid in menus).get(genre_id) or tmdb.GENRES.get(genre_id, "")
    return re.sub(r"^\S+\s+", "", raw_label) if raw_label else raw_label  # без ведущего эмодзи кнопки


async def send_movie_by_genre(bot, cid, genre_id, kind="movie"):
    """Рекомендация внутри жанра: TMDb discover + учёт вкуса пользователя.

    Жанр — обязательный фильтр (не подсказка): показанный тайтл ОБЯЗАН иметь этот
    genre_id в TMDb genre_ids, иначе его нельзя показывать (см. _discover_pick require_genre_ids).
    """
    genre_id = int(genre_id)
    label = _genre_label(genre_id)
    reason = {"kind": "genre", "label": label}
    category = {"kind": "genre", "value": genre_id, "reason": reason}
    try:
        it, tm = await asyncio.to_thread(
            _discover_pick, cid, [genre_id], _kind_prefs(cid, kind),
            require_genre_ids=[genre_id], reason=reason)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_leisure")
        return
    if not it:
        await bot.send_message(chat_id=cid, text="В этом жанре пока не нашёл нового. Попробуй другой.",
                               reply_markup=leisure_movies._movie_genre_menu_kb(kind=kind))
        return
    await _show_discovered(bot, cid, it, tm, category=category, kind=kind)


async def _show_discovered(bot, cid, it, tm, category=None, kind="movie"):
    """category — контекст жанра, из которого пришла карточка: сохраняем его
    в last_recos, чтобы «Другое кино»/«В любимые»/«Уже видел» (через _advance_movie)
    брали СЛЕДУЮЩУЮ рекомендацию из той же категории, а не сбрасывались на общий подбор,
    и чтобы подбор оставался внутри выбранного жанра."""
    tm = dict(tm or {})
    disp = leisure_movies._display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec = store.last_recos.get(_rec_key(cid, kind), {"kind": kind, "items": []})
    rec["items"].append(disp)
    rec["category"] = category
    store.last_recos[_rec_key(cid, kind)] = rec
    store.last_source[str(cid)] = "Сериалы" if kind == "tv" else "Кино"
    await leisure_movies._send_movie_card(bot, cid, it, len(rec["items"]) - 1, tm=tm, category=category, kind=kind)


def _passes_genre_gate(c, require_genre_ids=None):
    """Обязательная пост-проверка жанра перед отправкой карточки."""
    genre_ids = set(c.get("genre_ids") or [])
    if require_genre_ids and not set(require_genre_ids).issubset(genre_ids):
        return False
    return True


def _discover_pick(cid, genre_ids, prefs, require_genre_ids=None, reason=None):
    import countries
    """Берёт кандидатов из discover (movie+tv), фильтрует по вкусу/исключениям, ранжирует.

    Жанр — обязательный пост-фильтр (см. _passes_genre_gate): показанный тайтл
    обязан ему соответствовать. Перебираем ранжированный список, а не берём слепо
    топ-1, — если лидер не проходит гейт из-за неполных данных TMDb, пробуем следующего.
    reason — источник рекомендации по жанру, а не anchor-«понравился».
    """
    min_rating = max(
        movie_engine.RATING_STEPS[0],
        float((prefs or {}).get("min_rating") or movie_engine.RATING_STEPS[0]),
    )
    taste = movie_engine.taste_profile(cid, resolve_details=False)
    excluded = movie_engine._excluded_norms(cid)
    steps = [r for r in movie_engine.RATING_STEPS if r <= min_rating] or [movie_engine.RATING_STEPS[-1]]
    for mr in steps:
        pool = {}
        for kind in ((prefs or {}).get("only"),) if (prefs or {}).get("only") else ("movie", "tv"):
            for c in tmdb.discover(
                kind, genre_ids=genre_ids, min_rating=mr, year_gte=2000,
            ):
                if not c.get("id") or movie_engine._norm(c.get("name")) in excluded:
                    continue
                if not _passes_genre_gate(c, require_genre_ids):
                    continue
                if not countries.allowed(c, (prefs or {}).get("regions_off")):
                    continue
                pool[f"{c['kind']}:{c['id']}"] = c
        if pool:  # сначала самые сильные по рейтингу и числу голосов
            ranked = movie_engine.rank(movie_engine.top_quality(list(pool.values()), mr), taste, prefs)
            it, tm = _first_allowed_card(cid, ranked, prefs, reason=reason)
            if tm:
                return it, tm
    return None, None


async def movie_love(bot, cid, i, q=None):
    """Добавляет фильм в любимые без дублей."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        try:
            normalized = await asyncio.wait_for(
                asyncio.to_thread(normalize_movie_items, [title]), timeout=4.0,
            )
        except asyncio.TimeoutError:
            normalized = [canonical_movie_label(title)]
        if normalized:
            title = normalized[0]
        existing = {
            movie_title_for_lookup(item).casefold()
            for item in store.get_list(config.FAVORITE_MOVIES_KEY, cid)
        }
        if movie_title_for_lookup(title).casefold() not in existing:
            store.add_to_list(config.FAVORITE_MOVIES_KEY, cid, title)
        await _advance_movie(bot, cid)
