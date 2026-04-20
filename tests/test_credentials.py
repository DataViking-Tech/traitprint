"""Tests for the Credentials store."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from traitprint.credentials import (
    DEFAULT_API_URL,
    Credentials,
    CredentialsStore,
)


class TestCredentialsStore:
    def test_load_returns_none_when_missing(self, tmp_path: Path) -> None:
        store = CredentialsStore(tmp_path)
        assert store.exists() is False
        assert store.load() is None

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        store = CredentialsStore(tmp_path)
        creds = Credentials(
            api_url="https://example.test",
            email="ada@example.test",
            token="t0k3n",
        )
        store.save(creds)

        loaded = store.load()
        assert loaded is not None
        assert loaded.api_url == "https://example.test"
        assert loaded.email == "ada@example.test"
        assert loaded.token == "t0k3n"

    def test_default_api_url(self) -> None:
        creds = Credentials()
        assert creds.api_url == DEFAULT_API_URL
        assert creds.token == ""

    def test_save_sets_restrictive_permissions(self, tmp_path: Path) -> None:
        if os.name == "nt":  # pragma: no cover — POSIX-only guarantee.
            return
        store = CredentialsStore(tmp_path)
        store.save(Credentials(token="abc"))
        mode = stat.S_IMODE(os.stat(store.path).st_mode)
        # Owner-only read/write; no group/other bits.
        assert mode & 0o077 == 0

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        store = CredentialsStore(tmp_path)
        store.save(Credentials(token="x"))
        assert store.delete() is True
        assert store.exists() is False
        # Second delete is a no-op.
        assert store.delete() is False
