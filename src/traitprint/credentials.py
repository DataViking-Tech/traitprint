"""Credentials management for Traitprint cloud sync.

Stores API URL, email, and bearer token in ``<vault>/.credentials``. The
``.credentials`` filename is listed in the vault's ``.gitignore`` so it is
never committed to the vault's git history.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path

from pydantic import BaseModel

CREDENTIALS_FILENAME = ".credentials"
DEFAULT_API_URL = "https://api.traitprint.com"


class Credentials(BaseModel):
    """Cloud sync credentials persisted on disk.

    ``token`` is the Supabase access token (JWT, ~1h lifetime). ``refresh_token``
    is the longer-lived companion that can mint new access tokens — stored for
    forward-compat with refresh-on-401, currently unused.
    """

    api_url: str = DEFAULT_API_URL
    email: str = ""
    token: str = ""
    refresh_token: str = ""
    expires_at: str = ""

    model_config = {"extra": "ignore"}


def resolve_credentials(vault_dir: str | Path) -> Credentials | None:
    """Resolve the effective sync credentials for a vault directory.

    ``TRAITPRINT_API_TOKEN`` wins over the stored file token (the file's
    email/api_url metadata is reused when present, then
    ``TRAITPRINT_API_URL``, then the default URL). Returns ``None`` when no
    usable token exists — callers decide how to surface that (the CLI
    raises a ClickException, the MCP ``vault_sync`` tool a tool error).
    """
    stored = CredentialsStore(vault_dir).load()
    env_token = os.environ.get("TRAITPRINT_API_TOKEN")
    if env_token:
        api_url = (
            (stored.api_url if stored else None)
            or os.environ.get("TRAITPRINT_API_URL")
            or DEFAULT_API_URL
        )
        email = stored.email if stored else ""
        return Credentials(api_url=api_url, email=email, token=env_token)
    if stored is None or not stored.token:
        return None
    return stored


class CredentialsStore:
    """Read and write ``<vault>/.credentials`` with restrictive permissions."""

    def __init__(self, vault_dir: str | Path) -> None:
        self.vault_dir = Path(vault_dir)

    @property
    def path(self) -> Path:
        return self.vault_dir / CREDENTIALS_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Credentials | None:
        """Return credentials, or ``None`` if no file exists."""
        if not self.exists():
            return None
        raw = self.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return Credentials.model_validate(data)

    def save(self, creds: Credentials) -> None:
        """Write credentials with 0600 permissions."""
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        payload = creds.model_dump(mode="json")
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # Best-effort 0600 on POSIX; a no-op on platforms that lack chmod.
        with contextlib.suppress(OSError):
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def delete(self) -> bool:
        """Remove the credentials file. Returns True if a file was removed."""
        if not self.exists():
            return False
        self.path.unlink()
        return True
