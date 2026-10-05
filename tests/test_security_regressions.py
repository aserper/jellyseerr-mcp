import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from jellyseerr_mcp.clients.arr import RadarrClient
from jellyseerr_mcp.clients.nzbget import NZBGetClient
from jellyseerr_mcp.clients.sabnzbd import SABnzbdClient
from jellyseerr_mcp.config import AppConfig, ServiceConfig
from jellyseerr_mcp.http import ServiceClient, ServiceError, parse_xml, sanitize
from jellyseerr_mcp.server import build_server

NZB = b'<nzb><file><segments><segment>id</segment></segments></file></nzb>'


def test_short_secrets_and_reverse_proxy_redirect_boundaries():
    assert 'abc' not in sanitize('credential=abc', ('abc',))
    for location in ('/capture', '/proxy/../capture', '/proxy/%252e%252e/capture'):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(302,headers={'Location':location})
        client = ServiceClient(ServiceConfig('radarr','https://shared.test/proxy',api_key='secret'),httpx.MockTransport(handler))
        with pytest.raises(ServiceError):
            client.request_bytes('GET','api',follow_allowed_redirects=True)
        assert len(seen) == 1
        client.close()


def test_xml_element_depth_and_compression_limits():
    with pytest.raises(ValueError,match='limit'):
        parse_xml(b'<root>' + b'<i/>' * 50000 + b'</root>')
    with pytest.raises(ValueError,match='limit'):
        parse_xml(b'<a>'*33 + b'</a>'*33)
    client = ServiceClient(ServiceConfig('sonarr','https://example.test'),httpx.MockTransport(
        lambda request: httpx.Response(200,content=b'plain',headers={'Content-Encoding':'br'})))
    with pytest.raises(ServiceError,match='compressed'):
        client.request_json('GET','api/v3/status')
    client.close()


@pytest.mark.asyncio
async def test_wire_validation_refuses_boolean_ids_and_string_booleans():
    client = RadarrClient(ServiceConfig('radarr','https://radarr.test',allow_writes=True),httpx.MockTransport(
        lambda request: pytest.fail('invalid wire input reached network')))
    app = build_server(AppConfig(read_only=False),{'radarr':client})
    try:
        async with create_connected_server_and_client_session(app) as session:
            for name,arguments in [('radarr_search_movie',{'movie_id':True}),
                                   ('radarr_set_monitored',{'movie_id':1,'monitored':'false'})]:
                result = await session.call_tool(name,arguments)
                assert result.isError
    finally:
        client.close()


def test_nzbget_version_floor_and_no_raw_rpc_error_code():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'result':'17.0'})
    client = NZBGetClient(ServiceConfig('nzbget','https://nzbget.test',allow_writes=True),httpx.MockTransport(handler))
    with pytest.raises(ServiceError,match='v18'):
        client.pause('5')
    assert calls == [{'method':'version','params':[]}]
    client.close()
    client = NZBGetClient(ServiceConfig('nzbget','https://nzbget.test'),httpx.MockTransport(
        lambda request: httpx.Response(200,json={'error':{'code':'upstream-private-body','message':'secret'}})))
    with pytest.raises(ServiceError) as error:
        client.queue()
    assert 'upstream-private-body' not in str(error.value)
    client.close()


@pytest.mark.parametrize('backend',['nzbget','sabnzbd'])
def test_accepted_submission_keeps_native_id_if_verification_fails(backend):
    def handler(request):
        if backend == 'nzbget':
            method = json.loads(request.content)['method']
            results = {'version':'26.0','config':[{'Name':'Category1.Name','Value':'movies'}],'append':5}
            if method in results:
                return httpx.Response(200,json={'result':results[method]})
        else:
            if request.method == 'POST':
                return httpx.Response(200,json={'status':True,'nzo_ids':['SABnzbd_nzo_1']})
            if request.url.params['mode'] == 'get_cats':
                return httpx.Response(200,json={'categories':['movies']})
        raise httpx.ReadTimeout('probe unavailable',request=request)
    cls = NZBGetClient if backend == 'nzbget' else SABnzbdClient
    client = cls(ServiceConfig(backend,'https://downloader.test',allow_writes=True),httpx.MockTransport(handler))
    result = client.submit(NZB,'release.nzb','movies')
    assert result['accepted'] and result['job_id'] and not result['verified']
    client.close()


def test_sab_upstream_job_id_cannot_expand_a_query_scope():
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(200,json={'status':True,'nzo_ids':['id1,id2']})
        return httpx.Response(200,json={'categories':['movies']})
    client = SABnzbdClient(ServiceConfig('sabnzbd','https://sab.test',allow_writes=True),httpx.MockTransport(handler))
    with pytest.raises(ServiceError,match='invalid job ID'):
        client.submit(NZB,'release.nzb','movies')
    assert len(calls) == 2
    client.close()


@pytest.mark.asyncio
async def test_sync_http_does_not_block_other_mcp_reads():
    import asyncio
    from threading import Barrier
    barrier = Barrier(2)
    def handler(request):
        barrier.wait(timeout=3)
        return httpx.Response(200,json={'id':int(request.url.path.rsplit('/',1)[-1]),'title':'Movie'})
    client = RadarrClient(ServiceConfig('radarr','https://radarr.test'),httpx.MockTransport(handler))
    app = build_server(AppConfig(),{'radarr':client})
    try:
        async with create_connected_server_and_client_session(app) as session:
            results = await asyncio.gather(session.call_tool('radarr_get_movie',{'movie_id':1}),
                                           session.call_tool('radarr_get_movie',{'movie_id':2}))
            assert all(not result.isError for result in results)
    finally:
        client.close()


@pytest.mark.asyncio
async def test_same_service_writes_are_serialized():
    import asyncio
    import time
    from threading import Lock
    active = peak = 0
    guard = Lock()
    def handler(request):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak,active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return httpx.Response(200,json=[])
    client = RadarrClient(ServiceConfig('radarr','https://radarr.test',allow_writes=True),httpx.MockTransport(handler))
    app = build_server(AppConfig(read_only=False),{'radarr':client})
    try:
        async with create_connected_server_and_client_session(app) as session:
            results = await asyncio.gather(session.call_tool('radarr_set_monitored',{'movie_id':1,'monitored':True}),
                                           session.call_tool('radarr_set_monitored',{'movie_id':2,'monitored':False}))
            assert all(not result.isError for result in results) and peak == 1
    finally:
        client.close()


def test_sab_false_global_action_is_not_reported_as_accepted():
    client = SABnzbdClient(ServiceConfig('sabnzbd','https://sab.test',allow_writes=True),
        httpx.MockTransport(lambda request: httpx.Response(200,json={'status':False})))
    with pytest.raises(ServiceError,match='not accepted'):
        client.pause(whole_queue=True)
    client.close()
