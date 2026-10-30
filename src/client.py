import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from .auth import CanvasAuth

log = logging.getLogger(__name__)

MAX_RETRIES = 4


class CanvasAuthError(RuntimeError):
    """Raised when Canvas rejects the access token (expired, revoked, invalid)."""


def is_rate_limited(response: httpx.Response) -> bool:
    """Canvas throttles with 403 + 'Rate Limit Exceeded' (or occasionally 429)."""
    if response.status_code == 429:
        return True
    return response.status_code == 403 and "rate limit exceeded" in response.text.lower()


def raise_for_canvas_status(response: httpx.Response) -> None:
    """raise_for_status, but turn token problems into an actionable CanvasAuthError."""
    if response.status_code == 401:
        detail = ""
        try:
            errors = response.json().get("errors") or []
            if errors:
                detail = errors[0].get("message", "")
                if errors[0].get("expired_at"):
                    detail += f" (expired_at {errors[0]['expired_at']})"
        except Exception:
            pass
        if "expired" in detail.lower() or "invalid access token" in detail.lower():
            raise CanvasAuthError(
                f"Canvas rejected the access token: {detail}. "
                "Generate a new token (Canvas > Account > Settings > New Access Token) "
                "and update CANVAS_ACCESS_TOKEN in .env."
            )
    response.raise_for_status()


async def backoff_sleep(attempt: int, response: Optional[httpx.Response] = None) -> None:
    delay = 2 ** attempt
    if response is not None and response.headers.get("Retry-After", "").isdigit():
        delay = int(response.headers["Retry-After"])
    log.warning("Canvas rate limit hit; retrying in %ss", delay)
    await asyncio.sleep(delay)


class AsyncCanvasClient:
    """Asynchronous client for interacting with the Canvas LMS API."""

    def __init__(self, auth: CanvasAuth):
        self.auth = auth
        self.base_url = auth.base_url.rstrip("/") + "/api/v1"

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one request with auth headers, retrying on Canvas throttling."""
        for attempt in range(MAX_RETRIES + 1):
            headers = await self.auth.get_auth_headers()
            response = await client.request(method, url, headers=headers, **kwargs)
            if is_rate_limited(response) and attempt < MAX_RETRIES:
                await backoff_sleep(attempt, response)
                continue
            raise_for_canvas_status(response)
            return response
        raise AssertionError("unreachable")

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Internal helper to execute HTTP requests with auth and error handling."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            return await self._send(client, method, url, params=params, data=data, json=json_data)

    async def get_paginated(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetches all pages of a paginated endpoint and returns a flattened list."""
        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = f"{self.base_url}/{path.lstrip('/')}"
        # First request uses params; 'Link' next URLs are already fully qualified.
        current_params = params

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            while next_url:
                response = await self._send(client, method, next_url, params=current_params)
                data = response.json()
                if isinstance(data, list):
                    all_results.extend(data)
                else:
                    all_results.append(data)
                current_params = None
                next_url = response.links.get("next", {}).get("url")
        return all_results

    async def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET request returning parsed JSON."""
        response = await self._request("GET", path, params=params)
        return response.json()

    async def post_json(self, path: str, json_data: Dict[str, Any]) -> Any:
        """POST request with JSON body."""
        response = await self._request("POST", path, json_data=json_data)
        return response.json()

    async def delete(self, path: str) -> int:
        """DELETE request returning status code."""
        response = await self._request("DELETE", path)
        return response.status_code
