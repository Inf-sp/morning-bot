"""Обёртка Pexels Photo Search API для фото рецептов.

Синхронный вызов (requests), по аналогии с ai.llm_json —
вызывающая сторона сама оборачивает в asyncio.to_thread, этот модуль asyncio не трогает.
"""
import logging

import requests

import config

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.pexels.com/v1/search"
_TIMEOUT = 3
_PER_PAGE = 1


class PexelsError(Exception):
    pass


class PexelsTimeoutError(PexelsError):
    pass


class PexelsNetworkError(PexelsError):
    pass


class PexelsApiError(PexelsError):
    pass


class PexelsInvalidResponseError(PexelsError):
    pass


def search_photos(query: str, orientation: str = "square", size: str = "large",
                  locale: str = "en-US", per_page: int = _PER_PAGE,
                  page: int = 1, timeout: int | float = _TIMEOUT) -> list:
    """Ищет фото по `query` через Pexels: orientation=square, size=large,
    locale=en-US, per_page=1.

    Возвращает список «сырых» объектов photo из ответа Pexels.
    Каждый элемент содержит как минимум: id, alt, width, height, photographer,
    photographer_url, src (словарь размеров), url (страница фото на pexels.com).

    Пустая выдача возвращается как [], временные/API/JSON ошибки выбрасываются
    типизированно, чтобы вызывающий код не запускал fallback-запросы при ошибке.
    """
    if not config.PEXELS_API_KEY or not (query or "").strip():
        raise PexelsApiError("missing_api_key_or_query")
    try:
        r = requests.get(
            _SEARCH_URL,
            headers={"Authorization": config.PEXELS_API_KEY},
            params={
                "query": query.strip(),
                "orientation": orientation,
                "size": size,
                "locale": locale,
                "per_page": per_page,
                "page": page,
            },
            timeout=timeout,
        )
        if r.status_code != 200:
            _log.warning("pexels search failed: HTTP %s", r.status_code)
            raise PexelsApiError(f"http_{r.status_code}")
        data = r.json()
        photos = data.get("photos") or []
        if not isinstance(photos, list):
            raise PexelsInvalidResponseError("photos_not_list")
        return photos
    except requests.Timeout as e:
        raise PexelsTimeoutError("timeout") from e
    except requests.RequestException as e:
        raise PexelsNetworkError("network_error") from e
    except ValueError as e:
        raise PexelsInvalidResponseError("invalid_json") from e
