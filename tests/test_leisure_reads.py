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
    theme = leisure_reads.theme_of(now.date())
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


def test_hub_shows_one_read_with_source_language_minutes_and_a_change_button():
    read = {"title": "How Black Holes Grow Up", "link": "https://q.example/bh", "source": "Quanta", "lang": "EN",
            "minutes": 6, "about": "Как сверхмассивные чёрные дыры набирают массу так быстро"}
    msg = leisure_ui.leisure_today_screen([], read=read, library={"movies": 2, "artists": 1})
    assert ("Прочитать:\nHow Black Holes Grow Up · Quanta · EN · 6 мин\n"
            "Как сверхмассивные чёрные дыры набирают массу так быстро.\n\nТвоя библиотека") in msg.text
    links = [entity.url for entity in msg.entities if entity.type == "text_link"]
    assert links == ["https://q.example/bh"]
    callbacks = [b.callback_data for row in leisure_ui.leisure_hub_kb(read=True).inline_keyboard for b in row]
    assert callbacks[5] == "lz_read_next" and "lz_read_next" not in [
        b.callback_data for row in leisure_ui.leisure_hub_kb().inline_keyboard for b in row]


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

    assert "одно-два коротких предложения" in prompts[0] and "захотелось" in prompts[0]
    assert entry["v"] == 2 and entry["items"][0]["link"] == "https://q.example/bh"  # та же статья, не «показанная»
    assert entry["items"][0]["about"] == teaser
    assert leisure_reads.build("42", now) is not None and len(prompts) == 1  # новый вид — без повторной сборки


def test_long_or_list_shaped_teaser_is_kept_not_dropped(monkeypatch):
    long_teaser = ("Платформа Ondesign показывает, как команды On — от архитекторов до стилистов — придумывают вещи "
                   "для спорта и города. Внутри — эскизы, споры и прототипы, которые обычно остаются за кадром. "
                   "Третье предложение уже лишнее и только растягивает анонс до неприличной длины, его не будет, "
                   "потому что два предложения — это всё, что помещается в блок «Прочитать».")
    items = [{"title": "On’s new editorial platform", "link": "https://n.example/on", "summary": "Ondesign goes…"}]
    monkeypatch.setattr(leisure_reads.ai, "llm_json", lambda *_a, **_k: [{"id": "0", "about": long_teaser}])

    about = leisure_reads._about_ru(items)["https://n.example/on"]

    assert len(long_teaser) > leisure_reads._ABOUT_MAX and len(about) <= leisure_reads._ABOUT_MAX
    assert about.endswith("за кадром.") and "Третье" not in about  # по границе предложения, не обрыв


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
