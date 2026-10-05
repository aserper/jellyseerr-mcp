"""Cross-service dispatch tests: permissions apply before NZB quota or submission."""
import json

import httpx
import pytest

from jellyseerr_mcp.config import AppConfig, ServiceConfig
from jellyseerr_mcp.http import ServiceClient
from jellyseerr_mcp.server import build_server

NZB = b'<nzb><file><segments><segment>message-id</segment></segments></file></nzb>'


class Hydra(ServiceClient):
    def capabilities(self) -> dict:
        return {'search':['search']}
    def search(self, query: str) -> dict:
        return {'items':[{'id':'5','title':query}]}
    def get_nzb(self, result_id: str) -> tuple[bytes,str]:
        self.fetches.append(result_id)
        return NZB, 'result.nzb'


class Downloader(ServiceClient):
    def status(self) -> dict:
        return {'state':'paused'}
    def queue(self, offset: int = 0, limit: int = 25) -> dict:
        return {'items':[]}
    def history(self, offset: int = 0, limit: int = 25) -> dict:
        return {'items':[]}
    def categories(self) -> list[str]:
        return ['movies']
    def pause(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self.require_write()
        return {'id':job_id}
    def resume(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self.require_write()
        return {'id':job_id}
    def retry(self, job_id: str) -> dict:
        self.require_write()
        return {'id':job_id}
    def submit(self, content: bytes, filename: str, category: str) -> dict:
        self.require_write()
        assert content == NZB and filename == 'result.nzb' and category == 'movies'
        self.submissions.append(category)
        return {'job_id':'SABnzbd_nzo_one'}


def fixture(writable=False):
    transport = httpx.MockTransport(lambda request: pytest.fail('unexpected HTTP'))
    hydra = Hydra(ServiceConfig('nzbhydra','https://hydra.test',api_key='hydra-secret'),transport)
    hydra.fetches = []
    downloader = Downloader(ServiceConfig('sabnzbd','https://sab.test',api_key='sab-secret',allow_writes=writable),transport)
    downloader.submissions = []
    app = build_server(AppConfig(read_only=not writable),{'nzbhydra':hydra,'sabnzbd':downloader})
    return app,hydra,downloader


@pytest.mark.asyncio
async def test_readonly_handoff_never_fetches():
    app,hydra,downloader = fixture()
    try:
        with pytest.raises(Exception,match='read-only'):
            await app.call_tool('nzbhydra_download_result',{'result_id':'5','backend':'sabnzbd','category':'movies'})
        assert hydra.fetches == [] and downloader.submissions == []
    finally:
        hydra.close(); downloader.close()


@pytest.mark.asyncio
async def test_handoff_requires_actual_target_permission():
    app,hydra,downloader = fixture()
    config = AppConfig(read_only=False)
    app = build_server(config,{'nzbhydra':hydra,'sabnzbd':downloader})
    try:
        with pytest.raises(Exception,match='writes are disabled'):
            await app.call_tool('nzbhydra_download_result',{'result_id':'5','backend':'sabnzbd','category':'movies'})
        assert hydra.fetches == []
    finally:
        hydra.close(); downloader.close()


@pytest.mark.asyncio
async def test_explicit_handoff_not_claimed_as_managed_import():
    app,hydra,downloader = fixture(True)
    try:
        result = await app.call_tool('nzbhydra_download_result',{'result_id':'5','backend':'sabnzbd','category':'movies'})
        data = result[1] if isinstance(result,tuple) else json.loads(result[0].text)
        assert data['submission']['job_id'] == 'SABnzbd_nzo_one'
        assert data['managed_import'] is False
        assert hydra.fetches == ['5'] and downloader.submissions == ['movies']
        with pytest.raises(Exception):
            await app.call_tool('nzbhydra_download_result',{'result_id':'5','backend':'nzbget','category':'movies'})
        with pytest.raises(Exception):
            await app.call_tool('nzbhydra_download_result',{'result_id':'5','backend':'sabnzbd','category':'scripts'})
        assert hydra.fetches == ['5']
    finally:
        hydra.close(); downloader.close()
