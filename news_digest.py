"""Рассылка «Главные новости» в 19:00: NOS, NU.nl и NH Nieuws по RSS.

Отбор без LLM: сначала интересное — наука, техника, природа, космос (ленты науки
и техники за 3 дня), затем местные и главные новости за сутки; политика и криминал —
не больше одной новости, подкасты не попадают. Без дублей, 3–5 новостей. LLM только
переводит заголовки одним запросом; без него заголовки остаются на нидерландском.
"""
import difflib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import secure
import store
import util
from ui import news_digest as news_ui

_log = logging.getLogger(__name__)

KIND = "news_digest"
# (источник в карточке, адрес, наука/техника ли это)
FEEDS = (
    ("NU.nl", "https://www.nu.nl/rss/Wetenschap", True),
    ("NOS", "https://feeds.nos.nl/nosnieuwstech", True),
    ("NU.nl", "https://www.nu.nl/rss/Tech", True),
    ("NOS", "https://feeds.nos.nl/nosnieuwsalgemeen", False),
    ("NU.nl", "https://www.nu.nl/rss/Algemeen", False),
    ("NH Nieuws", "https://rss.nhnieuws.nl/rss", False),
)
_SOURCE_RANK = {"NOS": 0, "NU.nl": 1, "NH Nieuws": 2}
SCIENCE_MAX = 3
_SCIENCE_HOURS = 72   # научных новостей меньше — берём за 3 дня
_GENERAL_HOURS = 24
# Маркеры ищутся с начала слова: «ster» не должен находиться внутри «Amsterdam».
_INTEREST = re.compile(
    r"\b(?:wetenschap|onderzoek|studie|ontdek|ruimte|nasa\b|esa\b|planeet|planeten|sterren|ster\b|"
    r"maan\b|maanlanding|mars\b|astronaut|satelliet|fossiel|dino|archeolog|dier|natuur|oceaan|vulkaan|"
    r"klimaat|hersen|dna\b|vaccin|medisch|technolog|robot|ai\b|kunstmatige intelligentie|uitvinding|"
    r"museum|expositie|pingu)"
)
_POLITICS = re.compile(
    r"\b(?:kabinet|minister|tweede kamer|kamerleden|partij|verkiezing|coalitie|premier|politiek|pvv\b|"
    r"vvd\b|d66\b|cda\b|groenlinks|nsc\b|bbb\b|trump|poetin|oorlog|rechtbank|rechter|celstraf|"
    r"gevangenis|cel\b|verdachte|moord|doodgeschoten|steekpartij|politie|aangehouden|om eist|beroepsverbod)"
)
_SKIP = ("podcast",)
_SPORT = re.compile(
    r"\b(?:sport|voetbal|eredivisie|ajax\b|psv\b|feyenoord|az\b|wedstrijd|doelpunt|schaats|wielren|"
    r"tennis|formule 1|verstappen|olympi|kampioen|wk\b|ek\b|coach)"
)
_CULTURE = re.compile(
    r"\b(?:film|muziek|concert|festival|theater|tentoonstelling|kunst|schrijver|boek|zanger|band\b|"
    r"album|serie\b|televizier|musical)"
)
# Темы новостей в Настройках: (ключ, подпись). По умолчанию выключена только политика.
TOPICS = [("science", "Наука"), ("local", "Местное"), ("sport", "Спорт"),
          ("culture", "Культура"), ("politics", "Политика")]
DEFAULT_TOPICS_OFF = ("politics",)
SPORT_FEED = ("NOS", "https://feeds.nos.nl/nossportalgemeen", False)
_REGION = "noord-holland"
MAX_ITEMS = 5
_TIMEOUT_SECONDS = 10
_FEED_TTL = 15 * 60


def _fetch(source, url, science=False):
    cached = util.ttl_get("news_feed", url, _FEED_TTL)
    if cached is not None:
        return cached
    try:
        response = requests.get(url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "morning-bot/1.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as error:
        _log.warning("news feed unavailable source=%s: %r", source, error)
        return []
    items = []
    for node in root.iter("item"):
        title = " ".join(str(node.findtext("title") or "").split())
        link = str(node.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "")
        except (TypeError, ValueError):
            published = None
        if title and link and published:
            items.append({"source": source, "title": title, "link": link, "published": published,
                          "summary": " ".join(str(node.findtext("description") or "").split())[:400],
                          "science": science})
    util.ttl_set("news_feed", url, items)
    return items


def _local_score(item, city):
    text = f"{item['title']} {item['summary']}".casefold()
    score = 0
    if city and city.casefold() in text:
        score += 2
    if _REGION in text or item["source"] == "NH Nieuws":
        score += 1
    return score


def _text(item):
    return f" {item['title']} {item['summary']} ".casefold()


def _is_politics(item):
    return bool(_POLITICS.search(_text(item)))


def _interest_score(item):
    """Наука, техника, природа, космос — выше; политика и криминал — ниже."""
    text = _text(item)
    score = 3 if item.get("science") else 0
    score += 2 * min(2, len(set(_INTEREST.findall(text))))
    return score - (4 if _is_politics(item) else 0)


def topic(item, city="") -> str:
    """Одна тема новости по словам заголовка и описания; политика и криминал — первыми."""
    text = _text(item)
    if _is_politics(item):
        return "politics"
    if item.get("science") or _INTEREST.search(text):
        return "science"
    if _SPORT.search(text):
        return "sport"
    if _CULTURE.search(text):
        return "culture"
    if _local_score(item, city):
        return "local"
    return "other"


def select(items, city, now=None, *, topics_off=(), limit=MAX_ITEMS):
    """3–5 новостей без дублей: сначала интересное, затем местное, источник и свежесть.

    topics_off — выключенные в Настройках темы; такие новости не попадают в подборку.
    """
    now = now or datetime.now(config.TZ)
    fresh = [
        item for item in items
        if now - item["published"] <= timedelta(hours=_SCIENCE_HOURS if item.get("science") else _GENERAL_HOURS)
        and not any(marker in _text(item) for marker in _SKIP)
        and topic(item, city) not in topics_off
    ]
    fresh.sort(key=lambda item: (-_interest_score(item), -_local_score(item, city),
                                 _SOURCE_RANK.get(item["source"], 9), -item["published"].timestamp()))
    chosen = []
    for item in fresh:
        key = item["title"].casefold()
        if any(difflib.SequenceMatcher(None, key, other["title"].casefold()).ratio() > 0.75 for other in chosen):
            continue
        # Политики и криминала — не больше одной новости.
        if _is_politics(item) and any(_is_politics(other) for other in chosen):
            continue
        # Науки — до трёх: остаётся место для главного в городе и стране.
        if item.get("science") and sum(1 for other in chosen if other.get("science")) >= SCIENCE_MAX:
            continue
        # Не больше двух подряд местных мелочей: в итог попадают и главные новости страны.
        if _local_score(item, city) and sum(1 for other in chosen if _local_score(other, city)) >= 2:
            continue
        chosen.append(item)
        if len(chosen) == limit:
            break
    return chosen


def _translate(titles):
    """Заголовки по-русски одним запросом; без AI — оригиналы."""
    try:
        data = ai.llm_json(
            "Переведи на русский заголовки нидерландских новостей. Имена, названия мест и организаций "
            "не переводи. Сохрани порядок. "
            f"Заголовки: {secure.wrap_untrusted(str(titles), 'заголовки новостей')}. "
            'Верни JSON без markdown: {"titles": ["...", "..."]}.',
            600, tier="cheap", module="news",
            cache_context={"scenario": "news_digest_ru", "titles": titles, "schema_version": 1},
        )
    except Exception:
        _log.info("news digest translation unavailable; using original titles")
        return titles
    translated = data.get("titles") if isinstance(data, dict) else None
    if not isinstance(translated, list) or len(translated) != len(titles):
        return titles
    return [str(new or old).strip() or old for new, old in zip(translated, titles)]


async def send_digest(bot, cid):
    """Собирает и отправляет итог дня; без новостей — ничего не присылает."""
    import asyncio

    settings_data = store.get_settings(cid) or {}
    city = str(settings_data.get("city") or "")
    import settings as user_settings
    topics_off = user_settings.news_topics_off(cid)
    feeds_wanted = FEEDS if "sport" in topics_off else (*FEEDS, SPORT_FEED)
    feeds = await asyncio.gather(*(asyncio.to_thread(_fetch, source, url, science)
                                   for source, url, science in feeds_wanted))
    chosen = select([item for feed in feeds for item in feed], city,
                    topics_off=topics_off, limit=user_settings.news_count(cid))
    if not chosen:
        return
    titles = await asyncio.to_thread(_translate, [item["title"] for item in chosen])
    msg = news_ui.digest(city, [{**item, "title": title} for item, title in zip(chosen, titles)])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎚️ Настроить", callback_data="set_notif_new"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)
