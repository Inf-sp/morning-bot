from unittest.mock import MagicMock, patch

import pytest

import config
import vision


def _recipe():
    return {
        "name_en": "Shakshuka with feta",
        "cuisine": "turkish",
        "main_ingredients_en": "eggs, tomato sauce, feta",
        "visual_tags": ["eggs", "tomato sauce", "feta", "skillet", "prepared dish"],
        "negative_visual_tags": ["raw ingredients", "grocery", "restaurant interior", "chef", "kitchen"],
    }


@pytest.mark.unit
def test_validate_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with patch("requests.post") as mock_post:
        result = vision.validate_dish_photo("https://img/x.jpg", _recipe(), "breakfast")
    assert result is None
    mock_post.assert_not_called()


@pytest.mark.unit
def test_validate_returns_none_for_blank_url(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    with patch("requests.post") as mock_post:
        result = vision.validate_dish_photo("   ", _recipe(), "breakfast")
    assert result is None
    mock_post.assert_not_called()


@pytest.mark.unit
def test_validate_sends_image_url_and_recipe_context(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": (
            '{"match_score": 90, "is_prepared_dish": true, '
            '"contains_main_ingredients": true, "has_unrelated_subject": false, "reason": "ok"}'
        )}}]
    }
    with patch("requests.post", return_value=response) as mock_post:
        result = vision.validate_dish_photo("https://img/x.jpg", _recipe(), "breakfast")

    assert result == {
        "match_score": 90,
        "is_prepared_dish": True,
        "contains_main_ingredients": True,
        "has_unrelated_subject": False,
        "reason": "ok",
    }
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    content = kwargs["json"]["messages"][0]["content"]
    image_block = next(c for c in content if c["type"] == "image_url")
    assert image_block["image_url"]["url"] == "https://img/x.jpg"
    text_block = next(c for c in content if c["type"] == "text")
    assert "Shakshuka with feta" in text_block["text"]
    assert "breakfast" in text_block["text"]


@pytest.mark.unit
def test_validate_degrades_to_none_on_http_error(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    response = MagicMock(status_code=500)
    with patch("requests.post", return_value=response):
        result = vision.validate_dish_photo("https://img/x.jpg", _recipe(), "breakfast")
    assert result is None


@pytest.mark.unit
def test_validate_degrades_to_none_on_network_error(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    with patch("requests.post", side_effect=Exception("boom")):
        result = vision.validate_dish_photo("https://img/x.jpg", _recipe(), "breakfast")
    assert result is None


@pytest.mark.unit
def test_validate_degrades_to_none_on_invalid_json_content(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "not json at all"}}]}
    with patch("requests.post", return_value=response):
        result = vision.validate_dish_photo("https://img/x.jpg", _recipe(), "breakfast")
    assert result is None


@pytest.mark.unit
def test_validate_strips_markdown_fences_from_response():
    parsed = vision._parse_response('```json\n{"match_score": 80, "is_prepared_dish": true, '
                                     '"contains_main_ingredients": true, "has_unrelated_subject": false, "reason": "x"}\n```')
    assert parsed["match_score"] == 80
