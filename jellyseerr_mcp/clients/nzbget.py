"""NZBGet JSON-RPC client.

Wire format verified against the nzbgetcom/nzbget source (daemon/remote/XmlRpc.cpp):
- POST /jsonrpc with ``{"method": ..., "params": [...]}``; the server transparently
  flattens nested arrays, so ``editqueue(Command, Param, [IDs])`` is positional.
- JSON booleans must be bare ``true``/``false`` tokens (NextParamAsBool); JSON
  strings must be quoted. Python bool/int/str map correctly via ``json=``.
- HTTP 200 may carry an RPC fault: ``{"error": {"code": N, "message": ...}}``.
- ``append`` positional signature (v16 order, plus ``AutoCategory`` added in
  v25.2+): append(Filename, Content_b64, Category, Priority, AddToTop, AddPaused,
  DupeKey, DupeScore, DupeMode, AutoCategory[, PPParameters]). The upstream docs
  example omits AutoCategory and is inconsistent with the documented signature;
  we always send the 10-argument form ending with ``AutoCategory=false``. On
  pre-v25.2 servers the trailing ``false`` is harmlessly ignored (old parsers
  read PPParameters as string pairs; a boolean terminates that loop).
- ``editqueue`` 3-argument form (Command, Param, IDs) requires NZBGet v18+.
- Sizes arrive as Lo/Hi 32-bit halves; they are recombined exactly.
"""
from __future__ import annotations

import re
from base64 import b64encode
from typing import Any

from ..config import ServiceConfig
from ..http import ServiceClient, ServiceError, page, pagination, positive_id, select, validate_nzb

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+\-]{0,250}\.nzb$", re.IGNORECASE)
_CATEGORY_NAME_RE = re.compile(r"^Category(\d+)\.Name$")

_QUEUE_STATES = {
    "DOWNLOADING": "downloading",
    "FETCHING": "downloading",
    "QUEUED": "queued",
    "PAUSED": "paused",
    "PP_FINISHED": "finished",
}


def _hilo(data: dict, prefix: str) -> int | None:
    """Recombine NZBGet's 64-bit Lo/Hi size halves (defaults keep old servers working)."""
    if not isinstance(data, dict):
        return None
    low = data.get(prefix + "Lo")
    high = data.get(prefix + "Hi")
    if low is None and high is None:
        return None
    return (int(high or 0) << 32) | int(low or 0)


def _queue_state(native: Any) -> str:
    status = str(native or "").upper()
    if status in _QUEUE_STATES:
        return _QUEUE_STATES[status]
    if status.startswith(("PP_", "QS_")) or status in {
        "LOADING_PARS", "VERIFYING_SOURCES", "REPAIRING", "VERIFYING_REPAIRED",
        "RENAMING", "UNPACKING", "MOVING", "POST_UNPACK_RENAMING",
        "EXECUTING_SCRIPT", "POST_DOWNLOAD_RENAMING",
    }:
        return "postprocessing"
    return "unknown"


def _history_state(native: Any) -> tuple[str, str | None]:
    """Split a history status like ``FAILURE/UNPACK`` into (state, failure)."""
    status = str(native or "")
    head, _, detail = status.partition("/")
    if head == "SUCCESS":
        return "completed", None
    if head == "FAILURE":
        return "failed", detail or "unknown"
    if head == "DELETED":
        return "deleted", detail or None
    return "unknown", None


class NZBGetClient(ServiceClient):
    """Read-only by default; every mutation is permission-checked before any traffic."""

    def __init__(self, config: ServiceConfig, transport: Any = None):
        if config.name != "nzbget":
            raise ValueError("NZBGetClient requires a config named 'nzbget'")
        super().__init__(config, transport=transport)
        self._version: str | None = None

    # --- transport ---------------------------------------------------------

    def _rpc(self, method: str, params: list | None = None, *, mutation: bool = False) -> Any:
        if mutation:
            self.require_write()
            version = self.version()
            match = re.match(r"^(\d+)", version)
            if match is None or int(match.group(1)) < 18:
                raise ServiceError("nzbget: operator actions require NZBGet v18 or newer")
        payload = {"method": method, "params": list(params or [])}
        data = self.request_json("POST", "jsonrpc", json=payload, mutation=mutation)
        if not isinstance(data, dict):
            raise ServiceError(f"{self.config.name}: invalid RPC response")
        error = data.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            code = code if isinstance(code, int) and not isinstance(code, bool) else "unknown"
            # Generic only: the upstream message is never echoed back.
            raise ServiceError(f"{self.config.name}: RPC request failed (code {code})")
        if "result" not in data:
            raise ServiceError(f"{self.config.name}: invalid RPC response")
        return data["result"]

    # --- normalized reads --------------------------------------------------

    def version(self) -> str:
        if self._version is None:
            value = self._rpc("version")
            if not isinstance(value, str) or len(value) > 100:
                raise ServiceError("nzbget: invalid version response")
            self._version = value
        return self._version

    def status(self) -> dict:
        data = self._rpc("status")
        if not isinstance(data, dict):
            raise ServiceError(f"{self.config.name}: invalid status response")
        if data.get("DownloadPaused"):
            state = "paused"
        elif not data.get("ServerStandBy"):
            state = "downloading"
        else:
            state = "idle"
        return {
            "backend": self.config.name,
            "state": state,
            "version": self.version(),
            "native_state": select(data, ("DownloadPaused", "ServerStandBy", "PostPaused", "ScanPaused")),
            "speed_bytes_per_sec": _hilo(data, "DownloadRate"),
            "remaining_bytes": _hilo(data, "RemainingSize"),
            "free_disk_bytes": _hilo(data, "FreeDiskSpace"),
        }

    def queue(self, offset: int = 0, limit: int = 25) -> dict:
        pagination(offset, limit)
        groups = self._rpc("listgroups", [0])
        if not isinstance(groups, list):
            raise ServiceError(f"{self.config.name}: invalid queue response")
        return {"backend": "nzbget", **page([self._normalize_group(g) for g in groups if isinstance(g, dict)], offset, limit)}

    def history(self, offset: int = 0, limit: int = 25) -> dict:
        pagination(offset, limit)
        # Positional bool param: false excludes duplicate (DUP) history entries.
        items = self._rpc("history", [False])
        if not isinstance(items, list):
            raise ServiceError(f"{self.config.name}: invalid history response")
        return {"backend": "nzbget", **page([self._normalize_history(i) for i in items if isinstance(i, dict)], offset, limit)}

    def categories(self) -> list[str]:
        rows = self._rpc("config")
        if not isinstance(rows, list):
            raise ServiceError(f"{self.config.name}: invalid config response")
        found: dict[int, str] = {}
        for row in rows:
            # Project the category name only. Config rows are Name/Value pairs;
            # values may hold unrelated settings (including credentials), so
            # nothing else from this response may ever be returned or logged.
            if not isinstance(row, dict):
                continue
            match = _CATEGORY_NAME_RE.match(str(row.get("Name", "")))
            if not match:
                continue
            value = str(row.get("Value", "")).strip()
            if value:
                found.setdefault(int(match.group(1)), value)
        return [found[number] for number in sorted(found)]

    def _normalize_group(self, group: dict) -> dict:
        size = _hilo(group, "FileSize")
        remaining = _hilo(group, "RemainingSize")
        nzb_id = group.get("NZBID")
        item = {
            "backend": self.config.name,
            "id": str(nzb_id) if nzb_id is not None else None,
            "name": group.get("NZBName"),
            "category": group.get("Category") or "",
            "state": _queue_state(group.get("Status")),
            "native_state": group.get("Status"),
            "size_bytes": size,
            "remaining_bytes": remaining,
            "paused_bytes": _hilo(group, "PausedSize"),
            "failure": None,
            "path": group.get("DestDir") or None,
        }
        if group.get("FinalDir"):
            item["final_path"] = group["FinalDir"]
        if size is not None and remaining is not None and size > 0:
            item["progress_percent"] = max(0, min(100, round(100 * (size - remaining) / size)))
        return item

    def _normalize_history(self, item: dict) -> dict:
        state, failure = _history_state(item.get("Status"))
        size = _hilo(item, "FileSize")
        return {
            "backend": self.config.name,
            "id": str(item.get("NZBID")) if item.get("NZBID") is not None else None,
            "name": item.get("Name") or item.get("NZBName"),
            "category": item.get("Category") or "",
            "state": state,
            "native_state": item.get("Status"),
            "size_bytes": size,
            "remaining_bytes": None,
            "failure": failure,
            "path": item.get("DestDir") or None,
            "final_path": item.get("FinalDir") or None,
            "retry_eligible": item.get("RetryData") is True,
            "history_time": item.get("HistoryTime"),
        }

    # --- mutations ---------------------------------------------------------

    def pause(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self._validate_scope(job_id, whole_queue)
        if whole_queue:
            accepted = self._rpc("pausedownload", mutation=True)
            if accepted is not True:
                raise ServiceError(f"{self.config.name}: pause command was not accepted")
            status = self._verify_status()
            return self._action_result("pause", "whole_queue", None,
                                       verified=status.get("DownloadPaused") is True)
        nzb_id = positive_id(job_id, "job_id")
        accepted = self._rpc("editqueue", ["GroupPause", "", [nzb_id]], mutation=True)
        if accepted is not True:
            raise ServiceError(f"{self.config.name}: pause command was not accepted")
        return self._action_result("pause", "job", str(nzb_id), **self._verify_group(nzb_id, "PAUSED"))

    def resume(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self._validate_scope(job_id, whole_queue)
        if whole_queue:
            accepted = self._rpc("resumedownload", mutation=True)
            if accepted is not True:
                raise ServiceError(f"{self.config.name}: resume command was not accepted")
            status = self._verify_status()
            return self._action_result("resume", "whole_queue", None,
                                       verified=status.get("DownloadPaused") is False)
        nzb_id = positive_id(job_id, "job_id")
        accepted = self._rpc("editqueue", ["GroupResume", "", [nzb_id]], mutation=True)
        if accepted is not True:
            raise ServiceError(f"{self.config.name}: resume command was not accepted")
        return self._action_result("resume", "job", str(nzb_id),
                                   **self._verify_group(nzb_id, "PAUSED", inverted=True))

    def retry(self, job_id: str) -> dict:
        # Permission is checked before the eligibility preflight read so a
        # read-only deployment never generates outbound traffic for a retry.
        self.require_write()
        nzb_id = positive_id(job_id, "job_id")
        history = self._rpc("history", [False])
        match = next((i for i in history if isinstance(i, dict) and i.get("NZBID") == nzb_id), None)
        if match is None:
            raise ValueError("job_id does not match a history item")
        if not _history_state(match.get("Status"))[0] == "failed":
            raise ValueError("retry targets failed history items only")
        if match.get("RetryData") is not True:
            raise ValueError("history item has no redownloadable data")
        accepted = self._rpc("editqueue", ["HistoryRedownload", "", [nzb_id]], mutation=True)
        if accepted is not True:
            raise ServiceError(f"{self.config.name}: retry command was not accepted")
        in_queue = self._group_present(nzb_id)
        return self._action_result("retry", "job", str(nzb_id), accepted=True, verified=in_queue)

    def submit(self, content: bytes, filename: str, category: str) -> dict:
        self.require_write()
        validate_nzb(content)
        self._validate_filename(filename)
        if category not in self.categories():
            raise ValueError("category is not configured on nzbget")
        # Exact 10-argument positional form; AutoCategory=false keeps the
        # caller-selected category. PPParameters are deliberately not exposed.
        nzb_id = self._rpc("append", [filename, b64encode(content).decode("ascii"), category,
                                      0, False, False, "", 0, "SCORE", False], mutation=True)
        if isinstance(nzb_id, bool) or not isinstance(nzb_id, int) or nzb_id <= 0:
            raise ServiceError(f"{self.config.name}: submission was rejected")
        return self._action_result("submit", "job", str(nzb_id), accepted=True,
                                   verified=self._group_present(nzb_id),
                                   filename=filename, category=category)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _validate_scope(job_id: str | None, whole_queue: bool) -> None:
        if job_id is not None and whole_queue:
            raise ValueError("job_id and whole_queue are mutually exclusive")
        if job_id is None and not whole_queue:
            raise ValueError("specify either job_id or whole_queue=True")

    @staticmethod
    def _validate_filename(filename: str) -> str:
        if not isinstance(filename, str) or not _FILENAME_RE.fullmatch(filename):
            raise ValueError("filename must be a plain .nzb filename without paths")
        return filename

    def _verify_status(self) -> dict:
        try:
            result = self._rpc("status")
            return result if isinstance(result, dict) else {}
        except ServiceError:
            return {}

    def _verification_groups(self) -> list:
        try:
            result = self._rpc("listgroups", [0])
            return result if isinstance(result, list) else []
        except ServiceError:
            return []  # Accepted mutation remains accepted; callers reconcile later.

    def _group_present(self, nzb_id: int) -> bool:
        return any(isinstance(g, dict) and g.get("NZBID") == nzb_id for g in self._verification_groups())

    def _verify_group(self, nzb_id: int, pause_state: str, *, inverted: bool = False) -> dict:
        groups = self._verification_groups()
        match = next((g for g in groups if isinstance(g, dict) and g.get("NZBID") == nzb_id), None)
        if match is None:
            return {"verified": False, "state": None}
        native = match.get("Status")
        verified = (native == pause_state) if not inverted else (native != pause_state and _queue_state(native) != "unknown")
        return {"verified": bool(verified), "state": _queue_state(native)}

    def _action_result(self, action: str, scope: str, job_id: str | None, *,
                       accepted: bool = True, verified: bool = False, **extra: Any) -> dict:
        result = {"backend": self.config.name, "action": action, "scope": scope,
                  "job_id": job_id, "accepted": accepted, "verified": verified}
        result.update(extra)
        return result
