"""SABnzbd HTTP API client (mode-based, JSON output).

Verified against the official API documentation and the sabnzbd/sabnzbd source
(sabnzbd/api.py, stable 5.x):
- ``GET /api`` with ``apikey`` and ``output=json`` request parameters. The key
  is always supplied as an API parameter for this backend (never a header).
- HTTP status is unreliable for SABnzbd: failures arrive as HTTP 200 with
  ``{"status": false, "error": "..."}``. Responses are inspected, not assumed.
- Mutations are GET requests here, so every write call passes
  ``mutation=True`` (permission check + uncertain-outcome timeouts).
- Single-job pause/resume: ``mode=queue&name=pause|resume&value=<nzo_id>``
  returning ``{"status": bool, "nzo_ids": [affected]}``.
- Retry: ``mode=retry&value=<nzo_id>`` returns ``{"status": true,
  "nzo_id": <possibly new id>}``; the API does not validate job state itself,
  so a failed-only preflight read is made first.
- Submission: multipart ``mode=addfile`` with the file in field ``nzbfile``.
- Queue ``mb``/``mbleft`` are MiB values formatted to two decimals
  (``MEBI = 2**20``); conversions are exact to the reported precision.
  History slots carry exact integer ``bytes``.
- Categories come from ``mode=get_cats`` (includes ``*`` for Default).
"""
from __future__ import annotations

import re
from typing import Any

from ..config import ServiceConfig
from ..http import ServiceClient, ServiceError, pagination, validate_nzb

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+\-]{0,250}\.nzb$", re.IGNORECASE)
# Opaque nzo_id: nonempty, bounded, and free of separators/comma so it can
# never smuggle a second ID into comma-joined value parameters.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

_MIB = 1048576

_QUEUE_STATES = {
    "DOWNLOADING": "downloading",
    "FETCHING": "downloading",
    "QUEUED": "queued",
    "PROPAGATING": "queued",
    "GRABBING": "queued",
    "PAUSED": "paused",
    "CHECKING": "postprocessing",
}
_HISTORY_STATES = {
    "COMPLETED": "completed",
    "FAILED": "failed",
    "QUEUED": "postprocessing",
    "QUICKCHECK": "postprocessing",
    "VERIFYING": "postprocessing",
    "REPAIRING": "postprocessing",
    "EXTRACTING": "postprocessing",
    "MOVING": "postprocessing",
    "RUNNING": "postprocessing",
}


def _mib_to_bytes(value: Any) -> int | None:
    try:
        return int(round(float(str(value)) * _MIB))
    except (TypeError, ValueError, OverflowError):
        return None


def _kibps_to_bytes(value: Any) -> int | None:
    try:
        return int(round(float(str(value)) * 1024))
    except (TypeError, ValueError, OverflowError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class SABnzbdClient(ServiceClient):
    """Read-only by default; GET-based mutations are still permission-checked."""

    def __init__(self, config: ServiceConfig, transport: Any = None):
        if config.name != "sabnzbd":
            raise ValueError("SABnzbdClient requires a config named 'sabnzbd'")
        super().__init__(config, transport=transport)

    # --- transport ---------------------------------------------------------

    def _api(self, mode_params: dict, *, mutation: bool = False) -> Any:
        params = {"apikey": self.config.api_key, "output": "json", **mode_params}
        data = self.request_json("GET", "api", params=params, mutation=mutation)
        if isinstance(data, dict) and data.get("error"):
            # Generic only; the upstream error text is never echoed back.
            raise ServiceError(f"{self.config.name}: API request failed")
        if mutation and (not isinstance(data, dict) or data.get("status") is not True):
            raise ServiceError(f"{self.config.name}: command was not accepted")
        return data

    # --- normalized reads --------------------------------------------------

    def status(self) -> dict:
        data = self._api({"mode": "queue", "start": 0, "limit": 1})
        queue = data.get("queue") if isinstance(data, dict) else None
        if not isinstance(queue, dict):
            raise ServiceError(f"{self.config.name}: invalid status response")
        native = queue.get("status")
        paused = queue.get("paused") is True or native == "Paused"
        if paused:
            state = "paused"
        elif native == "Downloading":
            state = "downloading"
        elif native == "Idle":
            state = "idle"
        else:
            state = "unknown"
        return {
            "backend": self.config.name,
            "state": state,
            "native_state": {"status": native, "paused": queue.get("paused")},
            "speed_bytes_per_sec": _kibps_to_bytes(queue.get("kbpersec")),
            "remaining_bytes": _mib_to_bytes(queue.get("mbleft")),
        }

    def queue(self, offset: int = 0, limit: int = 25) -> dict:
        pagination(offset, limit)
        data = self._api({"mode": "queue", "start": offset, "limit": limit})
        queue = data.get("queue") if isinstance(data, dict) else None
        if not isinstance(queue, dict):
            raise ServiceError(f"{self.config.name}: invalid queue response")
        slots = [self._normalize_slot(s) for s in queue.get("slots") or [] if isinstance(s, dict)]
        total = _as_int(queue.get("noofslots_total"))
        if total is None:
            total = len(slots)
        return {
            "backend": self.config.name,
            "items": slots,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + limit < total,
            "pagination": "native",
        }

    def history(self, offset: int = 0, limit: int = 25) -> dict:
        pagination(offset, limit)
        data = self._api({"mode": "history", "start": offset, "limit": limit})
        history = data.get("history") if isinstance(data, dict) else None
        if not isinstance(history, dict):
            raise ServiceError(f"{self.config.name}: invalid history response")
        slots = [self._normalize_history(s) for s in history.get("slots") or [] if isinstance(s, dict)]
        total = _as_int(history.get("noofslots"))
        if total is None:
            total = len(slots)
        return {
            "backend": self.config.name,
            "items": slots,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + limit < total,
            "pagination": "native",
        }

    def categories(self) -> list[str]:
        data = self._api({"mode": "get_cats"})
        if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
            raise ServiceError(f"{self.config.name}: invalid categories response")
        return [name for name in data["categories"] if isinstance(name, str) and name]

    def _normalize_slot(self, slot: dict) -> dict:
        native = slot.get("status")
        size = _mib_to_bytes(slot.get("mb"))
        remaining = _mib_to_bytes(slot.get("mbleft"))
        item = {
            "backend": self.config.name,
            "id": slot.get("nzo_id") or None,
            "name": slot.get("filename"),
            "category": "" if slot.get("cat") in (None, "None") else slot.get("cat"),
            "state": _QUEUE_STATES.get(str(native or "").upper(), "unknown"),
            "native_state": native,
            "size_bytes": size,
            "remaining_bytes": remaining,
            "failure": None,
            "path": None,
        }
        percent = _as_int(slot.get("percentage"))
        if percent is not None and 0 <= percent <= 100:
            item["progress_percent"] = percent
        return item

    def _normalize_history(self, slot: dict) -> dict:
        native = slot.get("status")
        failed = str(native or "").upper() == "FAILED"
        storage = slot.get("storage") or slot.get("path") or None
        return {
            "backend": self.config.name,
            "id": slot.get("nzo_id") or None,
            "name": slot.get("name") or slot.get("nzb_name"),
            "category": "" if slot.get("category") in (None, "None") else slot.get("category"),
            "state": _HISTORY_STATES.get(str(native or "").upper(), "unknown"),
            "native_state": native,
            "size_bytes": _as_int(slot.get("bytes")),
            "remaining_bytes": None,
            "failure": slot.get("fail_message") if failed else None,
            "path": storage,
            "retry_eligible": failed,
            "completed": _as_int(slot.get("completed")),
        }

    # --- mutations ---------------------------------------------------------

    def pause(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self._validate_scope(job_id, whole_queue)
        if whole_queue:
            self._api({"mode": "pause"}, mutation=True)
            # {"status": true} here is unconditional; inspect actual state.
            verified = self._verify_status().get("state") == "paused"
            return self._action_result("pause", "whole_queue", None, verified=verified)
        job = self._validate_job_id(job_id)
        data = self._api({"mode": "queue", "name": "pause", "value": job}, mutation=True)
        affected = self._affected_ids(data, "pause")
        return self._action_result("pause", "job", job, verified=job in affected,
                                   affected_ids=sorted(affected))

    def resume(self, job_id: str | None = None, whole_queue: bool = False) -> dict:
        self._validate_scope(job_id, whole_queue)
        if whole_queue:
            self._api({"mode": "resume"}, mutation=True)
            verified = self._verify_status().get("state") in {"idle", "downloading"}
            return self._action_result("resume", "whole_queue", None, verified=verified)
        job = self._validate_job_id(job_id)
        data = self._api({"mode": "queue", "name": "resume", "value": job}, mutation=True)
        affected = self._affected_ids(data, "resume")
        return self._action_result("resume", "job", job, verified=job in affected,
                                   affected_ids=sorted(affected))

    def retry(self, job_id: str) -> dict:
        # Permission before the preflight read; SAB's retry endpoint does not
        # validate job state itself, so eligibility is established here.
        self.require_write()
        job = self._validate_job_id(job_id)
        data = self._api({"mode": "history", "start": 0, "limit": 1, "nzo_ids": job})
        slots = data.get("history", {}).get("slots") if isinstance(data, dict) else None
        match = next((s for s in slots or [] if isinstance(s, dict) and s.get("nzo_id") == job), None)
        if match is None:
            raise ValueError("job_id does not match a history item")
        if str(match.get("status", "")).upper() != "FAILED":
            raise ValueError("retry targets failed history items only")
        data = self._api({"mode": "retry", "value": job}, mutation=True)
        if not isinstance(data, dict) or data.get("status") is not True:
            raise ServiceError(f"{self.config.name}: retry command was not accepted")
        new_id = data.get("nzo_id") if isinstance(data.get("nzo_id"), str) else None
        verified = False
        if new_id:
            self._validate_returned_id(new_id)
            verified = self._queued(new_id)
        return self._action_result("retry", "job", job, verified=verified, new_job_id=new_id)

    def submit(self, content: bytes, filename: str, category: str) -> dict:
        self.require_write()
        validate_nzb(content)
        self._validate_filename(filename)
        if category not in self.categories():
            raise ValueError("category is not configured on sabnzbd")
        # apikey/output ride in the query string; mode/cat as form fields,
        # the NZB itself as the multipart ``nzbfile`` field.
        data = self._api_with_file(category, content, filename)
        if not isinstance(data, dict) or data.get("status") is not True:
            raise ServiceError(f"{self.config.name}: submission was rejected")
        job_ids = [str(i) for i in data.get("nzo_ids") or [] if isinstance(i, (str, int))]
        if not job_ids:
            raise ServiceError(f"{self.config.name}: submission returned no job id")
        for job in job_ids:
            self._validate_returned_id(job)
        new_id = job_ids[0]
        verified = self._queued(new_id)
        return self._action_result("submit", "job", new_id, verified=verified,
                                   job_ids=job_ids, filename=filename, category=category)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _validate_scope(job_id: str | None, whole_queue: bool) -> None:
        if job_id is not None and whole_queue:
            raise ValueError("job_id and whole_queue are mutually exclusive")
        if job_id is None and not whole_queue:
            raise ValueError("specify either job_id or whole_queue=True")

    @staticmethod
    def _validate_job_id(job_id: str | None) -> str:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError("job_id must be a single opaque nzo_id")
        return job_id

    @staticmethod
    def _validate_filename(filename: str) -> str:
        if not isinstance(filename, str) or not _FILENAME_RE.fullmatch(filename):
            raise ValueError("filename must be a plain .nzb filename without paths")
        return filename

    def _verify_status(self) -> dict:
        try:
            return self.status()
        except ServiceError:
            return {"state": "unknown"}

    @staticmethod
    def _validate_returned_id(job_id: str) -> None:
        if not _JOB_ID_RE.fullmatch(job_id):
            raise ServiceError("sabnzbd: outcome uncertain; upstream returned an invalid job ID")

    def _queued(self, job_id: str) -> bool:
        try:
            check = self._api({"mode": "queue", "start": 0, "limit": 1, "nzo_ids": job_id})
            slots = check.get("queue", {}).get("slots") if isinstance(check, dict) else None
            return any(isinstance(s, dict) and s.get("nzo_id") == job_id for s in slots or [])
        except ServiceError:
            return False  # Do not lose an accepted submission's native ID.

    def _affected_ids(self, data: Any, action: str) -> set[str]:
        if not isinstance(data, dict):
            raise ServiceError(f"{self.config.name}: {action} command was not accepted")
        if data.get("status") is not True:
            raise ServiceError(f"{self.config.name}: {action} command was not accepted")
        ids = data.get("nzo_ids")
        if not isinstance(ids, list):
            return set()
        return {str(i) for i in ids if isinstance(i, (str, int))}

    def _api_with_file(self, category: str, content: bytes, filename: str) -> Any:
        params = {"apikey": self.config.api_key, "output": "json"}
        data = {"mode": "addfile", "cat": category}
        files = {"nzbfile": (filename, content)}
        raw = self.request_json("POST", "api", params=params, data=data, files=files, mutation=True)
        if isinstance(raw, dict) and raw.get("error"):
            raise ServiceError(f"{self.config.name}: API request failed")
        return raw

    def _action_result(self, action: str, scope: str, job_id: str | None, *,
                       accepted: bool = True, verified: bool = False, **extra: Any) -> dict:
        result = {"backend": self.config.name, "action": action, "scope": scope,
                  "job_id": job_id, "accepted": accepted, "verified": verified}
        result.update(extra)
        return result
