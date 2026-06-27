"""Базовая проверка импортов — если это падает, бот не запустится вообще."""
import pytest


@pytest.mark.unit
def test_import_config():
    import config
    assert config.TZ is not None


@pytest.mark.unit
def test_import_ai():
    import ai
    assert callable(ai.llm)
    assert callable(ai.llm_json)


@pytest.mark.unit
def test_import_store():
    import store
    assert callable(store.get_settings)
    assert callable(store.get_list)


@pytest.mark.unit
def test_import_weather():
    import weather
    assert callable(weather.fetch_weather)
    assert callable(weather.weather_icon)


@pytest.mark.unit
def test_import_verify():
    import verify
    assert callable(verify.safe_error)
    assert callable(verify.safe_send)
    assert callable(verify.grade_text)


@pytest.mark.unit
def test_import_secure():
    import secure
    assert callable(secure.clamp)
    assert callable(secure.wrap_untrusted)
    assert callable(secure.redact)


@pytest.mark.unit
def test_config_lagom_is_str():
    import config
    assert isinstance(config.LAGOM, str)


@pytest.mark.unit
def test_config_myday_rules_callable():
    import config
    result = config.myday_rules("Amsterdam", "Netherlands", "NL")
    assert isinstance(result, str)
    assert len(result) > 0
