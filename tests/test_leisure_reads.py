"""«Прочитать» в хабе Досуга: одна статья, темы по очереди, время чтения, «Другая статья»."""
import os
from datetime import date, datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import leisure_reads
from ui import leisure as leisure_ui


def _profile(monkeypatch):
    profile = {}

    def mutate(_cid, change):
        updated = change(dict(profile))[0]
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(leisure_reads.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_reads.store, "mutate_profile", mutate)
    return profile


def test_day_set_puts_todays_theme_first_by_interests_and_fresh_only(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 3, tzinfo=config.TZ)
    monkeypatch.setattr(leisure_reads, "themes", lambda _cid: ("science", "design"))  # выбор в «Статьях»
    theme = leisure_reads.theme_of(now.date(), ("science", "design"))
    feeds = {
        "Quanta": [{"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "summary": "space",
                    "date": "Fri, 16 Oct 2026 10:00:00 +0000", "source": "Quanta"},
                   {"title": "Old news", "link": "https://q.example/old", "summary": "",
                    "date": "Fri, 02 Oct 2026 10:00:00 +0000", "source": "Quanta"}],
        "Science News": [{"title": "Soil microbes", "link": "https://s.example/soil", "summary": "",
                          "date": "2026-10-17T09:00:00Z", "source": "Science News"}],
        "Colossal": [{"title": "Paper sculptures", "link": "https://c.example/paper", "summary": "sculpture",
                      "date": "2026-10-17T09:00:00Z", "source": "Colossal"}],
    }
    monkeypatch.setattr(leisure_reads, "_feed", lambda source, _url: feeds.get(source, []))
    monkeypatch.setattr(leisure_reads, "_minutes", lambda link: 6 if "bh" in link else None)
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: {"items": [
        {"id": 0, "about": "Как сверхмассивные чёрные дыры набирают массу так быстро"}]})

    entry = leisure_reads.build("42", now)

    links = [item["link"] for item in entry["items"]]
    assert "https://q.example/old" not in links  # старше недели — нет
    first_theme = "science" if links[0].startswith(("https://q.", "https://s.")) else "design"
    assert first_theme == theme  # тема дня — первой
    if theme == "science":
        assert links[0] == "https://q.example/bh"  # по интересам (космос) — выше
    current = leisure_reads.current("42", now.date())
    assert current == entry["items"][0] and profile["hub_reads"]["seen"]
    assert leisure_reads.advance("42", now.date()) == entry["items"][1 % len(entry["items"])]
    assert leisure_reads.current("42", date(2026, 10, 19)) is None  # набор — только на свой день


def test_reading_time_counts_paragraph_words():
    page = "<html><nav>menu</nav>" + "<p>" + " ".join(["word"] * 1380) + "</p></html>"
    assert leisure_reads.minutes_of(page) == 6
    assert leisure_reads.minutes_of("<p>short</p>") is None


def test_hub_has_no_article_block_and_reads_are_a_button():
    msg = leisure_ui.leisure_today_screen([], library={"movies": 2, "artists": 1})
    assert "Прочитать" not in msg.text  # статьи — экраном «Статьи»
    rows = [[b.callback_data for b in row] for row in leisure_ui.leisure_hub_kb().inline_keyboard]
    assert rows[0] == ["lz_ev"] and rows[2] == ["music_reco", "lz_reads"]  # «События» сверху


def test_reads_are_a_circular_feed_most_interesting_first(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 9, tzinfo=config.TZ)
    items = [{"title": f"Plain story {n}", "link": f"https://x.example/{n}", "source": "Colossal", "lang": "EN",
              "summary": "", "about": "Обычная история"} for n in range(3)]
    items.insert(2, {"title": "A black hole near a quantum galaxy", "link": "https://q.example/bh", "source": "Quanta",
                     "lang": "EN", "summary": "", "minutes": 6, "about": "Чёрная дыра растёт быстрее моделей"})
    profile["hub_reads"] = {"v": leisure_reads._VERSION, "date": "2026-10-18", "index": 0, "items": items}
    top = leisure_reads.top("42", now.date())
    assert [item["link"] for item in top] == ["https://q.example/bh", "https://x.example/0", "https://x.example/1"]
    feed = leisure_reads.ranked("42", now.date())
    assert len(feed) == 4 and feed[0]["link"] == "https://q.example/bh"  # лента — весь набор, интересные первыми
    msg = leisure_ui.reads_card(feed, 0)
    assert msg.text == ("📰 A black hole near a quantum galaxy\n\nQuanta · EN · 6 мин\n\n"
                        "Чёрная дыра растёт быстрее моделей.")
    assert [e.url for e in msg.entities if e.type == "text_link"][0] == "https://q.example/bh"
    rows = msg.reply_markup.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["lz_reads_3", "noop", "lz_reads_1"]  # по кругу
    assert rows[0][1].text == "1/4" and [b.callback_data for b in rows[-1]] == ["m_leisure", "m_menu"]
    assert leisure_reads.top("42", date(2026, 10, 19)) == []


def test_old_day_set_is_rebuilt_with_a_teaser_and_the_same_articles(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 9, tzinfo=config.TZ)
    profile["hub_reads"] = {"date": "2026-10-18", "index": 0, "seen": {"https://q.example/bh": "2026-10-18"},
                            "items": [{"title": "How Black Holes Grow Up", "link": "https://q.example/bh",
                                       "about": "Старое одно предложение"}]}  # версия 1
    item = {"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "summary": "", "source": "Quanta",
            "date": "Sat, 17 Oct 2026 10:00:00 +0000"}
    monkeypatch.setattr(leisure_reads, "_feed", lambda source, _url: [item] if source == "Quanta" else [])
    prompts = []
    teaser = "Чёрные дыры росли быстрее, чем допускают модели. Новые наблюдения подсказывают почему."
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda prompt, *_a, **_k: prompts.append(prompt) or {
        "items": [{"id": 0, "about": teaser}]})

    assert leisure_reads.current("42", now.date()) is None  # старый вид не показывается — хаб пересоберёт фоном
    entry = leisure_reads.build("42", now)

    assert "краткое содержание" in prompts[0] and "два-три коротких предложения" in prompts[0]
    assert entry["v"] == leisure_reads._VERSION and entry["items"][0]["link"] == "https://q.example/bh"  # та же статья, не «показанная»
    assert entry["items"][0]["about"] == teaser
    assert leisure_reads.build("42", now) is not None and len(prompts) == 1  # новый вид — без повторной сборки


def test_long_or_list_shaped_teaser_is_kept_not_dropped(monkeypatch):
    long_teaser = ("Платформа Ondesign показывает, как команды On — от архитекторов до стилистов — придумывают вещи "
                   "для спорта и города. Внутри — эскизы, споры и прототипы, которые обычно остаются за кадром. "
                   "Третье предложение уже лишнее и только растягивает анонс до неприличной длины, его не будет, "
                   "потому что два предложения — это всё, что помещается в блок «Прочитать». Четвёртое "
                   "предложение тоже лишнее, оно добавлено только для того, чтобы текст был заметно длиннее.")
    items = [{"title": "On’s new editorial platform", "link": "https://n.example/on", "summary": "Ondesign goes…"}]
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: [{"id": "0", "about": long_teaser}])

    about = leisure_reads._about_ru(items)["https://n.example/on"]

    assert len(long_teaser) > leisure_reads._ABOUT_MAX and len(about) <= leisure_reads._ABOUT_MAX
    assert about.endswith("«Прочитать».") and "Четвёртое" not in about  # до трёх предложений, не обрыв


def test_missing_teaser_is_filled_later_with_one_request(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 12, tzinfo=config.TZ)
    profile["hub_reads"] = {"v": leisure_reads._VERSION, "date": "2026-10-18", "index": 0, "seen": {}, "items": [
        {"title": "On’s new editorial platform", "link": "https://n.example/on", "summary": "Ondesign goes…",
         "about": ""},
        {"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "about": "Уже есть анонс."}]}
    asked = []
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda prompt, *_a, **_k: asked.append(prompt) or {
        "items": [{"id": 0, "about": "Как команды On придумывают вещи для спорта и города."}]})

    entry = leisure_reads.build("42", now)

    assert [item["about"] for item in entry["items"]] == ["Как команды On придумывают вещи для спорта и города.",
                                                          "Уже есть анонс."]
    assert "On’s new editorial platform" in asked[0] and "Black Holes" not in asked[0]  # только статья без анонса
    leisure_reads.build("42", now)
    assert len(asked) == 1  # анонсы на месте — без повторного запроса


def test_article_preview_image_comes_from_og_image_or_feed_picture():
    page = '<html><head><meta property="og:image" content="https://img.example/a.jpg"></head></html>'
    assert leisure_reads.image_of(page) == "https://img.example/a.jpg"
    assert leisure_reads.image_of("", '<p><img src="https://img.example/b.png"></p>') == "https://img.example/b.png"
    assert leisure_reads.image_of('<meta property="og:image" content="http://insecure/a.jpg">') == ""


def test_reads_card_is_sent_as_photo_and_flips_in_place(monkeypatch):
    import asyncio
    import leisure_hub
    items = [{"title": f"Story {n}", "link": f"https://x.example/{n}", "source": "Colossal", "lang": "EN",
              "about": "История", "image": f"https://img.example/{n}.jpg"} for n in range(3)]
    monkeypatch.setattr(leisure_reads, "ranked", lambda _cid: items)
    shown = []

    async def show_card(_bot, _cid, msg, image, q=None):
        shown.append((msg.text, image))

    monkeypatch.setattr(leisure_hub, "_show_card", show_card)
    asyncio.run(leisure_hub.send_reads(None, "42", "4"))
    assert shown == [("📰 Story 1\n\nColossal · EN\n\nИстория.", "https://img.example/1.jpg")]  # 4 % 3


def test_reads_card_shows_publication_date_and_summary_from_article_text(monkeypatch):
    item = {"title": "The Incredible, Elegant DNA Repair System", "link": "https://q.example/dna", "source": "Quanta",
            "lang": "EN", "minutes": 8, "published": "2026-11-21",
            "about": "Клетки чинят разрывы ДНК сами. На этом и держится редактирование генов"}
    monkeypatch.setattr(leisure_ui, "local_today", lambda: date(2026, 11, 23))
    text = leisure_ui.reads_card([item]).text
    assert "Quanta · 21 ноября · EN · 8 мин" in text and text.endswith("редактирование генов.")
    assert "клетки" in leisure_reads.excerpt_of("<p>Cells repair broken DNA.</p><p>клетки</p>")
    prompts = []
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda prompt, *_a, **_k: prompts.append(prompt) or {})
    leisure_reads._about_ru([{"title": "T", "link": "https://x", "summary": "", "text": "Cells repair broken DNA."}])
    assert "Cells repair broken DNA." in prompts[0]


def test_titles_are_russian_with_source_language_and_themes_are_chosen(monkeypatch):
    profile = _profile(monkeypatch)
    now = datetime(2026, 10, 18, 9, tzinfo=config.TZ)
    chosen = {"themes": ("lgbt",)}
    monkeypatch.setattr(leisure_reads, "themes", lambda _cid: chosen["themes"])
    item = {"title": "Queer history of Amsterdam", "link": "https://e.example/q", "summary": "", "source": "Expreszo",
            "date": "Sat, 17 Oct 2026 10:00:00 +0000"}
    monkeypatch.setattr(leisure_reads, "_feed", lambda source, _url: [item] if source == "Expreszo" else [])
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: {"items": [
        {"id": 0, "title": "Квир-история Амстердама", "about": "Как город стал столицей прайдов."}]})
    entry = leisure_reads.build("42", now)
    article = entry["items"][0]
    assert (article["title"], article["original_title"], article["lang"]) == (
        "Квир-история Амстердама", "Queer history of Amsterdam", "NL")
    assert leisure_reads.lang_label("It's Nice That") == "ENG"
    chosen["themes"] = ("science",)  # сменил темы — набор дня собирается заново
    assert leisure_reads.top("42", now.date()) == []
    card = leisure_ui.reads_card([{**article, "published": "2026-10-17"}]).text
    assert card.startswith("📰 Квир-история Амстердама\n\nExpreszo · 17 октября · NL")
