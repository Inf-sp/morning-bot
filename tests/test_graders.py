"""Юнит-тесты чистых грейдеров verify.py - идут без telegram/env (импортируется только verify)."""
import pytest
import verify


@pytest.mark.unit
def test_emoji_trims_extra():
    out, warn = verify.grade_emoji("Старт 🎯 текст 🔥 ещё 😀 конец", max_n=1)
    assert warn and "trimmed 2" in warn[0]
    # остаётся ровно один эмодзи-кластер
    assert len(verify._EMOJI_CLUSTER.findall(out)) == 1


@pytest.mark.unit
def test_emoji_ok_when_one():
    out, warn = verify.grade_emoji("Один 🎯 эмодзи", max_n=1)
    assert warn == []
    assert out == "Один 🎯 эмодзи"


@pytest.mark.unit
def test_disclaimer_appended_when_missing():
    out, warn = verify.grade_disclaimer("Болит голова, попей воды")
    assert warn == ["health: disclaimer appended"]
    assert "врач" in out.lower()


@pytest.mark.unit
def test_disclaimer_not_duplicated():
    out, warn = verify.grade_disclaimer("Это не диагноз. Обратись к врачу.")
    assert warn == []
    assert out == "Это не диагноз. Обратись к врачу."


@pytest.mark.unit
@pytest.mark.parametrize("rain_real,expect_warn", [(False, True), (True, False), (None, False)])
def test_umbrella(rain_real, expect_warn):
    _, warn = verify.grade_umbrella("Не забудь зонт сегодня", rain_real)
    assert bool(warn) is expect_warn


@pytest.mark.unit
def test_valid_json():
    assert verify.valid_json('{"a": 1}') is True
    assert verify.valid_json('тут немного текста {"a": 1} и ещё') is True
    assert verify.valid_json("совсем не json") is False
    assert verify.valid_json(None) is False


@pytest.mark.unit
def test_html_balance():
    assert verify.grade_html("<b>ok</b> <i>x</i>") == []
    assert verify.grade_html("<b>broken") != []


@pytest.mark.unit
def test_grade_text_by_surface():
    # card не трогает эмодзи
    out, warn = verify.grade_text("🎯 🔥 🚀", "card")
    assert warn == [] and out == "🎯 🔥 🚀"
    # chat ограничивает до одного
    out, warn = verify.grade_text("🎯 🔥 🚀", "chat")
    assert warn and len(verify._EMOJI_CLUSTER.findall(out)) == 1
