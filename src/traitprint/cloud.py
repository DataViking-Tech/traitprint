"""HTTP client for the Traitprint cloud vault-sync edge function.

Protocol
--------
POST {api_url}/auth/login
    body     -> {"email", "password"}
    returns  -> {"token", "email"}

GET  {api_url}/vault-sync
    returns  -> {"vault": <VaultSchema|null>, "updated_at": <iso8601|null>}

POST {api_url}/vault-sync
    body     -> {"vault": <VaultSchema>}
    returns  -> {"accepted": bool, "updated_at": <iso8601>}
    on 409   -> {"server_updated_at": <iso8601>}

The server implements whole-vault last-write-wins: a POST is accepted only
when the client's ``vault.updated_at`` is strictly greater than the server's
stored timestamp (or no record exists). Otherwise the server returns HTTP 409
with the current server timestamp so the client can pull and retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from traitprint.credentials import Credentials
from traitprint.schema import VaultSchema


class CloudError(Exception):
    """Base class for cloud sync failures."""


class AuthError(CloudError):
    """Raised when the server rejects credentials or the token is missing/expired."""


class ConflictError(CloudError):
    """Raised when a push is rejected because the server has newer data."""

    def __init__(self, message: str, server_updated_at: datetime | None) -> None:
        super().__init__(message)
        self.server_updated_at = server_updated_at


@dataclass(frozen=True)
class PullResult:
    """Result of a pull operation."""

    vault: VaultSchema | None
    server_updated_at: datetime | None


@dataclass(frozen=True)
class PushResult:
    """Result of a push operation."""

    accepted: bool
    server_updated_at: datetime | None


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp or return ``None``."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # ``fromisoformat`` handles ``+00:00`` and ``Z`` (Py 3.11+) suffixes.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


class CloudClient:
    """Thin HTTP wrapper around the cloud vault-sync edge function."""

    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_credentials(
        cls,
        creds: Credentials,
        *,
        client: httpx.Client | None = None,
    ) -> CloudClient:
        """Build a client using stored credentials (token may be empty)."""
        return cls(creds.api_url, creds.token or None, client=client)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self, email: str, password: str) -> Credentials:
        """Exchange email+password for a bearer token.

        Returns a ``Credentials`` object ready to be persisted via
        ``CredentialsStore.save``.
        """
        url = f"{self.api_url}/auth/login"
        try:
            response = self._client.post(
                url, json={"email": email, "password": password}
            )
        except httpx.HTTPError as exc:
            raise CloudError(f"Login request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise AuthError("Invalid email or password.")
        if response.status_code >= 400:
            raise CloudError(
                f"Login failed: HTTP {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        token = data.get("token", "")
        if not token:
            raise AuthError("Login succeeded but no token was returned.")
        self.token = token
        return Credentials(
            api_url=self.api_url,
            email=data.get("email", email),
            token=token,
        )

    # ------------------------------------------------------------------
    # Vault sync
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            raise AuthError("Not authenticated. Run 'traitprint login' first.")
        return {"Authorization": f"Bearer {self.token}"}

    def pull(self) -> PullResult:
        """Fetch the server's vault (if any)."""
        url = f"{self.api_url}/vault-sync"
        try:
            response = self._client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise CloudError(f"Pull request failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthError("Token expired or invalid. Run 'traitprint login' again.")
        if response.status_code == 404:
            return PullResult(vault=None, server_updated_at=None)
        if response.status_code >= 400:
            raise CloudError(
                f"Pull failed: HTTP {response.status_code} {response.text[:200]}"
            )

        data = response.json() or {}
        raw_vault = data.get("vault")
        vault = VaultSchema.model_validate(raw_vault) if raw_vault else None
        return PullResult(
            vault=vault,
            server_updated_at=_parse_ts(data.get("updated_at")),
        )

    def push(self, vault: VaultSchema) -> PushResult:
        """Upload the vault. Raises ``ConflictError`` on 409."""
        url = f"{self.api_url}/vault-sync"
        payload = {"vault": vault.model_dump(mode="json")}
        try:
            response = self._client.post(
                url, headers=self._auth_headers(), json=payload
            )
        except httpx.HTTPError as exc:
            raise CloudError(f"Push request failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthError("Token expired or invalid. Run 'traitprint login' again.")
        if response.status_code == 409:
            data = response.json() if response.content else {}
            raise ConflictError(
                "Server has newer data. Run 'traitprint pull' first.",
                _parse_ts(data.get("server_updated_at")),
            )
        if response.status_code >= 400:
            raise CloudError(
                f"Push failed: HTTP {response.status_code} {response.text[:200]}"
            )

        data = response.json() if response.content else {}
        return PushResult(
            accepted=bool(data.get("accepted", True)),
            server_updated_at=_parse_ts(data.get("updated_at"))
            or _parse_ts(data.get("server_updated_at")),
        )
