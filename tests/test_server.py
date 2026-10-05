import json

import httpx
import pytest

from jellyseerr_mcp.client import JellyseerrClient
from jellyseerr_mcp.config import AppConfig, ServiceConfig
from jellyseerr_mcp.server import build_server


def unpack(result):
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


@pytest.mark.asyncio
async def test_ping_discovery_and_conditional_tools():
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local'),
                             httpx.MockTransport(lambda request: httpx.Response(200,json={'results':[]})))
    server = build_server(AppConfig(), {'jellyseerr':client})
    names = {tool.name for tool in await server.list_tools()}
    assert names == {'ping','get_services','search_media','get_request','request_media'}
    assert unpack(await server.call_tool('ping',{})) == {'ok':True,'service':'arrchestra-mcp'}
    assert not unpack(await server.call_tool('get_services',{}))['services'][0]['writes_enabled']
    client.close()


@pytest.mark.asyncio
async def test_readonly_enforced_over_wire_dispatch():
    # Even injecting a writable client does not bypass the server's global policy.
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local',allow_writes=True),
                             httpx.MockTransport(lambda request: pytest.fail('network call')))
    server = build_server(AppConfig(read_only=True,allow_raw_read=True), {'jellyseerr':client})
    assert not unpack(await server.call_tool('get_services',{}))['services'][0]['writes_enabled']
    with pytest.raises(Exception, match='read-only'):
        await server.call_tool('request_media',{'media_id':1,'media_type':'movie'})
    with pytest.raises(Exception, match='allowlisted'):
        await server.call_tool('raw_request',{'method':'DELETE','endpoint':'request/1'})
    client.close()


@pytest.mark.asyncio
async def test_mocked_seerr_tool_contract():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'results':[{'id':1,'title':'Movie'}]})
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local'),httpx.MockTransport(handler))
    server = build_server(AppConfig(),{'jellyseerr':client})
    result = unpack(await server.call_tool('search_media',{'query':'Test Movie'}))
    assert result['results'][0]['id'] == 1
    assert 'query=Test%20Movie' in str(requests[0].url)
    client.close()
