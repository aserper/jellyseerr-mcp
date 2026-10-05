"""Actual service clients registered as MCP tools, all HTTP mocked."""
import json

import httpx
import pytest

from jellyseerr_mcp.clients.arr import RadarrClient, SonarrClient
from jellyseerr_mcp.clients.nzbget import NZBGetClient
from jellyseerr_mcp.clients.nzbhydra import NZBHydraClient
from jellyseerr_mcp.clients.sabnzbd import SABnzbdClient
from jellyseerr_mcp.config import AppConfig, ServiceConfig
from jellyseerr_mcp.server import build_server, create_clients

NZB = b'<nzb><file><segments><segment>id</segment></segments></file></nzb>'


def output(result):
    return result[1] if isinstance(result,tuple) else json.loads(result[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['jellyseerr','radarr','sonarr','nzbget','sabnzbd','nzbhydra'])
async def test_each_service_builds_without_seerr(name):
    config = AppConfig(services={name:ServiceConfig(name,'https://unused.test',api_key='secret')})
    clients = create_clients(config)
    try:
        app = build_server(config,clients)
        tools = await app.list_tools()
        names = {t.name for t in tools}
        assert {'ping','get_services'} <= names
        assert 'raw_request' not in names
        if name != 'jellyseerr':
            assert 'request_media' not in names
        if name == 'nzbhydra':
            assert 'nzbhydra_download_result' not in names
        assert 'DELETE' not in json.dumps([t.model_dump() for t in tools])
    finally:
        for client in clients.values():
            client.close()


@pytest.mark.asyncio
async def test_managed_radarr_add_then_search_and_queue_ids():
    calls = []
    def handle(request):
        calls.append(request)
        path = request.url.path
        if request.method == 'GET':
            resources = {'/api/v3/movie':[], '/api/v3/qualityprofile':[{'id':7,'name':'Profile'}],
                '/api/v3/rootfolder':[{'id':1,'path':'/movies'}], '/api/v3/movie/10':{'id':10,'tmdbId':55},
                '/api/v3/queue':{'totalRecords':1,'records':[{'id':1,'movieId':10,'downloadId':'5'}]}}
            return httpx.Response(200,json=resources[path])
        payload = json.loads(request.content)
        if path == '/api/v3/movie':
            assert payload['addOptions']['searchForMovie'] is False
            return httpx.Response(201,json={'id':10,'tmdbId':55,'title':'Movie'})
        assert payload == {'name':'MoviesSearch','movieIds':[10]}
        return httpx.Response(201,json={'id':44,'status':'queued'})
    client = RadarrClient(ServiceConfig('radarr','https://radarr.test',allow_writes=True),httpx.MockTransport(handle))
    app = build_server(AppConfig(read_only=False),{'radarr':client})
    try:
        added = output(await app.call_tool('radarr_add_movie',{'tmdb_id':55,'quality_profile_id':7,'root_folder':'/movies'}))
        assert added['added'] and added['movie']['id'] == 10
        searched = output(await app.call_tool('radarr_search_movie',{'movie_id':10}))
        assert searched['commandId'] == 44
        queue = output(await app.call_tool('radarr_get_queue',{}))
        assert queue['items'][0]['downloadId'] == '5'
        assert len([r for r in calls if r.url.path.endswith('/command')]) == 1
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['nzbget','sabnzbd'])
async def test_real_hydra_to_downloader_clients(backend):
    seen = []
    def hydra_handle(request):
        seen.append(request)
        assert request.url.params['t'] == 'get' and request.url.params['id'] == '123'
        return httpx.Response(200,content=NZB,headers={'Content-Disposition':'attachment; filename="release.nzb"'})
    def downloader_handle(request):
        seen.append(request)
        if backend == 'nzbget':
            body = json.loads(request.content)
            if body['method'] == 'version':
                result = '26.0'
            elif body['method'] == 'config':
                result = [{'Name':'Category1.Name','Value':'movies'}]
            elif body['method'] == 'append':
                assert body['params'][0] == 'release.nzb'
                result = 5
            else:
                assert body['method'] == 'listgroups'
                result = [{'NZBID':5,'Status':'QUEUED'}]
            return httpx.Response(200,json={'result':result})
        if request.method == 'POST':
            assert b'name="nzbfile"' in request.content and NZB in request.content
            return httpx.Response(200,json={'status':True,'nzo_ids':['SABnzbd_nzo_one']})
        if request.url.params['mode'] == 'get_cats':
            return httpx.Response(200,json={'categories':['movies']})
        return httpx.Response(200,json={'queue':{'slots':[{'nzo_id':'SABnzbd_nzo_one'}]}})
    hydra = NZBHydraClient(ServiceConfig('nzbhydra','https://hydra.test',api_key='hydra-secret'),httpx.MockTransport(hydra_handle))
    cls = NZBGetClient if backend == 'nzbget' else SABnzbdClient
    target = cls(ServiceConfig(backend,'https://downloader.test',api_key='sab-secret',username='user',password='password',
                              allow_writes=True),httpx.MockTransport(downloader_handle))
    app = build_server(AppConfig(read_only=False),{'nzbhydra':hydra,backend:target})
    try:
        data = output(await app.call_tool('nzbhydra_download_result',{'result_id':'123','backend':backend,'category':'movies'}))
        assert data['submission']['accepted'] and data['submission']['job_id']
        assert data['managed_import'] is False
        assert 'hydra-secret' not in json.dumps(data) and 'sab-secret' not in json.dumps(data)
        assert len([r for r in seen if r.url.host == 'hydra.test']) == 1
    finally:
        hydra.close(); target.close()
