import json

import httpx
import pytest

from jellyseerr_mcp.config import AppConfig, SERVICES, ServiceConfig, load_config
from jellyseerr_mcp.clients.arr import RadarrClient
from jellyseerr_mcp.http import ServiceClient, ServiceError, positive_id, validate_nzb


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr('jellyseerr_mcp.config.load_dotenv', lambda **kwargs: None)
    for service in SERVICES:
        for suffix in ('URL', 'API_KEY', 'USERNAME', 'PASSWORD', 'TIMEOUT', 'ALLOW_WRITES'):
            monkeypatch.delenv(service.upper() + '_' + suffix, raising=False)
    for name in ('MCP_REQUEST_TIMEOUT', 'MCP_READ_ONLY', 'MCP_ALLOW_RAW_READ', 'MCP_SEARCH_TIMEOUT',
                 'NZBHYDRA_ALLOWED_REDIRECT_ORIGINS'):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize('name', SERVICES)
def test_each_service_alone(clean_env, name):
    clean_env.setenv(name.upper() + '_URL', 'https://example.test/proxy')
    if name == 'nzbget':
        clean_env.setenv('NZBGET_USERNAME', 'user')
        clean_env.setenv('NZBGET_PASSWORD', 'password-secret')
    else:
        clean_env.setenv(name.upper() + '_API_KEY', 'api-secret')
    config = load_config()
    assert set(config.service_configs()) == {name}
    assert not config.service_configs()[name].allow_writes
    assert 'api-secret' not in repr(config)
    assert 'password-secret' not in repr(config)


def test_partial_and_absent_configuration(clean_env):
    with pytest.raises(ValueError, match='at least one'):
        load_config()
    clean_env.setenv('SONARR_URL', 'https://example.test')
    with pytest.raises(ValueError, match='SONARR_API_KEY'):
        load_config()


@pytest.mark.parametrize('value', ['nan', 'inf', '0', '-1', '301', 'bad'])
def test_invalid_timeout(clean_env, value):
    clean_env.setenv('MCP_REQUEST_TIMEOUT', value)
    with pytest.raises(ValueError, match='MCP_REQUEST_TIMEOUT'):
        load_config()


def configured(clean_env, **env):
    clean_env.setenv('RADARR_URL', 'https://radarr.test')
    clean_env.setenv('RADARR_API_KEY', 'api-secret')
    for key, value in env.items():
        clean_env.setenv(key, value)
    return load_config().service_configs()


def test_search_timeout_defaults_and_overrides(clean_env):
    assert configured(clean_env)['radarr'].search_timeout == 120.0
    clean_env.setenv('MCP_SEARCH_TIMEOUT', '200')
    assert configured(clean_env)['radarr'].search_timeout == 200.0
    clean_env.setenv('RADARR_SEARCH_TIMEOUT', '45')
    assert configured(clean_env)['radarr'].search_timeout == 45.0  # per-service wins


def test_search_timeout_ignores_prefixes_for_services_without_indexer_fanout(clean_env):
    clean_env.setenv('NZBGET_URL', 'https://nzbget.test')
    clean_env.setenv('NZBGET_USERNAME', 'user')
    clean_env.setenv('NZBGET_PASSWORD', 'password-secret')
    clean_env.setenv('NZBGET_SEARCH_TIMEOUT', '45')
    services = configured(clean_env)
    assert services['radarr'].search_timeout == 120.0  # shared default, not the stray value
    assert services['nzbget'].search_timeout == 120.0  # a stray override is ignored


def test_search_timeout_zero_disables_the_bound(clean_env):
    assert configured(clean_env, MCP_SEARCH_TIMEOUT='0')['radarr'].search_timeout == 0.0
    assert configured(clean_env, RADARR_SEARCH_TIMEOUT='0')['radarr'].search_timeout == 0.0


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '301', 'bad'])
def test_invalid_search_timeout(clean_env, value):
    with pytest.raises(ValueError, match='SEARCH_TIMEOUT'):
        configured(clean_env, RADARR_SEARCH_TIMEOUT=value)


def test_write_optins(clean_env):
    clean_env.setenv('SONARR_URL', 'https://example.test')
    clean_env.setenv('SONARR_API_KEY', 'secret')
    clean_env.setenv('SONARR_ALLOW_WRITES', 'true')
    assert not load_config().service_configs()['sonarr'].allow_writes
    clean_env.setenv('MCP_READ_ONLY', 'false')
    assert load_config().service_configs()['sonarr'].allow_writes
    clean_env.setenv('MCP_READ_ONLY', 'yes')
    with pytest.raises(ValueError, match='true or false'):
        load_config()
    direct = AppConfig(services={'sonarr': ServiceConfig('sonarr', 'https://example.test', allow_writes=True)})
    assert not direct.service_configs()['sonarr'].allow_writes


@pytest.mark.parametrize('url', ['ftp://host', 'https://user:password@host', 'https://host?apikey=secret',
                                     'https://host/#fragment', 'https://host:bad', 'https://host/../api',
                                     'https://host\\evil', 'https://host /'])
def test_invalid_urls(url):
    with pytest.raises(ValueError):
        ServiceConfig('sonarr', url)


def test_timeout_can_be_disabled_and_read_timeout_honors_a_per_request_deadline(clean_env):
    """search_timeout=0 disables the bound; the per-request deadline is enforced.

    Uses a real listening socket that accepts and never answers, because an
    httpx.MockTransport is handed an already-complete response and can never
    raise ReadTimeout.
    """
    import socket
    import threading
    import time as time_module

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_forever():
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            connection.recv(4096)  # read the request, then stall without replying

    threading.Thread(target=accept_forever, daemon=True).start()
    clean_env.setenv('RADARR_URL', f'http://127.0.0.1:{port}')
    clean_env.setenv('RADARR_API_KEY', 'api-secret')
    clean_env.setenv('MCP_SEARCH_TIMEOUT', '0')
    clean_env.setenv('MCP_REQUEST_TIMEOUT', '1')
    try:
        client = RadarrClient(load_config().service_configs()['radarr'])
        started = time_module.monotonic()
        with pytest.raises(ServiceError, match='timed out'):
            client.releases(1)
        elapsed = time_module.monotonic() - started
        client.close()
    finally:
        listener.close()
    assert 0.5 < elapsed < 3.0  # ~1s deadline: not instant, not the 120s default


def test_safe_http_errors_and_no_retry():
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout('secret-api-key on https://host/?apikey=secret', request=request)
    client = ServiceClient(ServiceConfig('sonarr', 'https://example.test', api_key='secret-api-key', allow_writes=True),
                           httpx.MockTransport(handler))
    with pytest.raises(ServiceError, match='outcome uncertain') as exc:
        client.request_json('POST', 'api/v3/command', json={}, mutation=True)
    assert len(requests) == 1
    assert 'secret-api-key' not in str(exc.value)
    assert exc.value.__suppress_context__
    client.close()


def test_readonly_transport_zero_requests():
    client = ServiceClient(ServiceConfig('sonarr', 'https://example.test'),
                           httpx.MockTransport(lambda request: pytest.fail('network call')))
    with pytest.raises(PermissionError):
        client.request_json('POST', 'api/v3/command', mutation=True)
    client.close()


def test_response_size_and_secret_projection(monkeypatch):
    client = ServiceClient(ServiceConfig('sonarr', 'https://example.test', api_key='secret-api-key'),
        httpx.MockTransport(lambda request: httpx.Response(200, json={'apiKey':'another-secret', 'name':'a',
            'message':'url?apikey=secret-api-key'})))
    result = client.request_json('GET', 'api/v3/status')
    assert 'apiKey' not in result
    assert 'secret-api-key' not in json.dumps(result)
    monkeypatch.setattr('jellyseerr_mcp.http.MAX_RESPONSE_BYTES', 4)
    with pytest.raises(ServiceError, match='limit'):
        client.request_json('GET', 'api/v3/status')
    client.close()


def test_redirects_drop_credentials_and_refuse_unknown_origins():
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.host == 'hydra.test':
            return httpx.Response(302, headers={'Location':'https://indexer.test/nzb'})
        assert 'apikey' not in str(request.url)
        assert 'Authorization' not in request.headers
        assert 'X-Api-Key' not in request.headers
        assert 'cookie' not in request.headers
        return httpx.Response(200, content=b'OK')
    transport = httpx.MockTransport(handler)
    client = ServiceClient(ServiceConfig('nzbhydra', 'https://hydra.test', api_key='secret',
        allowed_redirect_origins=('https://indexer.test',)), transport)
    assert client.request_bytes('GET', 'api', params={'apikey':'secret'}, follow_allowed_redirects=True)[0] == b'OK'
    assert len(seen) == 2
    client.close()
    denied = ServiceClient(ServiceConfig('nzbhydra', 'https://hydra.test', api_key='secret'), transport)
    with pytest.raises(ServiceError, match='redirect'):
        denied.request_bytes('GET', 'api', follow_allowed_redirects=True)
    denied.close()


@pytest.mark.parametrize('path', ['https://evil.test', '//evil.test', '../config', '%2e%2e/config', 'api\\config'])
def test_no_arbitrary_endpoint(path):
    client = ServiceClient(ServiceConfig('sonarr', 'https://example.test'),
                           httpx.MockTransport(lambda request: pytest.fail('network call')))
    with pytest.raises(ValueError):
        client.request_json('GET', path)
    client.close()


@pytest.mark.parametrize('content', [b'<html/>', b'<!DOCTYPE nzb [<!ENTITY x "abc">]><nzb/>',
    '<nzb/>'.encode('utf-16'), b'<nzb/>', b'<nzb><file/></nzb>'])
def test_invalid_nzb(content):
    with pytest.raises(ValueError):
        validate_nzb(content)


def test_valid_nzb_and_ids():
    validate_nzb(b'<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file><segments><segment>id</segment></segments></file></nzb>')
    assert positive_id('15') == 15
    for value in (True, 1.5, '1/../2', '1,2', 0):
        with pytest.raises(ValueError):
            positive_id(value)


def test_dotenv_uses_working_directory_and_preserves_environment(clean_env, tmp_path):
    from dotenv import load_dotenv as actual_loader
    clean_env.setattr('jellyseerr_mcp.config.load_dotenv', actual_loader)
    clean_env.chdir(tmp_path)
    (tmp_path / '.env').write_text('SONARR_URL=https://file.test\nSONARR_API_KEY=file-key\n')
    clean_env.setenv('SONARR_API_KEY', 'process-key')
    config = load_config().service_configs()['sonarr']
    assert config.url == 'https://file.test' and config.api_key == 'process-key'


def test_allowlist_rejects_paths_and_config_keys_must_match():
    with pytest.raises(ValueError, match='origins'):
        ServiceConfig('nzbhydra','https://hydra.test',allowed_redirect_origins=('https://indexer.test/path',))
    with pytest.raises(ValueError, match='registry keys'):
        AppConfig(services={'radarr': ServiceConfig('sonarr','https://sonarr.test')}).service_configs()


def test_http_query_credentials_never_reach_debug_logs(caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG,logger='httpx')
    client = ServiceClient(ServiceConfig('sabnzbd','https://sab.test',api_key='secret-query-key'),
                           httpx.MockTransport(lambda request: httpx.Response(200,json={'queue':{}})))
    client.request_json('GET','api',params={'apikey':'secret-query-key'})
    assert 'secret-query-key' not in caplog.text
    client.close()
