"""Контроль доступа: allowlist, инвайт-коды, owner-гард.

Все операции над списком разрешённых пользователей и инвайтами — здесь.
Owner (CHAT_ID из env) всегда допущен и никогда не пишется в store.
"""
import secrets
import time
import config
import store

_INVITE_TTL = 48 * 3600  # 48 часов


def _load_allowed() -> list:
    return store._load(config.ALLOWED_CIDS_KEY).get("cids", [])


def _save_allowed(cids: list):
    store._save(config.ALLOWED_CIDS_KEY, {"cids": cids})


def _load_invites() -> dict:
    return store._load(config.PENDING_INVITES_KEY).get("invites", {})


def _save_invites(invites: dict):
    store._save(config.PENDING_INVITES_KEY, {"invites": invites})


def is_owner(cid) -> bool:
    return bool(config.CHAT_ID) and str(cid) == str(config.CHAT_ID)


def is_allowed(cid) -> bool:
    """True, если cid — owner или в allowlist."""
    if is_owner(cid):
        return True
    return str(cid) in _load_allowed()


def allow_user(cid):
    """Добавить cid в allowlist (если ещё не там)."""
    cids = _load_allowed()
    key = str(cid)
    if key not in cids:
        cids.append(key)
        _save_allowed(cids)


def revoke_user(cid):
    """Удалить cid из allowlist."""
    cids = _load_allowed()
    key = str(cid)
    if key in cids:
        cids.remove(key)
        _save_allowed(cids)


def get_allowed_cids() -> list:
    """Все активные cid: owner + allowlist (без дублей)."""
    cids = list(_load_allowed())
    if config.CHAT_ID and str(config.CHAT_ID) not in cids:
        cids.insert(0, str(config.CHAT_ID))
    return cids


# ---------- Инвайты ----------

def _purge_expired(invites: dict) -> dict:
    now = time.time()
    return {k: v for k, v in invites.items() if now - v < _INVITE_TTL}


def create_invite() -> str:
    """Создать одноразовый инвайт-код. Возвращает код."""
    invites = _purge_expired(_load_invites())
    code = secrets.token_hex(4)          # 8 hex-символов
    invites[code] = time.time()
    _save_invites(invites)
    return code


def use_invite(code: str, cid) -> bool:
    """Попытаться активировать инвайт. True при успехе (добавляет cid в allowlist)."""
    invites = _purge_expired(_load_invites())
    if code not in invites:
        return False
    del invites[code]
    _save_invites(invites)
    allow_user(cid)
    return True


def pending_invites() -> dict:
    """Список ещё не использованных инвайтов {code: ts}."""
    return _purge_expired(_load_invites())
