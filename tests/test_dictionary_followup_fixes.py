import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import config
import dictionary_import
import learning_dictionary as ld
import leisure_collection
from dictionary_model import present_conjugation, _dutch_present_stem
from dictionary_repository import DictionaryRepository


# --- 1. SRS-прогресс при схлопывании дублей -------------------------------

def _fresh_copy():
    return {
        "id": "w-old", "lang": "nl", "term": "bevelen", "translation": "Приказывать",
        "srs_level": 0, "srs_easiness": 2.5, "srs_interval_days": 0,
        "srs_due_at": "2026-09-01", "srs_history": [], "srs_last_exercise_type": "",
    }


def _trained_copy():
    return {
        "id": "w-new", "lang": "nl", "term": "Bevelen", "translation": "Отдавать приказ",
        "srs_level": 3, "srs_easiness": 2.6, "srs_interval_days": 6,
        "srs_due_at": "2026-10-10",
        "srs_history": [
            {"ts": "2026-09-20T10:00:00+02:00", "exercise_type": "choice", "result": "chose_option"},
            {"ts": "2026-10-04T09:00:00+02:00", "exercise_type": "recall_free", "result": "recalled_free"},
        ],
        "srs_last_exercise_type": "recall_free",
    }


SRS_EXPECTED = {
    "srs_level": 3, "srs_interval_days": 6, "srs_due_at": "2026-10-10",
    "srs_last_exercise_type": "recall_free",
}


@pytest.mark.parametrize("order", ["fresh_first", "trained_first"])
def test_normalize_user_dictionary_keeps_most_advanced_srs(monkeypatch, order):
    stored = [_fresh_copy(), _trained_copy()]
    if order == "trained_first":
        stored.reverse()
    first_id = stored[0]["id"]
    saved = []
    monkeypatch.setattr(ld.store, "get_list", lambda *_args: [dict(item) for item in stored])
    monkeypatch.setattr(ld.store, "set_list", lambda _k, _c, value: saved.append(value))

    result = ld.normalize_user_dictionary("srs-dupes")

    assert len(result) == 1
    entry = result[0]
    assert entry["id"] == first_id
    assert {key: entry[key] for key in SRS_EXPECTED} == SRS_EXPECTED
    assert len(entry["srs_history"]) == 2
    assert "Приказывать" in entry["translation"] and "Отдавать приказ" in entry["translation"]


def test_repository_all_keeps_most_advanced_srs(monkeypatch):
    class Records:
        def __init__(self):
            self.items = [_fresh_copy(), _trained_copy()]

        def all(self):
            return [dict(item) for item in self.items]

        def save(self, items):
            self.items = items

    repository = DictionaryRepository("42")
    repository.records = Records()

    entries = repository.all()

    assert len(entries) == 1
    assert entries[0]["id"] == "w-old"
    assert {key: entries[0][key] for key in SRS_EXPECTED} == SRS_EXPECTED
    assert repository.records.items == entries


# --- 2. Коллекции сохраняют id ---------------------------------------------

def test_favorite_collection_normalization_preserves_item_ids(monkeypatch):
    stored = {
        config.FAVORITE_MOVIES_KEY: {"42": [
            {"id": "m1", "value": "🎬 **Укрытие (2023)**"}, "Укрытие", "Дюна",
        ]},
        config.FAVORITE_BOOKS_KEY: {"42": [{"id": "b1", "name": "*Дюна*"}, "Дюна"]},
        config.FAVORITE_ARTISTS_KEY: {"42": ["Radiohead"]},
    }
    saved = {}
    monkeypatch.setattr(leisure_collection.store, "_load", lambda key: stored[key])
    monkeypatch.setattr(leisure_collection.store, "_save", lambda key, value: saved.__setitem__(key, value))

    assert leisure_collection.normalize_favorite_collections() is True

    assert saved[config.FAVORITE_MOVIES_KEY]["42"] == [
        {"id": "m1", "value": "Укрытие (2023)"}, "Дюна",
    ]
    assert saved[config.FAVORITE_BOOKS_KEY]["42"] == [{"id": "b1", "name": "Дюна"}]
    assert config.FAVORITE_ARTISTS_KEY not in saved


def test_movie_resolution_preserves_item_ids(monkeypatch):
    monkeypatch.setattr(
        leisure_collection, "_resolve_movie_label",
        lambda _title: {"name": "Укрытие", "kind": "tv", "year": "2023"},
    )
    items = [{"id": "m1", "value": "Укрытие (2023)"}, "Укрытие"]

    assert leisure_collection.normalize_movie_items(items) == [
        {"id": "m1", "value": "Укрытие (сериал, 2023)"},
    ]


# --- 3. Спряжение безударных -eren/-elen -----------------------------------

@pytest.mark.parametrize("infinitive, stem", [
    ("veranderen", "verander"), ("luisteren", "luister"), ("wandelen", "wandel"),
    ("herinneren", "herinner"), ("twijfelen", "twijfel"), ("ontwikkelen", "ontwikkel"),
    ("weigeren", "weiger"), ("verbeteren", "verbeter"), ("rekenen", "reken"),
    ("openen", "open"), ("fietsen", "fiets"), ("maken", "maak"), ("lopen", "loop"),
    ("zitten", "zit"), ("leren", "leer"), ("spelen", "speel"), ("studeren", "studeer"),
    ("passeren", "passeer"), ("beheren", "beheer"), ("bevelen", "beveel"),
    ("geven", "geef"), ("voelen", "voel"),
])
def test_dutch_present_stem(infinitive, stem):
    assert _dutch_present_stem(infinitive) == stem


def test_unstressed_eren_conjugation():
    forms = present_conjugation({"lang": "nl", "term": "veranderen", "pos": "глагол"})

    assert forms[:2] == ["ik verander", "jij/u/hij verandert"]


# --- 4. Очередь Add-запросов не крутится вечно -----------------------------

def test_rejected_queued_add_is_dropped_after_max_attempts(monkeypatch):
    cid, sent, calls = "queued-reject-limit", [], []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def rejected(term, *_args, **_kwargs):
        calls.append(term)
        return None

    dictionary_import.store.set_profile(cid, {})
    dictionary_import._queue_dictionary_analysis(cid, "абракадабра", "nl")
    monkeypatch.setattr(dictionary_import, "_normalize_dict_entry_full", rejected)

    for _ in range(dictionary_import._DICT_PENDING_MAX_REJECTIONS - 1):
        asyncio.run(dictionary_import.process_queued_dictionary_adds(Bot(), [cid]))
    queue = dictionary_import.store.get_profile(cid)["dictionary_pending_analysis"]
    assert queue[0]["attempts"] == dictionary_import._DICT_PENDING_MAX_REJECTIONS - 1
    assert sent == []

    asyncio.run(dictionary_import.process_queued_dictionary_adds(Bot(), [cid]))
    asyncio.run(dictionary_import.process_queued_dictionary_adds(Bot(), [cid]))

    assert len(calls) == dictionary_import._DICT_PENDING_MAX_REJECTIONS
    assert "dictionary_pending_analysis" not in dictionary_import.store.get_profile(cid)
    assert len(sent) == 1
    assert "«абракадабра»" in sent[0]["text"]


def test_provider_failure_does_not_count_as_rejection(monkeypatch):
    cid = "queued-provider-down"

    async def unavailable(*_args, **_kwargs):
        raise dictionary_import.DictionaryAnalysisUnavailable()

    dictionary_import.store.set_profile(cid, {})
    dictionary_import._queue_dictionary_analysis(cid, "мудрость", "nl")
    monkeypatch.setattr(dictionary_import, "_normalize_dict_entry_full", unavailable)

    for _ in range(dictionary_import._DICT_PENDING_MAX_REJECTIONS + 1):
        asyncio.run(dictionary_import.process_queued_dictionary_adds(object(), [cid]))

    queue = dictionary_import.store.get_profile(cid)["dictionary_pending_analysis"]
    assert [item.get("attempts", 0) for item in queue] == [0]


# --- 5. Фоновая пересборка сдвигает пакет ----------------------------------

def test_migration_rotates_batch_after_failed_attempt(monkeypatch):
    cid = "migration-rotation"
    words = [
        {"id": f"w{i}", "lang": "nl", "term": term, "translation": "x",
         "pos": "существительное", "article": "de",
         "dictionary_rebuild_version": ld._DICTIONARY_REBUILD_VERSION}
        for i, term in enumerate(["Benadering", "Tafel", "Stoel", "Kast", "Lamp", "Deur"])
    ]
    batches = []

    async def analyze(prompt, *_args, **_kwargs):
        batches.append([word["term"] for word in words if word["term"] in prompt])
        return {"items": []}

    monkeypatch.setattr(ld.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(ld.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(ld.ai, "allm_json", analyze)
    ld.store.set_profile(cid, {})
    assert ld.queue_dictionary_rebuild(cid) == len(words)

    for _ in range(2):
        ld.store.mutate_profile(cid, lambda p: (
            p.get("dictionary_card_migration", {}).update(retry_after_at=0) or p, None,
        ))
        asyncio.run(ld.process_dictionary_rebuilds(object(), [cid]))

    size = ld._DICTIONARY_REBUILD_BATCH_SIZE
    assert batches[0] == [word["term"] for word in words[:size]]
    assert batches[1] == [word["term"] for word in words[size:2 * size]]
