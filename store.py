import json
import copy
import logging
import uuid as _uuid
import threading
from pathlib import Path
import config

_HERE = Path(__file__).parent

_log = logging.getLogger(__name__)

# --- Postgres (с откатом в память) ---
_conn = None
_mem = {}
_mem_locks = {}
_conn_lock = threading.RLock()

def _db():
    global _conn
    if not config.DATABASE_URL:
        return None
    try:
        if _conn is None or _conn.closed:
            import psycopg2
            _conn = psycopg2.connect(config.DATABASE_URL)
            _conn.autocommit = True
            with _conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
        with _conn.cursor() as cur:
            cur.execute("SELECT 1")
        return _conn
    except Exception:
        try:
            import psycopg2
            _conn = psycopg2.connect(config.DATABASE_URL)
            _conn.autocommit = True
            with _conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
            return _conn
        except Exception as e:
            _log.warning("store: DB reconnect failed, using memory: %s", e)
            return None

def _load(key):
    conn = _db()
    if conn is None:
        return {k: list(v) if isinstance(v, list) else v for k, v in _mem.get(key, {}).items()}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM kv WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else {}
    except Exception as e:
        _log.warning("store: _load(%s) DB error, using memory: %s", key, e)
        return {k: list(v) if isinstance(v, list) else v for k, v in _mem.get(key, {}).items()}

def _save(key, data):
    conn = _db()
    if conn is None:
        _mem[key] = data
        return
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO kv (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                        (key, json.dumps(data, ensure_ascii=False)))
    except Exception as e:
        _log.warning("store: _save(%s) DB error, falling back to memory: %s", key, e)
        _mem[key] = data

def mutate_kv(key, mutator_fn):
    """Atomically load/mutate/save one JSON KV record.

    With Postgres, uses transaction-scoped advisory lock per key, so concurrent
    worker processes cannot race on limit counters. In memory fallback is only
    process-local and intended for local/dev operation.
    """
    conn = _db()
    if conn is None:
        lock = _mem_locks.setdefault(key, threading.Lock())
        with lock:
            current = copy.deepcopy(_mem.get(key, {}))
            new_value, result = mutator_fn(current if isinstance(current, dict) else {})
            _mem[key] = new_value
            return result
    with _conn_lock:
        old_autocommit = conn.autocommit
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
                cur.execute("SELECT value FROM kv WHERE key = %s FOR UPDATE", (key,))
                row = cur.fetchone()
                current = row[0] if row else {}
                new_value, result = mutator_fn(current if isinstance(current, dict) else {})
                cur.execute("INSERT INTO kv (key, value) VALUES (%s, %s) "
                            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                            (key, json.dumps(new_value, ensure_ascii=False)))
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.autocommit = old_autocommit

# --- helpers ---
def get_settings(chat_id):
    return _load(config.SETTINGS_FILE).get(str(chat_id), config.DEFAULT_CITY)

def set_settings(chat_id, lat, lon, city, country="", cc=""):
    d = _load(config.SETTINGS_FILE)
    d[str(chat_id)] = {"lat": lat, "lon": lon, "city": city, "country": country, "cc": cc}
    _save(config.SETTINGS_FILE, d)


def get_last_admin_deploy_notified_version():
    state = _load(config.DEPLOY_REPORT_KEY)
    return str(
        state.get("last_admin_deploy_notified_version", "")
        or state.get("last_sent_version", "")
        or ""
    )


def set_last_admin_deploy_notified_version(version, sent_at):
    state = _load(config.DEPLOY_REPORT_KEY)
    state["last_admin_deploy_notified_version"] = str(version or "")
    state["last_sent_version"] = str(version or "")
    state["sent_at"] = str(sent_at or "")
    _save(config.DEPLOY_REPORT_KEY, state)

def get_profile(chat_id):
    """Память пользователя (dict). Пусто -> {}."""
    return _load(config.PROFILE_KEY).get(str(chat_id), {})

def set_profile(chat_id, prof):
    d = _load(config.PROFILE_KEY)
    d[str(chat_id)] = prof
    _save(config.PROFILE_KEY, d)

def get_wardrobe_daylook(chat_id):
    """Кэш дневного образа: {"date","version","item_ids","look_data","text"}.
    Читать напрямую не стоит — используйте get_valid_wardrobe_daylook для проверки
    ссылочной целостности (версия гардероба + существование вещей по id)."""
    return get_profile(chat_id).get("wardrobe_daylook", {})

def set_wardrobe_daylook(chat_id, data):
    prof = get_profile(chat_id)
    prof["wardrobe_daylook"] = data
    set_profile(chat_id, prof)

def clear_wardrobe_daylook(chat_id):
    prof = get_profile(chat_id)
    if "wardrobe_daylook" in prof:
        prof.pop("wardrobe_daylook", None)
        set_profile(chat_id, prof)

WARDROBE_HISTORY_LIMIT = 14


def get_wardrobe_history(chat_id) -> list:
    """Персистентная история собранных образов (не путать с recent_looks —
    in-memory антиповтор по названиям, не переживающий рестарт). Каждая запись:
    {"date","weather_tags","item_ids"}. Последние WARDROBE_HISTORY_LIMIT записей."""
    hist = get_profile(chat_id).get("wardrobe_history", [])
    return hist if isinstance(hist, list) else []


def add_wardrobe_history_entry(chat_id, date, weather_tags, item_ids):
    prof = get_profile(chat_id)
    hist = prof.get("wardrobe_history", [])
    if not isinstance(hist, list):
        hist = []
    hist.append({"date": date, "weather_tags": list(weather_tags or []), "item_ids": list(item_ids or [])})
    prof["wardrobe_history"] = hist[-WARDROBE_HISTORY_LIMIT:]
    set_profile(chat_id, prof)

_LEVEL_MIGRATION = {
    "A1": "simple", "A2": "simple",
    "B1": "medium",
    "B2": "hard", "C1": "hard", "C2": "hard",
}


def _migrate_level_value(chat_id, language, raw):
    """Одноразовая ленивая миграция старых CEFR-значений (A1-C2) на новую
    3-уровневую шкалу (simple/medium/hard) — конвертирует и сразу перезаписывает."""
    new_value = _LEVEL_MIGRATION.get(raw)
    if not new_value:
        return raw
    set_level(chat_id, language, new_value)
    return new_value


def get_level(chat_id, language):
    raw = _load(config.LEVELS_FILE).get(str(chat_id), {}).get(language, "medium")
    if raw in ("simple", "medium", "hard"):
        return raw
    return _migrate_level_value(chat_id, language, raw)

def set_level(chat_id, language, level):
    d = _load(config.LEVELS_FILE)
    d.setdefault(str(chat_id), {})[language] = level
    _save(config.LEVELS_FILE, d)

def has_level(chat_id, language):
    return language in _load(config.LEVELS_FILE).get(str(chat_id), {})

def ensure_level(chat_id, language, level="medium"):
    d = _load(config.LEVELS_FILE)
    user = d.setdefault(str(chat_id), {})
    if language not in user:
        user[language] = level
        _save(config.LEVELS_FILE, d)

def get_learning_language(chat_id):
    code = str(get_profile(chat_id).get("learning_language") or "").strip().lower()
    return code if code in ("nl", "en") else ""

def set_learning_language(chat_id, language):
    code = str(language or "").strip().lower()
    if code not in ("nl", "en"):
        return
    prof = get_profile(chat_id)
    prof["learning_language"] = code
    set_profile(chat_id, prof)

ZONE_SUBCATS = {
    "Верх": ["Футболки", "Поло", "Рубашки", "Лонгсливы", "Свитеры", "Кардиганы",
             "Худи", "Пиджаки", "Другое"],
    "Верхняя одежда": ["Ветровки", "Куртки", "Пальто", "Пуховики", "Плащи", "Другое"],
    "Низ": ["Джинсы", "Брюки", "Чиносы", "Шорты", "Спортивные брюки", "Другое"],
    "Обувь": ["Кеды", "Кроссовки", "Лоферы", "Ботинки", "Сандалии", "Тапочки", "Другое"],
    "Аксессуары": ["Кепки", "Шапки", "Ремни", "Часы", "Очки", "Украшения", "Шарфы",
                   "Перчатки", "Сумки", "Рюкзаки", "Носки", "Другое"],
    "Другое": ["Другое"],
}
ZONE_ORDER = ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары", "Другое"]

def _empty_wardrobe() -> dict:
    """Новый пустой гардероб. Функция, а не модульная константа — иначе
    вложенный dict "zones" оказался бы общей ссылкой между всеми пользователями."""
    return {"_v": 0, "zones": {}}


def _all_item_ids(w) -> set:
    return {it["id"] for zone in (w or {}).get("zones", {}).values()
            for items in zone.values() for it in items}


def _migrate_legacy_wardrobe(old: dict) -> dict:
    """Старый плоский формат {категория_строка: [вещь_строка,...]} -> новая схема
    с zone/subcategory/id. Без LLM-вызова: zone/subcategory угадываются эвристикой
    (локальный импорт wardrobe — избегаем цикла store<->wardrobe на уровне модуля)."""
    import uuid
    import wardrobe as _wardrobe_mod
    new = {"_v": 0, "zones": {}}
    for cat, items in (old or {}).items():
        if cat == "_v" or not isinstance(items, list):
            continue
        zone = _wardrobe_mod._zone_of(str(cat))
        for raw_name in items:
            name = str(raw_name).strip()
            if not name:
                continue
            # Название вещи не всегда содержит тип (может быть просто "белая") —
            # старая категория (например "футболки") часто несёт этот смысл, поэтому
            # угадываем сначала по названию вещи, а если не нашлось — по категории.
            subcat = _wardrobe_mod._guess_subcategory(zone, name, fallback_text=str(cat))
            bucket = new["zones"].setdefault(zone, {}).setdefault(subcat, [])
            if any(x["name"].lower() == name.lower() for x in bucket):
                continue
            bucket.append({
                "id": uuid.uuid4().hex, "name": name, "zone": zone, "subcategory": subcat,
                "color": "", "color_secondary": None, "material": None,
                "style": None, "season": None,
            })
    return new


def load_wardrobe(cid=None):
    """Per-user wardrobe. Для CHAT_ID одноразово мигрирует глобальный wardrobe.json.
    Старый плоский формат конвертируется в {"_v","zones"} при первом чтении."""
    if cid is not None:
        key = f"wardrobe_user_{cid}"
        w = _load(key)
        if not w and config.CHAT_ID and str(cid) == str(config.CHAT_ID):
            # Одноразовая миграция: глобальный шкаф владельца → per-user ключ
            global_w = _load(config.WARDROBE_FILE)
            if isinstance(global_w, dict) and any(k != "_v" for k in global_w):
                w = global_w
        if not w:
            return _empty_wardrobe()
        if "zones" not in w:
            w = _migrate_legacy_wardrobe(w)
            _save(key, w)
        # В in-memory fallback-режиме _load не делает глубокую копию вложенных dict
        # (только list верхнего уровня) — без неё мутация возвращённого объекта до
        # save_wardrobe могла бы незаметно повлиять на _mem напрямую.
        return copy.deepcopy(w)
    return _load(config.WARDROBE_FILE) or {}

def save_wardrobe(w, cid=None):
    if cid is not None:
        _save(f"wardrobe_user_{cid}", w)
    else:
        _save(config.WARDROBE_FILE, w)


def mutate_wardrobe(cid, mutator_fn):
    """Единственный легитимный способ изменить гардероб. mutator_fn(w) мутирует w
    на месте. Всегда: инкремент версии, сохранение, инвалидация зависимых кэшей."""
    w = load_wardrobe(cid)
    before_ids = _all_item_ids(w)
    result = mutator_fn(w)
    if result is not None:
        w = result
    w["_v"] = int(w.get("_v", 0)) + 1
    save_wardrobe(w, cid)
    after_ids = _all_item_ids(w)
    _invalidate_dependents(cid, removed_ids=before_ids - after_ids,
                           added_ids=after_ids - before_ids)
    return w


def add_wardrobe_items(cid, items: list) -> list:
    """items — нормализованные объекты без id (id генерируется здесь).
    Дедуп по (subcategory, name.lower()) в рамках зоны."""
    import uuid

    def _mut(w):
        for it in items:
            it = dict(it)
            it["id"] = uuid.uuid4().hex
            bucket = w.setdefault("zones", {}).setdefault(it["zone"], {}).setdefault(it["subcategory"], [])
            if not any(existing["name"].lower() == it["name"].lower() for existing in bucket):
                bucket.append(it)

    mutate_wardrobe(cid, _mut)
    return items


def remove_wardrobe_items(cid, item_ids) -> int:
    """Удаляет вещи по id. Возвращает число реально удалённых."""
    item_ids = set(item_ids)
    removed = {"n": 0}

    def _mut(w):
        for zone in w.get("zones", {}).values():
            for subcat, lst in list(zone.items()):
                before = len(lst)
                zone[subcat] = [it for it in lst if it.get("id") not in item_ids]
                removed["n"] += before - len(zone[subcat])

    mutate_wardrobe(cid, _mut)
    return removed["n"]


def reset_wardrobe(cid):
    """Полная замена гардероба пустым (используется при mode=replace в анкете)."""
    save_wardrobe(_empty_wardrobe(), cid)
    clear_wardrobe_daylook(cid)


def get_valid_wardrobe_daylook(cid):
    """Читает wardrobe_daylook и валидирует ссылочную целостность: версия должна
    совпадать с текущей версией гардероба, и все item_ids обязаны существовать в
    текущем шкафу. Невалидный кэш = {} (как будто его нет вовсе) — единственная
    точка чтения кэша образа дня, устраняет «призрачные вещи» структурно."""
    cached = get_wardrobe_daylook(cid)
    if not isinstance(cached, dict) or not cached:
        return {}
    w = load_wardrobe(cid)
    if cached.get("version") != w.get("_v", 0):
        return {}
    ids = set(cached.get("item_ids") or [])
    if not ids or not (ids <= _all_item_ids(w)):
        return {}
    return cached


def _invalidate_dependents(cid, removed_ids, added_ids):
    """Единая точка синхронизации кэшей при любой мутации гардероба. Согласно
    текущему скоупу зависимых кэшей — только wardrobe_daylook требует активной
    инвалидации (recent_looks/last_look — текстовые снапшоты на момент показа,
    wardrobe_gaps пересчитывается отдельно в wardrobe._resync_wardrobe_gaps,
    кэш send_improve (w["_analysis"]) самоинвалидируется через хэш состава вещей —
    активная очистка ему не нужна)."""
    if not removed_ids:
        return
    daylook = get_wardrobe_daylook(cid)
    if daylook and (set(daylook.get("item_ids") or []) & removed_ids):
        clear_wardrobe_daylook(cid)


def wardrobe_to_text(w):
    """Текст для LLM-промптов: 'Подкатегория: вещь1, вещь2' построчно."""
    lines = []
    for subs in (w or {}).get("zones", {}).values():
        for subcat, items in subs.items():
            if items:
                lines.append(f"{subcat}: " + ", ".join(it["name"] for it in items))
    return "\n".join(lines)

def _migrate_flat(key, chat_id, d) -> dict:
    """Если данные — плоский список (legacy), мигрируем в per-user dict для CHAT_ID."""
    if isinstance(d, list) and config.CHAT_ID and str(chat_id) == str(config.CHAT_ID):
        new_d = {str(chat_id): d}
        _save(key, new_d)
        return new_d
    return {}

def get_list(key, chat_id):
    d = _load(key)
    if not isinstance(d, dict):
        d = _migrate_flat(key, chat_id, d)
    return d.get(str(chat_id), [])

def add_to_list(key, chat_id, item):
    d = _load(key)
    if not isinstance(d, dict):
        d = _migrate_flat(key, chat_id, d)
    d.setdefault(str(chat_id), []).append(item)
    _save(key, d)

def set_list(key, chat_id, items):
    d = _load(key)
    if not isinstance(d, dict):
        d = _migrate_flat(key, chat_id, d)
    d[str(chat_id)] = items
    _save(key, d)

# Ключи с per-user данными вида {str(cid): ...}
_PER_USER_KEYS = {
    config.SETTINGS_FILE, config.PROFILE_KEY, config.LEVELS_FILE,
    config.ARTISTS_KEY, config.WATCHLIST_KEY, config.READLIST_KEY,
    config.COUNTRIES_KEY, config.BOOKS_KEY,
    config.FAVCOUNTRIES_KEY, config.MOVIE_BLACKLIST_KEY, config.BOOK_BLACKLIST_KEY,
    config.MUSIC_DISLIKE_KEY, config.TRAVEL_DISLIKE_KEY, config.WORRIES_KEY,
    config.NOTES_KEY, config.DICT_KEY,
    config.LAGOM_KEY, config.DIARY_KEY, config.LIFEHACK_KEY,
    config.FRIDGE_KEY, config.MY_RECIPES_KEY, config.LEFTOVER_RECIPES_SEEN_KEY, config.QUOTE_AUTHORS_KEY,
    config.MOTIV_LAGOM_SEEN_KEY, config.CONCERTS_CACHE_KEY,
}

def purge_user(cid):
    """Удаляет все данные пользователя из БД: per-user ключи + wardrobe_user_{cid}."""
    cid_str = str(cid)
    # Per-user JSON-словари
    for key in _PER_USER_KEYS:
        d = _load(key)
        if isinstance(d, dict) and cid_str in d:
            del d[cid_str]
            _save(key, d)
    # Отдельный ключ шкафа
    wardrobe_key = f"wardrobe_user_{cid_str}"
    conn = _db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kv WHERE key = %s", (wardrobe_key,))
        except Exception as e:
            _log.warning("purge_user: cannot delete %s: %s", wardrobe_key, e)
    elif wardrobe_key in _mem:
        del _mem[wardrobe_key]

# --- общее состояние в памяти (сбрасывается при рестарте) ---
challenge_state = {}
chat_history = {}
add_wardrobe_mode = {}
game_state = {}
game_config = {}
train_state = {}        # chat_id -> состояние тренажёра слов (формат/ответ/слово)
train_polls = {}        # poll_id -> chat_id для native quiz poll
smart_reveal_state = {}         # chat_id -> вопрос/подсказка «умного раскрытия» до ответа
smart_reveal_result_state = {}  # chat_id -> результат «умного раскрытия», ждёт Понял/Повторить позже
dialogue_state = {}     # chat_id -> состояние диалогового тренажёра (шаги, история реплик)
dict_pending_add = {}   # chat_id -> нормализованная запись словаря, ожидающая подтверждения
dict_pending_batch = {}  # chat_id -> [записи], предложенные из большого текста, ждут "Добавить всё"
pending_input = {}
last_inline_message = {}  # chat_id -> message_id последнего сообщения с инлайн-кнопками (для авто-снятия)
last_recos = {}
suggested_countries = {}
last_action = {}        # chat_id -> ("oneshot", key) | ("role", role, text) | None
last_answer = {}        # chat_id -> текст последнего ответа ассистента (для «Сохранить в заметки»)
last_recipe = {}        # chat_id -> dict рецепта (для «Полный рецепт»)
recent_looks = {}       # chat_id -> [последние луки] (не повторять 3 дня)
last_word = {}          # chat_id -> последнее показанное слово/фраза (для «Добавить слово»)
game_recent = {}        # chat_id -> [последние загаданные персонажи]
list_sel = {}           # "chat_id:ctx" -> set(индексов) для чистки списков
last_source = {}        # chat_id -> откуда последний ответ (для категорий избранного)
last_surface = {}       # chat_id -> surface последнего ответа (для «Короче/Глубже»)
last_look = {}          # chat_id -> последний показанный образ (для фидбека гардероба)

# --- ListRecord: стабильный id + revision для списков вне гардероба (PR3a) ---
# Формат элемента: строка -> {"id": uuid4_hex, "value": строка} (обёртка);
# dict -> тот же dict с добавленным полем "id" (без вложенности — существующий
# код, читающий поля записи напрямую, продолжает работать без изменений).
_list_revisions = {}    # "key:chat_id" -> int, версия коллекции (PR3a)


def _revision_slot(key, chat_id):
    return f"{key}:{chat_id}"


def get_list_revision(key, chat_id):
    return _list_revisions.get(_revision_slot(key, chat_id), 0)


def _bump_list_revision(key, chat_id):
    slot = _revision_slot(key, chat_id)
    _list_revisions[slot] = _list_revisions.get(slot, 0) + 1
    return _list_revisions[slot]


def ensure_list_ids_via(getter, setter, key, chat_id):
    """Как ensure_list_ids, но читает/пишет коллекцию через произвольные
    getter(chat_id)/setter(chat_id, items) вместо get_list/set_list — нужно для
    коллекций, хранящихся не отдельным KV-ключом, а полем внутри профиля
    (например memory.get_lagom/set_lagom). `key` используется только как имя
    слота revision, не как storage-ключ."""
    items = getter(chat_id)
    changed = False
    out = []
    for it in items:
        if isinstance(it, dict):
            if "id" not in it:
                it = {**it, "id": _uuid.uuid4().hex}
                changed = True
            out.append(it)
        else:
            out.append({"id": _uuid.uuid4().hex, "value": it})
            changed = True
    if changed:
        setter(chat_id, out)
        _bump_list_revision(key, chat_id)
    return out


def ensure_list_ids(key, chat_id):
    """Возвращает список для (key, chat_id), лениво проставляя стабильный "id"
    каждому элементу без него. Строковые элементы оборачиваются в
    {"id":..., "value": строка}; dict-элементы получают поле "id" на месте.
    Сохраняет список обратно и бампает revision, только если что-то изменилось."""
    return ensure_list_ids_via(
        lambda cid: get_list(key, cid),
        lambda cid, items: set_list(key, cid, items),
        key, chat_id,
    )


def remove_from_list_by_ids_via(getter, setter, key, chat_id, ids):
    """Как remove_from_list_by_ids, но через произвольные getter/setter — см.
    ensure_list_ids_via."""
    if not ids:
        return 0
    items = ensure_list_ids_via(getter, setter, key, chat_id)
    ids = set(ids)
    kept = [it for it in items if it.get("id") not in ids]
    removed = len(items) - len(kept)
    if removed:
        setter(chat_id, kept)
        _bump_list_revision(key, chat_id)
    return removed


def remove_from_list_by_ids(key, chat_id, ids):
    """Удаляет записи с указанными id из коллекции (key, chat_id). Возвращает
    число реально удалённых записей. Бампает revision, только если что-то
    удалено — устаревший view с прежней revision будет корректно отклонён."""
    return remove_from_list_by_ids_via(
        lambda cid: get_list(key, cid),
        lambda cid, items: set_list(key, cid, items),
        key, chat_id, ids,
    )
