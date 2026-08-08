from __future__ import annotations

import httpx
from typing import Any, Dict, Optional

from .config import AppConfig


class JellyseerrClient:
    def __init__(self, config: AppConfig):
        self._base_url = f"{config.jellyseerr_url}/api/v1"
        self._timeout = config.timeout
        self._headers = {
            "X-Api-Key": config.jellyseerr_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Synchronous client
        self._client = httpx.Client(headers=self._headers, timeout=self._timeout)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            resp = self._client.request(method.upper(), url, params=params or None, json=json or None)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.text
            raise RuntimeError(f"Jellyseerr API error for '{e.request.method} {e.request.url}': {e.response.status_code} - {detail}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Jellyseerr connection error for '{e.request.method} {e.request.url}': {e}") from e


    # Convenience methods for common operations
    def search_media(self, query: str) -> Any:
        # Jellyseerr requires URL-encoded query params and rejects '+'
        # (httpx encodes spaces as '+' in params, which the API rejects).
        # Use quote() to get %20 encoding and embed directly in the URL
        # so httpx doesn't re-encode.
        from urllib.parse import quote
        encoded = quote(query, safe="")
        return self.request("GET", f"search?query={encoded}")

    def request_media(
        self,
        media_id: int,
        media_type: str,
        is_4k: bool = False,
        seasons: Optional[list[int]] = None,
    ) -> Any:
        # Look up the service (radarr/sonarr) to get serverId, profileId, and rootFolder
        service_type = "radarr" if media_type == "movie" else "sonarr"
        services = self.request("GET", f"service/{service_type}")

        # Handle both list and single-object responses
        if isinstance(services, list):
            service = next((s for s in services if s.get("is4k") == is_4k), services[0] if services else None)
        else:
            service = services

        if not service:
            raise ValueError(f"No {service_type} service configured in Jellyseerr")

        payload = {
            "mediaId": media_id,
            "mediaType": media_type,
            "is4k": is_4k,
            "serverId": service.get("id", 0),
            "profileId": service.get("activeProfileId"),
            "rootFolder": service.get("activeDirectory"),
        }

        # TV shows require seasons array (Jellyseerr v3.3.0 bug) and languageProfileId
        if media_type == "tv":
            payload["seasons"] = seasons or [1]
            payload["languageProfileId"] = service.get("activeLanguageProfileId", 7)

        return self.request("POST", "request", json=payload)

    def get_request(self, request_id: int) -> Any:
        return self.request("GET", f"request/{request_id}")
