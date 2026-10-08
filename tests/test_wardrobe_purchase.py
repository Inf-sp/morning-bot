import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import ai
import bot_text
import routing
import wardrobe
import wardrobe_management
import wardrobe_purchase as purchase
from ui import wardrobe as wardrobe_ui
from wardrobe_model import normalize_parsed_item
from fakes import RecordingBot

_ITEMS = (
    "Серая футболка", "Белая рубашка", "Чёрная футболка", "Оливковая худи",
    "Синие джинсы", "Чёрные брюки", "Чёрные ботинки", "Тёмно-синяя ветровка", "Часы",
)


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _wardrobe(names=_ITEMS):
    w = {"_v": 1, "zones": {}}
    for name in names:
        item = normalize_parsed_item({"name": name, "color": name.split()[0].lower()})
        w["zones"].setdefault(item["zone"], {}).setdefault(item["subcategory"], []).append(item)
    return w



class Query:
    def __init__(self):
        self.edited = []

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)


def _user(cid, monkeypatch, names=_ITEMS):
    """Реальный store (memory), шкаф из names; AI и сеть на открытии запрещены."""
    wardrobe.store.set_profile(cid, {})
    wardrobe.store.save_wardrobe({"_v": 0, "zones": {}}, cid)
    items = [normalize_parsed_item({"name": n, "color": n.split()[0].lower()}) for n in names]
    wardrobe.store.add_wardrobe_items(cid, items)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])
    monkeypatch.setattr(wardrobe.store, "get_settings", lambda _cid: {"lat": 52.4})
    monkeypatch.setattr(wardrobe, "datetime", _October)
    monkeypatch.setattr(wardrobe_management, "datetime", _October)

    async def no_ai(*_args, **_kwargs):
        raise AssertionError("AI on open")

    monkeypatch.setattr(ai, "allm_json", no_ai)


class _October:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 10, 7, 12, tzinfo=tz)


def test_analysis_counts_items_outfits_and_uses_plurals():
    facts = purchase.wardrobe_facts(_wardrobe(), cold_season=True)
    data = purchase.analysis(facts)

    assert (facts["total"], facts["tops"], facts["bottoms"], facts["shoes"]) == (9, 4, 2, 1)
    assert data["weaknesses"] == ["1 пара обуви на все случаи", "нет тёплой куртки к зиме"]
    text = wardrobe_ui.purchase_screen({**data, "has_picks": True}).text
    assert text.startswith("💳 Что докупить\n\n👔 Твой шкаф · 9 вещей · 16 образов\n")
    assert "Слабо: 1 пара обуви на все случаи · нет тёплой куртки к зиме" in text
    assert "Самое полезное" not in text and "Явных пробелов нет" not in text
    assert "присматриваешь" not in text

    two_shoes = purchase.analysis({**facts, "shoes": 2, "cold_season": False, "light": True})
    assert two_shoes["weaknesses"][0] == "2 пары обуви на все случаи"


def test_candidates_are_ranked_by_real_new_outfits():
    w = _wardrobe()
    base = purchase.count_outfits(w)
    pool = purchase.rank_candidates(w)

    gains = [c["gain"] for c in pool]
    assert gains == sorted(gains, reverse=True)
    assert pool[0]["item"] == "Белые кожаные кеды"
    after = purchase.count_outfits(purchase._with_item(w, purchase.wardrobe_item(pool[0])))
    assert pool[0]["gain"] == after - base > 0


def test_owned_well_covered_and_rejected_candidates_are_excluded():
    shoes = [f"{color} кеды" for color in ("Белые", "Чёрные", "Серые", "Синие", "Бежевые", "Зелёные")]
    pool = purchase.rank_candidates(_wardrobe((*_ITEMS, *shoes)))
    assert not [c for c in pool if c["zone"] == "Обувь"]
    assert "Тёмно-синяя непромокаемая ветровка" not in {c["item"] for c in pool}

    full = purchase.rank_candidates(_wardrobe())
    visible = purchase.visible_pool(full, ["белые кожаные КЕДЫ"])
    assert "Белые кожаные кеды" not in {c["item"] for c in visible}
    assert purchase.rejected_names({"wardrobe_purchase_rejections": ["Старый формат"]}) == ["Старый формат"]


def test_small_wardrobe_asks_to_add_items(monkeypatch):
    cid = "purchase-small"
    _user(cid, monkeypatch, names=_ITEMS[:3])
    bot = RecordingBot()

    asyncio.run(wardrobe.handle_callback(bot, cid, None, "w_buy"))

    assert bot.sent[0]["text"] == "💳 Что докупить\n\nДобавь хотя бы 5 вещей — тогда разбор будет точным."
    assert _labels(bot.sent[0]["reply_markup"]) == [["✅ Добавить вещи"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data == "w_fill"


def test_screen_one_shows_top_three_with_short_callbacks(monkeypatch):
    cid = "purchase-screen-one"
    _user(cid, monkeypatch)
    bot = RecordingBot()

    asyncio.run(wardrobe.handle_callback(bot, cid, None, "w_buy"))

    message = bot.sent[0]
    labels = _labels(message["reply_markup"])
    best = wardrobe._purchase_state(cid)["pool"][0]
    assert (best["zone"], best["gain"]) == ("Обувь", 16)
    assert labels[0] == [best["item"]]
    assert labels[3:] == [["⬅️ Назад", "#️⃣ Главная"]]
    assert best["item"] not in message["text"] and "Самое полезное" not in message["text"]
    styles = [row[0].api_kwargs.get("style") for row in message["reply_markup"].inline_keyboard[:3]]
    assert styles == ["danger"] * 3
    assert message["reply_markup"].inline_keyboard[3][0].callback_data == "m_wardrobe"
    callbacks = [b.callback_data for row in message["reply_markup"].inline_keyboard for b in row]
    assert all(len(data.encode()) <= 64 and "кед" not in data for data in callbacks)
    assert all(routing.resolve_callback_handler(data)["handled"] for data in callbacks if data.startswith("w_"))
    assert wardrobe.store.pending_input[cid] == "wardrobe_buy"

    # Старая кнопка «Другие покупки» из истории чата всё ещё работает.
    query = Query()
    asyncio.run(wardrobe.handle_callback(bot, cid, query, "w_buy_more"))
    first = {row[0] for row in labels[:3]}
    second = {row[0].text for row in query.edited[0]["reply_markup"].inline_keyboard[:3]}
    assert not first & second
    wardrobe.store.pending_input.pop(cid, None)


def test_screen_two_card_uses_real_counts_and_items(monkeypatch):
    cid = "purchase-card"
    _user(cid, monkeypatch)
    bot, query = RecordingBot(), Query()
    asyncio.run(wardrobe.send_purchase_screen(bot, cid))
    first = bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data

    asyncio.run(wardrobe.handle_callback(bot, cid, query, first))

    text = query.edited[0]["text"]
    assert text.startswith(f"🛒 {wardrobe._purchase_state(cid)['pool'][0]['item']}\n\nПочему тебе: ")
    assert "Было 16 образов → станет 32" in text
    assert "Готовые образы:\n• серая футболка + синие джинсы" in text
    assert "💡 " in text and "http" not in text and "€" not in text
    assert _labels(query.edited[0]["reply_markup"]) == [
        ["✅ Добавить в шкаф"], ["Не нравится"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    keyboard = query.edited[0]["reply_markup"].inline_keyboard
    assert keyboard[1][0].api_kwargs == {"style": "danger"}
    assert keyboard[1][0].callback_data == first.replace("w_buy_i:", "w_buy_no:")
    wardrobe.store.pending_input.pop(cid, None)


def test_bought_item_is_added_and_screen_recomputed(monkeypatch):
    cid = "purchase-bought"
    _user(cid, monkeypatch)
    bot, query = RecordingBot(), Query()
    asyncio.run(wardrobe.send_purchase_screen(bot, cid))
    old_key = wardrobe._purchase_state(cid)["key"]
    item_id = purchase.item_id("Белые кожаные кеды")

    asyncio.run(wardrobe.handle_callback(bot, cid, query, f"w_buy_got:{item_id}"))

    names = [it["name"] for _z, _s, it in wardrobe._flat_wardrobe_items(wardrobe.store.load_wardrobe(cid))]
    assert "Белые кожаные кеды" in names
    assert query.edited[0]["text"].startswith("✅ «Белые кожаные кеды» — в шкафу")
    assert wardrobe._purchase_state(cid)["key"] != old_key
    assert "👔 Твой шкаф · 10 вещей" in bot.sent[-1]["text"]
    assert "Белые кожаные кеды" not in bot.sent[-1]["text"]
    wardrobe.store.pending_input.pop(cid, None)


def test_not_needed_is_persisted_and_replaced(monkeypatch):
    cid = "purchase-rejected"
    _user(cid, monkeypatch)
    bot, query = RecordingBot(), Query()
    item_id = purchase.item_id("Белые кожаные кеды")

    asyncio.run(wardrobe.handle_callback(bot, cid, query, f"w_buy_no:{item_id}"))
    asyncio.run(wardrobe.send_purchase_screen(bot, cid))

    profile = wardrobe.store.get_profile(cid)
    assert profile["wardrobe_purchase_rejections"] == {"items": ["Белые кожаные кеды"]}
    assert "Белые кожаные кеды" not in query.edited[0]["text"]
    assert "Белые кожаные кеды" not in bot.sent[-1]["text"]
    assert len(query.edited[0]["reply_markup"].inline_keyboard) == 4
    wardrobe.store.pending_input.pop(cid, None)


def test_open_uses_cache_and_night_warm_adds_ai_ideas(monkeypatch):
    cid = "purchase-cache"
    _user(cid, monkeypatch)
    calls = []

    async def ai_ideas(_prompt, *_args, **_kwargs):
        calls.append(ai._AI_MODE.get())
        return {"items": [{
            "item": "Тёмно-синий пуховик", "zone": "Верхняя одежда", "subcategory": "Пуховики",
            "color": "тёмно-синий", "warmth": "тёплые",
            "why": "Есть только ветровка, а тёплой верхней одежды нет.", "tip": "Бери длину до середины бедра.",
        }]}

    monkeypatch.setattr(ai, "allm_json", ai_ideas)
    asyncio.run(wardrobe.warm_purchase_cache(cid))
    asyncio.run(wardrobe.warm_purchase_cache(cid))
    assert calls == ["background"]

    async def no_ai(*_args, **_kwargs):
        raise AssertionError("AI on open")

    monkeypatch.setattr(ai, "allm_json", no_ai)
    rebuilt = []
    monkeypatch.setattr(purchase, "rank_candidates", lambda *a: rebuilt.append(a) or [])
    bot, query = RecordingBot(), Query()
    asyncio.run(wardrobe.send_purchase_screen(bot, cid))
    asyncio.run(wardrobe.show_purchase_card(bot, cid, purchase.item_id("Тёмно-синий пуховик"), q=query))

    assert rebuilt == []
    assert ["Тёмно-синий пуховик"] in _labels(bot.sent[0]["reply_markup"])
    assert "Почему тебе: Есть только ветровка, а тёплой верхней одежды нет." in query.edited[0]["text"]
    assert "💡 Бери длину до середины бедра." in query.edited[0]["text"]
    wardrobe.store.pending_input.pop(cid, None)


def test_ungrounded_ai_reason_falls_back_to_code_facts():
    w = _wardrobe()
    facts = purchase.wardrobe_facts(w, cold_season=True)
    candidate = purchase.rank_candidates(w, [{
        "item": "Белые кожаные кеды", "zone": "Обувь", "why": "У тебя 7 красных пиджаков.",
    }])[0]

    card = purchase.card(w, candidate, facts)

    assert "пиджак" not in card["why"]
    assert card["why"].startswith("На 4 верха и 2 низа сейчас 1 пара обуви")


def test_old_purchase_callbacks_open_screen_one(monkeypatch):
    opened = []

    async def screen(_bot, cid, q=None, **_kwargs):
        opened.append((cid, q))

    monkeypatch.setattr(wardrobe, "send_purchase_screen", screen)
    monkeypatch.setattr(wardrobe_management, "send_purchase_screen", screen)
    for data in ("w_buy_page:2", "w_buy_new:1", "w_buy_new", "w_buy_gap", "w_buy_pick", "w_buy_back"):
        asyncio.run(wardrobe.handle_callback(object(), "42", "q", data))
        assert routing.resolve_callback_handler(data)["handled"]

    assert opened == [("42", "q")] * 6


def test_unknown_card_id_returns_to_screen_one(monkeypatch):
    cid = "purchase-stale"
    _user(cid, monkeypatch)
    query = Query()

    asyncio.run(wardrobe.handle_callback(RecordingBot(), cid, query, "w_buy_i:deadbeef"))

    assert query.edited[0]["text"].startswith("💳 Что докупить")
    wardrobe.store.pending_input.pop(cid, None)


def test_purchase_check_button_waits_for_item_description():
    bot = RecordingBot()

    asyncio.run(wardrobe.handle_callback(bot, "purchase-check", None, "w_check"))

    assert wardrobe.store.pending_input.pop("purchase-check") == "wardrobe_check"
    assert bot.sent[0]["text"].startswith("Опиши вещь, которую присматриваешь")


def test_free_text_after_screen_one_gets_personal_answer(monkeypatch):
    cid = "purchase-free-text"
    _user(cid, monkeypatch)
    asked = []

    async def recommend(_bot, routed_cid, text):
        asked.append((routed_cid, text))

    async def no_match(*_args, **_kwargs):
        return False

    monkeypatch.setattr(wardrobe, "recommend_purchase", recommend)
    monkeypatch.setattr(wardrobe_management, "recommend_purchase", recommend)
    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.assistant, "try_add_lifehack_from_chat", no_match)
    monkeypatch.setattr(bot_text.assistant, "try_edit_lifehack_from_chat", no_match)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", no_match)
    bot = RecordingBot()
    asyncio.run(wardrobe.send_purchase_screen(bot, cid))

    update = SimpleNamespace(effective_chat=SimpleNamespace(id=cid), message=SimpleNamespace(text="зелёная худи"))
    asyncio.run(bot_text.handle(update, SimpleNamespace(bot=bot), lambda *_args: asyncio.sleep(0)))

    assert asked == [(cid, "зелёная худи")]


def test_picks_stay_until_added_or_disliked(monkeypatch):
    cid = "purchase-stable"
    _user(cid, monkeypatch)
    bot = RecordingBot()

    def picks():
        asyncio.run(wardrobe.send_purchase_screen(bot, cid))
        return [row[0] for row in bot.sent[-1]["reply_markup"].inline_keyboard[:3]]

    first = picks()
    # Порядок пула поменялся (например, ночной пересчёт) — подборка та же.
    state = wardrobe.store.get_profile(cid)[purchase.PROFILE_KEY]
    monkeypatch.setattr(wardrobe_management, "_purchase_cache",
                        lambda *_a, **_k: {**state, "pool": list(reversed(state["pool"]))})
    assert [b.text for b in picks()] == [b.text for b in first]

    asyncio.run(wardrobe.handle_callback(bot, cid, Query(), first[1].callback_data.replace("w_buy_i:", "w_buy_no:")))
    after = [b.text for b in picks()]
    assert first[1].text not in after
    assert after[:2] == [first[0].text, first[2].text]
    wardrobe.store.pending_input.pop(cid, None)
