import httpx
from typing import Any, Dict, List, Optional
from .auth import CanvasAuth

class AsyncCanvasClient:
    """Asynchronous client for interacting with the Canvas LMS API."""

    def __init__(self, auth: CanvasAuth):
        self.auth = auth
        self.base_url = auth.base_url.rstrip("/") + "/api/v1"

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
        headers = await self.auth.get_auth_headers()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=headers,
            )
            # Note: 401 errors should be handled by the auth layer via refresh logic.
            # If we reach here with a 401, it means the refresh failed or was not triggered.
            response.raise_for_status()
            return response

    async def get_paginated(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetches all pages of a paginated endpoint and returns a flattened list."""
        all_results = []
        # Start with the initial URL
        next_url = f"{self.base_url}/{path.lstrip('/')}"
        
        # For the very first request, we use params. 
        # Subsequent requests use the absolute URL from the 'Link' header.
        current_params = params

        async with httpx.AsyncClient(follow_redirects=True) as client:
            while next_url:
                headers = await self.auth.get_auth_headers()
                response = await client.request(
                    method, 
                    next_url, 
                    params=current_params, 
                    headers=headers
                )
                response.raise_for_status()
                
                data = response.json()
                if isinstance(data, list):
                    all_results.extend(data)
                else:
                    # If it's a single object (not a list), just add it.
                    all_results.append(data)

                # Reset params for subsequent requests because the 'Link' URL is already fully qualified
                current_params = None

                # Check for 'next' link in the Link header
                next_url = None
                links = response.headers.get("Link")
                if links:
                    parts = links.split(",")
                    for part in parts:
                        if 'rel="next"' in part:
                            # Extract URL from <URL>; rel="next"
                            next_url = part.split(";")[0].strip("<> ")
                            break
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
