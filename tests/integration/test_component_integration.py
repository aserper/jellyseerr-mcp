"""Server -> actual service client -> mocked HTTP (never real acquisition)."""
import json

import httpx
import pytest

from jellyseerr_mcp.client import JellyseerrClient
from jellyseerr_mcp.config import AppConfig, ServiceConfig
from jellyseerr_mcp.server import build_server


@pytest.mark.asyncio
async def test_search_tool_client_http_integration():
    def handler(request):
        assert request.url.path == '/api/v1/search'
        assert str(request.url).endswith('query=Integration%20Mov')
        return httpx.Response(200,json={'results':[{'id':999,'title':'Integration Mov'}]})
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://integration.test',api_key='integration-key'),
                             httpx.MockTransport(handler))
    server = build_server(AppConfig(),{'jellyseerr':client})
    result = await server.call_tool('search_media',{'query':'Integration Mov'})
    data = result[1] if isinstance(result,tuple) else json.loads(result[0].text)
    assert data['results'][0]['id'] == 999
    client.close()
