import os
import json
import config

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
        except Exception:
            return None

def _load(key):
    conn = _db()
    if conn is None:
        return dict(_mem.get(key, {}))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM kv WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else {}
    except Exception:
        return dict(_mem.get(key, {}))

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
    except Exception:
        _mem[key] = data

# --- helpers ---
def get_settings(chat_id):
    return _load(config.SETTINGS_FILE).get(str(chat_id), config.DEFAULT_CITY)

def set_settings(chat_id, lat, lon, city, country=""):
    d = _load(config.SETTINGS_FILE)
    d[str(chat_id)] = {"lat": lat, "lon": lon, "city": city, "country": country}
    _save(config.SETTINGS_FILE, d)

def get_level(chat_id, language):
    return _load(config.LEVELS_FILE).get(str(chat_id), {}).get(language, "B1")

def set_level(chat_id, language, level):
    d = _load(config.LEVELS_FILE)
    d.setdefault(str(chat_id), {})[language] = level
    _save(config.LEVELS_FILE, d)

def load_wardrobe():
    w = _load(config.WARDROBE_FILE)
    if not w:
        try:
            if os.path.exists(config.WARDROBE_FILE):
                with open(config.WARDROBE_FILE) as f:
                    seed = json.load(f)
                if seed:
                    _save(config.WARDROBE_FILE, seed)
                    return seed
        except Exception:
            pass
    return w

def save_wardrobe(w):
    _save(config.WARDROBE_FILE, w)

def merge_wardrobe(new_items: dict):
    w = load_wardrobe()
    added = 0
    for cat, items in new_items.items():
        cat = cat.lower().strip()
        w.setdefault(cat, [])
        for it in items:
            it = it.strip().lower()
            if it and it not in [x.lower() for x in w[cat]]:
                w[cat].append(it)
                added += 1
    save_wardrobe(w)
    return added

def wardrobe_to_text(w):
    return "\n".join(f"{c.capitalize()}: {', '.join(i)}" for c, i in w.items())

def get_list(key, chat_id):
    return _load(key).get(str(chat_id), [])

def add_to_list(key, chat_id, item):
    d = _load(key)
    d.setdefault(str(chat_id), []).append(item)
    _save(key, d)

def set_list(key, chat_id, items):
    d = _load(key)
    d[str(chat_id)] = items
    _save(key, d)

# --- общее состояние в памяти (сбрасывается при рестарте) ---
challenge_state = {}
chat_history = {}
add_wardrobe_mode = {}
game_state = {}
game_config = {}
grammar_state = {}
pending_input = {}
last_recos = {}
suggested_countries = {}
last_action = {}        # chat_id -> ("oneshot", key) | ("role", role, text) | None
last_answer = {}        # chat_id -> текст последнего ответа ассистента (для «Сохранить в заметки»)
last_recipe = {}        # chat_id -> dict рецепта (для «Полный рецепт»)
recent_looks = {}       # chat_id -> [последние луки] (не повторять 3 дня)
del_index = {}          # chat_id -> [(категория, вещь)] для удаления