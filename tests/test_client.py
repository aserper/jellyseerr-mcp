import json

import httpx
import pytest

from jellyseerr_mcp.client import JellyseerrClient
from jellyseerr_mcp.config import AppConfig, ServiceConfig


def test_client_and_search_encoding():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'results': []})
    client = JellyseerrClient(AppConfig(jellyseerr_url='https://test.local', jellyseerr_api_key='test-api-key'),
                            httpx.MockTransport(handler))
    assert client.search_media('Test Movie & café') == {'results': []}
    assert str(requests[0].url) == 'https://test.local/api/v1/search?query=Test%20Movie%20%26%20caf%C3%A9'
    assert requests[0].headers['X-Api-Key'] == 'test-api-key'
    client.close()
    assert client._http.is_closed


def test_get_request_details():
    def handler(request):
        assert request.url.path == '/api/v1/request/123'
        return httpx.Response(200, json={'id':123})
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local'), httpx.MockTransport(handler))
    assert client.get_request(123) == {'id':123}
    client.close()


def test_tv_request_does_not_invent_language_profile_or_seasons():
    sent = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[{'id':1,'is4k':False,'isDefault':True,
                'activeProfileId':4,'activeDirectory':'/tv'}])
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={'id':9,'status':'pending'})
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local',allow_writes=True),httpx.MockTransport(handler))
    assert client.request_media(123,'tv',seasons=[3,1,3])['id'] == 9
    assert sent[0]['seasons'] == [1,3]
    assert 'languageProfileId' not in sent[0]
    with pytest.raises(ValueError):
        client.request_media(123,'tv',seasons=[])
    client.close()


def test_permissions_before_network_and_raw_allowlist():
    client = JellyseerrClient(ServiceConfig('jellyseerr','https://test.local'),
                             httpx.MockTransport(lambda request: pytest.fail('network call')))
    with pytest.raises(PermissionError):
        client.request_media(1,'movie')
    for method,endpoint in [('DELETE','request/1'),('GET','settings/main'),('POST','status')]:
        with pytest.raises(PermissionError):
            client.raw_read(method,endpoint)
    client.close()
