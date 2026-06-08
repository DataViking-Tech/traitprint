"""End-to-end tests for login/push/pull CLI commands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from traitprint import cloud as cloud_module
from traitprint.cli import cli
from traitprint.credentials import Credentials, CredentialsStore
from traitprint.schema import VaultSchema
from traitprint.vault import VaultStore

NOW = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
# Intentionally far in the future so the freshly-`init`'d vault's ``updated_at``
# (stamped at real test-run time) is always *older* than ``LATER``. This makes
# "server is newer" scenarios reliable regardless of when the suite runs.
LATER = datetime(2099, 1, 1, tzinfo=timezone.utc)


class FakeServer:
    def __init__(self) -> None:
        self.vault: dict | None = None
        self.updated_at: str | None = None
        self.token = "fake-token"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("authorization", "")

        if path == "/auth/v1/token" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("password") == "wrong":
                return httpx.Response(
                    400,
                    json={"error_code": "invalid_credentials", "msg": "bad creds"},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": self.token,
                    "refresh_token": "refresh-token",
                    "expires_at": 9999999999,
                    "user": {"email": body.get("email", "")},
                },
            )

        if path == "/functions/v1/vault-sync":
            if auth != f"Bearer {self.token}":
                return httpx.Response(401)
            if request.method == "GET":
                return httpx.Response(
                    200, json={"vault": self.vault, "updated_at": self.updated_at}
                )
            if request.method == "POST":
                body = json.loads(request.content)
                vault = body["vault"]
                client_ts = vault["updated_at"]
                if self.updated_at and client_ts <= self.updated_at:
                    return httpx.Response(
                        409, json={"server_updated_at": self.updated_at}
                    )
                self.vault = vault
                self.updated_at = client_ts
                return httpx.Response(
                    200, json={"accepted": True, "updated_at": self.updated_at}
                )
        return httpx.Response(404)


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    """Patch ``httpx.Client`` so every ``CloudClient`` uses our MockTransport."""
    srv = FakeServer()
    real_client = httpx.Client

    def patched_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(srv.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cloud_module.httpx, "Client", patched_client)
    yield srv


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    runner = CliRunner()
    d = tmp_path / "vault"
    result = runner.invoke(cli, ["--path", str(d), "init"])
    assert result.exit_code == 0, result.output
    return d


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ------------------------------------------------------------------
# login / logout
# ------------------------------------------------------------------


class TestLogin:
    def test_saves_credentials_on_success(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "login",
                "--email",
                "ada@example.test",
                "--password",
                "s3cret",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Logged in as ada@example.test" in result.output

        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        assert creds.token == server.token
        assert creds.api_url == "http://test"

    def test_bad_password_exits_nonzero(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "login",
                "--email",
                "ada@example.test",
                "--password",
                "wrong",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code != 0
        assert CredentialsStore(vault_dir).exists() is False

    def test_login_requires_vault(
        self, runner: CliRunner, tmp_path: Path, server: FakeServer
    ) -> None:
        nowhere = tmp_path / "nowhere"
        result = runner.invoke(
            cli,
            [
                "--path",
                str(nowhere),
                "login",
                "--email",
                "ada@example.test",
                "--password",
                "s3cret",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code != 0
        assert "No vault found" in result.output


class TestLoginToken:
    def test_token_flag_skips_password_flow(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "login",
                "--token",
                "my-api-token",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "API token" in result.output

        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        assert creds.token == "my-api-token"
        assert creds.api_url == "http://test"
        # No /auth/login call was made — server only handles login on POST.
        # Token was trusted as-is and persisted.

    def test_token_flag_records_email_when_provided(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "login",
                "--email",
                "ada@example.test",
                "--token",
                "my-api-token",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ada@example.test" in result.output

        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        assert creds.email == "ada@example.test"
        assert creds.token == "my-api-token"

    def test_env_token_skips_password_flow(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRAITPRINT_API_TOKEN", "env-token")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "login", "--api-url", "http://test"],
        )
        assert result.exit_code == 0, result.output
        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        assert creds.token == "env-token"

    def test_env_password_used_without_prompt(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRAITPRINT_PASSWORD", "s3cret")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "login",
                "--email",
                "ada@example.test",
                "--api-url",
                "http://test",
            ],
        )
        assert result.exit_code == 0, result.output
        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        assert creds.token == server.token

    def test_token_takes_precedence_over_password(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRAITPRINT_PASSWORD", "wrong")
        monkeypatch.setenv("TRAITPRINT_API_TOKEN", "winning-token")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "login", "--api-url", "http://test"],
        )
        assert result.exit_code == 0, result.output
        creds = CredentialsStore(vault_dir).load()
        assert creds is not None
        # Token path won, so we never hit the bad-password branch.
        assert creds.token == "winning-token"


class TestLogout:
    def test_removes_credentials(self, runner: CliRunner, vault_dir: Path) -> None:
        CredentialsStore(vault_dir).save(
            Credentials(token="x", email="ada@example.test")
        )
        result = runner.invoke(cli, ["--path", str(vault_dir), "logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output
        assert CredentialsStore(vault_dir).exists() is False

    def test_logout_when_not_logged_in(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "logout"])
        assert result.exit_code == 0
        assert "No credentials" in result.output


# ------------------------------------------------------------------
# push / pull helpers
# ------------------------------------------------------------------


def _do_login(runner: CliRunner, vault_dir: Path) -> None:
    result = runner.invoke(
        cli,
        [
            "--path",
            str(vault_dir),
            "login",
            "--email",
            "ada@example.test",
            "--password",
            "s3cret",
            "--api-url",
            "http://test",
        ],
    )
    assert result.exit_code == 0, result.output


class TestPush:
    def test_push_requires_login(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code != 0
        assert "Not logged in" in result.output

    def test_push_uploads_vault(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code == 0, result.output
        assert "[push]" in result.output
        assert "Push complete" in result.output
        assert server.vault is not None

    def test_push_works_with_env_token_no_login(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No `traitprint login` was ever run — env token alone should be enough.
        assert CredentialsStore(vault_dir).exists() is False
        monkeypatch.setenv("TRAITPRINT_API_TOKEN", server.token)
        monkeypatch.setenv("TRAITPRINT_API_URL", "http://test")
        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code == 0, result.output
        assert server.vault is not None

    def test_push_dry_run_no_upload(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "push", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "[push]" in result.output
        assert "Dry run" in result.output
        assert server.vault is None

    def test_push_conflict_when_server_newer(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        # Seed server with a future timestamp.
        server.vault = VaultSchema(updated_at=LATER).model_dump(mode="json")
        server.updated_at = LATER.isoformat()

        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower() or "newer" in result.output.lower()


def _seed_broken_story(vault_dir: Path) -> None:
    """Add an incomplete STAR story (an error-level audit finding)."""
    from traitprint.schema import StorySchema

    store = VaultStore(vault_dir)
    v = store.load()
    v.stories.append(StorySchema(title="Broken", situation="only the situation"))
    store.save(v)


class TestPrePushAudit:
    def test_error_findings_block_push(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        _seed_broken_story(vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code != 0
        assert "Pre-push coherence audit failed" in result.output
        # Nothing was uploaded.
        assert server.vault is None

    def test_skip_audit_bypasses_the_gate(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        _seed_broken_story(vault_dir)
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "push", "--skip-audit"]
        )
        assert result.exit_code == 0, result.output
        assert "Push complete" in result.output
        assert server.vault is not None

    def test_strict_blocks_on_warning_only_vault(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        # A freshly-init'd empty vault has only a 'vault.empty' warning, no
        # errors — so it pushes by default but is blocked under --strict.
        _do_login(runner, vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "push", "--strict"])
        assert result.exit_code != 0
        assert "Pre-push coherence audit failed" in result.output
        assert server.vault is None

    def test_warnings_do_not_block_default_push(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        # Same empty vault, default mode: warning is advisory, push proceeds.
        _do_login(runner, vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "push"])
        assert result.exit_code == 0, result.output
        assert "Coherence note" in result.output
        assert server.vault is not None


class TestPull:
    def test_pull_requires_login(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "pull"])
        assert result.exit_code != 0
        assert "Not logged in" in result.output

    def test_pull_noop_when_server_empty(
        self, runner: CliRunner, vault_dir: Path, server: FakeServer
    ) -> None:
        _do_login(runner, vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "pull"])
        assert result.exit_code == 0, result.output
        assert "[noop]" in result.output

    def test_pull_updates_local_when_server_newer(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
    ) -> None:
        _do_login(runner, vault_dir)
        server_vault = VaultSchema(updated_at=LATER)
        server_vault.profile.display_name = "Ada Lovelace"
        server.vault = server_vault.model_dump(mode="json")
        server.updated_at = LATER.isoformat()

        result = runner.invoke(cli, ["--path", str(vault_dir), "pull"])
        assert result.exit_code == 0, result.output
        assert "[pull]" in result.output

        loaded = VaultStore(vault_dir).load()
        assert loaded.profile.display_name == "Ada Lovelace"
        assert loaded.updated_at == LATER

    def test_pull_dry_run_no_write(
        self,
        runner: CliRunner,
        vault_dir: Path,
        server: FakeServer,
    ) -> None:
        _do_login(runner, vault_dir)
        server_vault = VaultSchema(updated_at=LATER)
        server_vault.profile.display_name = "Ada Lovelace"
        server.vault = server_vault.model_dump(mode="json")
        server.updated_at = LATER.isoformat()

        before = VaultStore(vault_dir).load().profile.display_name

        result = runner.invoke(cli, ["--path", str(vault_dir), "pull", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert VaultStore(vault_dir).load().profile.display_name == before
