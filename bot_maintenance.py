"""Startup audits and low-priority maintenance jobs."""


def _run_startup_audits():
    """Проверить исходники после готовности polling, не задерживая запуск."""
    audits = (
        ("Callback", verify.audit_callbacks),
        ("Architecture", verify.audit_architecture),
        ("Trainer contract", verify.audit_trainer_contracts),
        ("Navigation", verify.audit_navigation_contracts),
    )
    for label, audit in audits:
        try:
            violations = audit()
            if violations:
                logging.warning("%s audit: violations -> %s", label, "; ".join(violations))
            else:
                logging.info("%s audit: OK", label)
        except Exception:
            logging.exception("%s audit failed", label)
    try:
        leaks = secure.scan_secrets()
        if leaks:
            logging.warning("Secrets scan: findings -> %s", "; ".join(leaks))
        else:
            logging.info("Secrets scan: OK")
    except Exception:
        logging.exception("Secrets scan failed")


async def job_startup_audits(context):
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_startup_audits, when=30, name="startup_audits_once",
            job_kwargs={"id": "startup_audits_once", "replace_existing": True},
        )
        return
    await asyncio.to_thread(_run_startup_audits)


async def job_retry_dictionary_adds(context):
    """Повторяет только сохранённые Add-запросы; пользователь ничего не вводит заново."""
    if tracking.has_active_actions():
        return
    import dictionary_import
    await dictionary_import.process_queued_dictionary_adds(
        context.bot, access.get_allowed_cids(), limit=1,
    )


async def job_dictionary_maintenance(context):
    """Нормализует словарь и ставит legacy-карточки в фоновую миграцию."""
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_dictionary_maintenance, when=60, name="dictionary_maintenance_once",
            job_kwargs={"id": "dictionary_maintenance_once", "replace_existing": True},
        )
        return
    for cid in access.get_allowed_cids():
        try:
            dictionary.normalize_user_dictionary(cid)
            dictionary.queue_dictionary_rebuild(cid)
        except Exception:
            logging.exception("Dictionary maintenance failed user_id=%s", cid)


async def job_requested_dictionary_rechecks(context):
    """Забирает пользовательские запросы полной проверки по одному за проход."""
    if tracking.has_active_actions():
        return
    handled = await dictionary.process_requested_dictionary_rechecks(
        context.bot, access.get_allowed_cids(), limit=1,
    )
    if not handled:
        await dictionary.process_dictionary_rebuilds(
            context.bot, access.get_allowed_cids(), limit=1,
        )


async def job_normalize_favorite_collections(context):
    """Один спокойный проход по старым личным спискам после запуска."""
    if tracking.has_active_actions():
        context.application.job_queue.run_once(
            job_normalize_favorite_collections, when=60,
            name="normalize_favorite_collections_once",
            job_kwargs={"id": "normalize_favorite_collections_once", "replace_existing": True},
        )
        return
    try:
        if await asyncio.to_thread(leisure_collection.normalize_favorite_collections, True):
            logging.info("Favorite collections: canonical labels applied")
    except Exception:
        logging.exception("Favorite collections normalization failed")


async def job_warm_home_pages(context):
    """Молча готовит главные экраны на день.

    Ошибка одного раздела не мешает прогреть остальные. Пользователю ничего
    не отправляется; при открытии раздела бот читает уже готовый кэш. Финальная
    задача myday сначала дозаполняет всю цепочку зависимостей и только потом
    собирает сводку.
    """
    scheduled_section = str(getattr(getattr(context, "job", None), "data", "") or "")
    finalizing_myday = scheduled_section in ("", "myday")
    retry_missing = scheduled_section == "retry"
    retry_myday = False
    for cid in access.get_allowed_cids():
        if tracking.has_active_actions():
            logging.info("home cache warm skipped: user action active")
            retry_myday = retry_myday or finalizing_myday
            break
        steps = (
            ("wardrobe", lambda: wardrobe.warm_home_cache(cid)),
            ("cooking", lambda: asyncio.to_thread(restaurant_discovery.get_restaurant, cid)),
            ("learning", lambda: asyncio.to_thread(learning.warm_home_cache, cid)),
            ("travel", lambda: travel.warm_home_cache(cid)),
            ("cinema", lambda: leisure_movies.warm_movie_home_cache(cid)),
            ("music", lambda: leisure_music.warm_music_home_cache(cid)),
            ("books", lambda: leisure_books.warm_books_home_cache(cid)),
            ("games", lambda: leisure_games.warm_games_home_cache(cid)),
            ("myday", lambda: myday.warm_day_cache(cid, bot=context.bot)),
        )
        if scheduled_section and not finalizing_myday and not retry_missing:
            steps = tuple(step for step in steps if step[0] == scheduled_section)
        warmed = []
        dependency_failed = False
        for name, call in steps:
            if tracking.has_active_actions():
                logging.info("home cache warm paused cid=%s before=%s", cid, name)
                dependency_failed = True
                break
            if name == "myday" and dependency_failed and not retry_missing:
                break
            if retry_missing and home_cache.is_ready(name, cid):
                continue
            await asyncio.sleep(0)
            try:
                result = await call()
                ready = (
                    bool(result.get("name")) if name == "cooking" and isinstance(result, dict)
                    else bool(any(result.values())) if isinstance(result, dict)
                    else result is not False
                )
                if ready:
                    warmed.append(name)
                else:
                    dependency_failed = True
                    logging.warning("home cache warm incomplete cid=%s section=%s", cid, name)
            except Exception:
                dependency_failed = True
                logging.exception("home cache warm failed cid=%s section=%s", cid, name)
        if finalizing_myday and (dependency_failed or "myday" not in warmed):
            retry_myday = True
        logging.info("home cache warm complete cid=%s sections=%s", cid, ",".join(warmed))
    if retry_myday:
        _schedule_myday_warm_retry(context)


def _schedule_myday_warm_retry(context, delay_seconds=15 * 60):
    """Повторяет всю финальную цепочку, если прогрев был прерван или неполон."""
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None:
        return False
    job_name = "warm_home_myday_retry"
    get_jobs_by_name = getattr(job_queue, "get_jobs_by_name", None)
    if callable(get_jobs_by_name) and get_jobs_by_name(job_name):
        return True
    job_queue.run_once(
        job_warm_home_pages,
        when=delay_seconds,
        data="myday",
        **_job_options(job_name),
    )
    return True
