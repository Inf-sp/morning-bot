"""Страны и регионы для кино и музыки: «🇯🇵 Япония» и фильтр регионов кино.

Названия и части света — из открытого датасета mledoze/countries (кэш 90 дней), страна
артиста — MusicBrainz, при сбое — Wikidata (wikidata_books). Страна не угадывается:
нет данных — нет строки, а фильм с неизвестной страной фильтр регионов не отсеивает.
"""
import logging
import re
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
    if util.on_event_loop():  # не качаем справочник в обработчике: фоном, пока — прошлая таблица
        util.build_off_loop("countries_table", _table)
        return (saved or {}).get("value") or {}
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
            value[cc] = [name, "russia" if cc == "RU" else region_keys.get(row.get("region"), ""),
                         (row.get("name") or {}).get("common") or ""]
    if value:
        store._save(config.COUNTRIES_TABLE_KEY, {"ts": time.time(), "value": value})
    return value


def country_by_name(text) -> tuple:
    """(cc, название по-русски), если текст — название страны («Испания», «Spain»); иначе ("", "")."""
    wanted = " ".join(str(text or "").split()).casefold().strip(" .,!")
    if not wanted:
        return "", ""
    for cc, row in _table().items():
        names = [str(value).casefold() for value in (row[0], row[2] if len(row) > 2 else "", _SHORT_RU.get(cc))
                 if value]
        if wanted in names:
            return cc, name_ru(cc)
    return "", ""


# Привычные сокращения вместо двухбуквенного ISO-кода.
_SHORT_CODES = {"US": "USA", "GB": "UK", "AE": "UAE"}


# Привычные короткие названия вместо длинных из датасета («Южно-Африканская Республика» → «ЮАР») — для заголовков.
_SHORT_RU = {"ZA": "ЮАР", "US": "США", "AE": "ОАЭ", "CF": "ЦАР", "CD": "ДР Конго", "DO": "Доминикана"}


def name_ru(cc) -> str:
    """Название страны по-русски по коду, привычное и короткое («ZA» → «ЮАР»); нет в таблице — ""."""
    code = str(cc or "").upper()
    return _SHORT_RU.get(code) or (_table().get(code) or [""])[0]


def short_label(cc) -> str:
    """Страна кратко, без флага: «USA», «UK», «NL»; без кода — ""."""
    cc = str(cc or "").upper()
    return _SHORT_CODES.get(cc, cc) if util.flag_from_cc(cc) else ""


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

def _person_cache(key, name, loader, fetch=True, keep_empty=False, ttl=_PERSON_TTL):
    """Значение из кэша; fetch=False — только кэш, без сети (экран Досуга не ждёт).

    keep_empty — пустой ответ тоже окончательный (у сольного артиста лет активности нет) и не перепроверяется."""
    norm = " ".join(str(name or "").split()).casefold()
    if not norm:
        return ""
    saved = store._load(key) or {}
    entry = saved.get(norm) if isinstance(saved, dict) else None
    fresh = isinstance(entry, dict) and time.time() - float(entry.get("ts") or 0) < ttl
    # «Не нашли» не держим полгода: прогрев и «Обновить карточки» (fetch=True) спрашивают снова.
    if fresh and (entry.get("value") or not fetch or keep_empty):
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


def _mb_get(path, params) -> dict:
    """GET к MusicBrainz с паузой между запросами (их правила — не чаще запроса в секунду)."""
    with _mb_lock:
        wait = _MUSICBRAINZ_GAP - (time.monotonic() - _mb_last[0])
        if wait > 0:
            time.sleep(wait)
        _mb_last[0] = time.monotonic()
    return requests.get(
        f"https://musicbrainz.org/ws/2/{path}", params={**params, "fmt": "json"},
        headers={"User-Agent": "morning-bot/1.0 ( https://github.com/ )"}, timeout=_TIMEOUT,
    ).json()


def _musicbrainz_artists(name) -> list:
    """Совпадения MusicBrainz по имени (без регистра, «the», знаков и диакритики, оценка от 90), лучшие первыми;
    иначе не угадываем. Страна и годы берут один запрос: ответ держится в памяти час, сбой — не держится."""
    wanted = _artist_key(name)
    cached = util.ttl_get("musicbrainz_artists", wanted, 3600)
    if cached is not None:
        return cached
    data = _mb_get("artist", {"query": f'artist:"{name}"', "limit": 3})
    found = []
    for artist in data.get("artists") or []:
        names = [artist.get("name"), artist.get("sort-name"), *[a.get("name") for a in artist.get("aliases") or []]]
        if int(artist.get("score") or 0) >= 90 and wanted in {_artist_key(value) for value in names if value}:
            found.append(artist)
    return util.ttl_set("musicbrainz_artists", wanted, found)


def _musicbrainz_country(name):
    for artist in _musicbrainz_artists(name):
        codes = (artist.get("area") or {}).get("iso-3166-1-codes") or []
        country = artist.get("country") or (codes[0] if len(codes) == 1 else "")
        if country:
            return str(country).upper()
    return ""


_GROUP_TYPES = {"Group", "Orchestra", "Choir"}


def _group_years(name) -> str:
    """Годы активности группы из MusicBrainz: «с 2011» или «2005–2019». У человека life-span — годы жизни, а не
    карьеры, поэтому сольному артисту лет нет; распалась без даты — тоже без лет."""
    matches = _musicbrainz_artists(_known_name(name))
    if not matches or matches[0].get("type") not in _GROUP_TYPES:
        return ""
    span = matches[0].get("life-span") or {}
    begin, end = str(span.get("begin") or "")[:4], str(span.get("end") or "")[:4]
    if not begin.isdigit():
        return ""
    if end.isdigit():
        return begin if end == begin else f"{begin}–{end}"
    return "" if span.get("ended") else f"с {begin}"


_FEATURING_RE = re.compile(r"\s+(?:&|and|x|feat\.?|ft\.?|featuring|with)\s+|,\s*", re.IGNORECASE)


def _artist_key(name) -> str:
    """«The Weeknd» ≈ «Weeknd», «Beyoncé» ≈ «Beyonce»: регистр, «the», знаки и диакритика не важны."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"^the\s+|,\s*the$", "", text.strip())
    return re.sub(r"[^a-z0-9а-яё]+", "", text)


def main_artist(name) -> str:
    """Первый артист совместного релиза: «Doja Cat & SZA» → «Doja Cat»."""
    return _FEATURING_RE.split(str(name or "").strip(), maxsplit=1)[0].strip()


def _full_name(name) -> str:
    return " ".join(str(name or "").split())


def _known_name(name) -> str:
    """Под каким именем искать артиста: полное, если MusicBrainz его знает («Chase & Status» — группа, а не
    американская «Chase» 1969 года), иначе первый артист совместного релиза («Doja Cat & SZA» → «Doja Cat»).
    Сбой MusicBrainz — исключение: вызывающий решает сам."""
    name = _full_name(name)
    main = main_artist(name)
    return name if main == name or _musicbrainz_artists(name) else main


def _artist_country(name):
    """MusicBrainz, при сбое или без ответа — Wikidata (без ключа) по полному имени."""
    import wikidata_books
    known = _full_name(name)
    try:
        known = _known_name(name)
        found = _musicbrainz_country(known)
    except Exception:
        _log.warning("musicbrainz unavailable for %r, trying wikidata", name, exc_info=True)
        found = ""
    return found or wikidata_books.artist_country(known)


def artist_label(name, fetch=True) -> str:
    """«🇬🇧 UK» для артиста (у совместного релиза, которого нет целиком, — первого) или ""."""
    return label(_person_cache(config.ARTIST_COUNTRY_KEY, _full_name(name), _artist_country, fetch))


_ALBUM_TTL = 30 * 86400  # новый альбом выходит нечасто, но и не раз в полгода


def _latest_album(name) -> dict:
    """Последний вышедший студийный альбом артиста из MusicBrainz: {"title", "date"}; без концертников,
    сборников и ремиксов (у них есть secondary-types). Нет совпадения или альбомов — {}."""
    matches = _musicbrainz_artists(_known_name(name))
    if not matches or not matches[0].get("id"):
        return {}
    today = time.strftime("%Y-%m-%d")
    groups = _mb_get("release-group", {"artist": matches[0]["id"], "type": "album", "limit": 100})
    albums = [group for group in groups.get("release-groups") or []
              if group.get("primary-type") == "Album" and not group.get("secondary-types")
              and str(group.get("first-release-date") or "")[:4].isdigit()
              and str(group.get("first-release-date")) <= today]
    if not albums:
        return {}
    latest = max(albums, key=lambda group: str(group["first-release-date"]))
    return {"title": str(latest.get("title") or "").strip(), "date": str(latest["first-release-date"])}


def artist_latest_album(name, fetch=True) -> dict:
    """Последний альбом артиста ({"title", "date"} или {}); fetch=False — только кэш, без сети."""
    album = _person_cache(config.ARTIST_ALBUM_KEY, _full_name(name), _latest_album, fetch,
                          keep_empty=True, ttl=_ALBUM_TTL)
    return album if isinstance(album, dict) else {}


_FANS_TTL = 30 * 86400


def _deezer_fans(name) -> dict:
    """Поклонники артиста на Deezer (без ключа): {"fans": N}; сначала полное имя («Chase & Status»), без
    точного совпадения — первый артист совместного релиза; нет на Deezer — 0. Сбой — исключение (не кэшируется)."""
    name = _full_name(name)
    for query in dict.fromkeys((name, main_artist(name))):
        response = requests.get("https://api.deezer.com/search/artist", params={"q": query, "limit": 5},
                                timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if "error" in data:  # Deezer отвечает 200 с {"error": …} при лимите — это не «артиста нет»
            raise RuntimeError(f"deezer error: {data['error']}")
        wanted = _artist_key(query)
        fans = [int(row.get("nb_fan") or 0) for row in data.get("data") or [] if _artist_key(row.get("name")) == wanted]
        if fans:
            return {"fans": max(fans)}
    return {"fans": 0}


def artist_fans(name, fetch=True) -> int | None:
    """Число поклонников артиста на Deezer — мера известности; None — неизвестно (сбой, нет кэша при fetch=False)."""
    found = _person_cache(config.ARTIST_FANS_KEY, _full_name(name), _deezer_fans, fetch, keep_empty=True,
                          ttl=_FANS_TTL)
    return int(found["fans"]) if isinstance(found, dict) else None


def artist_years(name, fetch=True) -> str:
    """Годы активности группы по MusicBrainz («с 2011», «2005–2019»); у сольного артиста и без данных — ""."""
    return _person_cache(config.ARTIST_YEARS_KEY, _full_name(name), _group_years, fetch, keep_empty=True)


_SUMMARY_TTL = 30 * 86400


def _wikidata_id(name) -> str:
    """Q-id артиста из связей MusicBrainz (url-rels → wikidata); нет совпадения или связи — ""."""
    matches = _musicbrainz_artists(_known_name(name))
    if not matches or not matches[0].get("id"):
        return ""
    data = _mb_get(f"artist/{matches[0]['id']}", {"inc": "url-rels"})
    for relation in data.get("relations") or []:
        if relation.get("type") == "wikidata":
            return str((relation.get("url") or {}).get("resource") or "").rstrip("/").rsplit("/", 1)[-1]
    return ""


def _translate_intro(text) -> str:
    import ai
    prompt = ("Translate into Russian for a music recommendation card. Keep names of people, bands, albums, songs "
              f"and labels in the original. Return only the translation.\n\n{text}")
    return " ".join(ai.llm(prompt, 400, 0.2, tier="cheap", module="countries",
                           fallback_allowed=True, privacy_level="public").split())


def _artist_summary(name) -> str:
    """Русская Википедия (2 предложения) → краткое описание Wikidata → английская статья с переводом AI.
    Сбой сети или AI — исключение: _person_cache его не кэширует."""
    import wikidata_books
    qid = _wikidata_id(name)
    if not qid:
        return ""
    titles = wikidata_books.article_titles(qid)
    if titles.get("ruwiki") and (intro := wikidata_books.article_intro("ru", titles["ruwiki"])):
        return intro
    if titles.get("ru_desc"):
        return titles["ru_desc"][:1].upper() + titles["ru_desc"][1:] + "."
    if titles.get("enwiki") and (intro := wikidata_books.article_intro("en", titles["enwiki"])):
        return _translate_intro(intro)
    return ""


def artist_summary(name, fetch=True) -> str:
    """Описание артиста для карточки без AI (резерв «Музыки»): кэш на месяц, сбой не кэшируется; нет данных — ""."""
    return _person_cache(config.ARTIST_SUMMARY_KEY, _full_name(name), _artist_summary, fetch, ttl=_SUMMARY_TTL)


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
