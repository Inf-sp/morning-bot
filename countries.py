"""Страны и регионы для кино, книг и музыки: «🇯🇵 Япония» и фильтр регионов кино.

Названия и части света — из открытого датасета mledoze/countries (кэш 90 дней), страна
артиста — MusicBrainz, страна автора — Wikidata (wikidata_books). Страна не угадывается:
нет данных — нет строки, а фильм с неизвестной страной фильтр регионов не отсеивает.
"""
import logging
import threading
import time

import requests

import config
import store
import util

_log = logging.getLogger(__name__)

_TABLE_TTL = 90 * 86400
_PERSON_TTL = 180 * 86400
_TIMEOUT = 12
_DATASET_URL = "https://raw.githubusercontent.com/mledoze/countries/master/countries.json"
_MUSICBRAINZ_GAP = 1.1  # правила MusicBrainz: не чаще запроса в секунду
_mb_lock = threading.Lock()
_mb_last = [0.0]

# Регионы предпочтений кино (ключ, подпись) — Россия отдельно от Европы, Океания всегда разрешена.
REGIONS = (("asia", "Азия"), ("americas", "Америка"), ("europe", "Европа"),
           ("russia", "Россия"), ("africa", "Африка"))
_REGION_GENITIVE = {"asia": "Азии", "americas": "Америки", "europe": "Европы",
                    "russia": "России", "africa": "Африки"}

# Язык оригинала → регион, когда страна производства неизвестна; неоднозначные (en, es, pt, ar) — нет.
_LANGUAGE_REGION = {
    **dict.fromkeys(("ja", "ko", "zh", "cn", "hi", "ta", "te", "ml", "bn", "th", "id", "vi", "tl",
                     "ms", "fa", "he", "tr", "ur", "kn", "mr", "pa"), "asia"),
    "ru": "russia",
    **dict.fromkeys(("fr", "de", "it", "sv", "da", "no", "nb", "fi", "pl", "nl", "cs", "hu", "ro",
                     "el", "is", "et", "lv", "lt", "sk", "sl", "hr", "sr", "bg", "uk", "ca", "eu", "ga"),
                    "europe"),
    **dict.fromkeys(("sw", "zu", "af", "am", "yo", "xh", "ha", "ig"), "africa"),
}


def _table() -> dict:
    """{cc: [название по-русски, регион]} из mledoze/countries; сбой — {}."""
    saved = store._load(config.COUNTRIES_TABLE_KEY) or {}
    if isinstance(saved, dict) and time.time() - float(saved.get("ts") or 0) < _TABLE_TTL:
        return saved.get("value") or {}
    try:
        rows = requests.get(_DATASET_URL, timeout=_TIMEOUT).json()
    except Exception:
        _log.warning("countries dataset unavailable", exc_info=True)
        return (saved or {}).get("value") or {}
    region_keys = {"Asia": "asia", "Americas": "americas", "Europe": "europe", "Africa": "africa",
                   "Oceania": "oceania"}
    value = {}
    for row in rows if isinstance(rows, list) else []:
        cc = str(row.get("cca2") or "").upper()
        name = (((row.get("translations") or {}).get("rus") or {}).get("common")
                or (row.get("name") or {}).get("common") or "")
        if cc:
            value[cc] = [name, "russia" if cc == "RU" else region_keys.get(row.get("region"), "")]
    if value:
        store._save(config.COUNTRIES_TABLE_KEY, {"ts": time.time(), "value": value})
    return value


# Привычные сокращения вместо двухбуквенного ISO-кода.
_SHORT_CODES = {"US": "USA", "GB": "UK", "AE": "UAE"}


def label(cc) -> str:
    """Страна в Досуге — флаг и сокращение: «🇷🇺 RU», «🇺🇸 USA», «🇬🇧 UK»; без кода — ""."""
    cc = str(cc or "").upper()
    flag = util.flag_from_cc(cc)
    return f"{flag} {_SHORT_CODES.get(cc, cc)}" if flag else ""


def region(cc) -> str:
    return (_table().get(str(cc or "").upper()) or ["", ""])[1]


def title_countries(tm) -> list:
    """Страны производства TMDb (detail) или страна происхождения сериала."""
    tm = tm or {}
    return [str(cc).upper() for cc in (tm.get("countries") or tm.get("origin_country") or []) if cc]


def title_regions(tm) -> set:
    regions = {region(cc) for cc in title_countries(tm)} - {""}
    if not regions:
        language = _LANGUAGE_REGION.get(str((tm or {}).get("original_language") or "").lower())
        regions = {language} if language else set()
    return regions


def allowed(tm, regions_off) -> bool:
    """Фильм или сериал проходит фильтр регионов: хоть одна его страна во включённом регионе."""
    off = set(regions_off or ())
    if not off:
        return True
    regions = title_regions(tm)
    return not regions or not regions <= off


def regions_off_text(regions_off) -> str:
    """«без Азии и Африки» для сводки предпочтений или ""."""
    names = [_REGION_GENITIVE[key] for key, _label in REGIONS if key in set(regions_off or ())]
    if not names:
        return ""
    return "без " + (", ".join(names[:-1]) + " и " + names[-1] if len(names) > 1 else names[0])


def title_label(tm) -> str:
    countries = title_countries(tm)
    return label(countries[0]) if countries else ""


# ---------- страна артиста: MusicBrainz ----------

def _person_cache(key, name, loader, fetch=True):
    """Значение из кэша; fetch=False — только кэш, без сети (экран Досуга не ждёт)."""
    norm = " ".join(str(name or "").split()).casefold()
    if not norm:
        return ""
    saved = store._load(key) or {}
    entry = saved.get(norm) if isinstance(saved, dict) else None
    fresh = isinstance(entry, dict) and time.time() - float(entry.get("ts") or 0) < _PERSON_TTL
    # «Не нашли» не держим полгода: прогрев и «Обновить карточки» (fetch=True) спрашивают снова.
    if fresh and (entry.get("value") or not fetch):
        return entry.get("value") or ""
    if not fetch:
        return ""
    try:
        value = loader(name)
    except Exception:
        _log.warning("country lookup failed for %r", name, exc_info=True)
        return ""  # сбой сети не запоминаем

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[norm] = {"ts": time.time(), "value": value or ""}
        return data, None

    store.mutate_kv(key, mutate)
    return value or ""


def _musicbrainz_country(name):
    with _mb_lock:
        wait = _MUSICBRAINZ_GAP - (time.monotonic() - _mb_last[0])
        if wait > 0:
            time.sleep(wait)
        _mb_last[0] = time.monotonic()
    data = requests.get(
        "https://musicbrainz.org/ws/2/artist",
        params={"query": f'artist:"{name}"', "fmt": "json", "limit": 3},
        headers={"User-Agent": "morning-bot/1.0 ( https://github.com/ )"}, timeout=_TIMEOUT,
    ).json()
    for artist in data.get("artists") or []:
        # Только точное совпадение имени с высокой оценкой — иначе страну не показываем.
        if (int(artist.get("score") or 0) >= 95
                and str(artist.get("name") or "").casefold() == str(name).casefold() and artist.get("country")):
            return str(artist["country"]).upper()
    return ""


def artist_label(name, fetch=True) -> str:
    """«🇬🇧 Великобритания» для артиста или ""."""
    return label(_person_cache(config.ARTIST_COUNTRY_KEY, name, _musicbrainz_country, fetch))


def author_label(name, fetch=True) -> str:
    """«🇬🇧 Великобритания» для автора книги (гражданство в Wikidata) или ""."""
    import wikidata_books
    first = str(name or "").split(",")[0].strip()  # «A, B» — соавторы через запятую: ищем первого
    return label(_person_cache(config.AUTHOR_COUNTRY_KEY, first, wikidata_books.author_country, fetch))


def title_info(kind, tmdb_id, fetch=True) -> dict:
    """{country_label, network} фильма или сериала из деталей TMDb (кэш 30 дней); fetch=False — только кэш."""
    if not tmdb_id:
        return {}
    key = f"{kind or 'movie'}:{tmdb_id}"
    saved = store._load(config.TMDB_TITLE_INFO_KEY) or {}
    entry = saved.get(key) if isinstance(saved, dict) else None
    fresh = isinstance(entry, dict) and time.time() - float(entry.get("ts") or 0) < 30 * 86400
    known = fresh and bool((entry.get("value") or {}).get("countries"))
    if fresh and (known or not fetch):
        value = entry.get("value") or {}
    elif not fetch:
        return {}
    else:
        import tmdb
        try:
            details = tmdb.detail(tmdb_id, kind or "movie") or {}
        except Exception:
            _log.warning("tmdb title info failed %s", key, exc_info=True)
            return {}
        if not details:  # TMDb не ответил — не запоминаем пустоту, следующий прогрев спросит снова
            _log.warning("tmdb title info: no details for %s", key)
            return {}
        value = {"countries": title_countries(details), "network": details.get("network") or ""}

        def mutate(data):
            data = data if isinstance(data, dict) else {}
            data[key] = {"ts": time.time(), "value": value}
            return data, None

        store.mutate_kv(config.TMDB_TITLE_INFO_KEY, mutate)
    countries = value.get("countries") or []
    return {"country_label": label(countries[0]) if countries else "", "network": value.get("network") or ""}
