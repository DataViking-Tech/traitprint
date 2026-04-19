"""Vault storage operations — load, save, create."""

from __future__ import annotations

import json
from pathlib import Path

from traitprint.schema import VaultSchema

DEFAULT_VAULT_DIR = Path.home() / ".traitprint"


class VaultStore:
    """Manages reading and writing the vault.json file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.directory = Path(path) if path else DEFAULT_VAULT_DIR

    @property
    def vault_path(self) -> Path:
        """Path to vault.json inside the vault directory."""
        return self.directory / "vault.json"

    def exists(self) -> bool:
        """Check whether vault.json exists."""
        return self.vault_path.is_file()

    def load(self) -> VaultSchema:
        """Read and validate vault.json."""
        raw = self.vault_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return VaultSchema.model_validate(data)

    def save(self, vault: VaultSchema) -> None:
        """Write vault to disk with pretty formatting."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = vault.model_dump(mode="json")
        self.vault_path.write_text(
            json.dumps(payload, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def create_empty(self) -> VaultSchema:
        """Return an empty vault with schema_version=0."""
        return VaultSchema(schema_version=0)
