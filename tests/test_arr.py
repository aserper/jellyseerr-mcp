"""Mocked contract tests for the Radarr/Sonarr arr clients.

Assertions cover the upstream contracts verified against Radarr/Sonarr v3 API
sources: exact paths, query params, JSON payloads, permission-before-network
ordering, rejection gating, ownership validation and paging honesty.
"""
from __future__ import annotations

import json
import re

import pytest
from httpx import MockTransport, Request, Response

from jellyseerr_mcp.clients.arr import RadarrClient, SonarrClient
from jellyseerr_mcp.config import ServiceConfig
from jellyseerr_mcp.http import ServiceError

RADARR = 'http://radarr.test:7878'
SONARR = 'http://sonarr.test:8989'


class FakeApi:
    """Route-matched mock backend that records every request."""

    def __init__(self, routes):
        self.routes = [(method, re.compile(pattern + r'\Z'), handler)
                       for pattern, method, handler in routes]
        self.calls: list[Request] = []

    def transport(self) -> MockTransport:
        return MockTransport(self.handle)

    def handle(self, request):
        self.calls.append(request)
        for method, pattern, handler in self.routes:
            if request.method == method and pattern.match(request.url.path):
                status, payload = handler(request)
                return Response(status, json=payload) if status != 204 else Response(204)
        return Response(404, json={'message': f'unmocked {request.method} {request.url.path}'})

    def bodies(self):
        return [json.loads(call.content) if call.content else {} for call in self.calls]

    def params(self, index=0):
        return dict(self.calls[index].url.params)

    def path(self, index=0):
        return self.calls[index].url.path


def make_client(cls, base, routes, *, allow_writes=True, api_key='secret-key-1'):
    api = FakeApi(routes)
    config = ServiceConfig(cls.__name__.removesuffix('Client').lower(), base,
                           api_key=api_key, timeout=4.0, allow_writes=allow_writes)
    return cls(config, transport=api.transport()), api


def movie(id=1, tmdb=11, title='Example Movie', **extra):
    data = {'id': id, 'title': title, 'tmdbId': tmdb, 'year': 2019, 'monitored': True,
            'status': 'released', 'path': f'/mnt/media/movies/{title}',
            'qualityProfileId': 7, 'rootFolderPath': '/mnt/media/movies',
            'hasFile': True, 'sizeOnDisk': 4321, 'overview': 'long text', 'images': [],
            'tags': [3], 'addOptions': {'searchForMovie': False}}
    data.update(extra)
    return data


def series(id=5, tvdb=999, title='Example Show', seasons=None, **extra):
    data = {'id': id, 'title': title, 'tvdbId': tvdb, 'year': 2020, 'monitored': True,
            'status': 'continuing', 'path': '/mnt/media/tv/example-show',
            'qualityProfileId': 7, 'rootFolderPath': '/mnt/media/tv',
            'seasons': seasons if seasons is not None else
            [{'seasonNumber': 0, 'monitored': False}, {'seasonNumber': 1, 'monitored': True},
             {'seasonNumber': 2, 'monitored': False}],
            'statistics': {'episodeCount': 20}, 'images': [], 'tags': []}
    data.update(extra)
    return data


def episode(id=101, series_id=5, season=1, number=3, monitored=True, **extra):
    data = {'id': id, 'seriesId': series_id, 'seasonNumber': season, 'episodeNumber': number,
            'title': 'Pilot II', 'monitored': monitored, 'hasFile': False,
            'airDateUtc': '2020-04-01T00:00:00Z'}
    data.update(extra)
    return data


def release(guid='guid-1', indexer_id=3, title='Example Movie 2019', **extra):
    data = {'guid': guid, 'indexerId': indexer_id, 'indexer': 'ExampleIndexer',
            'title': title, 'size': 5_000_000_000, 'seeders': 42, 'leechers': 1,
            'protocol': 'usenet', 'approved': True, 'temporarilyRejected': False,
            'rejected': False, 'rejections': [], 'downloadUrl': 'https://indexer/dl?apikey=zzz',
            'infoUrl': 'https://indexer/info'}
    data.update(extra)
    return data


def root_folders():
    return [{'id': 1, 'path': '/mnt/media/movies', 'accessible': True, 'freeSpace': 10**12,
             'unmappedFolders': []}]


def tv_root_folders():
    return [{'id': 2, 'path': '/mnt/media/tv', 'accessible': True, 'freeSpace': 10**12}]


def profiles():
    return [{'id': 7, 'name': 'FHD/UHD', 'upgradeAllowed': True, 'cutoff': 8, 'items': []},
            {'id': 8, 'name': 'Any', 'upgradeAllowed': False, 'cutoff': 1, 'items': []}]


def paged(records, total=None):
    return {'page': 1, 'pageSize': len(records), 'sortKey': 'date', 'sortDirection': 'descending',
            'totalRecords': total if total is not None else len(records), 'records': records}


# ---------------------------------------------------------------- Radarr reads

def test_radarr_find_catalog_filters_and_paginates_locally():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie', 'GET', lambda req: (200, [movie(1, title='Alpha'),
                                                     movie(2, title='Beta'),
                                                     movie(3, title='Alphabet Soup')]))])
    result = client.find('ALP', offset=1, limit=1)
    assert api.path(0) == '/api/v3/movie' and api.params(0) == {}
    assert result['pagination'] == 'local' and result['total'] == 2
    assert [i['id'] for i in result['items']] == [3]
    assert result['offset'] == 1 and result['limit'] == 1 and result['has_more'] is False
    assert set(result['items'][0]) <= {'id', 'title', 'tmdbId', 'year', 'monitored', 'status',
                                       'path', 'qualityProfileId', 'hasFile', 'sizeOnDisk'}
    client.close()


def test_radarr_find_lookup_uses_lookup_endpoint():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie/lookup', 'GET', lambda req: (200, [movie(0, tmdb=99, title='Searched')]))
    ])
    result = client.find('searched', source='lookup', limit=25)
    assert api.path(0) == '/api/v3/movie/lookup' and api.params(0) == {'term': 'searched'}
    assert result['total'] == 1 and result['items'][0]['tmdbId'] == 99
    with pytest.raises(ValueError, match='source'):
        client.find('x', source='everywhere')
    with pytest.raises(ValueError, match='query'):
        client.find('q' * 513)
    with pytest.raises(ValueError, match='offset'):
        client.find('alp', offset=-1)
    assert len(api.calls) == 1  # invalid input never reaches the network
    client.close()


def test_radarr_get_projects_safe_fields_only():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie/1', 'GET', lambda req: (200, movie(1)))])
    result = client.get('1')
    assert api.path(0) == '/api/v3/movie/1'
    assert result['tmdbId'] == 11 and result['monitored'] is True
    assert 'overview' not in result and 'images' not in result and 'tags' not in result
    client.close()


def test_radarr_options_projects_safe_fields_only():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/rootfolder', 'GET', lambda req: (200, root_folders())),
        (r'/api/v3/qualityprofile', 'GET', lambda req: (200, profiles())),
        (r'/api/v3/remotepathmapping', 'GET', lambda req: (200, [
            {'id': 4, 'host': 'dl.host', 'remotePath': '/remote', 'localPath': '/local'}])),
    ])
    result = client.options()
    assert [set(m) for m in result['remote_path_mappings']] == [{'id', 'host', 'remotePath', 'localPath'}]
    assert set(result['quality_profiles'][0]) == {'id', 'name', 'upgradeAllowed'}
    assert set(result['root_folders'][0]) == {'id', 'path', 'freeSpace', 'accessible'}
    client.close()


def test_radarr_queue_uses_native_paging_and_projects():
    records = [{'id': 9, 'movieId': 1, 'title': 'Example Movie', 'status': 'downloading',
                'trackedDownloadStatus': 'warning', 'trackedDownloadState': 'downloading',
                'size': 100, 'sizeleft': 50, 'timeleft': '00:01:00', 'protocol': 'usenet',
                'downloadClient': 'NZBGet', 'indexer': 'Idx', 'outputPath': '/dl/x.nzb',
                'downloadId': 'SAB-abc123',
                'estimatedCompletionTime': '2026-01-01T00:00:00Z',
                'statusMessages': [{'title': 'Download failed', 'messages': ['nope'], 'extra': 1}],
                'quality': {'quality': {'name': 'HDTV-1080p'}}}]
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/queue', 'GET', lambda req: (200, paged(records, total=30)))])
    result = client.queue(page_number=2, limit=10)
    assert api.params(0) == {'page': '2', 'pageSize': '10', 'includeUnknownMovieItems': 'true'}
    assert result['pagination'] == 'native' and result['total'] == 30
    assert result['offset'] == 10 and result['has_more'] is True
    assert result['items'][0]['statusMessages'] == [{'title': 'Download failed', 'messages': ['nope']}]
    assert result['items'][0]['downloadId'] == 'SAB-abc123'  # native downloader correlation id
    with pytest.raises(ValueError, match='limit'):
        client.queue(limit=101)
    client.close()


def test_radarr_history_projects_bounded_data():
    records = [{'id': 3, 'movieId': 1, 'eventType': 3, 'date': '2026-01-01T00:00:00Z',
                'sourceTitle': 'Release.Name', 'quality': {}, 'downloadId': 'nzb-9',
                'data': {'indexer': 'Idx', 'releaseGroup': 'GRP', 'downloadClient': 'NZBGet',
                'reason': 'upgrade', 'hugeBlob': 'x' * 500}}]
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/history', 'GET', lambda req: (200, paged(records)))])
    result = client.history()
    assert api.params(0) == {'page': '1', 'pageSize': '25'}
    item = result['items'][0]
    assert item['movieId'] == 1 and item['downloadId'] == 'nzb-9'
    assert set(item['data']) == {'indexer', 'releaseGroup', 'downloadClient', 'reason'}
    client.close()


def test_radarr_health_command_and_release_projection():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/health', 'GET', lambda req: (200, [
            {'id': 1, 'source': 'Indexer', 'type': 'warning',
             'message': 'upstream dump ' + 'x' * 600, 'wikiUrl': 'https://wiki.test'}])),
        (r'/api/v3/command/77', 'GET', lambda req: (200, {
            'id': 77, 'name': 'MoviesSearch', 'commandName': 'MoviesSearch', 'status': 'completed',
            'queued': 'x', 'started': 'x', 'ended': 'x', 'duration': '00:00:02',
            'body': {'movieIds': [1]}})),
        (r'/api/v3/release', 'GET', lambda req: (200, [release()]))])
    health = client.health()
    assert set(health[0]) == {'id', 'source', 'type', 'message'}
    assert len(health[0]['message']) == 512  # upstream dumps stay bounded
    command = client.command(77)
    assert command['id'] == 77 and command['name'] == 'MoviesSearch' and command['status'] == 'completed'
    result = client.releases(1, offset=0, limit=1)
    assert api.params(2) == {'movieId': '1'}
    assert result['pagination'] == 'local' and result['total'] == 1 and result['limit'] == 1
    item = result['items'][0]
    assert item['guid'] == 'guid-1' and item['approved'] is True and item['rejections'] == []
    assert 'downloadUrl' not in item and 'infoUrl' not in item  # authenticated links never leave
    with pytest.raises(ValueError, match='limit'):
        client.releases(1, limit=101)
    assert len(api.calls) == 3  # invalid paging never reaches the network
    client.close()


def test_empty_success_bodies_become_empty_results():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (204, None)),
        (r'/api/v3/health', 'GET', lambda req: (204, None))])
    assert client.releases(1)['items'] == [] and client.health() == []
    assert api.calls[0].method == 'GET' and len(api.calls) == 2
    client.close()


def test_http_error_raises_sanitized_service_error():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie', 'GET', lambda req: (500, {'message': 'internal detail http://secret'}))])
    with pytest.raises(ServiceError, match='HTTP 500') as excinfo:
        client.find('whatever')
    assert 'internal detail' not in str(excinfo.value)
    client.close()


# ------------------------------------------------------------ Radarr mutations

def test_radarr_add_posts_minimal_verified_payload_and_never_auto_searches():
    routes = [
        (r'/api/v3/movie', 'GET', lambda req: (200, [])),  # precheck: not in library
        (r'/api/v3/qualityprofile', 'GET', lambda req: (200, profiles())),
        (r'/api/v3/rootfolder', 'GET', lambda req: (200, root_folders())),
        (r'/api/v3/movie', 'POST', lambda req: (201, movie(42, tmdb=11, title='From Tmdb'))),
    ]
    client, api = make_client(RadarrClient, RADARR, routes)
    result = client.add(11, quality_profile_id=7, root_folder='/mnt/media/movies')
    assert [c.method + ' ' + c.url.path for c in api.calls] == [
        'GET /api/v3/movie', 'GET /api/v3/qualityprofile', 'GET /api/v3/rootfolder',
        'POST /api/v3/movie']
    assert api.calls[0].headers['X-Api-Key'] == 'secret-key-1'
    payload = api.bodies()[3]
    assert payload == {'tmdbId': 11, 'qualityProfileId': 7, 'rootFolderPath': '/mnt/media/movies',
                       'monitored': True, 'addOptions': {'searchForMovie': False}}
    assert result['added'] is True and result['movie']['id'] == 42
    # no MoviesSearch command was pushed after the add
    assert not any(c.url.path.startswith('/api/v3/command') for c in api.calls)
    client.close()


def test_radarr_add_existing_returns_existing_resource_without_mutation():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie', 'GET', lambda req: (200, [movie(1, monitored=False)]))])
    result = client.add(11, quality_profile_id=7, root_folder='/mnt/media/movies')
    assert result == {'added': False, 'movie': client._movie(movie(1, monitored=False))}
    assert len(api.calls) == 1 and api.calls[0].method == 'GET'
    client.close()


def test_radarr_add_rejects_unknown_profile_or_root_before_any_mutation():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie', 'GET', lambda req: (200, [])),
        (r'/api/v3/qualityprofile', 'GET', lambda req: (200, profiles())),
        (r'/api/v3/rootfolder', 'GET', lambda req: (200, root_folders())),
        (r'/api/v3/movie', 'POST', lambda req: (201, movie(9)))])
    with pytest.raises(ValueError, match='quality_profile_id'):
        client.add(11, quality_profile_id=99, root_folder='/mnt/media/movies')
    assert not any(c.method == 'POST' for c in api.calls)
    with pytest.raises(ValueError, match='root_folder'):
        client.add(11, quality_profile_id=7, root_folder='/elsewhere')
    assert not any(c.method == 'POST' for c in api.calls)
    client.close()


def test_radarr_mutations_permission_checked_before_any_network():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie', 'GET', lambda req: (200, [])),
        (r'/api/v3/movie', 'POST', lambda req: (201, movie(1))),
        (r'/api/v3/movie/editor', 'PUT', lambda req: (202, [movie(1)])),
        (r'/api/v3/command', 'POST', lambda req: (201, {'id': 5, 'name': 'MoviesSearch', 'status': 'queued'})),
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))],
        allow_writes=False)
    for call in (lambda: client.add(11, 7, '/mnt/media/movies'),
                 lambda: client.monitor(1, True),
                 lambda: client.search(1),
                 lambda: client.grab(1, 'guid-1', 3)):
        with pytest.raises(PermissionError, match='writes are disabled'):
            call()
    assert api.calls == []  # no request at all, not even preflight reads
    client.close()


def test_radarr_monitor_uses_editor_endpoint_with_monitored_only_payload():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie/editor', 'PUT', lambda req: (202, [movie(1, monitored=False)]))])
    result = client.monitor('1', False)
    assert api.calls[0].method == 'PUT' and api.path(0) == '/api/v3/movie/editor'
    assert api.bodies()[0] == {'movieIds': [1], 'monitored': False}
    assert result['monitored'] is False and result['movies'][0]['monitored'] is False
    client.close()


def test_radarr_search_pushes_movies_search_command():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie/1', 'GET', lambda req: (200, movie(1))),
        (r'/api/v3/command', 'POST', lambda req: (201, {'id': 5, 'name': 'MoviesSearch',
                                                        'status': 'queued'}))])
    result = client.search(1)
    assert api.bodies()[1] == {'name': 'MoviesSearch', 'movieIds': [1]}
    assert result == {'commandId': 5, 'name': 'MoviesSearch', 'status': 'queued'}
    client.close()


def test_radarr_search_missing_movie_fails_before_command():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/movie/404', 'GET', lambda req: (404, {'message': 'Not Found'})),
        (r'/api/v3/command', 'POST', lambda req: (201, {'id': 5, 'name': 'MoviesSearch'}))])
    with pytest.raises(ServiceError, match='HTTP 404'):
        client.search(404)
    assert not any(c.url.path.startswith('/api/v3/command') for c in api.calls)
    client.close()


def test_radarr_grab_requeries_then_posts_verified_release():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [release(guid='other'),
                                                       release(guid='guid-1')])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))])
    result = client.grab(1, 'guid-1', '3')
    assert api.calls[0].method == 'GET' and api.params(0) == {'movieId': '1'}
    assert api.bodies()[1] == {'guid': 'guid-1', 'indexerId': 3, 'movieId': 1}
    assert result == {'grabbed': True, 'guid': 'guid-1', 'indexerId': 3}
    client.close()


def test_radarr_grab_refuses_stale_or_wrong_indexer_release():
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [release(guid='guid-1')])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))])
    with pytest.raises(ValueError, match='stale'):
        client.grab(1, 'gone-guid', 3)
    with pytest.raises(ValueError, match='stale'):
        client.grab(1, 'guid-1', 9)
    with pytest.raises(ValueError, match='guid'):
        client.grab(1, 'g' * 2049, 3)  # native guid kept intact when valid; oversized refused
    assert len(api.calls) == 2 and not any(c.method == 'POST' for c in api.calls)
    client.close()


def test_radarr_grab_refuses_rejected_release_without_forcing():
    rejected = release(approved=False, rejected=True, temporarilyRejected=False,
                       rejections=['Quality not wanted', 'Indexer blocked'])
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [rejected])),
        (r'/api/v3/release', 'POST', lambda req: (200, rejected))])
    with pytest.raises(ValueError) as excinfo:
        client.grab(1, 'guid-1', 3)
    assert 'not approved' in str(excinfo.value)
    assert 'Quality not wanted' not in str(excinfo.value)  # no upstream body echo
    assert not any(c.method == 'POST' for c in api.calls)
    client.close()


# ---------------------------------------------------------------- Sonarr reads

def test_sonarr_find_catalog_and_lookup():
    routes = [
        (r'/api/v3/series', 'GET', lambda req: (200, [series(1, title='Alpha'),
                                                      series(2, title='Beta')])),
        (r'/api/v3/series/lookup', 'GET', lambda req: (200, [series(0, tvdb=77, title='Looked')]))]
    client, api = make_client(SonarrClient, SONARR, routes)
    result = client.find('alp')
    assert api.path(0) == '/api/v3/series' and result['total'] == 1
    assert result['items'][0]['seasons'] == [{'seasonNumber': 0, 'monitored': False},
                                             {'seasonNumber': 1, 'monitored': True},
                                             {'seasonNumber': 2, 'monitored': False}]
    lookup = client.find('77', source='lookup')
    assert api.params(1) == {'term': '77'} and lookup['items'][0]['tvdbId'] == 77
    with pytest.raises(ValueError, match='query'):
        client.find('q' * 513)
    assert len(api.calls) == 2  # invalid input never reaches the network
    client.close()


def test_sonarr_get_and_episodes_pagination():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/series/5', 'GET', lambda req: (200, series(5))),
        (r'/api/v3/episode', 'GET', lambda req: (200, [episode(i, series_id=5)
                                                       for i in range(101, 111)]))])
    got = client.get('5')
    assert got['tvdbId'] == 999 and got['path'] == '/mnt/media/tv/example-show'
    page = client.episodes(5, offset=5, limit=3)
    assert api.params(1) == {'seriesId': '5'}
    assert page['pagination'] == 'local' and page['total'] == 10
    assert [i['id'] for i in page['items']] == [106, 107, 108]
    assert page['has_more'] is True
    assert set(page['items'][0]) <= {'id', 'seriesId', 'seasonNumber', 'episodeNumber', 'title',
                                     'monitored', 'hasFile', 'airDateUtc'}
    client.close()


def test_sonarr_missing_projects_series_title_with_native_paging():
    records = [episode(101, series_id=5, series={'id': 5, 'title': 'Example Show'})]
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/wanted/missing', 'GET', lambda req: (200, paged(records, total=12)))])
    result = client.missing(page_number=2, limit=10)
    assert api.params(0) == {'page': '2', 'pageSize': '10', 'monitored': 'true',
                             'includeSeries': 'true'}
    assert result['pagination'] == 'native' and result['total'] == 12
    assert result['items'][0]['seriesTitle'] == 'Example Show'
    assert result['has_more'] is True  # offset 10, 1 record, 12 total
    client.close()


def test_sonarr_queue_and_history_project_native_fields():
    records = [{'id': 9, 'seriesId': 5, 'episodeId': 101, 'title': 'Pilot II',
                'status': 'downloading', 'trackedDownloadStatus': 'ok',
                'trackedDownloadState': 'downloading', 'size': 10, 'sizeleft': 5,
                'timeleft': '00:00:30', 'protocol': 'usenet', 'downloadClient': 'NZBGet',
                'indexer': 'Idx', 'outputPath': '/dl/x.nzb', 'statusMessages': [],
                'downloadId': 'SAB-xyz789', 'series': {'id': 5, 'title': 'Example Show'}}]
    history_records = [{'id': 3, 'seriesId': 5, 'episodeId': 101, 'eventType': 1,
                        'date': '2026-01-01T00:00:00Z', 'sourceTitle': 'Release.Name',
                        'downloadId': 'nzb-3', 'data': {'indexer': 'Idx', 'releaseGroup': 'GRP'}}]
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/queue', 'GET', lambda req: (200, paged(records))),
        (r'/api/v3/history', 'GET', lambda req: (200, paged(history_records)))])
    queue = client.queue()
    assert api.params(0) == {'page': '1', 'pageSize': '25', 'includeUnknownSeriesItems': 'true',
                             'includeSeries': 'true'}
    assert queue['items'][0]['seriesId'] == 5 and queue['items'][0]['episodeId'] == 101
    assert queue['items'][0]['downloadId'] == 'SAB-xyz789'
    history = client.history(limit=50)
    assert api.params(1) == {'page': '1', 'pageSize': '50'}
    assert history['items'][0]['seriesId'] == 5 and history['items'][0]['downloadId'] == 'nzb-3'
    assert history['items'][0]['data']['indexer'] == 'Idx'
    client.close()


# ------------------------------------------------------------ Sonarr mutations

def sonarr_add_routes(existing=()):
    def lookup(request):
        return 200, [series(0, tvdb=77, title='Example Show',
                            seasons=[{'seasonNumber': 0, 'monitored': False},
                                     {'seasonNumber': 1, 'monitored': True},
                                     {'seasonNumber': 2, 'monitored': True}])]
    return [
        (r'/api/v3/series', 'GET', lambda req: (200, list(existing))),
        (r'/api/v3/series/lookup', 'GET', lookup),
        (r'/api/v3/qualityprofile', 'GET', lambda req: (200, profiles())),
        (r'/api/v3/rootfolder', 'GET', lambda req: (200, tv_root_folders())),
        (r'/api/v3/series', 'POST', lambda req: (201, series(9, tvdb=77))),
    ]


def test_sonarr_add_looks_up_then_posts_exact_season_selection():
    client, api = make_client(SonarrClient, SONARR, sonarr_add_routes())
    result = client.add(77, quality_profile_id=7, root_folder='/mnt/media/tv', seasons=[1, 2])
    assert api.calls[0].url.params.get('tvdbId') == '77'
    assert api.params(1) == {'term': 'tvdb:77'}  # verified tvdb: lookup prefix
    payload = api.bodies()[4]
    assert payload['title'] == 'Example Show' and payload['tvdbId'] == 77
    assert payload['qualityProfileId'] == 7 and payload['rootFolderPath'] == '/mnt/media/tv'
    assert payload['monitored'] is True
    assert payload['seasons'] == [{'seasonNumber': 0, 'monitored': False},
                                  {'seasonNumber': 1, 'monitored': True},
                                  {'seasonNumber': 2, 'monitored': True}]
    assert payload['addOptions'] == {'searchForMissingEpisodes': False}
    assert result['added'] is True and result['series']['id'] == 9
    assert not any(c.url.path.startswith('/api/v3/command') for c in api.calls)
    client.close()


def test_sonarr_add_existing_returns_existing_without_mutation():
    client, api = make_client(SonarrClient, SONARR,
                              sonarr_add_routes(existing=[series(9, tvdb=77)]))
    result = client.add(77, quality_profile_id=7, root_folder='/mnt/media/tv', seasons=[1])
    assert result['added'] is False and result['series']['id'] == 9
    assert len(api.calls) == 1 and api.calls[0].method == 'GET'
    client.close()


def test_sonarr_add_validates_profile_root_and_lookup_before_mutation():
    client, api = make_client(SonarrClient, SONARR, sonarr_add_routes())
    with pytest.raises(ValueError, match='quality_profile_id'):
        client.add(77, quality_profile_id=123, root_folder='/mnt/media/tv', seasons=[1])
    with pytest.raises(ValueError, match='root_folder'):
        client.add(77, quality_profile_id=7, root_folder='/mnt/media/movies', seasons=[1])
    with pytest.raises(ValueError, match='seasons'):
        client.add(77, quality_profile_id=7, root_folder='/mnt/media/tv',
                   seasons='all')  # type: ignore[arg-type]  # runtime validation probe
    with pytest.raises(ValueError, match='bounded'):
        client.add(77, quality_profile_id=7, root_folder='/mnt/media/tv', seasons=list(range(101)))
    with pytest.raises(ValueError, match='seasons not present in lookup'):
        client.add(77, quality_profile_id=7, root_folder='/mnt/media/tv', seasons=[9])
    assert not any(c.method == 'POST' for c in api.calls)
    client.close()


def test_sonarr_monitor_preserves_unlisted_season_monitoring():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/series/5', 'GET', lambda req: (200, series(5))),
        (r'/api/v3/series/5', 'PUT', lambda req: (202, series(5)))])
    result = client.monitor(5, seasons=[2], monitored=True)
    body = api.bodies()[1]
    seasons = {s['seasonNumber']: s['monitored'] for s in body['seasons']}
    assert seasons == {0: False, 1: True, 2: True}  # only season 2 flipped
    assert body['monitored'] is True and body['path'] == '/mnt/media/tv/example-show'
    assert result['seasons'] == [{'seasonNumber': 2, 'monitored': True}]
    client.close()


def test_sonarr_monitor_rejects_unknown_seasons_without_mutation():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/series/5', 'GET', lambda req: (200, series(5))),
        (r'/api/v3/series/5', 'PUT', lambda req: (202, series(5)))])
    with pytest.raises(ValueError, match='seasons not present'):
        client.monitor(5, seasons=[9])
    assert not any(c.method == 'PUT' for c in api.calls)
    client.close()


def test_sonarr_monitor_series_flag_round_trips_full_resource():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/series/5', 'GET', lambda req: (200, series(5, monitored=True))),
        (r'/api/v3/series/5', 'PUT', lambda req: (202, series(5, monitored=False)))])
    result = client.monitor(5, monitored=False)
    body = api.bodies()[1]
    assert body['monitored'] is False
    assert body['path'] == '/mnt/media/tv/example-show'  # full resource preserved
    assert [s['monitored'] for s in body['seasons']] == [False, True, False]  # untouched
    assert result['series']['monitored'] is False and result['seasons'] == []
    client.close()


def test_sonarr_monitor_episodes_validates_ownership_then_bulk_updates():
    routes = [
        (r'/api/v3/episode', 'GET', lambda req: (200, [episode(101, series_id=5),
                                                       episode(102, series_id=5)])),
        (r'/api/v3/episode/monitor', 'PUT', lambda req: (202, []))]
    client, api = make_client(SonarrClient, SONARR, routes)
    result = client.monitor(5, episode_ids=[102, 101], monitored=False)
    assert api.calls[0].url.params.get_list('episodeIds') == ['102', '101']
    assert api.bodies()[1] == {'episodeIds': [102, 101], 'monitored': False}
    assert result['episodes'] == 2 and result['seasons'] == []
    with pytest.raises(ValueError, match='not owned by series 5'):
        client.monitor(5, episode_ids=[102, 777])
    with pytest.raises(ValueError, match='bounded'):
        client.monitor(5, episode_ids=list(range(1, 105)))
    with pytest.raises(ValueError, match='separate monitor calls'):
        client.monitor(5, seasons=[1], episode_ids=[102])
    assert len(api.calls) == 3  # 2 successful calls + 1 ownership GET; nothing further
    client.monitor(5, episode_ids=[])  # explicit empty selection is a no-op
    assert len(api.calls) == 3  # no GET, no PUT, and no accidental series-flag toggle
    client.close()


def test_sonarr_search_validates_ownership_and_bound():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/episode', 'GET', lambda req: (200, [episode(101, series_id=5),
                                                       episode(102, series_id=5)])),
        (r'/api/v3/command', 'POST', lambda req: (201, {'id': 6, 'name': 'EpisodeSearch',
                                                        'status': 'queued'}))])
    result = client.search(5, [101, 102])
    assert api.bodies()[1] == {'name': 'EpisodeSearch', 'episodeIds': [101, 102]}
    assert result == {'commandId': 6, 'name': 'EpisodeSearch', 'status': 'queued'}
    with pytest.raises(ValueError, match='not owned by series 5'):
        client.search(5, [101, 202])
    with pytest.raises(ValueError, match='bounded'):
        client.search(5, list(range(200, 301)))
    assert len(api.calls) == 3 and not any(c.url.path.startswith('/api/v3/command') and c.method == 'POST'
                                           for c in api.calls[2:])
    client.close()


def test_sonarr_grab_requires_scope_then_requeries_and_posts():
    routes = [
        (r'/api/v3/episode/101', 'GET', lambda req: (200, episode(101, series_id=5))),
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))]
    client, api = make_client(SonarrClient, SONARR, routes)
    with pytest.raises(ValueError, match='episode_id or season_number'):
        client.grab(5, 'guid-1', 3)
    assert api.calls == []
    with pytest.raises(ValueError, match='not both'):
        client.grab(5, 'guid-1', 3, episode_id=101, season_number=1)
    with pytest.raises(ValueError, match='not owned by series 6'):
        client.grab(6, 'guid-1', 3, episode_id=101)
    assert len(api.calls) == 1  # one ownership GET; no release search
    result = client.grab(5, 'guid-1', 3, episode_id=101)
    assert api.calls[1].url.path == '/api/v3/episode/101'  # ownership before search
    assert api.params(2) == {'episodeId': '101'}
    assert api.bodies()[3] == {'guid': 'guid-1', 'indexerId': 3, 'seriesId': 5, 'episodeId': 101}
    assert result['grabbed'] is True
    client.close()


def test_sonarr_releases_validate_episode_ownership_and_scope():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/episode/101', 'GET', lambda req: (200, episode(101, series_id=5))),
        (r'/api/v3/episode/999', 'GET', lambda req: (404, {'message': 'Not Found'})),
        (r'/api/v3/release', 'GET', lambda req: (200, [release()]))])
    with pytest.raises(ValueError, match='not both'):
        client.releases(5, episode_id=101, season_number=2)
    with pytest.raises(ValueError, match='not owned by series 6'):
        client.releases(6, episode_id=101)
    assert len(api.calls) == 1  # one ownership GET; no release search
    result = client.releases(5, episode_id=101)
    assert api.params(2) == {'episodeId': '101'}  # call 2: release search after ownership GET
    assert result['pagination'] == 'local' and result['total'] == 1
    assert result['items'][0]['guid'] == 'guid-1'
    with pytest.raises(ServiceError, match='HTTP 404'):
        client.releases(5, episode_id=999)  # unknown episode surfaces sanitized 404
    client.close()


def test_sonarr_grab_season_scope_and_rejection_gate():
    def grab_routes(releases):
        return [
            (r'/api/v3/release', 'GET', lambda req: (200, releases)),
            (r'/api/v3/release', 'POST', lambda req: (200, releases))]
    client, api = make_client(SonarrClient, SONARR, grab_routes([release(guid='s2')]))
    client.grab(5, 's2', 3, season_number=0)
    assert api.params(0) == {'seriesId': '5', 'seasonNumber': '0'}
    assert 'episodeId' not in api.bodies()[1]
    rejected = release(guid='bad', approved=False, temporarilyRejected=True,
                       rejections=['Season pack not wanted'])
    client2, api2 = make_client(SonarrClient, SONARR, grab_routes([rejected]))
    with pytest.raises(ValueError) as excinfo:
        client2.grab(5, 'bad', 3, season_number=2)
    assert 'not approved' in str(excinfo.value)
    assert 'Season pack not wanted' not in str(excinfo.value)  # no upstream body echo
    assert not any(c.method == 'POST' for c in api2.calls)
    client.close()
    client2.close()


def test_sonarr_grab_rejects_negative_season_and_stale_guid():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))])
    with pytest.raises(ValueError, match='season_number'):
        client.grab(5, 'guid-1', 3, season_number=-1)
    with pytest.raises(ValueError, match='guid'):
        client.grab(5, 'g' * 2049, 3, season_number=1)
    with pytest.raises(ValueError, match='stale'):
        client.grab(5, 'missing', 3, season_number=1)
    assert len(api.calls) == 1 and not any(c.method == 'POST' for c in api.calls)
    client.close()


def test_sonarr_mutations_permission_checked_before_any_network():
    client, api = make_client(SonarrClient, SONARR, [
        (r'/api/v3/series', 'GET', lambda req: (200, [])),
        (r'/api/v3/series', 'POST', lambda req: (201, series(1))),
        (r'/api/v3/series/5', 'GET', lambda req: (200, series(5))),
        (r'/api/v3/series/5', 'PUT', lambda req: (202, series(5))),
        (r'/api/v3/episode', 'GET', lambda req: (200, [])),
        (r'/api/v3/episode/monitor', 'PUT', lambda req: (202, [])),
        (r'/api/v3/command', 'POST', lambda req: (201, {'id': 6, 'name': 'EpisodeSearch'})),
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))],
        allow_writes=False)
    for call in (lambda: client.add(77, 7, '/mnt/media/tv', [1]),
                 lambda: client.monitor(5, seasons=[1]),
                 lambda: client.monitor(5, episode_ids=[101]),
                 lambda: client.monitor(5, monitored=False),
                 lambda: client.search(5, [101]),
                 lambda: client.grab(5, 'guid-1', 3, episode_id=101)):
        with pytest.raises(PermissionError, match='writes are disabled'):
            call()
    assert api.calls == []  # permission gate precedes preflight reads too
    client.close()


# ------------------------------------------------- target search deadline


def test_target_search_uses_search_deadline_and_metadata_uses_request_deadline():
    """Only target search carries the longer deadline; metadata keeps the short one.

    Regression for live release searches failing with "request timed out": the
    deadline is per operation, not per service. Asserted on the deadline actually
    handed to the transport, because MockTransport cannot model socket timeouts.
    """
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/movie/1', 'GET', lambda req: (200, movie(1)))])
    config = ServiceConfig('radarr', RADARR, api_key='secret-key-1', timeout=15.0,
                           allow_writes=True, search_timeout=120.0)
    client.close()
    client = RadarrClient(config, transport=api.transport())
    client.releases(1, limit=1)
    assert api.calls[0].extensions['timeout'] == {'connect': 120.0, 'read': 120.0,
                                                  'write': 120.0, 'pool': 120.0}
    client.get(1)
    assert api.calls[1].extensions['timeout']['read'] == 15.0  # client default, not the search budget
    client.close()


def test_grab_requery_uses_the_search_deadline():
    """grab() requeries releases first, so it must not inherit the short deadline."""
    client, api = make_client(RadarrClient, RADARR, [
        (r'/api/v3/release', 'GET', lambda req: (200, [release()])),
        (r'/api/v3/release', 'POST', lambda req: (200, release()))])
    config = ServiceConfig('radarr', RADARR, api_key='secret-key-1', timeout=15.0,
                           allow_writes=True, search_timeout=120.0)
    client.close()
    client = RadarrClient(config, transport=api.transport())
    assert client.grab(1, 'guid-1', 3)['grabbed'] is True
    assert api.calls[0].method == 'GET'
    assert api.calls[0].extensions['timeout']['read'] == 120.0
    assert api.calls[1].extensions['timeout']['read'] == 15.0  # mutation keeps the default
    client.close()
