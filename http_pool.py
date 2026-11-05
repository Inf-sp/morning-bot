"""Общий пул HTTP-соединений для `requests`: keep-alive вместо нового TCP + TLS на каждый запрос.

`requests.get/post` по умолчанию создают сессию на один запрос и закрывают её — к Gemini, Groq, погоде и т. д.
каждый раз заново открывается соединение (лишние 0,1–0,3 с). `install()` при старте бота направляет все
`requests.*` в одну общую сессию с пулом: код вызовов не меняется, а тесты, подменяющие `requests.get/post`,
по-прежнему перехватывают запросы (пул ставится только в `bot.main`).

Cookie не сохраняются (как и раньше: каждый запрос шёл без cookie), прокси и CA из окружения учитываются на
каждом запросе (`trust_env`), таймауты задают сами вызовы.
"""
import threading
from http.cookiejar import DefaultCookiePolicy

import requests
from requests.adapters import HTTPAdapter

POOL_HOSTS = 32  # сколько хостов держат открытые соединения
POOL_PER_HOST = 16  # соединений на хост: параллельные фоновые сборки и нажатия
_lock = threading.Lock()
_session = None
_original = None


def session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            shared = requests.Session()
            shared.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            adapter = HTTPAdapter(pool_connections=POOL_HOSTS, pool_maxsize=POOL_PER_HOST)
            shared.mount("https://", adapter)
            shared.mount("http://", adapter)
            _session = shared
        return _session


def _pooled_request(method, url, **kwargs):
    return session().request(method=method, url=url, **kwargs)


def install() -> None:
    """Все `requests.get/post/…/request` — через общую сессию. Повторный вызов ничего не меняет."""
    global _original
    with _lock:
        if _original is not None:
            return
        _original = requests.api.request
        requests.api.request = _pooled_request  # get/post/put/… внутри requests.api зовут этот request
        requests.request = _pooled_request


def uninstall() -> None:
    """Вернуть одноразовые сессии `requests` (тесты)."""
    global _original, _session
    with _lock:
        if _original is None:
            return
        requests.api.request = _original
        requests.request = _original
        _original = None
        if _session is not None:
            _session.close()
            _session = None
