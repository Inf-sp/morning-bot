"""Контракт ai.llm_json: всегда возвращает dict, либо кидает понятную ошибку.

Регресс на «⚠️ Что-то пошло не так» в Досуг · Планирование путешествий: модель
иногда отдаёт строку/число/массив, а вызывающие делают p["..."] / p.get(...) вне
try — это падало TypeError/AttributeError и превращалось в generic-ошибку.
"""
import pytest

pytest.importorskip("requests")
import ai


def _patch_llm(monkeypatch, raw):
    monkeypatch.setattr(ai, "llm", lambda *a, **k: raw)


@pytest.mark.unit
def test_returns_dict_for_object(monkeypatch):
    _patch_llm(monkeypatch, '{"flag": "x", "country": "Норвегия"}')
    assert ai.llm_json("p") == {"flag": "x", "country": "Норвегия"}


@pytest.mark.unit
def test_unwraps_first_dict_from_list(monkeypatch):
    _patch_llm(monkeypatch, '[{"country": "Япония"}, {"country": "Перу"}]')
    assert ai.llm_json("p") == {"country": "Япония"}


@pytest.mark.unit
@pytest.mark.parametrize("raw", ['"просто строка"', "42", "null", "[]", "[1, 2, 3]"])
def test_non_dict_raises_friendly_error(monkeypatch, raw):
    _patch_llm(monkeypatch, raw)
    with pytest.raises(Exception, match="Не удалось разобрать"):
        ai.llm_json("p")
