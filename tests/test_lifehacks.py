import asyncio
import json
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import assistant
import bot_text
import myday
from fakes import RecordingBot



def test_chat_command_adds_dutch_article_lifehack_to_json(tmp_path, monkeypatch):
    path = tmp_path / "lifehacks.json"
    path.write_text(json.dumps([
        {"cat": "Быт и дом", "emoji": "🏠", "tips": []},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(myday, "_HERE", tmp_path)
    kv = {}
    monkeypatch.setattr(myday.store, "_load", lambda key: kv.get(key, {}))
    monkeypatch.setattr(myday.store, "_save", lambda key, value: kv.__setitem__(key, value))

    bot = RecordingBot()
    text = (
        "Добавь лайфхак\n\n"
        "DE — синий, HET — оранжевый. Представляй слова с de синими, "
        "а с het — оранжевыми. Цветовые ассоциации помогают быстрее запомнить артикли."
    )

    assert asyncio.run(assistant.try_add_lifehack_from_chat(bot, "42", text)) is True

    saved = kv[myday.config.LIFEHACK_CATALOG_KEY]["items"]
    assert json.loads(path.read_text(encoding="utf-8"))[0]["cat"] == "Быт и дом"  # файл не трогаем
    language = next(item for item in saved if item["category"] == "язык")
    assert language["source"] == "user"
    assert language["tags"] == ["язык"]
    assert language["text"].startswith("DE — синий")
    assert bot.sent[0]["text"] == (
        "✅ Лайфхак сохранён\n\n"
        "Категория: 🇳🇱 Язык\n"
        "Будет появляться в разделе «Полезное» в «Мой день»."
    )


def _pool(monkeypatch, items, last_topic=""):
    saved = {"42": {"default": {"items": items, "last_topic": last_topic}}}
    monkeypatch.setattr(myday, "_pool_ensure_fresh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(myday, "_pool_get", lambda *_args: saved["42"]["default"])
    monkeypatch.setattr(myday, "_lifehack_signature", lambda _cid: "sig")

    def mutate(_key, change):
        change(saved)

    monkeypatch.setattr(myday.store, "mutate_kv", mutate)
    return saved["42"]["default"]


TIPS = [
    {"id": 1, "topic": "home", "text": "Протри экран телефона микрофиброй раз в день, чтобы меньше бликов.", "shown_at": None},
    {"id": 2, "topic": "wardrobe", "text": "Суши шерстяную кофту на полотенце горизонтально, чтобы она не растянулась.", "shown_at": None},
    {"id": 3, "topic": "kitchen", "text": "Храни авокадо отдельно от бананов, чтобы оно не перезрело за ночь.", "shown_at": None},
]


def test_lifehack_prefers_weather_topic_then_alternates_topics(monkeypatch):
    monkeypatch.setattr(myday, "lifehack_topics_on", lambda _cid: ["wardrobe", "kitchen", "home"])
    bucket = _pool(monkeypatch, [dict(tip) for tip in TIPS], last_topic="home")

    label, text = myday.daily_lifehack("42", rain=True)  # дождь — гардероб первым
    assert label == "👔 Гардероб" and text.startswith("Суши шерстяную кофту")
    label, text = myday.daily_lifehack("42")  # вчера гардероб — сегодня другая тема
    assert bucket["last_topic"] != "wardrobe" and not text.startswith("Суши")


def test_disabled_topic_and_general_base_only_as_fallback(monkeypatch):
    monkeypatch.setattr(myday, "lifehack_topics_on", lambda _cid: ["kitchen"])
    _pool(monkeypatch, [dict(tip) for tip in TIPS])
    monkeypatch.setattr(myday, "_local_lifehack_candidates", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("общая база при живом пуле не нужна")))

    assert myday.daily_lifehack("42")[0] == "🥣 Кухня"

    _pool(monkeypatch, [])
    monkeypatch.setattr(myday, "_mark_local_lifehack_seen", lambda *_a: None)
    monkeypatch.setattr(myday, "_local_lifehack_candidates", lambda *_a, **_k: [
        {"id": "l1", "category": "язык", "text": "de/het"},
        {"id": "l2", "category": "кухня", "text": "Заморозь зелень в масле, чтобы она дольше хранилась."}])
    assert myday.daily_lifehack("42")[1].startswith("Заморозь зелень")  # только включённая тема


def test_lifehack_pool_is_personal_and_regenerates_when_data_changes(monkeypatch):
    import settings
    prompts, stored = [], []
    monkeypatch.setattr(myday, "lifehack_topics_on", lambda _cid: ["wardrobe", "language"])
    monkeypatch.setattr(myday, "_lifehack_profile", lambda _cid: {
        "wardrobe": ["Серая худи хлопок", "Пуховик"], "language": {"language": "нидерландский", "level": "B1+"},
        "kitchen": {"products": ["авокадо"]}})
    monkeypatch.setattr(myday, "_store_ai_lifehacks", lambda items: stored.append(items))

    def llm(prompt, *_a, **_k):
        prompts.append(prompt)
        return {"tips": [
            {"topic": "wardrobe", "text": "Стирай серую худи наизнанку при 30 градусах, чтобы принт не трескался."},
            {"topic": "kitchen", "text": "Храни авокадо в холодильнике после разреза, чтобы оно не потемнело."},
        ]}

    monkeypatch.setattr(myday.ai, "llm_json", llm)
    tips = myday._generate_lifehack_pool("42")

    assert [extra["topic"] for _text, extra in tips] == ["wardrobe"]  # выключенная тема отброшена
    assert "Серая худи" in prompts[0] and "авокадо" not in prompts[0]
    assert stored == []  # персональные советы не попадают в общий каталог
    del settings

    saved, generated = {}, []
    monkeypatch.setattr(myday, "_pool_get", lambda *_a: saved.get("b") or {})
    monkeypatch.setattr(myday, "_pool_save", lambda _k, _c, _p, items, signature="": saved.__setitem__(
        "b", {"items": items, "signature": signature, "week": myday._iso_week_key(),
              "version": myday._LIFEHACK_POOL_VERSION}))
    many = [(f"Сложи кофту рулоном номер {i}, чтобы она не мялась в шкафу и быстрее сохла.", {"topic": "wardrobe"})
            for i in range(8)]
    gen = lambda: generated.append(1) or many  # noqa: E731
    myday._pool_ensure_fresh("k", "42", "default", gen, signature="a")
    myday._pool_ensure_fresh("k", "42", "default", gen, signature="a")
    myday._pool_ensure_fresh("k", "42", "default", gen, signature="b")  # новая вещь, продукт или язык
    assert len(generated) == 2


def test_lifehack_command_can_collect_text_in_second_message(monkeypatch):
    bot = RecordingBot()
    saved = []
    monkeypatch.setattr(
        assistant.myday,
        "add_lifehack_to_file",
        lambda text: saved.append(text) or {"duplicate": False, "category": "🇳🇱 Язык"},
    )
    assistant.store.pending_input.pop("42", None)

    assert asyncio.run(assistant.try_add_lifehack_from_chat(bot, "42", "Добавь лайфхак")) is True
    assert "Напиши текст лайфхака" in bot.sent[-1]["text"]
    assert asyncio.run(
        assistant.try_add_lifehack_from_chat(bot, "42", "DE — синий, HET — оранжевый.")
    ) is True
    assert saved == ["DE — синий, HET — оранжевый."]
    assert bot.sent[-1]["text"].startswith("✅ Лайфхак сохранён")


def test_chat_router_prioritizes_lifehack_command_over_dictionary(monkeypatch):
    routed = []

    async def fake_lifehack(_bot, cid, text):
        routed.append((cid, text))
        return True

    async def fail_dictionary(*_args, **_kwargs):
        raise AssertionError("lifehack command must bypass dictionary routing")

    async def remove_keyboard(_bot, _cid):
        return None

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", fail_dictionary)
    monkeypatch.setattr(bot_text.assistant, "try_add_lifehack_from_chat", fake_lifehack)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id="lifehack-router"),
        message=SimpleNamespace(text="Добавь лайфхак\nDE — синий, HET — оранжевый."),
    )
    context = SimpleNamespace(bot=RecordingBot())

    asyncio.run(bot_text.handle(update, context, remove_keyboard))

    assert routed == [("lifehack-router", "Добавь лайфхак\nDE — синий, HET — оранжевый.")]


def test_lifehack_catalog_is_seeded_into_store_once(tmp_path, monkeypatch):
    (tmp_path / "lifehacks.json").write_text(json.dumps([
        {"id": "lh_1", "text": "Клади ключи в одно место у двери, чтобы не искать их утром.", "category": "дом"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(myday, "_HERE", tmp_path)
    kv = {}
    monkeypatch.setattr(myday.store, "_load", lambda key: kv.get(key, {}))
    monkeypatch.setattr(myday.store, "_save", lambda key, value: kv.__setitem__(key, value))

    assert [item["id"] for item in myday.lifehack_records()] == ["lh_1"]
    (tmp_path / "lifehacks.json").write_text("[]", encoding="utf-8")

    assert [item["id"] for item in myday.lifehack_records()] == ["lh_1"]


def test_lifehack_topics_toggle_on_myday_settings(monkeypatch):
    import asyncio
    import settings
    saved, shown, resets = {}, [], []
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(myday, "reset_day_cache", resets.append)

    async def show(_bot, _cid, _msg, reply_markup=None, query=None):
        shown.append([b.text for row in reply_markup.inline_keyboard for b in row])

    monkeypatch.setattr(settings.rich_delivery, "show", show)
    asyncio.run(settings.handle_callback(None, "42", "set_lhtopic_language"))

    assert myday.lifehack_topics_on("42") == ["wardrobe", "kitchen", "city", "home"]
    assert "□ Язык" in shown[-1] and "✅ Гардероб" in shown[-1] and resets == ["42"]
