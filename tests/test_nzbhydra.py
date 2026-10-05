"""Tests for the NZBHydra2 public Newznab client (jellyseerr_mcp.clients.nzbhydra).

All HTTP is mocked via httpx.MockTransport; requests are recorded and asserted
after the call so assertion failures are not masked by client error wrapping.
XML/JSON shapes mirror the verified upstream contracts (ExternalApi.java,
NewznabJsonTransformer.java, docs/external-api.md).
"""
from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest

from jellyseerr_mcp.config import ServiceConfig
from jellyseerr_mcp.clients.nzbhydra import NZBHydraClient
from jellyseerr_mcp.http import ServiceError

HYDRA_URL = "http://hydra.test:5076"
API_KEY = "test-key-123"


def make_config(**overrides: Any) -> ServiceConfig:
    values: dict[str, Any] = dict(
        name="nzbhydra",
        url=HYDRA_URL,
        api_key=API_KEY,
        timeout=10.0,
        allow_writes=False,
        allowed_redirect_origins=(),
    )
    values.update(overrides)
    return ServiceConfig(**values)


class Fake:
    """MockTransport handler that records every request."""

    def __init__(self, responder: Any) -> None:
        self.seen: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self._responder(request)

    def params(self, index: int = 0) -> httpx.QueryParams:
        return self.seen[index].url.params


def make_client(handler: Fake, **overrides: Any) -> NZBHydraClient:
    return NZBHydraClient(make_config(**overrides), transport=httpx.MockTransport(handler))


CAPS_JSON: dict[str, Any] = {
    "server": {
        "@attributes": {
            "title": "NZBHydra 2",
            "appversion": "5.1.0",
            "version": "5.1.0",
            "email": "theotherp@posteo.net",
            "url": "https://github.com/theotherp/nzbhydra2",
        }
    },
    "limits": {"@attributes": {"max": "100", "default": "100"}},
    "registration": {"@attributes": {"open": "no", "available": "no"}},
    "searching": {
        "search": {
            "@attributes": {
                "available": "yes",
                "supportedParams": "q,cat,limit,offset,minage,maxage,minsize,maxsize",
            }
        },
        "tv-search": {
            "@attributes": {
                "available": "yes",
                "supportedParams": "q,season,ep,cat,limit,offset,minage,maxage,minsize,maxsize,tvdbid,imdbid",
            }
        },
        "movie-search": {
            "@attributes": {
                "available": "yes",
                "supportedParams": "q,cat,limit,offset,minage,maxage,minsize,maxsize,imdbid,tmdbid",
            }
        },
        "audio-search": {"@attributes": {"available": "no", "supportedParams": ""}},
        "book-search": {"@attributes": {"available": "no", "supportedParams": ""}},
    },
    "categories": {
        "category": [
            {
                "@attributes": {"id": "2000", "name": "Movies"},
                "subcat": [{"@attributes": {"id": "2030", "name": "Movies SD"}}],
            },
            {"@attributes": {"id": "5000", "name": "TV"}},
        ]
    },
}

CAPS_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<caps>"
    b'<server title="NZBHydra 2" appversion="5.1.0" version="5.1.0" '
    b'email="theotherp@posteo.net" url="https://github.com/theotherp/nzbhydra2"/>'
    b'<retention days="3000"/>'
    b'<limits max="100" default="100"/>'
    b"<searching>"
    b'<search available="yes" supportedParams="q,cat,limit,offset,minage,maxage,minsize,maxsize"/>'
    b'<tv-search available="no" supportedParams=""/>'
    b'<movie-search available="yes" supportedParams="q,cat,imdbid,tmdbid"/>'
    b'<audio-search available="no"/>'
    b'<book-search available="no"/>'
    b"</searching>"
    b"<categories>"
    b'<category id="2000" name="Movies"><subcat id="2030" name="Movies SD"/></category>'
    b'<category id="5000" name="TV"/>'
    b"</categories>"
    b"</caps>"
)


def search_json_item(identifier: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": f"Release {identifier}",
        "guid": identifier,
        "id": identifier,
        "link": f"{HYDRA_URL}/api?apikey={API_KEY}&t=get&id={identifier}",
        "pubDate": "2026-01-02T03:04:05+00:00",
        "category": "Linux Distro",
        "description": f"<p>raw body {identifier} apikey={API_KEY}</p>",
        "comments": "http://indexer.test/comments/1",
        "enclosure": {
            "@attributes": {
                "url": "http://indexer.test/dl?apikey=secret",
                "length": 1048576,
                "type": "application/x-nzb",
            }
        },
        "attr": [
            {"@attributes": {"name": "size", "value": "1048576"}},
            {"@attributes": {"name": "grabs", "value": "12"}},
            {"@attributes": {"name": "hydraIndexerName", "value": "IndexerOne"}},
            {"@attributes": {"name": "hydraIndexerScore", "value": "50"}},
            {"@attributes": {"name": "poster", "value": "poster@test"}},
        ],
    }
    item.update(overrides)
    return item


SEARCH_JSON: dict[str, Any] = {
    "channel": {
        "title": "NZBHydra 2",
        "response": {"@attributes": {"offset": 0, "total": 2}},
        "item": [search_json_item("12345"), search_json_item("67890", title="Big Release")],
    }
}

SEARCH_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/XML/feeds">'
    b"<channel>"
    b"<title>NZBHydra 2</title>"
    b'<newznab:response offset="0" total="1"/>'
    b"<item>"
    b"<title>Release 12345</title>"
    b'<guid isPermaLink="false">12345</guid>'
    b"<link>http://hydra.test:5076/api?apikey=test-key-123&amp;t=get&amp;id=12345</link>"
    b"<pubDate>Tue, 02 Jan 2026 03:04:05 +0000</pubDate>"
    b"<category>Linux Distro</category>"
    b"<description>raw body with apikey=test-key-123</description>"
    b'<enclosure url="http://indexer.test/dl?apikey=secret" length="1048576" type="application/x-nzb"/>'
    b'<newznab:attr name="size" value="1048576"/>'
    b'<newznab:attr name="grabs" value="12"/>'
    b'<newznab:attr name="hydraIndexerName" value="IndexerOne"/>'
    b"</item>"
    b"</channel>"
    b"</rss>"
)

NZB_BYTES = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<nzb xmlns="http://www.newznab.com/DTD/2010/XML/feeds">'
    b'<file poster="poster@test" date="1767225600" subject="Ubuntu 24.04">'
    b"<groups><group>alt.binaries.test</group></groups>"
    b'<segments><segment bytes="1024" number="1">abcdef</segment></segments>'
    b"</file></nzb>"
)


# -- capabilities ------------------------------------------------------


def test_capabilities_parses_json_and_caches():
    fake = Fake(lambda request: httpx.Response(200, json=CAPS_JSON))
    client = make_client(fake)
    caps = client.capabilities()
    assert caps["searching"]["tvsearch"]["available"] is True
    assert "tvdbid" in caps["searching"]["tvsearch"]["supported_params"]
    assert caps["searching"]["movie"]["available"] is True
    assert caps["searching"]["search"]["available"] is True
    assert caps["limits"] == {"max": 100, "default": 100}
    assert caps["categories"][0] == {
        "id": 2000,
        "name": "Movies",
        "subcats": [{"id": 2030, "name": "Movies SD"}],
    }
    assert fake.params()["t"] == "caps"
    assert fake.params()["apikey"] == API_KEY
    client.capabilities()
    assert len(fake.seen) == 1  # cached; no repeated call
    fresh = client.capabilities(refresh=True)
    assert len(fake.seen) == 2
    assert fresh["searching"]["movie"]["available"] is True
    client.close()


def test_capabilities_accepts_xml_response_for_json_request():
    fake = Fake(lambda request: httpx.Response(200, content=CAPS_XML, headers={"content-type": "application/xml"}))
    caps = make_client(fake).capabilities()
    assert caps["searching"]["tvsearch"]["available"] is False  # XML variant says no
    assert caps["searching"]["movie"]["supported_params"] == ["q", "cat", "imdbid", "tmdbid"]
    assert caps["retention_days"] == 3000
    assert caps["limits"] == {"max": 100, "default": 100}


def test_capabilities_rejects_dtd_entities():
    unsafe = (
        b'<?xml version="1.0"?><!DOCTYPE caps [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<caps><searching><search available='yes'/></searching></caps>"
    )
    fake = Fake(lambda request: httpx.Response(200, content=unsafe, headers={"content-type": "application/xml"}))
    with pytest.raises(ServiceError, match="invalid or unsafe"):
        make_client(fake).capabilities()



def test_capabilities_never_exposes_server_metadata():
    fake = Fake(lambda request: httpx.Response(200, json=CAPS_JSON))
    caps = make_client(fake).capabilities()
    assert set(caps["server"]) <= {"title", "version", "appversion"}
    assert "email" not in caps["server"] and "url" not in caps["server"] and "image" not in caps["server"]


# -- search ------------------------------------------------------------


def test_search_json_results_are_compact_and_redacted():
    fake = Fake(lambda request: httpx.Response(200, json=SEARCH_JSON))
    result = make_client(fake).search(query="Ubuntu 24.04")
    assert fake.params()["t"] == "search"
    assert fake.params()["o"] == "json"
    assert fake.params()["apikey"] == API_KEY
    assert fake.params()["q"] == "Ubuntu 24.04"
    assert fake.params()["offset"] == "0" and fake.params()["limit"] == "25"
    assert result["total"] == 2 and result["offset"] == 0 and result["limit"] == 25
    first = result["results"][0]
    assert first["id"] == "12345"
    assert first["title"] == "Release 12345"
    assert first["size_bytes"] == 1048576
    assert first["indexer"] == "IndexerOne"
    assert first["grabs"] == 12
    assert first["date"] == "2026-01-02T03:04:05+00:00"
    assert first["category"] == "Linux Distro"
    assert result["results"][1]["size_bytes"] == 1048576
    for forbidden in ("link", "description", "comments", "enclosure", "url", "poster"):
        assert all(forbidden not in item for item in result["results"])
    serialized = json.dumps(result)
    assert API_KEY not in serialized
    assert "indexer.test" not in serialized


def test_search_parses_xml_response():
    fake = Fake(lambda request: httpx.Response(200, content=SEARCH_XML, headers={"content-type": "application/xml"}))
    result = make_client(fake).search(query="Ubuntu")
    item = result["results"][0]
    assert item["id"] == "12345" and item["size_bytes"] == 1048576
    assert item["indexer"] == "IndexerOne" and item["grabs"] == 12
    assert item["category"] == "Linux Distro"
    assert result["total"] == 1
    serialized = json.dumps(result)
    assert API_KEY not in serialized and "indexer.test" not in serialized


def test_search_sends_typed_parameters():
    observed: dict[str, httpx.QueryParams] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        observed[request.url.params["t"]] = request.url.params
        return httpx.Response(200, json=SEARCH_JSON)

    client = make_client(Fake(responder))
    client.search(categories=[5000, 2030], indexers=["IndexerOne", "IndexerTwo"])
    client.search(search_type="movie", tmdb_id=12345, imdb_id="tt1234567")
    client.search(
        search_type="tvsearch",
        tvdb_id=121361,
        season=2,
        episode=5,
        min_age=1,
        max_age=30,
        min_size_mb=100,
        max_size_mb=5000,
    )
    assert set(observed) == {"search", "movie", "tvsearch"}
    assert observed["search"]["cat"] == "5000,2030"
    assert observed["search"]["indexers"] == "IndexerOne,IndexerTwo"
    assert "q" not in observed["search"]  # empty query omitted
    assert observed["movie"]["tmdbid"] == "12345"
    assert observed["movie"]["imdbid"] == "tt1234567"
    tv = observed["tvsearch"]
    assert tv["tvdbid"] == "121361"
    assert tv["season"] == "2" and tv["ep"] == "5"
    assert tv["minage"] == "1" and tv["maxage"] == "30"
    assert tv["minsize"] == "100" and tv["maxsize"] == "5000"


@pytest.mark.parametrize("search_type", ["book", "audio", "movies", "tv", "CAPS", ""])
def test_search_rejects_unsupported_types(search_type: str):
    fake = Fake(lambda request: httpx.Response(200, json=SEARCH_JSON))
    with pytest.raises(ValueError, match="unsupported search_type"):
        make_client(fake).search(search_type=search_type)
    assert fake.seen == []  # no request was made


@pytest.mark.parametrize(
    "kwargs",
    [
        {"season": 1},
        {"episode": 2},
        {"search_type": "movie", "season": 1},
        {"search_type": "tvsearch", "tmdb_id": 5},
        {"search_type": "movie", "tvdb_id": 5},
        {"search_type": "search", "imdb_id": "tt123"},
        {"search_type": "movie", "tmdb_id": -3},
    ],
)
def test_search_rejects_incompatible_type_combinations(kwargs: dict):
    fake = Fake(lambda request: httpx.Response(200, json=SEARCH_JSON))
    with pytest.raises(ValueError):
        make_client(fake).search(**kwargs)
    assert fake.seen == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 101},
        {"limit": 0},
        {"offset": -1},
        {"query": "x" * 513},
        {"min_age": 4000},
        {"min_age": 5, "max_age": 2},
        {"min_size_mb": 3000, "max_size_mb": 2000},
        {"categories": [0]},
        {"categories": [-5]},
        {"categories": list(range(26))},
        {"imdb_id": "abc"},
        {"indexers": ["a,b"]},
        {"tmdb_id": 1.5},
    ],
)
def test_search_rejects_out_of_bounds_inputs(kwargs: dict):
    fake = Fake(lambda request: httpx.Response(200, json=SEARCH_JSON))
    with pytest.raises(ValueError):
        make_client(fake).search(**kwargs)
    assert fake.seen == []


def test_search_validates_against_cached_caps():
    caps_no_tv = copy.deepcopy(CAPS_JSON)
    caps_no_tv["searching"]["tv-search"]["@attributes"]["available"] = "no"
    caps_no_ids = copy.deepcopy(CAPS_JSON)
    caps_no_ids["searching"]["tv-search"]["@attributes"]["supportedParams"] = "q,season,ep"

    caps_call = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.params["t"] == "caps":
            caps_call["count"] += 1
            return httpx.Response(200, json=caps_no_ids if caps_call["count"] == 1 else caps_no_tv)
        return httpx.Response(200, json=SEARCH_JSON)

    client = make_client(Fake(responder))
    client.capabilities()
    with pytest.raises(ServiceError, match="tvdbid"):
        client.search(search_type="tvsearch", tvdb_id=121361)

    client.capabilities(refresh=True)
    with pytest.raises(ServiceError, match="tvsearch search is not supported"):
        client.search(search_type="tvsearch", query="something")


@pytest.mark.parametrize(
    "payload, content_type",
    [
        (json.dumps({"code": "300", "description": "Invalid or outdated search result ID"}), "application/json"),
        (b'<?xml version="1.0"?><error code="300" description="Invalid or outdated search result ID"/>', "application/xml"),
    ],
)
def test_search_surfaces_newznab_error_envelopes(payload: Any, content_type: str):
    fake = Fake(lambda request: httpx.Response(200, content=payload, headers={"content-type": content_type}))
    with pytest.raises(ServiceError) as excinfo:
        make_client(fake).search(query="x")
    assert "300" in str(excinfo.value)
    assert "Invalid or outdated" not in str(excinfo.value)  # upstream bodies are never echoed


def test_search_surfaces_malformed_catchall_xml():
    body = b'<error code="900" description="unexpected'  # upstream catch-all can emit unclosed XML
    fake = Fake(lambda request: httpx.Response(200, content=body, headers={"content-type": "application/xml"}))
    with pytest.raises(ServiceError, match="invalid or unsafe"):
        make_client(fake).search(query="x")


def test_search_trims_oversized_reply():
    items = [search_json_item(str(1000 + i)) for i in range(30)]
    payload = {"channel": {"response": {"@attributes": {"offset": 0, "total": 30}}, "item": items}}
    fake = Fake(lambda request: httpx.Response(200, json=payload))
    client = make_client(fake)
    result = client.search(query="x")
    assert len(result["results"]) == 25
    assert fake.params()["limit"] == "25"
    assert result["total"] == 30
    result10 = client.search(query="x", offset=10, limit=10)
    assert len(result10["results"]) == 10 and result10["limit"] == 10
    assert fake.params(1)["offset"] == "10" and fake.params(1)["limit"] == "10"


def test_search_zero_results():
    payload = {"channel": {"response": {"@attributes": {"offset": 0, "total": 0}}}}
    fake = Fake(lambda request: httpx.Response(200, json=payload))
    result = make_client(fake).search(query="nothing")
    assert result["results"] == [] and result["total"] == 0


def test_search_wraps_http_failures_sanitized():
    fake = Fake(lambda request: httpx.Response(500, text="boom with apikey=" + API_KEY))
    with pytest.raises(ServiceError) as excinfo:
        make_client(fake).search(query="x")
    assert API_KEY not in str(excinfo.value)


# -- get_nzb -----------------------------------------------------------


def test_get_nzb_returns_content_and_filename():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=NZB_BYTES,
            headers={"content-type": "application/x-nzb", "content-disposition": 'attachment; filename="Ubuntu 24.04.nzb"'},
        )

    fake = Fake(responder)
    content, filename = make_client(fake).get_nzb("12345")
    assert fake.params()["t"] == "get"
    assert fake.params()["id"] == "12345"
    assert fake.params()["apikey"] == API_KEY
    assert content == NZB_BYTES
    assert filename == "Ubuntu 24.04.nzb"


def test_get_nzb_accepts_integer_result_id():
    fake = Fake(
        lambda request: httpx.Response(
            200,
            content=NZB_BYTES,
            headers={"content-disposition": "attachment; filename=plain.nzb"},
        )
    )
    content, filename = make_client(fake).get_nzb(98765)
    assert fake.params()["id"] == "98765"
    assert content == NZB_BYTES
    assert filename == "plain.nzb"


@pytest.mark.parametrize(
    "bad_id", ["", "abc", "-1", "0", "9" * 25, "12 3", "١٢٣", None, True, 12.5, ["1"]]
)
def test_get_nzb_rejects_invalid_result_ids(bad_id: Any):
    fake = Fake(lambda request: httpx.Response(200, content=NZB_BYTES))
    with pytest.raises(ValueError, match="result_id"):
        make_client(fake).get_nzb(bad_id)
    assert fake.seen == []


def test_get_nzb_rejects_unsafe_and_non_nzb_content():
    unsafe = (
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<nzb><file><segments><segment bytes='1' number='1'>a</segment></segments></file></nzb>"
    )
    wrong_root = b"<rss><channel/></rss>"
    fake = Fake(lambda request: httpx.Response(200, content=unsafe, headers={"content-type": "application/x-nzb"}))
    with pytest.raises(ServiceError, match="unsafe"):
        make_client(fake).get_nzb("12345")
    fake2 = Fake(lambda request: httpx.Response(200, content=wrong_root, headers={"content-type": "application/x-nzb"}))
    with pytest.raises(ValueError, match="NZB"):
        make_client(fake2).get_nzb("12345")


def test_get_nzb_rejects_oversized_content():
    oversized = b"<nzb>" + b"a" * (4 * 1024 * 1024 + 1) + b"</nzb>"
    fake = Fake(lambda request: httpx.Response(200, content=oversized, headers={"content-type": "application/x-nzb"}))
    with pytest.raises(ServiceError, match="unsafe"):
        make_client(fake).get_nzb("12345")


@pytest.mark.parametrize(
    "payload, content_type",
    [
        (json.dumps({"code": "300", "description": "Invalid or outdated search result ID"}), "application/json"),
        (b'<?xml version="1.0"?><error code="300" description="Invalid or outdated search result ID"/>', "application/xml"),
    ],
)
def test_get_nzb_surfaces_error_envelope(payload: Any, content_type: str):
    fake = Fake(lambda request: httpx.Response(200, content=payload, headers={"content-type": content_type}))
    with pytest.raises(ServiceError) as excinfo:
        make_client(fake).get_nzb("12345")
    assert "300" in str(excinfo.value)


def test_get_nzb_follows_allowed_redirect_and_drops_credentials():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hydra.test":
            return httpx.Response(302, headers={"location": "http://indexer.test:8085/dl?indexerkey=zzz"})
        return httpx.Response(
            200,
            content=NZB_BYTES,
            headers={"content-type": "application/x-nzb", "content-disposition": "attachment; filename=x.nzb"},
        )

    fake = Fake(responder)
    client = make_client(fake, allowed_redirect_origins=["http://indexer.test:8085"])
    content, filename = client.get_nzb("12345")
    assert content == NZB_BYTES and filename == "x.nzb"
    assert len(fake.seen) == 2
    assert "apikey" not in fake.seen[1].url.params  # Hydra credentials never forwarded cross-origin


def test_get_nzb_blocks_disallowed_redirect_without_leaking():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hydra.test":
            return httpx.Response(302, headers={"location": "http://evil.test/dl"})
        return httpx.Response(200, content=NZB_BYTES)

    fake = Fake(responder)
    with pytest.raises(ServiceError) as excinfo:
        make_client(fake).get_nzb("12345")
    assert len(fake.seen) == 1  # second hop never requested
    assert "evil.test" not in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


def test_all_calls_are_read_only_with_optin_redirects_only():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") == "get":
            return httpx.Response(200, content=NZB_BYTES, headers={"content-type": "application/x-nzb"})
        return httpx.Response(200, json=SEARCH_JSON)

    client = make_client(Fake(responder))
    calls: list[tuple[bool, bool]] = []
    original = client.request_bytes

    def spy(method: str, path: str, **kwargs: Any) -> tuple[bytes, dict]:
        calls.append((kwargs.get("mutation", False), kwargs.get("follow_allowed_redirects", False)))
        return original(method, path, **kwargs)

    client.request_bytes = spy  # type: ignore[method-assign]
    client.search(query="x")
    client.get_nzb("12345")
    assert all(mutation is False for mutation, _ in calls)
    assert calls[0] == (False, False)  # search: no redirect following
    assert calls[1] == (False, True)  # get_nzb: opt-in redirect handling


def test_close_is_available():
    fake = Fake(lambda request: httpx.Response(200, json=CAPS_JSON))
    client = make_client(fake)
    client.capabilities()
    client.close()  # inherited from the shared ServiceClient
