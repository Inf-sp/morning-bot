"""Personal movie recommendation flow extracted from the movie controller."""

from ui.constants import ui_label

def _movie_prefs(cid):
    """Предпочтения кино из настроек → dict для движка (приоритеты, не запреты)."""
    return {
        "type_pref": settings.get(cid, "movie_type_pref", "") or None,
        "recency": settings.get(cid, "movie_recency", "") or None,
        "min_rating": _as_float(settings.get(cid, "movie_min_rating", None)),
    }


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


async def _tmdb_engine_pick(cid, prefs=None):
    """Возвращает (it, tm) из TMDb-движка или (None, None), если данных мало.

    tm — нормализованный TMDb-dict кандидата (совместим с карточкой), дополненный
    деталями и полем because. it — лёгкий dict с title/hook для совместимости.
    """
    if prefs is None:
        prefs = _movie_prefs(cid)
    try:
        cands, taste = await asyncio.to_thread(movie_engine.recommend, cid, prefs)
    except Exception:
        return None, None
    if not cands:
        return None, None
    c = cands[0]
    return _candidate_to_card(cid, c)


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
        pass
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
    because = tm.get("because")
    if because:
        if tm.get("via") == "similar":
            genres = ", ".join(tm.get("shared_genres") or [])
            return f"Подходит по жанрам: {genres}" if genres else ""
        return f"Потому что вам понравился «{because}»"
    return ""


def _reason_label(reason):
    kind = reason.get("kind")
    label = reason.get("label", "")
    if kind == "genre":
        # Подборку по жанру отдельной строкой на карточке не подписываем.
        return ""
    return ""


async def _llm_movie_pick(cid, used):
    """Старый LLM-путь как фолбэк движка."""
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "movie", str(cid))
            items = _normalize_movie_items(data.get("items", []) if isinstance(data, dict) else [])
        except Exception:
            items = []
        if items:
            break
    if not items:
        items = _fallback_movie_items(cid)
    if not items:
        return None, None
    remaining = tracking.remaining_action_seconds()
    timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
    if timeout <= 0.2:
        return items[0], None
    try:
        picked = await asyncio.wait_for(
            asyncio.to_thread(_pick_good_movie, items, used, _movie_prefs(cid)), timeout=timeout)
    except Exception:
        return items[0], None
    if picked[0] is not None:
        return picked
    fallbacks = _fallback_movie_items(cid)
    if fallbacks != items:
        remaining = tracking.remaining_action_seconds()
        timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
        if timeout <= 0.2:
            return fallbacks[0], None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_pick_good_movie, fallbacks, used, _movie_prefs(cid)), timeout=timeout)
        except Exception:
            return fallbacks[0], None
    return None, None

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "movie", title, "hidden")
    await _advance_movie(bot, cid)

async def _advance_movie(bot, cid):
    """Загрузить следующую рекомендацию кино и показать карточку.

    Если текущая сессия рекомендаций привязана к жанру (last_recos["category"],
    проставлено в _show_discovered), следующая карточка ОБЯЗАНА остаться в той же категории —
    «Другое кино»/«В любимые»/«Уже видел» внутри «Комедии» не должны сбрасывать
    подбор на общий алгоритм. Без category — обычный путь Recommendations/Similar по любимым.
    """
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    category = rec.get("category")
    if category:
        it, tm = await _advance_in_category(cid, category)
        if not it:
            label = category["reason"]["label"]
            text = f"В этом жанре «{label}» пока не нашёл нового. Попробуй другой."
            kb = _movie_genre_menu_kb()
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
    else:
        it, tm = await _tmdb_engine_pick(cid)
        if it is None:
            used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
            it, tm = await _llm_movie_pick(cid, used)
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=_movie_home_only_kb()); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec["items"].append(disp)
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm, category=category)


async def _advance_in_category(cid, category):
    """Следующий кандидат внутри выбранного жанра с тем же обязательным фильтром."""
    genre_id = category["value"]
    return await asyncio.to_thread(
        _discover_pick, cid, [genre_id], _movie_prefs(cid),
        require_genre_ids=[genre_id], reason=category["reason"])

async def send_movie_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу фильм или сериал под твой вкус внутри него."
    await _show_menu_over_card(bot, cid, text, _movie_genre_menu_kb(), q)


async def _show_menu_over_card(bot, cid, text, kb, q):
    """Показывает текстовое меню поверх текущего сообщения.

    Если сообщение текстовое — редактирует его. Если это карточка с постером
    (media), edit_text невозможен: снимаем кнопки у старой карточки (чтобы по ней
    нельзя было случайно нажать) и отправляем меню новым сообщением.
    """
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


# ---------- экран «Предпочтения кино» ----------
_PREF_TYPE = [(ui_label("cinema", "Фильмы"), "movie"), ("Сериалы", "tv")]
_PREF_RECENCY = [("Новинки", "new"), ("Любые годы", "")]
_PREF_RATING = [("6.5", "6.5"), ("7.0", "7.0"), ("7.5", "7.5"), ("8.0", "8.0")]


def _movie_prefs_kb(cid):
    tpref = settings.get(cid, "movie_type_pref", "") or ""
    rpref = settings.get(cid, "movie_recency", "") or ""
    rating = str(settings.get(cid, "movie_min_rating", "") or "")
    rows = [[InlineKeyboardButton(("✅ " if tpref == value else "") + label,
                                  callback_data=f"mpref_type_{value}")]
            for label, value in _PREF_TYPE]
    rows.extend([[InlineKeyboardButton(("✅ " if rpref == value else "") + label,
                                      callback_data=f"mpref_recency_{value or 'any'}")]
                 for label, value in _PREF_RECENCY])
    rows.extend([[InlineKeyboardButton(("✅ " if rating == value else "") + f"⭐️ {label}",
                                      callback_data=f"mpref_rating_{value}")]
                 for label, value in _PREF_RATING])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="movie_favorites"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_movie_prefs(bot, cid, q=None):
    text = ("🎬 Кино\n\n"
            "Это приоритеты, а не жёсткие фильтры — я учитываю их при подборе, "
            "но всё равно могу предложить что-то за их пределами.")
    kb = _movie_prefs_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def toggle_movie_pref(bot, cid, data, q=None):
    """Обработка mpref_* переключателей."""
    if data.startswith("mpref_type_"):
        v = data[len("mpref_type_"):]
        if v in {"movie", "tv"}:
            current = settings.get(cid, "movie_type_pref", "") or ""
            settings.set_(cid, "movie_type_pref", "" if current == v else v)
    elif data.startswith("mpref_recency_"):
        v = data[len("mpref_recency_"):]
        if v in {"new", "any"}:
            settings.set_(cid, "movie_recency", "" if v == "any" else v)
    elif data.startswith("mpref_rating_"):
        v = data[len("mpref_rating_"):]
        if v in {value for _label, value in _PREF_RATING}:
            current = str(settings.get(cid, "movie_min_rating", "") or "")
            settings.set_(cid, "movie_min_rating", "" if current == v else v)
    await send_movie_prefs(bot, cid, q)


def _genre_label(genre_id):
    raw_label = dict((gid, lbl) for lbl, gid in _GENRE_MENU).get(genre_id) or tmdb.GENRES.get(genre_id, "")
    return re.sub(r"^\S+\s+", "", raw_label) if raw_label else raw_label  # без ведущего эмодзи кнопки


async def send_movie_by_genre(bot, cid, genre_id):
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
            _discover_pick, cid, [genre_id], _movie_prefs(cid),
            require_genre_ids=[genre_id], reason=reason)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_movie")
        return
    if not it:
        await bot.send_message(chat_id=cid, text="В этом жанре пока не нашёл нового. Попробуй другой.",
                               reply_markup=_movie_genre_menu_kb())
        return
    await _show_discovered(bot, cid, it, tm, category=category)


async def _show_discovered(bot, cid, it, tm, category=None):
    """category — контекст жанра, из которого пришла карточка: сохраняем его
    в last_recos, чтобы «Другое кино»/«В любимые»/«Уже видел» (через _advance_movie)
    брали СЛЕДУЮЩУЮ рекомендацию из той же категории, а не сбрасывались на общий подбор,
    и чтобы подбор оставался внутри выбранного жанра."""
    tm = dict(tm or {})
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    rec["items"].append(disp)
    rec["category"] = category
    store.last_recos[str(cid)] = rec
    store.last_source[str(cid)] = "Кино"
    await _send_movie_card(bot, cid, it, len(rec["items"]) - 1, tm=tm, category=category)


def _passes_genre_gate(c, require_genre_ids=None):
    """Обязательная пост-проверка жанра перед отправкой карточки."""
    genre_ids = set(c.get("genre_ids") or [])
    if require_genre_ids and not set(require_genre_ids).issubset(genre_ids):
        return False
    return True


def _discover_pick(cid, genre_ids, prefs, require_genre_ids=None, reason=None):
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
        for kind in ("movie", "tv"):
            for c in tmdb.discover(
                kind, genre_ids=genre_ids, min_rating=mr, year_gte=2000,
            ):
                if not c.get("id") or movie_engine._norm(c.get("name")) in excluded:
                    continue
                if not _passes_genre_gate(c, require_genre_ids):
                    continue
                pool[f"{c['kind']}:{c['id']}"] = c
        if pool:
            ranked = movie_engine.rank(list(pool.values()), taste, prefs)
            return _candidate_to_card(cid, ranked[0], reason=reason)
    return None, None


async def movie_love(bot, cid, i, q=None):
    """Добавляет фильм в любимые без дублей и отражает состояние на карточке."""
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
        if q is not None:
            await q.message.edit_reply_markup(reply_markup=_movie_kb(i))
