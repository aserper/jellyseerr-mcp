"""Backward-compatible Jellyseerr/Seerr tools with explicit mutation policy."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import AppConfig, ServiceConfig
from .http import ServiceClient, non_negative_id, positive_id


class JellyseerrClient(ServiceClient):
    def __init__(self, config: AppConfig | ServiceConfig, transport: httpx.BaseTransport | None = None):
        if isinstance(config, AppConfig):
            config = config.service_configs()["jellyseerr"]
        super().__init__(config, transport=transport)

    def request(self, method: str, endpoint: str, *, params: dict | None = None,
                json: dict | None = None) -> Any:
        method = method.upper()
        if method not in {"GET", "POST", "PUT"}:
            raise ValueError("Unsupported service method")
        return self.request_json(method, "api/v1/" + endpoint.lstrip("/"),
                                 params=params, json=json, mutation=method != "GET")

    def search_media(self, query: str) -> dict:
        if not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        # Preserve %20: Seerr rejects httpx's conventional '+' encoding.
        return self.request("GET", "search?query=" + quote(query, safe=""))

    def request_media(self, media_id: int, media_type: str, is_4k: bool = False,
                      seasons: list[int] | None = None) -> dict:
        self.require_write()
        media_id = positive_id(media_id, "media_id")
        if media_type not in {"movie", "tv"}:
            raise ValueError("media_type must be movie or tv")
        if seasons is not None and (media_type != "tv" or not seasons or len(seasons) > 100
                or any(isinstance(s, bool) or not isinstance(s, int) or s < 0 for s in seasons)):
            raise ValueError("seasons must be a nonempty list of TV season numbers")
        service_type = "radarr" if media_type == "movie" else "sonarr"
        services = self.request("GET", "service/" + service_type)
        if isinstance(services, dict):
            services = [services]
        matches = [s for s in (services or []) if bool(s.get("is4k", False)) == is_4k]
        default = [s for s in matches if s.get("isDefault")]
        if len(default) == 1:
            service = default[0]
        elif len(matches) == 1:
            service = matches[0]
        else:
            raise ValueError(f"Configure one default {service_type} service for this quality in Seerr")
        profile = service.get("activeProfileId")
        if profile is None or not service.get("activeDirectory"):
            raise ValueError("Seerr service needs an active quality profile and root directory")
        payload = {"mediaId": media_id, "mediaType": media_type, "is4k": is_4k,
                   "serverId": non_negative_id(service.get("id"), "server_id"),
                   "profileId": profile, "rootFolder": service["activeDirectory"]}
        if media_type == "tv":
            # Legacy default retained. Explicit empty seasons is not silently rewritten.
            payload["seasons"] = sorted(set(seasons)) if seasons is not None else [1]
            language = service.get("activeLanguageProfileId")
            if language is not None:
                payload["languageProfileId"] = language
        return self.request("POST", "request", json=payload)

    def get_request(self, request_id: int) -> dict:
        return self.request("GET", f"request/{positive_id(request_id, 'request_id')}")

    def raw_read(self, method: str, endpoint: str, params: dict | None = None,
                 body: dict | None = None) -> dict:
        # Compatibility escape hatch is inspection-only, even in operator mode.
        if method.upper() != "GET" or body is not None or params:
            raise PermissionError("raw_request permits allowlisted GET endpoints without body/params only")
        if endpoint not in {"status", "/status"}:
            raise PermissionError("raw_request endpoint is not allowlisted")
        return self.request("GET", "status")
