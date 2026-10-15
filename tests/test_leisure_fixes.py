import asyncio

import leisure_music
import leisure_novelty
import recipe_generation
from ui import leisure as leisure_ui


def _italic(msg):
    raw = msg.text.encode("utf-16-le")
    return [raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le") for e in msg.entities if e.type == "italic"]


def test_music_dislike_hides_artist_from_saved_card_after_restart(monkeypatch):
    hidden, sent = [], []
    monkeypatch.setattr(leisure_music.store, "last_recos", {})  # память процесса пуста после перезапуска
    monkeypatch.setattr(leisure_music, "_cached_artist", lambda _cid: {"artist": "Wolf Alice"})
    monkeypatch.setattr(leisure_music, "_invalidate_artist", lambda _cid: None)
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "add", lambda *args: hidden.append(args))

    async def send_listen(_bot, _cid, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(leisure_music, "send_listen", send_listen)
    asyncio.run(leisure_music.listen_dislike(None, "42", status="s"))

    assert hidden == [("42", "artist", "Wolf Alice", "hidden")] and sent[0]["status"] == "s"


def test_artist_fact_is_italic_with_period():
    msg = leisure_ui.artist_card({"artist": "Wolf Alice",
                                  "fact": "Wolf Alice получила премию Mercury Prize в 2015 за альбом ‘My Love Is Cool’"})
    assert msg.text.endswith("‘My Love Is Cool’.") and _italic(msg)[-1].endswith("Cool’.")


def test_music_novelty_card_shows_genre_country_and_description():
    msg = leisure_ui.novelty_card("music", {"title": "WILDCHILD", "artist": "Alex Warren", "genre_label": "Поп",
                                            "country_label": "🇺🇸 USA", "about": "Танцевальный поп."})
    assert "Поп · 🇺🇸 USA" in msg.text and msg.text.endswith("Танцевальный поп.")


def test_breakfast_from_my_products_rejects_lunch_dishes():
    assert not recipe_generation.fits_meal({"name": "Салат из пасты с креветками и авокадо"}, "breakfast")
    assert recipe_generation.fits_meal({"name": "Омлет с сыром"}, "breakfast")
    assert recipe_generation.fits_meal({"name": "Паста с томатами"}, "dinner")


def test_artist_card_shows_genre_and_country_line():
    msg = leisure_ui.artist_card({"artist": "Wolf Alice", "genre_label": "Инди", "country_label": "🇬🇧 UK"})
    assert msg.text.startswith("🇬🇧 Wolf Alice\n\nИнди · UK")


def test_artist_card_is_a_lively_recommendation_without_why_block():
    msg = leisure_ui.artist_card({
        "artist": "Fontaines D.C.", "genre_label": "Рок", "style": "пост-панк", "country_label": "🇮🇪 IE",
        "years": "с 2017", "why": ["Старое поле из кэша"],
        "desc": "Дублинский пост-панк с шершавыми гитарами. Вокал скорее читает стихи, чем поёт.",
        "tracks": [{"title": "Boys in the Better Land", "note": "Быстрый и злой", "url": "https://music.youtube.com/a"},
                   "A Hero's Death - мантра на одном риффе", "Starburster - тревожный, с одышкой"],
        "fact": "Альбом «A Hero's Death» номинировали на «Грэмми»",
    })

    assert msg.text == (
        "🇮🇪 Fontaines D.C.\n\n"
        "Пост-панк · IE · с 2017\n\n"  # точный жанр, страна кратко, годы группы
        "Дублинский пост-панк с шершавыми гитарами. Вокал скорее читает стихи, чем поёт.\n\n"
        "С чего начать:\n"
        "• Boys in the Better Land — быстрый и злой\n"  # пометка со строчной
        "• A Hero's Death — мантра на одном риффе\n"
        "• Starburster — тревожный, с одышкой\n\n"
        "Альбом «A Hero's Death» номинировали на «Грэмми»."
    )
    assert "Почему" not in msg.text and _italic(msg)[-1] == "Альбом «A Hero's Death» номинировали на «Грэмми»."


def test_local_fallback_artists_follow_the_card_format():
    import leisure_music
    for items in leisure_music._LOCAL_ARTIST_FALLBACKS.values():
        for item in items:
            assert item["style"] and item["fact"] and "why" not in item
            assert item["desc"].count(". ") >= 1  # 2–3 предложения
            assert len(item["tracks"]) == 3 and all(" - " in track for track in item["tracks"])


def test_configure_buttons_name_what_they_open():
    import telegram_runtime
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = telegram_runtime._enhance_markup(InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Настроить словарь", callback_data="a"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]]))["inline_keyboard"]
    # «Настроить …» — синяя и в один столбец с «Главная» под ней.
    assert [[(b["text"], b.get("style")) for b in row] for row in rows] == [
        [("Настроить словарь", "primary")], [("Главная", "primary")]]




def test_cards_show_year_even_for_this_year_release():
    _title, card = leisure_ui.movie_card({"title": "Диггер"}, {"name": "Диггер", "kind": "movie",
                                                               "release_date": "2026-10-01"})
    assert "1 октября 2026" in card.text
