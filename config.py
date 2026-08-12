import os
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).parent

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    value = str(os.environ.get(name, "" if not default else "1")).strip().casefold()
    return value in {"1", "true", "yes", "on"}


WEATHER_FREE_DAILY_LIMIT = _env_int("WEATHER_FREE_DAILY_LIMIT", 1000)
WEATHER_HARD_DAILY_LIMIT = _env_int("WEATHER_HARD_DAILY_LIMIT", WEATHER_FREE_DAILY_LIMIT)
WEATHER_WARNING_LIMIT = _env_int("WEATHER_WARNING_LIMIT", int(WEATHER_HARD_DAILY_LIMIT * 0.7))
WEATHER_CRITICAL_LIMIT = _env_int("WEATHER_CRITICAL_LIMIT", int(WEATHER_HARD_DAILY_LIMIT * 0.9))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
GEMINI_DAILY_LIMIT = _env_int("GEMINI_DAILY_LIMIT", 0)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL_DAILY_LIMIT = _env_int("GROQ_MODEL_DAILY_LIMIT", 1000)
GROQ_SIMPLE_MODEL = os.environ.get("GROQ_SIMPLE_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
GROQ_STANDARD_MODEL = os.environ.get("GROQ_STANDARD_MODEL", "qwen/qwen3.6-27b").strip() or "qwen/qwen3.6-27b"
GROQ_COMPLEX_MODEL = os.environ.get("GROQ_COMPLEX_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
LANGUAGETOOL_API_URL = os.environ.get(
    "LANGUAGETOOL_API_URL", "https://api.languagetool.org/v2",
).strip().rstrip("/")
SPOONACULAR_API_KEY = os.environ.get("SPOONACULAR_API_KEY", "").strip()
THEMEALDB_API_KEY = os.environ.get("THEMEALDB_API_KEY", "1").strip() or "1"
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "").strip()
AZURE_SPEECH_VOICE = os.environ.get("AZURE_SPEECH_VOICE", "nl-NL-MaartenNeural").strip() or "nl-NL-MaartenNeural"
AZURE_SPEECH_RATE = os.environ.get("AZURE_SPEECH_RATE", "-10%").strip() or "-10%"
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/openai/gpt-oss-20b").strip() or "@cf/openai/gpt-oss-20b"
CHAT_ID = os.environ.get("CHAT_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ZEROENTROPY_API_KEY = os.environ.get("ZEROENTROPY_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_DAILY_LIMIT = _env_int("OPENROUTER_DAILY_LIMIT", 50)
CF_NEURON_DAILY_LIMIT = _env_int("CF_NEURON_DAILY_LIMIT", 10000)
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID") or CHAT_ID
# Classic Telegram messages keep the same readable text size on every client.
# Rich Messages remain an opt-in compatibility experiment.
TELEGRAM_RICH_MESSAGES = _env_bool("TELEGRAM_RICH_MESSAGES", False)


def _read_text_file(path, default=""):
    try:
        return (_HERE / path).read_text(encoding="utf-8").strip()
    except Exception:
        return default


APP_VERSION = os.environ.get("APP_VERSION", "").strip() or _read_text_file("VERSION")
RAILWAY_DEPLOYMENT_ID = os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip()
RAILWAY_ENVIRONMENT = os.environ.get("RAILWAY_ENVIRONMENT", "").strip()
RAILWAY_SERVICE_NAME = os.environ.get("RAILWAY_SERVICE_NAME", "").strip()
RAILWAY_REPLICA_ID = os.environ.get("RAILWAY_REPLICA_ID", "").strip()

API_USAGE_KEY = "api_usage.json"
SERVICE_MONITOR_KEY = "service_monitor.json"
API_QUOTAS = {
    "openweather": [
        {"mode": "fixed", "unit": "requests", "period": "day", "limit": WEATHER_FREE_DAILY_LIMIT},
    ],
    "gemini": [
        {"mode": "local", "unit": "requests", "period": "day"},
        {"mode": "local", "unit": "tokens", "period": "day"},
    ],
    "tavily": [
        {"mode": "local", "unit": "credits", "period": "month"},
    ],
    "telegram": [
        {"mode": "local", "unit": "messages", "period": "day"},
    ],
    "cloudflare": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "groq": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "languagetool": [
        {"mode": "local", "unit": "requests", "period": "day"},
        {"mode": "local", "unit": "characters", "period": "day"},
    ],
    "themealdb": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "spoonacular": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "azure_speech": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "tmdb": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "ticketmaster": [
        {"mode": "headers", "unit": "requests", "period": "day", "enabled": False},
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "zeroentropy": [
        {"mode": "local", "unit": "tokens", "period": "day"},
    ],
    "pexels": [
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
    "unsplash": [
        {"mode": "headers", "unit": "requests", "period": "hour"},
        {"mode": "local", "unit": "requests", "period": "day"},
    ],
}

TZ = ZoneInfo("Europe/Amsterdam")

# --- Storage keys ---
TRANSIENT_MESSAGES_KEY = "transient_messages.json"
SETTINGS_FILE = "settings.json"
LEVELS_FILE = "levels.json"
WARDROBE_FILE = "wardrobe.json"
WARDROBE_GAPS_KEY = "wardrobe_gaps.json"
DIARY_KEY = "diary.json"
# Канонические имена пользовательских коллекций.
FAVORITE_ARTISTS_KEY = "favorite_artists.json"
FAVORITE_MOVIES_KEY = "favorite_movies.json"
SAVED_COUNTRIES_KEY = "saved_countries.json"
LEGACY_COUNTRIES_KEY = "mycountries.json"
FAVORITE_BOOKS_KEY = "favorite_books.json"
BOOK_RECO_CACHE_KEY = "book_reco_cache.json"  # {cid: {date, item}} — текущая карточка книги на день
MOVIE_RECO_CACHE_KEY = "movie_reco_cache.json"  # {cid: {date, signature, item, tm}} — персональная карточка кино на день
BOOK_WEEKLY_CACHE_KEY = "book_weekly_cache.json"  # {week, date, items} — заметные новинки книг недели
BOOK_DAILY_CACHE_KEY = "book_daily_cache.json"  # {YYYY-MM-DD: {birthday}} — один проверенный литературный именинник на день
LOCAL_CINEMA_CACHE_KEY = "local_cinema_cache.json"  # {cid: {city, ts, movies}} — подтверждённая городская афиша
MOVIE_NOW_PLAYING_CACHE_KEY = "movie_now_playing_cache.json"  # {cid: {city, week, items}} — стабильный каталог проката на неделю
CINEMA_DAILY_CACHE_KEY = "cinema_daily_cache.json"  # {YYYY-MM-DD: {birthday}} — один проверенный именинник дня для всех пользователей
MUSIC_RECO_CACHE_KEY = "music_reco_cache.json"  # {cid: {date, item}} — персональный артист на день
MUSIC_DAILY_CACHE_KEY = "music_daily_cache.json"  # {YYYY-MM-DD: {legend}} — один проверенный музыкальный именинник дня для всех пользователей
YOUTUBE_TRACK_CACHE_KEY = "youtube_track_cache.json"  # {artist + track: {ts, url}} — подтверждённые ссылки на треки
MOVIE_BLACKLIST_KEY = "movie_blacklist.json"
BOOK_BLACKLIST_KEY = "book_blacklist.json"
MUSIC_DISLIKE_KEY = "music_dislike.json"
TRAVEL_DISLIKE_KEY = "travel_dislike.json"
MOVIE_SEEN_KEY = "movie_seen.json"
MOVIE_SHOWN_KEY = "movie_shown.json"
BOOK_SEEN_KEY = "book_seen.json"
MUSIC_SEEN_KEY = "music_seen.json"
RECOMMENDATION_STOPLIST_KEY = "recommendation_stoplist.json"
THOUGHTS_KEY = "thoughts.json"
THOUGHT_REVIEWS_KEY = "thought_reviews.json"
DICT_KEY = "dict.json"
TTS_CACHE_KEY = "tts_cache.json"
PROFILE_KEY = "profile.json"   # память пользователя: фокус, фидбек гардероба, наблюдения
LIFEHACK_KEY = "lifehacks_seen.json"       # anti-repeat для fallback lifehacks.json
LIFEHACK_POOL_KEY = "myday_lifehack_pool.json"  # недельный AI-пул базы знаний {cid: {...}}
FRIDGE_KEY = "fridge.json"
LEFTOVER_RECIPES_SEEN_KEY = "leftover_recipes_seen.json"  # anti-repeat: {cid: [последние N названий]}
ACTIVE_MEAL_KEY = "active_meal.json"          # {cid: "breakfast"|"lunch"|"dinner"|"fridge"}
RECIPE_QUEUE_KEY = "recipe_queue.json"        # {cid: {"meal":..., "items":[...], "pos": int}}
RECIPE_HISTORY_KEY = "recipe_history.json"    # {cid: [последние 100 названий]} — общая anti-repeat история
CUISINE_WEIGHTS_KEY = "cuisine_weights.json"  # {cid: {"italian": 3, "japanese": -1, ...}} — обучение по действиям
QUOTE_AUTHORS_KEY = "quote_authors_seen.json"
CONCERTS_CACHE_KEY = "concerts_cache.json"  # {cid: {"ts": epoch, "cc": "NL", "events": [...]}}, прогревается перед пятничной афишей
CONCERT_ARTIST_CHECKS_KEY = "concert_artist_checks.json"  # {cid: {country: {artist: last check + future events}}}
POPULAR_MUSIC_EVENTS_CACHE_KEY = "popular_music_events_cache.json"  # {country + week: подтверждённые крупные события}
SEEN_CONCERTS_KEY = "seen_concerts.json"  # {cid: [concert_id, ...]} — для уведомления о новых концертах любимых артистов
ARTIST_EXTERNAL_EVENTS_KEY = "artist_external_events.json"  # глобальный кэш внешнего поиска концертов (Tavily+Firecrawl) по нормализованному имени артиста, TTL 7 дней: {artist_key: {"ts": epoch, "events": [...]}}
COST_LOG_KEY = "cost_log.json"     # лог LLM-вызовов для сводки расходов
AI_TRAFFIC_LOG_KEY = "ai_traffic_log.json"  # попытки AI без текста запросов и ответов
AI_RESPONSE_CACHE_KEY = "ai_response_cache.json"  # кэш дорогих AI-ответов по хэшу промпта
WEATHER_CACHE_KEY = "weather_cache.json"  # устойчивый кэш OpenWeather: {cache_key: {"ts": epoch, "data": {...}}}
TRAVEL_COUNTRY_CARDS_KEY = "travel_country_cards.json"  # глобальные карточки по ISO-коду
TRAVEL_IDEA_KEY = "travel_idea.json"  # последняя идея маршрута пользователя
MEDICINE_LABEL_CACHE_KEY = "medicine_label_cache.json"  # DailyMed: names + SPL sections by SET ID
MEDICINE_AUDIT_LOG_KEY = "medicine_audit_log.json"  # technical source/provider metadata, no question text
ALLOWED_CIDS_KEY = "allowed_cids.json"    # список разрешённых chat_id (мульти-юзер)
PENDING_INVITES_KEY = "pending_invites.json"  # одноразовые инвайт-коды {code: ts}
ERROR_LOG_KEY = "error_log.json"   # rolling-лог ошибок для админ-экрана «Ошибки» {log: [{ts, source, kind, msg}]}
ACTION_LATENCY_KEY = "action_latency.json"  # задержка действий без текста запросов и ответов
ACTIVITY_KEY = "activity.json"     # last_seen + счётчики и состояние напоминания после неактивности
ADMIN_STATE_KEY = "admin_state.json"  # per-admin cursors and compact dashboard state
DEPLOY_REPORT_KEY = "deploy_report.json"  # служебное состояние деплой-уведомлений

LEGACY_STORAGE_KEYS = {
    FAVORITE_ARTISTS_KEY: ("artists.json",),
    FAVORITE_MOVIES_KEY: ("watchlist.json",),
    SAVED_COUNTRIES_KEY: ("favcountries.json", "mycountries.json"),
    FAVORITE_BOOKS_KEY: ("mybooks.json",),
    THOUGHTS_KEY: ("worries.json",),
}

DEFAULT_CITY = {"lat": 52.63, "lon": 4.74, "city": "Алкмар", "country": "Нидерланды", "cc": "NL"}
