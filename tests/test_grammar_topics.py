from datetime import datetime

import live_language

FIELDS = {"id", "level", "topic", "meaning", "explain", "example", "translation"}


def test_each_language_has_300_unique_complete_topics():
    for language in ("nl", "en"):
        topics = live_language.grammar_topics(language)
        assert len(topics) >= 300
        assert len({topic["topic"].casefold() for topic in topics}) == len(topics)  # без дублей
        for topic in topics:
            assert set(topic) == FIELDS and all(str(topic[key]).strip() for key in FIELDS), topic["id"]
            assert topic["level"] in ("A1", "A2", "B1", "B2")
            assert topic["example"] not in topic["explain"]  # пример — отдельной строкой, не в объяснении


def _profile(monkeypatch, level="simple"):
    profile = {}

    def mutate(_cid, change):
        updated, _ = change(dict(profile))
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(live_language.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(live_language.store, "mutate_profile", mutate)
    monkeypatch.setattr(live_language.store, "get_level", lambda _cid, _lang: level)
    return profile


def _on_day(monkeypatch, day):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, day, 9, tzinfo=tz)

    monkeypatch.setattr(live_language, "datetime", Clock)


def test_one_topic_per_day_without_repeats_and_by_level(monkeypatch):
    _profile(monkeypatch, "simple")
    shown = []
    for day in range(1, 11):
        _on_day(monkeypatch, day)
        first = live_language.grammar_of_day("42", "nl")
        assert live_language.grammar_of_day("42", "nl") == first  # до завтра та же тема
        shown.append(first["topic"])
    assert len(set(shown)) == 10  # каждый день новая
    assert live_language.grammar_of_day("42", "nl")["level"] == "A1"  # «Простой» — с основ

    _profile(monkeypatch, "hard")
    assert live_language.grammar_of_day("42", "nl")["level"] == "B1"  # «Сложный» — с B1


def test_after_all_topics_the_cycle_starts_again(monkeypatch):
    profile = _profile(monkeypatch)
    every = [topic["id"] for topic in live_language.grammar_topics("en")]
    profile["grammar_progress"] = {"en": {"date": "2026-10-01", "id": every[-1], "seen": every}}
    _on_day(monkeypatch, 2)
    card = live_language.grammar_of_day("42", "en")
    assert card["topic"] and profile["grammar_progress"]["en"]["seen"] == [profile["grammar_progress"]["en"]["id"]]
