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


def is_owner(cid) -> bool:
    return bool(config.CHAT_ID) and str(cid) == str(config.CHAT_ID)


def is_allowed(cid) -> bool:
    """True, если cid — owner или в allowlist."""
    if is_owner(cid):
        return True
    return str(cid) in _load_allowed()


def allow_user(cid):
    """Добавить cid в allowlist (если ещё не там)."""
    key = str(cid)

    def change(data):
        data = data if isinstance(data, dict) else {}
        cids = list(data.get("cids") or [])
        if key not in cids:
            cids.append(key)
        data["cids"] = cids
        return data, None

    store.mutate_kv(config.ALLOWED_CIDS_KEY, change)


def revoke_user(cid):
    """Удалить cid из allowlist."""
    key = str(cid)

    def change(data):
        data = data if isinstance(data, dict) else {}
        data["cids"] = [value for value in (data.get("cids") or []) if value != key]
        return data, None

    store.mutate_kv(config.ALLOWED_CIDS_KEY, change)


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
    code = secrets.token_hex(4)          # 8 hex-символов

    def change(data):
        data = data if isinstance(data, dict) else {}
        invites = _purge_expired(dict(data.get("invites") or {}))
        invites[code] = time.time()
        data["invites"] = invites
        return data, None

    store.mutate_kv(config.PENDING_INVITES_KEY, change)
    return code


def use_invite(code: str, cid) -> bool:
    """Попытаться активировать инвайт. True при успехе (добавляет cid в allowlist)."""
    def consume(data):
        data = data if isinstance(data, dict) else {}
        invites = _purge_expired(dict(data.get("invites") or {}))
        claimed = code in invites
        if claimed:
            del invites[code]
        data["invites"] = invites
        return data, claimed

    if not store.mutate_kv(config.PENDING_INVITES_KEY, consume):
        return False
    allow_user(cid)
    return True


def pending_invites() -> dict:
    """Список ещё не использованных инвайтов {code: ts}."""
    def change(data):
        data = data if isinstance(data, dict) else {}
        invites = _purge_expired(dict(data.get("invites") or {}))
        data["invites"] = invites
        return data, invites

    return store.mutate_kv(config.PENDING_INVITES_KEY, change)
