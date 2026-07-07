"""Обёртка Pexels Photo Search API для фото рецептов — основной источник фото
блюд (photo_provider.py использует его первым, до Unsplash-фолбэка).

Синхронный вызов (requests), по аналогии с ai.llm_json / unsplash.search_photos —
вызывающая сторона сама оборачивает в asyncio.to_thread, этот модуль asyncio не трогает.

Деградация: при отсутствии ключа, сетевой ошибке, не-200 ответе, пустых
результатах или невалидном JSON — функция возвращает [], без исключений наружу.
"""
import logging

import requests

import config

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.pexels.com/v1/search"
_TIMEOUT = 6  # секунд — не подвешивать бота, если Pexels тормозит
_PER_PAGE = 15


def search_photos(query: str, per_page: int = _PER_PAGE, page: int = 1) -> list:
    """Ищет фото по `query` через Pexels: orientation=square, size=large,
    locale=en-US, per_page=15 (карточки Telegram, top-N кандидатов для scoring).

    Возвращает список «сырых» объектов photo из ответа Pexels (для scoring в
    photo_provider.py) — [] при отсутствии ключа/сетевой ошибке/пустой выдаче.
    Каждый элемент содержит как минимум: id, alt, width, height, photographer,
    photographer_url, src (словарь размеров), url (страница фото на pexels.com).
    """
    if not config.PEXELS_API_KEY or not (query or "").strip():
        return []
    try:
        r = requests.get(
            _SEARCH_URL,
            headers={"Authorization": config.PEXELS_API_KEY},
            params={
                "query": query.strip(),
                "orientation": "square",
                "size": "large",
                "locale": "en-US",
                "per_page": per_page,
                "page": page,
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("pexels search failed: HTTP %s", r.status_code)
            return []
        data = r.json()
        photos = data.get("photos") or []
        return photos if isinstance(photos, list) else []
    except Exception as e:
        import secure
        _log.warning("pexels request error: %s", secure.redact(str(e)))
        return []
