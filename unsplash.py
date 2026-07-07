"""Обёртка Unsplash Search Photos API для фото рецептов — fallback-источник,
используется photo_provider.py вторым, после Pexels (см. docs/food.md).

Синхронный вызов (requests), по аналогии с ai.llm_json / tmdb._get — вызывающая
сторона оборачивает в asyncio.to_thread сама, этот модуль asyncio не трогает.

Деградация: при отсутствии ключа, сетевой ошибке, не-200 ответе, пустых
результатах или невалидном JSON — функция возвращает [], без исключений наружу.
"""
import logging

import requests

import config

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.unsplash.com/search/photos"
_TIMEOUT = 6  # секунд — не подвешивать бота, если Unsplash тормозит
_PER_PAGE = 10


def search_photos(query: str, per_page: int = _PER_PAGE) -> list:
    """Ищет фото по `query` через Unsplash: orientation=squarish (карточки Telegram),
    content_filter=high, order_by=relevant.

    Возвращает список «сырых» объектов photo из ответа Unsplash (для scoring в
    photo_provider.py) — [] при отсутствии ключа/сетевой ошибке/пустой выдаче.
    Каждый элемент содержит как минимум: id, description, alt_description,
    urls (словарь размеров), user (photographer), links.
    """
    if not config.UNSPLASH_ACCESS_KEY or not (query or "").strip():
        return []
    try:
        r = requests.get(
            _SEARCH_URL,
            headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
            params={
                "query": query.strip(),
                "orientation": "squarish",
                "content_filter": "high",
                "order_by": "relevant",
                "per_page": per_page,
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("unsplash search failed: HTTP %s", r.status_code)
            return []
        data = r.json()
        results = data.get("results") or []
        return results if isinstance(results, list) else []
    except Exception as e:
        # секретный ключ никогда не попадает в текст исключения requests,
        # но на всякий случай прогоняем через redact перед логированием
        import secure
        _log.warning("unsplash request error: %s", secure.redact(str(e)))
        return []
