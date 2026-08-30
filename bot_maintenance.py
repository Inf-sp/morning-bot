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
