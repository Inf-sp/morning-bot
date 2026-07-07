import os
import json
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).parent

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
WEATHER_FREE_DAILY_LIMIT = 1000
WEATHER_HARD_DAILY_LIMIT = 1000
WEATHER_WARNING_LIMIT = 700
WEATHER_CRITICAL_LIMIT = 900
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ZEROENTROPY_API_KEY = os.environ.get("ZEROENTROPY_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

TZ = ZoneInfo("Europe/Amsterdam")

# --- Storage keys ---
SETTINGS_FILE = "settings.json"
LEVELS_FILE = "levels.json"
WARDROBE_FILE = "wardrobe.json"
WARDROBE_GAPS_KEY = "wardrobe_gaps.json"
DIARY_KEY = "diary.json"
ARTISTS_KEY = "artists.json"
WATCHLIST_KEY = "watchlist.json"
READLIST_KEY = "readlist.json"
FAVCOUNTRIES_KEY = "favcountries.json"
COUNTRIES_KEY = "mycountries.json"
BOOKS_KEY = "mybooks.json"
MOVIE_BLACKLIST_KEY = "movie_blacklist.json"
BOOK_BLACKLIST_KEY = "book_blacklist.json"
MUSIC_DISLIKE_KEY = "music_dislike.json"
TRAVEL_DISLIKE_KEY = "travel_dislike.json"
MOVIE_SEEN_KEY = "movie_seen.json"
MOVIE_SHOWN_KEY = "movie_shown.json"
BOOK_SEEN_KEY = "book_seen.json"
MUSIC_SEEN_KEY = "music_seen.json"
WORRIES_KEY = "worries.json"
NOTES_KEY = "notes.json"
DICT_KEY = "dict.json"
LAGOM_KEY = "lagom.json"
PROFILE_KEY = "profile.json"   # память пользователя: фокус, фидбек гардероба, наблюдения
CITY_FACTS_FILE = "city_facts.json"
CITY_FACT_IDX_KEY = "city_fact_idx.json"   # anti-repeat индексы кураторских фактов {cid: {city: [seen]}}
LIFEHACK_KEY = "lifehacks_seen.json"
FRIDGE_KEY = "fridge.json"
MY_RECIPES_KEY = "my_recipes.json"
LEFTOVER_RECIPES_SEEN_KEY = "leftover_recipes_seen.json"  # anti-repeat: {cid: [последние N названий]}
ACTIVE_MEAL_KEY = "active_meal.json"          # {cid: "breakfast"|"lunch"|"dinner"|"fridge"}
RECIPE_QUEUE_KEY = "recipe_queue.json"        # {cid: {"meal":..., "items":[...], "pos": int}}
RECIPE_HISTORY_KEY = "recipe_history.json"    # {cid: [последние 100 названий]} — общая anti-repeat история
CUISINE_WEIGHTS_KEY = "cuisine_weights.json"  # {cid: {"italian": 3, "japanese": -1, ...}} — обучение по действиям
DISH_PHOTO_CACHE_KEY = "dish_photo_cache.json"  # {normalized_query: {photo_url, source, score, ...}} — общий кэш, не per-user
QUOTE_AUTHORS_KEY = "quote_authors_seen.json"
MOTIV_LAGOM_SEEN_KEY = "motiv_lagom_seen.json"
CONCERTS_CACHE_KEY = "concerts_cache.json"  # {cid: {"ts": epoch, "cc": "NL", "events": [...]}}, обновляется по вс
SEEN_CONCERTS_KEY = "seen_concerts.json"  # {cid: [concert_id, ...]} — для уведомления о новых концертах любимых артистов
COST_LOG_KEY = "cost_log.json"     # лог LLM-вызовов для сводки расходов
ALLOWED_CIDS_KEY = "allowed_cids.json"    # список разрешённых chat_id (мульти-юзер)
PENDING_INVITES_KEY = "pending_invites.json"  # одноразовые инвайт-коды {code: ts}
ERROR_LOG_KEY = "error_log.json"   # rolling-лог ошибок для админ-экрана «Логи» {log: [{ts, source, kind, msg}]}
ACTIVITY_KEY = "activity.json"     # last_seen + счётчики активности: {cid: {last_ts, count, days:[YYYY-MM-DD]}}

DEFAULT_CITY = {"lat": 52.63, "lon": 4.74, "city": "Алкмар", "country": "Нидерланды", "cc": "NL"}


def _load_json(path, default):
    try:
        with open(_HERE / path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# --- Посещённые страны (сид-файл countries.json) ---
VISITED = ", ".join(_load_json("countries.json", []))

# --- Шаблоны промптов (prompts.json) ---
_PROMPTS = _load_json("prompts.json", {})
STYLE_PROFILE = _PROMPTS.get("style_profile", "")
CONTENT_TASTE = _PROMPTS.get("content_taste", "")


def _load_lagom_items():
    return _load_json("lagom.json", [])

_LAGOM_ITEMS = _load_lagom_items()

LAGOM = ("Лагом-установки пользователя (используй как ориентир тона и ценностей):\n"
         + "\n".join(f"• {it}" for it in _LAGOM_ITEMS)) if _LAGOM_ITEMS else ""


def place_hint(city="", country="", cc=""):
    """Подсказка о локации для генерации фактов - зависит от выбранной страны."""
    city = city or ""
    country = country or ""
    if (cc or "").upper() == "NL":
        return (f"{city} (Северная Голландия, Нидерланды) - история региона, "
                "местные законы и менталитет, архитектура, инфраструктура (NS, велоправила, налоги, ЖКХ)")
    if country and city:
        return f"{city} ({country})"
    return city or country or "выбранное место"

MYDAY_RULES = """ПРАВИЛА КАТЕГОРИЙ:

[Интересный факт]
• Только про {place_hint}
• РЕАЛЬНЫЙ и проверяемый факт — без домыслов, без выдумок
• Локальный: история, архитектура, инфраструктура, природа, менталитет, законы
• Максимум 2 коротких предложения, без выводов и оценок
• Не повторяй банальные туристические клише

[Цитата]
• От реального мыслителя, учёного или предпринимателя (Сенека, Марк Аврелий, Навал, Джобс, Мунгер и т.п.)
• Короткая, без воды, без банальностей
• Не выдумывай цитаты — только реально существующие

[Образ]
• Используй ТОЛЬКО вещи из гардероба пользователя (точные названия)
• 1 верх + 1 низ + обувь + опциональный аксессуар
• Сочетание по цвету и стилю (минимализм, натуральные ткани)
• Температурный ориентир: ≥24°C без дождя → шорты + футболка; +17..+23 → лёгкие брюки + футболка/рубашка; ≤16°C или дождь/ветер → слои, ветровка, закрытая обувь"""


def myday_rules(city="", country="", cc=""):
    return MYDAY_RULES.replace("{place_hint}", place_hint(city, country, cc))
