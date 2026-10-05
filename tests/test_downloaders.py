"""Mocked contract tests for NZBGetClient and SABnzbdClient.

Every outbound request is captured by httpx.MockTransport handlers; nothing in
this module touches a real service. Wire shapes were verified against the
nzbgetcom/nzbget and sabnzbd/sabnzbd sources (see client module docstrings).
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from jellyseerr_mcp.clients.nzbget import NZBGetClient
from jellyseerr_mcp.clients.sabnzbd import SABnzbdClient
from jellyseerr_mcp.config import ServiceConfig
from jellyseerr_mcp.http import ServiceError

NZB_ID = 5
NZO_ID = "SABnzbd_nzo_abc12"
AUTH = "Basic " + base64.b64encode(b"user:pass").decode()

VALID_NZB = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b"<nzb><file><groups><group>alt</group></groups>"
    b"<segments><segment number=\"1\">abc@x</segment></segments>"
    b"</file></nzb>"
)


def nzbget_config(**overrides: Any) -> ServiceConfig:
    kwargs: dict[str, Any] = dict(name="nzbget", url="http://nzb.local:6789",
                                  username="user", password="pass")
    kwargs.update(overrides)
    return ServiceConfig(**kwargs)


def sab_config(**overrides: Any) -> ServiceConfig:
    kwargs: dict[str, Any] = dict(name="sabnzbd", url="http://sab.local:8080",
                                  api_key="sabkey12345")
    kwargs.update(overrides)
    return ServiceConfig(**kwargs)


class Recorder:
    """MockTransport handler that records requests and returns scripted responses."""

    def __init__(self, responses: list[Any]):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses)
        self.version_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jsonrpc") and request.content and json.loads(request.content).get("method") == "version":
            self.version_requests.append(request)
            return httpx.Response(200, json={"result": "26.0"})
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.content]

    def queries(self) -> list[dict]:
        return [dict(httpx.QueryParams(r.url.query)) for r in self.requests]


def json_response(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------------------------------------------------------------------------
# NZBGet
# ---------------------------------------------------------------------------


class TestNZBGetReads:
    def test_queue_sends_jsonrpc_and_normalizes(self):
        groups = [{
            "NZBID": NZB_ID, "NZBName": "Show.S01", "Category": "tv", "Status": "DOWNLOADING",
            "FileSizeLo": 0xFFFFFFFF, "FileSizeHi": 1,
            "RemainingSizeLo": 0, "RemainingSizeHi": 0, "PausedSizeLo": 0, "PausedSizeHi": 0,
            "DestDir": "/data/tv", "FinalDir": "",
        }]
        rec = Recorder([json_response({"result": groups})])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.queue(offset=0, limit=25)
        finally:
            client.close()
        assert len(rec.requests) == 1
        request = rec.requests[0]
        assert request.method == "POST"
        assert request.url.path == "/jsonrpc"
        assert request.headers["Authorization"] == AUTH
        assert request.headers.get("X-Api-Key") is None
        body = json.loads(request.content)
        assert body == {"method": "listgroups", "params": [0]}
        item = result["items"][0]
        assert item["id"] == "5"
        assert item["name"] == "Show.S01"
        assert item["category"] == "tv"
        assert item["state"] == "downloading"
        assert item["native_state"] == "DOWNLOADING"
        assert item["size_bytes"] == (1 << 32) + 0xFFFFFFFF  # exact 64-bit recombination
        assert item["remaining_bytes"] == 0
        assert item["path"] == "/data/tv"
        assert item["backend"] == "nzbget"

    def test_queue_local_paging(self):
        groups = [{"NZBID": i, "NZBName": f"n{i}", "Category": "", "Status": "QUEUED",
                   "FileSizeLo": 10, "FileSizeHi": 0, "RemainingSizeLo": 5, "RemainingSizeHi": 0,
                   "PausedSizeLo": 0, "PausedSizeHi": 0, "DestDir": "/d"} for i in (1, 2, 3)]
        rec = Recorder([json_response({"result": groups})])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.queue(offset=1, limit=1)
        finally:
            client.close()
        assert result["pagination"] == "local"
        assert [i["id"] for i in result["items"]] == ["2"]
        assert result["total"] == 3
        assert result["has_more"] is True

    def test_history_normalizes_failure_and_retry_flag(self):
        items = [{
            "NZBID": 7, "Name": "Movie.2020", "Category": "movies",
            "Status": "FAILURE/UNPACK", "RetryData": True, "HistoryTime": 123,
            "FileSizeLo": 100, "FileSizeHi": 0, "DestDir": "/d/Movie", "FinalDir": "",
        }]
        rec = Recorder([json_response({"result": items})])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.history()
        finally:
            client.close()
        assert json.loads(rec.requests[0].content) == {"method": "history", "params": [False]}
        item = result["items"][0]
        assert item["state"] == "failed"
        assert item["failure"] == "UNPACK"
        assert item["retry_eligible"] is True
        assert item["path"] == "/d/Movie"
        assert "url" not in item and "URL" not in item

    def test_unknown_states_preserved(self):
        rec = Recorder([json_response({"result": [
            {"NZBID": 1, "NZBName": "x", "Category": "", "Status": "FUTURE_STAGE_9",
             "FileSizeLo": 0, "FileSizeHi": 0, "RemainingSizeLo": 0, "RemainingSizeHi": 0,
             "PausedSizeLo": 0, "PausedSizeHi": 0, "DestDir": "/d"},
        ]})])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            item = client.queue()["items"][0]
        finally:
            client.close()
        assert item["state"] == "unknown"
        assert item["native_state"] == "FUTURE_STAGE_9"

    def test_status_reads_hilo_and_pause_flags(self):
        status = {"DownloadPaused": True, "ServerStandBy": True, "PostPaused": False,
                  "ScanPaused": False, "DownloadRateLo": 512, "DownloadRateHi": 0,
                  "RemainingSizeLo": 1024, "RemainingSizeHi": 0,
                  "FreeDiskSpaceLo": 0, "FreeDiskSpaceHi": 16}
        rec = Recorder([json_response({"result": status})])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.status()
        finally:
            client.close()
        assert result["state"] == "paused"
        assert result["speed_bytes_per_sec"] == 512
        assert result["free_disk_bytes"] == 16 << 32
        assert result["native_state"]["DownloadPaused"] is True

    def test_categories_project_names_only(self):
        rows = [
            {"Name": "MainDir", "Value": "/data"},
            {"Name": "Server1.Password", "Value": "nzbsecret"},
            {"Name": "Category2.Name", "Value": "movies"},
            {"Name": "Category1.Name", "Value": "tv"},
            {"Name": "Category3.Name", "Value": ""},
        ]
        rec = Recorder([json_response({"result": rows})])
        client = NZBGetClient(nzbget_config(password="nzbsecret"), transport=httpx.MockTransport(rec))
        try:
            cats = client.categories()
        finally:
            client.close()
        assert cats == ["tv", "movies"]  # ordered by category number, names only
        dumped = json.dumps(cats)
        assert "nzbsecret" not in dumped and "MainDir" not in dumped and "/data" not in dumped

    def test_rpc_error_is_generic(self):
        rec = Recorder([json_response(
            {"error": {"name": "JSONRPCError", "code": 2, "message": "Invalid parameter (TopSecretValue)"}},
        )])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(ServiceError) as excinfo:
                client.status()
        finally:
            client.close()
        assert "TopSecretValue" not in str(excinfo.value)
        assert "code 2" in str(excinfo.value)


class TestNZBGetMutations:
    def test_editqueue_pause_positional_args(self):
        groups = [{"NZBID": NZB_ID, "Status": "PAUSED"}]
        rec = Recorder([json_response({"result": True}), json_response({"result": groups})])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.pause(job_id="5")
        finally:
            client.close()
        assert rec.bodies()[0] == {"method": "editqueue", "params": ["GroupPause", "", [NZB_ID]]}
        assert result["scope"] == "job" and result["job_id"] == "5"
        assert result["verified"] is True and result["state"] == "paused"

    def test_pause_resume_whole_queue(self):
        rec = Recorder([
            json_response({"result": True}),
            json_response({"result": {"DownloadPaused": True, "ServerStandBy": True}}),
            json_response({"result": True}),
            json_response({"result": {"DownloadPaused": False, "ServerStandBy": True}}),
        ])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            paused = client.pause(whole_queue=True)
            resumed = client.resume(whole_queue=True)
        finally:
            client.close()
        assert rec.bodies()[0] == {"method": "pausedownload", "params": []}
        assert rec.bodies()[2] == {"method": "resumedownload", "params": []}
        assert paused["verified"] is True
        assert resumed["verified"] is True

    def test_scope_validation(self):
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(Recorder([])))
        with pytest.raises(ValueError):
            client.pause()
        with pytest.raises(ValueError):
            client.pause(job_id="5", whole_queue=True)
        with pytest.raises(ValueError):
            client.resume(whole_queue=False)

    def test_readonly_zero_requests(self):
        rec = Recorder([])
        client = NZBGetClient(nzbget_config(), transport=httpx.MockTransport(rec))
        with pytest.raises(PermissionError):
            client.pause(job_id="5")
        with pytest.raises(PermissionError):
            client.pause(whole_queue=True)
        with pytest.raises(PermissionError):
            client.submit(VALID_NZB, "file.nzb", "tv")
        assert rec.requests == []

    @pytest.mark.parametrize("bad", ["0", "-3", "abc", "5.0", " 5", "05", ""])
    def test_bad_nzbid_rejected(self, bad: str):
        rec = Recorder([])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        with pytest.raises(ValueError):
            client.pause(job_id=bad)
        assert rec.requests == []

    def test_retry_preflight_and_redownload(self):
        history = [{"NZBID": 9, "Name": "old", "Status": "FAILURE/HEALTH", "RetryData": True}]
        groups = [{"NZBID": 9, "Status": "QUEUED"}]
        rec = Recorder([json_response({"result": history}), json_response({"result": True}),
                        json_response({"result": groups})])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.retry(job_id="9")
        finally:
            client.close()
        assert rec.bodies()[1] == {"method": "editqueue", "params": ["HistoryRedownload", "", [9]]}
        assert result["accepted"] is True and result["verified"] is True

    def test_retry_rejects_non_failed_or_uneligible(self):
        for history in (
            [{"NZBID": 9, "Status": "SUCCESS/ALL", "RetryData": True}],
            [{"NZBID": 9, "Status": "FAILURE/HEALTH", "RetryData": False}],
            [],
        ):
            rec = Recorder([json_response({"result": history})])
            client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
            with pytest.raises((ValueError, Exception)) as excinfo:
                client.retry(job_id="9")
            assert not isinstance(excinfo.value, httpx.HTTPError)
            assert len(rec.requests) == 1  # preflight only, no mutation

    def test_submit_append_base64_and_auto_category(self):
        groups = [{"NZBID": 42, "Status": "QUEUED"}]
        config_rows = [{"Name": "Category1.Name", "Value": "tv"}]
        rec = Recorder([json_response({"result": config_rows}),
                        json_response({"result": 42}),
                        json_response({"result": groups})])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.submit(VALID_NZB, "Show.S01E01.nzb", "tv")
        finally:
            client.close()
        append_body = rec.bodies()[1]
        assert append_body["method"] == "append"
        params = append_body["params"]
        assert params[0] == "Show.S01E01.nzb"
        assert base64.b64decode(params[1]) == VALID_NZB
        assert params[2] == "tv"
        assert params[3] == 0 and params[4] is False and params[5] is False
        assert params[6] == "" and params[7] == 0 and params[8] == "SCORE"
        # AutoCategory is the documented 10th positional argument and is False.
        assert len(params) == 10 and params[9] is False
        assert result["job_id"] == "42" and result["verified"] is True
        assert result["filename"] == "Show.S01E01.nzb"

    def test_submit_rejects_unsafe_names_and_categories(self):
        rec = Recorder([json_response({"result": [{"Name": "Category1.Name", "Value": "tv"}]})])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "../evil.nzb", "tv")
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "sub/dir.nzb", "tv")
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "script.py", "tv")
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "ok.nzb", "notconfigured")
            with pytest.raises(ValueError):
                client.submit(b"not xml at all", "ok.nzb", "tv")  # invalid NZB
        finally:
            client.close()
        # Only the categories read happened; nothing was appended.
        assert all(json.loads(r.content)["method"] == "config" for r in rec.requests)

    def test_timeout_mutation_no_retry(self):
        rec = Recorder([httpx.ReadTimeout("slow")])
        client = NZBGetClient(nzbget_config(allow_writes=True), transport=httpx.MockTransport(rec))
        with pytest.raises(Exception) as excinfo:
            client.pause(job_id="5")
        assert "uncertain" in str(excinfo.value)
        assert len(rec.requests) == 1  # exactly one attempt, never retried


# ---------------------------------------------------------------------------
# SABnzbd
# ---------------------------------------------------------------------------


class TestSABReads:
    def test_queue_params_and_mib_conversion(self):
        queue = {
            "queue": {
                "status": "Downloading", "paused": False, "kbpersec": "1024.00",
                "mbleft": "2.00", "mb": "4.00", "noofslots_total": 2, "noofslots": 2,
                "start": 0, "limit": 25,
                "slots": [{
                    "nzo_id": NZO_ID, "filename": "Show.S01", "cat": "tv",
                    "status": "Downloading", "mb": "4.00", "mbleft": "2.00",
                    "percentage": "50",
                }, {
                    "nzo_id": "SABnzbd_nzo_zz9", "filename": "Paused.Job", "cat": "None",
                    "status": "Paused", "mb": "1.00", "mbleft": "1.00",
                }],
            },
        }
        rec = Recorder([json_response(queue)])
        client = SABnzbdClient(sab_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.queue(offset=0, limit=25)
        finally:
            client.close()
        query = rec.queries()[0]
        assert query["apikey"] == "sabkey12345"
        assert query["output"] == "json"
        assert query["mode"] == "queue" and query["start"] == "0" and query["limit"] == "25"
        assert rec.requests[0].headers.get("X-Api-Key") is None
        first = result["items"][0]
        assert first["id"] == NZO_ID
        assert first["size_bytes"] == int(round(4.00 * 1048576))
        assert first["remaining_bytes"] == int(round(2.00 * 1048576))
        assert first["progress_percent"] == 50
        assert first["native_state"] == "Downloading"
        second = result["items"][1]
        assert second["category"] == ""  # SAB "None" sentinel normalized
        assert second["state"] == "paused"
        assert result["pagination"] == "native" and result["total"] == 2

    def test_queue_unknown_state_preserved(self):
        queue = {"queue": {"status": "Idle", "paused": False, "noofslots_total": 1,
                           "slots": [{"nzo_id": NZO_ID, "filename": "x", "cat": "",
                                      "status": "MysteriousNew", "mb": "1", "mbleft": "1"}]}}
        rec = Recorder([json_response(queue)])
        client = SABnzbdClient(sab_config(), transport=httpx.MockTransport(rec))
        try:
            item = client.queue()["items"][0]
        finally:
            client.close()
        assert item["state"] == "unknown" and item["native_state"] == "MysteriousNew"

    def test_history_exact_bytes(self):
        history = {"history": {"noofslots": 1, "slots": [{
            "nzo_id": NZO_ID, "name": "Movie.2020", "nzb_name": "Movie.2020.nzb",
            "category": "movies", "status": "Failed", "fail_message": "Not enough articles",
            "bytes": 2436906376, "storage": "/complete/Movie.2020", "path": "/incomplete/Movie.2020",
            "retry": 0,
        }]}}
        rec = Recorder([json_response(history)])
        client = SABnzbdClient(sab_config(), transport=httpx.MockTransport(rec))
        try:
            result = client.history()
        finally:
            client.close()
        item = result["items"][0]
        assert item["size_bytes"] == 2436906376
        assert item["state"] == "failed"
        assert item["failure"] == "Not enough articles"
        assert item["path"] == "/complete/Movie.2020"
        assert item["retry_eligible"] is True

    def test_categories(self):
        rec = Recorder([json_response({"categories": ["*", "movies", "tv"]})])
        client = SABnzbdClient(sab_config(), transport=httpx.MockTransport(rec))
        try:
            assert client.categories() == ["*", "movies", "tv"]
        finally:
            client.close()
        assert rec.queries()[0]["mode"] == "get_cats"

    def test_http200_error_body_is_generic(self):
        rec = Recorder([json_response({"status": False, "error": "API Key Incorrect"})])
        client = SABnzbdClient(sab_config(api_key="wrongkey"), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(Exception) as excinfo:
                client.status()
        finally:
            client.close()
        assert "API Key Incorrect" not in str(excinfo.value)
        assert rec.requests[0].url.path == "/api"

    def test_bad_api_param_guard_on_empty_key(self):
        # SAB config without api_key still builds requests; server rejects them
        # and the client surfaces a generic error.
        rec = Recorder([json_response({"status": False, "error": "API Key Required"})])
        client = SABnzbdClient(sab_config(api_key=""), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(Exception):
                client.categories()
        finally:
            client.close()
        assert rec.queries()[0]["apikey"] == ""


class TestSABMutations:
    def test_pause_single_job_affected_ids(self):
        rec = Recorder([json_response({"status": True, "nzo_ids": [NZO_ID]})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.pause(job_id=NZO_ID)
        finally:
            client.close()
        query = rec.queries()[0]
        assert query == {"apikey": "sabkey12345", "output": "json", "mode": "queue",
                         "name": "pause", "value": NZO_ID}
        assert rec.requests[0].method == "GET"  # SAB mutates over GET
        assert result["verified"] is True and result["affected_ids"] == [NZO_ID]

    def test_pause_job_status_true_but_not_affected(self):
        rec = Recorder([json_response({"status": True, "nzo_ids": []})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.pause(job_id=NZO_ID)
        finally:
            client.close()
        assert result["verified"] is False

    def test_pause_job_rejected(self):
        rec = Recorder([json_response({"status": False, "error": "item does not exist"})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(Exception) as excinfo:
                client.pause(job_id=NZO_ID)
        finally:
            client.close()
        assert "item does not exist" not in str(excinfo.value)

    def test_whole_queue_pause_verifies_state(self):
        queue_downloading = {"queue": {"status": "Downloading", "paused": False,
                                       "noofslots_total": 0, "slots": []}}
        queue_paused = {"queue": {"status": "Paused", "paused": True,
                                  "noofslots_total": 0, "slots": []}}
        rec = Recorder([json_response({"status": True}), json_response(queue_paused),
                        json_response({"status": True}), json_response(queue_downloading)])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            paused = client.pause(whole_queue=True)
            resumed = client.resume(whole_queue=True)
        finally:
            client.close()
        assert rec.queries()[0]["mode"] == "pause"
        assert paused["scope"] == "whole_queue" and paused["verified"] is True
        assert resumed["verified"] is True
        # The {"status": true} from mode=pause is not trusted on its own.
        assert len(rec.requests) == 4

    def test_scope_validation(self):
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(Recorder([])))
        with pytest.raises(ValueError):
            client.pause()
        with pytest.raises(ValueError):
            client.resume(job_id=NZO_ID, whole_queue=True)

    def test_job_id_injection_guards(self):
        rec = Recorder([])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        for bad in ["a,b", "SABnzbd_nzo_x,SABnzbd_nzo_y", "a b", "", "x" * 65, "id&value=1", "a\nb"]:
            with pytest.raises(ValueError):
                client.pause(job_id=bad)
        assert rec.requests == []

    def test_retry_failed_item(self):
        history = {"history": {"noofslots": 1, "slots": [
            {"nzo_id": NZO_ID, "name": "j", "status": "Failed", "bytes": 10},
        ]}}
        queue = {"queue": {"status": "Downloading", "paused": False, "noofslots_total": 1,
                           "slots": [{"nzo_id": "SABnzbd_nzo_NEW1", "filename": "j",
                                      "cat": "tv", "status": "Queued", "mb": "1", "mbleft": "1"}]}}
        rec = Recorder([json_response(history),
                        json_response({"status": True, "nzo_id": "SABnzbd_nzo_NEW1"}),
                        json_response(queue)])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            result = client.retry(job_id=NZO_ID)
        finally:
            client.close()
        assert rec.queries()[0]["nzo_ids"] == NZO_ID  # preflight history filter
        assert rec.queries()[1]["mode"] == "retry" and rec.queries()[1]["value"] == NZO_ID
        assert result["accepted"] is True
        assert result["new_job_id"] == "SABnzbd_nzo_NEW1"
        assert result["verified"] is True

    def test_retry_rejects_non_failed(self):
        history = {"history": {"noofslots": 1, "slots": [
            {"nzo_id": NZO_ID, "name": "j", "status": "Completed"},
        ]}}
        rec = Recorder([json_response(history)])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        with pytest.raises(ValueError):
            client.retry(job_id=NZO_ID)
        assert len(rec.requests) == 1  # preflight read only

    def test_retry_unknown_item(self):
        rec = Recorder([json_response({"history": {"noofslots": 0, "slots": []}})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        with pytest.raises(ValueError):
            client.retry(job_id=NZO_ID)
        assert len(rec.requests) == 1

    def test_readonly_zero_requests(self):
        rec = Recorder([])
        client = SABnzbdClient(sab_config(), transport=httpx.MockTransport(rec))
        with pytest.raises(PermissionError):
            client.pause(job_id=NZO_ID)
        with pytest.raises(PermissionError):
            client.resume(whole_queue=True)
        with pytest.raises(PermissionError):
            client.retry(job_id=NZO_ID)
        with pytest.raises(PermissionError):
            client.submit(VALID_NZB, "f.nzb", "tv")
        assert rec.requests == []

    def test_submit_multipart_and_category_check(self):
        def handler(request: httpx.Request) -> httpx.Response:
            rec.requests.append(request)
            if len(rec.requests) == 1:  # get_cats
                return json_response({"categories": ["*", "tv"]})
            if len(rec.requests) == 2:  # addfile
                body = request.content.decode("latin-1")
                assert "multipart/form-data" in request.headers["content-type"]
                assert 'name="mode"' in body and "addfile" in body
                assert 'name="cat"' in body and "tv" in body
                assert 'filename="Show.S01E01.nzb"' in body
                assert VALID_NZB.split(b"\n")[1].decode() in body
                assert "sabkey12345" in str(request.url)  # apikey rides in query
                return json_response({"status": True, "nzo_ids": ["SABnzbd_nzo_new9"]})
            return json_response({"queue": {"status": "Downloading", "paused": False,
                                            "noofslots_total": 1,
                                            "slots": [{"nzo_id": "SABnzbd_nzo_new9",
                                                       "filename": "Show.S01E01", "cat": "tv",
                                                       "status": "Queued", "mb": "1", "mbleft": "1"}]}})

        rec = Recorder([])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(handler))
        try:
            result = client.submit(VALID_NZB, "Show.S01E01.nzb", "tv")
        finally:
            client.close()
        assert result["job_id"] == "SABnzbd_nzo_new9"
        assert result["verified"] is True
        assert result["filename"] == "Show.S01E01.nzb" and result["category"] == "tv"

    def test_submit_category_validated_before_upload(self):
        rec = Recorder([json_response({"categories": ["*", "tv"]})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "f.nzb", "movies")
        finally:
            client.close()
        assert len(rec.requests) == 1  # get_cats only; no addfile

    def test_submit_rejects_invalid_nzb_and_paths(self):
        rec = Recorder([json_response({"categories": ["*", "tv"]})])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        try:
            with pytest.raises(ValueError):
                client.submit(b"<nzb><file></file></nzb", "f.nzb", "tv")  # malformed XML
            dtd = (b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                   b"<nzb><file><segments><segment number=\"1\">a</segment></segments>"
                   b"</file></nzb>")
            with pytest.raises(ValueError):
                client.submit(dtd, "f.nzb", "tv")
            with pytest.raises(ValueError):
                client.submit(VALID_NZB, "../f.nzb", "tv")
        finally:
            client.close()
        # validate_nzb and filename checks run before the categories lookup,
        # so malformed content or unsafe names produce zero outbound requests.
        assert rec.requests == []

    def test_timeout_mutation_no_retry(self):
        rec = Recorder([httpx.ConnectTimeout("slow")])
        client = SABnzbdClient(sab_config(allow_writes=True), transport=httpx.MockTransport(rec))
        with pytest.raises(Exception) as excinfo:
            client.pause(whole_queue=True)
        assert "uncertain" in str(excinfo.value)
        assert len(rec.requests) == 1
