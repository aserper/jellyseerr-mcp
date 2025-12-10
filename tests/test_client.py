import pytest
from unittest.mock import MagicMock, patch
from jellyseerr_mcp.client import JellyseerrClient
from jellyseerr_mcp.config import AppConfig

@pytest.fixture
def mock_config():
    return AppConfig(
        jellyseerr_url="http://test.local",
        jellyseerr_api_key="test-api-key",
        timeout=10.0,
        auth_issuer_url=None,
        auth_resource_server_url=None,
        auth_required_scopes=None,
    )

@pytest.fixture
def mock_httpx_client():
    with patch("jellyseerr_mcp.client.httpx.Client") as mock:
        yield mock

def test_client_init(mock_config, mock_httpx_client):
    client = JellyseerrClient(mock_config)
    
    assert client._base_url == "http://test.local/api/v1"
    assert client._timeout == 10.0
    mock_httpx_client.assert_called_with(
        headers={
            "X-Api-Key": "test-api-key",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=10.0
    )

def test_request_success(mock_config, mock_httpx_client):
    # Setup mock
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status.return_value = None
    
    mock_instance = mock_httpx_client.return_value
    mock_instance.request.return_value = mock_response
    
    client = JellyseerrClient(mock_config)
    result = client.request("GET", "test")
    
    assert result == {"ok": True}
    mock_instance.request.assert_called_with("GET", "http://test.local/api/v1/test", params=None, json=None)

def test_search_media(mock_config, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_instance = mock_httpx_client.return_value
    mock_instance.request.return_value = mock_response
    
    client = JellyseerrClient(mock_config)
    client.search_media("Test Movie")
    
    mock_instance.request.assert_called_with(
        "GET", 
        "http://test.local/api/v1/search", 
        params={"query": "Test+Movie"}, 
        json=None
    )

def test_get_request_details(mock_config, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": 1}
    mock_instance = mock_httpx_client.return_value
    mock_instance.request.return_value = mock_response
    
    client = JellyseerrClient(mock_config)
    client.get_request(123)
    
    mock_instance.request.assert_called_with(
        "GET", 
        "http://test.local/api/v1/request/123", 
        params=None, 
        json=None
    )
