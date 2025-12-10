import pytest
from unittest.mock import MagicMock, patch
from jellyseerr_mcp.server import ping, search_media, request_media, get_request, raw_request

# Mock the logger to avoid cluttering test output
@pytest.fixture(autouse=True)
def mock_logger():
    with patch("jellyseerr_mcp.server.logger") as mock:
        yield mock

# Mock the global _client in server.py
@pytest.fixture
def mock_client():
    with patch("jellyseerr_mcp.server._client") as mock:
        yield mock

def test_ping():
    result = ping()
    assert result == {
        "ok": True,
        "service": "jellyseerr-mcp",
    }

def test_search_media_success(mock_client):
    # Setup mock return value
    expected_data = {"results": [{"id": 1, "title": "Test Movie"}]}
    mock_client.search_media.return_value = expected_data
    
    # Call the function
    result = search_media(query="Test Movie")
    
    # Assertions
    assert result == expected_data
    mock_client.search_media.assert_called_once_with("Test Movie")

def test_request_media_success(mock_client):
    expected_data = {"id": 1, "status": "pending"}
    mock_client.request_media.return_value = expected_data
    
    result = request_media(media_id=123, media_type="movie")
    
    assert result == expected_data
    mock_client.request_media.assert_called_once_with(media_id=123, media_type="movie")

def test_get_request_success(mock_client):
    expected_data = {"id": 1, "media": {"title": "Test Movie"}}
    mock_client.get_request.return_value = expected_data
    
    result = get_request(request_id=1)
    
    assert result == expected_data
    mock_client.get_request.assert_called_once_with(request_id=1)

def test_raw_request_success(mock_client):
    expected_data = {"foo": "bar"}
    mock_client.request.return_value = expected_data
    
    result = raw_request(method="GET", endpoint="/status", params={"a": "b"})
    
    assert result == expected_data
    mock_client.request.assert_called_once_with(method="GET", endpoint="/status", params={"a": "b"}, json=None)

def test_raw_request_invalid_method(mock_client):
    with pytest.raises(ValueError, match="Unsupported method"):
        raw_request(method="PATCH", endpoint="/status")
