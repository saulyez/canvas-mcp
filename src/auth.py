import os
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

class CanvasAuth:
    """Handles OAuth2 authentication and token management for Canvas."""

    def __init__(self, token_storage_path: Optional[str] = None):
        self.domain = os.getenv("CANVAS_DOMAIN")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.redirect_uri = os.getenv("REDIRECT_URI")
        self.direct_token = os.getenv("CANVAS_ACCESS_TOKEN")

        if not all([self.domain, self.client_id, self.client_secret]) and not self.direct_token:
            raise ValueError("Missing required OAuth2 credentials or CANVAS_ACCESS_TOKEN in environment variables.")

        self.base_url = f"https://{self.domain}"
        self.token_path = Path(token_storage_path or os.getenv("TOKEN_STORAGE_PATH", "./.canvas_tokens.json"))
        self.tokens: Dict[str, Any] = self._load_tokens()

        if self.direct_token:
            # stdout is the MCP stdio channel — never print() here.
            log.info("Direct Access Token detected. Bypassing OAuth2 flow.")
            self.tokens["access_token"] = self.direct_token
            # Canvas doesn't tell us a manual token's expiry; a real expiry surfaces as
            # CanvasAuthError from the client (401 "Expired access token.").
            self.tokens["expires_at"] = time.time() + (365 * 24 * 3600)
            self._save_tokens()


    def _load_tokens(self) -> Dict[str, Any]:
        """Loads tokens from the local JSON storage."""
        if self.token_path.exists():
            try:
                return json.loads(self.token_path.read_text())
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_tokens(self) -> None:
        """Persists tokens to the local JSON storage."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(self.tokens, indent=2))

    async def get_access_token(self) -> str:
        """Returns a valid access token, refreshing it if necessary."""
        if not self.tokens:
            raise ValueError("No tokens found. Please perform initial OAuth2 authentication.")

        access_token = self.tokens.get("access_token")
        refresh_token = self.tokens.get("refresh_token")
        expires_at = self.tokens.get("expires_at")  # Epoch timestamp

        # Check if token is expired (with a 60s buffer)
        if access_token and expires_at and (time.time() < expires_at - 60):
            return access_token

        if refresh_token:
            await self.refresh_access_token(refresh_token)
            return self.tokens["access_token"]
        
        raise ValueError("Access token expired and no refresh token available.")

    async def exchange_code_for_token(self, code: str) -> None:
        """Exchanges the OAuth2 authorization code for access and refresh tokens."""
        url = f"{self.base_url}/login/oauth2/token"
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            self._update_tokens_after_exchange(token_data)

    async def refresh_access_token(self, refresh_token: str) -> None:
        """Uses the refresh token to obtain a new access token."""
        url = f"{self.base_url}/login/oauth2/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            self._update_tokens_after_exchange(token_data)

    def _update_tokens_after_exchange(self, token_data: Dict[str, Any]) -> None:
        """Updates the internal state and persists tokens."""
        expires_in = token_data.get("expires_in", 3600)
        
        self.tokens["access_token"] = token_data["access_token"]
        if "refresh_token" in token_data:
            self.tokens["refresh_token"] = token_data["refresh_token"]
        self.tokens["expires_at"] = time.time() + expires_in
        
        self._save_tokens()

    async def get_auth_headers(self) -> Dict[str, str]:
        """Returns the Authorization header for httpx requests."""
        token = await self.get_api_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def get_api_access_token(self) -> str:
        """Helper to bridge async access token retrieval with the header generation."""
        return await self.get_access_token()
