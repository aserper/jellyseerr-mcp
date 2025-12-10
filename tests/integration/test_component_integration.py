import pytest
from unittest.mock import MagicMock, patch
import os
from jellyseerr_mcp import server
from jellyseerr_mcp.config import AppConfig

# Integration test that verifies server -> client -> mocked_http interaction

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("JELLYSEERR_URL", "http://integration.test")
    monkeypatch.setenv("JELLYSEERR_API_KEY", "integration-key")

@pytest.fixture(autouse=True)
def setup_teardown_client(mock_env):
    # Manually initialize the client as server.run() would
    # We patch httpx.Client to capture the "network" calls
    with patch("httpx.Client") as mock_http:
        # Mock the response
        mock_instance = mock_http.return_value
        mock_instance.request.return_value.json.return_value = {"results": [{"id": 999, "title": "Integration Mov"}]}
        mock_instance.request.return_value.raise_for_status.return_value = None
        
        # Initialize server._client
        config = AppConfig(
            jellyseerr_url="http://integration.test",
            jellyseerr_api_key="integration-key"
        )
        # We need to inject this into the server module
        with patch("jellyseerr_mcp.server._client", server.JellyseerrClient(config)):
            yield mock_instance

def test_search_media_integration(setup_teardown_client):
    """
    Tests that calling the server tool correctly calls the client which calls httpx.
    """
    # Call the server tool
    result = server.search_media("Integration Mov")
    
    # Verify result from mock
    assert result == {"results": [{"id": 999, "title": "Integration Mov"}]}
    
    # Verify httpx call
    setup_teardown_client.request.assert_called_with(
        "GET",
        "http://integration.test/api/v1/search",
        params={"query": "Integration+Mov"},
        json=None
    )
