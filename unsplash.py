"""Обёртка Unsplash Search Photos API для фото рецептов (§8 spec-gotovka-redesign.md).

Синхронный вызов (requests), по аналогии с ai.llm_json / tmdb._get — вызывающая
сторона оборачивает в asyncio.to_thread сама, этот модуль asyncio не трогает.

Деградация: при отсутствии ключа, сетевой ошибке, не-200 ответе, пустых
результатах или невалидном JSON — функция просто возвращает None, без
исключений наружу (карточка рецепта в этом случае отправляется без фото).
"""
import logging

import requests

import config

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.unsplash.com/search/photos"
_TIMEOUT = 6  # секунд — не подвешивать бота, если Unsplash тормозит


def get_dish_photo_url(query: str) -> str | None:
    """Ищет фото блюда на Unsplash по `query` (ожидается search_query_en из LLM).

    Возвращает URL поля urls.regular первого результата или None, если фото
    недоступно (нет ключа, ошибка сети/HTTP, пустая выдача, битый JSON).
    """
    if not config.UNSPLASH_ACCESS_KEY or not (query or "").strip():
        return None
    try:
        r = requests.get(
            _SEARCH_URL,
            headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
            params={"query": query.strip(), "per_page": 1},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("unsplash search failed: HTTP %s", r.status_code)
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        urls = results[0].get("urls") or {}
        return urls.get("regular") or urls.get("small") or None
    except Exception as e:
        # секретный ключ никогда не попадает в текст исключения requests,
        # но на всякий случай прогоняем через redact перед логированием
        import secure
        _log.warning("unsplash request error: %s", secure.redact(str(e)))
        return None
