from __future__ import annotations

import httpx
from typing import Any, Dict, Optional

from .config import AppConfig


class JellyseerrClient:
    def __init__(self, config: AppConfig):
        self._base_url = f"{config.jellyseerr_url}/api/v1"
        self._timeout = config.timeout
        self._headers = {
            "Authorization": f"Bearer {config.jellyseerr_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Lazy-created async client
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self._headers, timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        client = await self._get_client()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        resp = await client.request(method.upper(), url, params=params or None, json=json or None)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = e.response.text
            raise RuntimeError(f"Jellyseerr API error {e.response.status_code}: {detail}") from e
        return resp.json()

    # Convenience methods for common operations
    async def search_media(self, query: str) -> Any:
        return await self.request("GET", "search", params={"query": query})

    async def request_media(self, media_id: int, media_type: str) -> Any:
        payload = {"mediaId": media_id, "mediaType": media_type}
        return await self.request("POST", "request", json=payload)

    async def get_request(self, request_id: int) -> Any:
        return await self.request("GET", f"request/{request_id}")
