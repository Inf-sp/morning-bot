import os
import json
import logging
from pathlib import Path
import config

_HERE = Path(__file__).parent

_log = logging.getLogger(__name__)

# --- Postgres (с откатом в память) ---
_conn = None
_mem = {}

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

# --- helpers ---
def get_settings(chat_id):
    return _load(config.SETTINGS_FILE).get(str(chat_id), config.DEFAULT_CITY)

def set_settings(chat_id, lat, lon, city, country="", cc=""):
    d = _load(config.SETTINGS_FILE)
    d[str(chat_id)] = {"lat": lat, "lon": lon, "city": city, "country": country, "cc": cc}
    _save(config.SETTINGS_FILE, d)

def get_profile(chat_id):
    """Память пользователя (dict). Пусто -> {}."""
    return _load(config.PROFILE_KEY).get(str(chat_id), {})

def set_profile(chat_id, prof):
    d = _load(config.PROFILE_KEY)
    d[str(chat_id)] = prof
    _save(config.PROFILE_KEY, d)

def get_level(chat_id, language):
    return _load(config.LEVELS_FILE).get(str(chat_id), {}).get(language, "B1")

def set_level(chat_id, language, level):
    d = _load(config.LEVELS_FILE)
    d.setdefault(str(chat_id), {})[language] = level
    _save(config.LEVELS_FILE, d)

def load_wardrobe(cid=None):
    """Per-user wardrobe. Для CHAT_ID одноразово мигрирует глобальный wardrobe.json."""
    if cid is not None:
        key = f"wardrobe_user_{cid}"
        w = _load(key)
        if not w and config.CHAT_ID and str(cid) == str(config.CHAT_ID):
            # Одноразовая миграция: глобальный шкаф владельца → per-user ключ
            global_w = _load(config.WARDROBE_FILE)
            if isinstance(global_w, dict) and any(k != "_v" for k in global_w):
                _save(key, global_w)
                return global_w
        return w or {}
    return _load(config.WARDROBE_FILE) or {}

def save_wardrobe(w, cid=None):
    if cid is not None:
        _save(f"wardrobe_user_{cid}", w)
    else:
        _save(config.WARDROBE_FILE, w)

def merge_wardrobe(new_items: dict, cid=None):
    w = load_wardrobe(cid)
    added = 0
    for cat, items in new_items.items():
        cat = cat.lower().strip()
        w.setdefault(cat, [])
        for it in items:
            it = it.strip().lower()
            if it and it not in {x.lower() for x in w[cat]}:
                w[cat].append(it)
                added += 1
    save_wardrobe(w, cid)
    return added

def wardrobe_to_text(w):
    return "\n".join(f"{c.capitalize()}: {', '.join(i)}" for c, i in w.items()
                     if c != "_v" and isinstance(i, list))

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
    config.COUNTRIES_KEY, config.BOOKS_KEY, config.FAVORITES_KEY,
    config.FAVCOUNTRIES_KEY, config.MOVIE_BLACKLIST_KEY, config.BOOK_BLACKLIST_KEY,
    config.MUSIC_DISLIKE_KEY, config.TRAVEL_DISLIKE_KEY, config.WORRIES_KEY,
    config.NOTES_KEY, config.DICT_KEY, config.TOPICS_NL_KEY, config.TOPICS_EN_KEY,
    config.LAGOM_KEY, config.DIARY_KEY, config.CITY_FACTS_KEY, config.LIFEHACK_KEY,
    config.FRIDGE_KEY, config.MY_RECIPES_KEY, config.QUOTE_AUTHORS_KEY,
    config.MOTIV_LAGOM_SEEN_KEY, config.MICRO_TOPICS_KEY, config.MICRO_LESSONS_KEY,
    config.MICRO_PROGRESS_KEY,
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
grammar_state = {}
train_state = {}        # chat_id -> состояние тренажёра слов (формат/ответ/слово)
pending_input = {}
last_recos = {}
suggested_countries = {}
last_action = {}        # chat_id -> ("oneshot", key) | ("role", role, text) | None
last_answer = {}        # chat_id -> текст последнего ответа ассистента (для «Сохранить в заметки»)
last_recipe = {}        # chat_id -> dict рецепта (для «Полный рецепт»)
recent_looks = {}       # chat_id -> [последние луки] (не повторять 3 дня)
del_index = {}          # chat_id -> [(категория, вещь)] для удаления
last_word = {}          # chat_id -> последнее показанное слово/фраза (для «Добавить слово»)
game_recent = {}        # chat_id -> [последние загаданные персонажи]
list_sel = {}           # "chat_id:ctx" -> set(индексов) для чистки списков (словарь/темы)
last_source = {}        # chat_id -> откуда последний ответ (для категорий избранного)
last_surface = {}       # chat_id -> surface последнего ответа (для «Короче/Глубже»)
last_look = {}          # chat_id -> последний показанный образ (для фидбека гардероба)
micro_state = {}        # chat_id -> {"topic_id", "lang", "title", "pattern", "level", "code", "awaiting_sentence"}
dehet_state = {}        # chat_id -> {"words", "idx", "score", "results"}