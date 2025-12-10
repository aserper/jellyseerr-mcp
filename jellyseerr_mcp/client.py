from __future__ import annotations

import httpx
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

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
    def search_media(self, query: str, limit: int = 20) -> Any:
        # URL encode the query to handle spaces and special characters
        encoded_query = quote_plus(query)
        return self.request("GET", "search", params={"query": encoded_query})

    def request_media(self, media_id: int, media_type: str, is_4k: bool = False) -> Any:
        # Discover media details to find the correct media ID to request
        media_details = self.request("GET", f"{media_type}/{media_id}")

        # Jellyseerr API requests are complex. We need to find the service `id` for the desired quality.
        # This is a simplified example; a real implementation would need to handle seasons, etc.
        service_slug = "radarr" if media_type == "movie" else "sonarr"
        if is_4k:
            service_slug += "_4k"

        service = next((s for s in media_details.get("services", []) if s.get("slug") == service_slug), None)

        if not service:
            raise ValueError(f"Could not find a service matching slug '{service_slug}' for media_id {media_id}")

        payload = {
            "mediaId": media_details["id"],
            "mediaType": media_type,
            "is4k": is_4k,
            "serverId": service["id"],
        }
        return self.request("POST", "request", json=payload)

    def get_request(self, request_id: int) -> Any:
        return self.request("GET", f"request/{request_id}")
