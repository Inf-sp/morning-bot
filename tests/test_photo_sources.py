from unittest.mock import MagicMock, patch

import pytest

import config
import pexels
import unsplash


@pytest.mark.unit
def test_pexels_search_returns_empty_without_api_key(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    with patch("requests.get") as mock_get:
        result = pexels.search_photos("shakshuka")
    assert result == []
    mock_get.assert_not_called()


@pytest.mark.unit
def test_pexels_search_returns_empty_for_blank_query(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "test-key")
    with patch("requests.get") as mock_get:
        result = pexels.search_photos("   ")
    assert result == []
    mock_get.assert_not_called()


@pytest.mark.unit
def test_pexels_search_uses_square_orientation_and_auth_header(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "test-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {"photos": [{"id": 1}]}
    with patch("requests.get", return_value=response) as mock_get:
        result = pexels.search_photos("shakshuka", per_page=10)

    assert result == [{"id": 1}]
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "test-key"
    assert kwargs["params"]["orientation"] == "square"
    assert kwargs["params"]["per_page"] == 10
    assert kwargs["params"]["query"] == "shakshuka"


@pytest.mark.unit
def test_pexels_search_degrades_to_empty_on_http_error(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "test-key")
    response = MagicMock(status_code=429)
    with patch("requests.get", return_value=response):
        result = pexels.search_photos("shakshuka")
    assert result == []


@pytest.mark.unit
def test_pexels_search_degrades_to_empty_on_network_error(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "test-key")
    with patch("requests.get", side_effect=Exception("boom")):
        result = pexels.search_photos("shakshuka")
    assert result == []


@pytest.mark.unit
def test_unsplash_search_returns_empty_without_api_key(monkeypatch):
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    with patch("requests.get") as mock_get:
        result = unsplash.search_photos("shakshuka")
    assert result == []
    mock_get.assert_not_called()


@pytest.mark.unit
def test_unsplash_search_uses_squarish_orientation_and_content_filter(monkeypatch):
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "test-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {"results": [{"id": "u1"}]}
    with patch("requests.get", return_value=response) as mock_get:
        result = unsplash.search_photos("shakshuka", per_page=10)

    assert result == [{"id": "u1"}]
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Client-ID test-key"
    assert kwargs["params"]["orientation"] == "squarish"
    assert kwargs["params"]["content_filter"] == "high"
    assert kwargs["params"]["order_by"] == "relevant"
    assert kwargs["params"]["per_page"] == 10


@pytest.mark.unit
def test_unsplash_search_degrades_to_empty_on_http_error(monkeypatch):
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "test-key")
    response = MagicMock(status_code=500)
    with patch("requests.get", return_value=response):
        result = unsplash.search_photos("shakshuka")
    assert result == []


@pytest.mark.unit
def test_unsplash_search_degrades_to_empty_on_network_error(monkeypatch):
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "test-key")
    with patch("requests.get", side_effect=Exception("boom")):
        result = unsplash.search_photos("shakshuka")
    assert result == []
