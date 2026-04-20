"""Tests for sync planning and cloud client behavior."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from traitprint.cloud import (
    AuthError,
    CloudClient,
    CloudError,
    ConflictError,
)
from traitprint.schema import VaultSchema
from traitprint.sync import do_pull, do_push, plan_pull, plan_push
from traitprint.vault import VaultStore

# ------------------------------------------------------------------
# Plan helpers
# ------------------------------------------------------------------


def _vault(ts: datetime) -> VaultSchema:
    return VaultSchema(updated_at=ts)


NOW = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
# Far-future timestamp for tests that compare against a freshly-saved vault
# (whose ``updated_at`` is stamped at real test-run time).
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


class TestPlanPush:
    def test_push_when_server_empty(self) -> None:
        plan = plan_push(_vault(NOW), None)
        assert plan.direction == "push"

    def test_push_when_local_newer(self) -> None:
        plan = plan_push(_vault(LATER), NOW)
        assert plan.direction == "push"

    def test_noop_when_equal(self) -> None:
        plan = plan_push(_vault(NOW), NOW)
        assert plan.direction == "noop"

    def test_conflict_when_server_newer(self) -> None:
        plan = plan_push(_vault(NOW), LATER)
        assert plan.direction == "conflict"


class TestPlanPull:
    def test_noop_when_no_server_vault(self) -> None:
        from traitprint.cloud import PullResult

        plan = plan_pull(_vault(NOW), PullResult(vault=None, server_updated_at=None))
        assert plan.direction == "noop"

    def test_pull_when_no_local(self) -> None:
        from traitprint.cloud import PullResult

        plan = plan_pull(None, PullResult(vault=_vault(NOW), server_updated_at=NOW))
        assert plan.direction == "pull"

    def test_pull_when_server_newer(self) -> None:
        from traitprint.cloud import PullResult

        plan = plan_pull(
            _vault(NOW), PullResult(vault=_vault(LATER), server_updated_at=LATER)
        )
        assert plan.direction == "pull"

    def test_conflict_when_local_newer(self) -> None:
        from traitprint.cloud import PullResult

        plan = plan_pull(
            _vault(LATER), PullResult(vault=_vault(NOW), server_updated_at=NOW)
        )
        assert plan.direction == "conflict"


# ------------------------------------------------------------------
# Cloud client (MockTransport)
# ------------------------------------------------------------------


class FakeServer:
    """In-memory stand-in for the vault-sync edge function."""

    def __init__(self) -> None:
        self.vault: dict | None = None
        self.updated_at: str | None = None
        self.token = "server-issued-token"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("authorization", "")

        if path == "/auth/login" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("password") == "wrong":
                return httpx.Response(401, json={"error": "invalid"})
            return httpx.Response(
                200, json={"token": self.token, "email": body.get("email", "")}
            )

        if path == "/vault-sync":
            if auth != f"Bearer {self.token}":
                return httpx.Response(401, json={"error": "unauthorized"})
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
                    200,
                    json={"accepted": True, "updated_at": self.updated_at},
                )
        return httpx.Response(404)


@pytest.fixture()
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture()
def http_client(server: FakeServer) -> httpx.Client:
    transport = httpx.MockTransport(server.handler)
    return httpx.Client(transport=transport, base_url="http://test")


@pytest.fixture()
def cloud(http_client: httpx.Client) -> CloudClient:
    return CloudClient("http://test", token="server-issued-token", client=http_client)


class TestCloudClient:
    def test_login_returns_token(
        self, server: FakeServer, http_client: httpx.Client
    ) -> None:
        client = CloudClient("http://test", client=http_client)
        creds = client.login("ada@example.test", "s3cret")
        assert creds.token == server.token
        assert creds.email == "ada@example.test"
        assert creds.api_url == "http://test"

    def test_login_bad_password_raises(self, http_client: httpx.Client) -> None:
        client = CloudClient("http://test", client=http_client)
        with pytest.raises(AuthError):
            client.login("ada@example.test", "wrong")

    def test_pull_empty_server(self, cloud: CloudClient) -> None:
        result = cloud.pull()
        assert result.vault is None
        assert result.server_updated_at is None

    def test_pull_without_token_raises(self, http_client: httpx.Client) -> None:
        client = CloudClient("http://test", token=None, client=http_client)
        with pytest.raises(AuthError):
            client.pull()

    def test_pull_bad_token_raises_auth_error(self, http_client: httpx.Client) -> None:
        client = CloudClient("http://test", token="nope", client=http_client)
        with pytest.raises(AuthError):
            client.pull()

    def test_push_then_pull_roundtrip(
        self, cloud: CloudClient, server: FakeServer
    ) -> None:
        v = VaultSchema(updated_at=NOW)
        result = cloud.push(v)
        assert result.accepted is True
        assert server.vault is not None

        pulled = cloud.pull()
        assert pulled.vault is not None
        assert pulled.server_updated_at == NOW

    def test_push_conflict_when_server_newer(
        self, cloud: CloudClient, server: FakeServer
    ) -> None:
        cloud.push(VaultSchema(updated_at=LATER))
        with pytest.raises(ConflictError) as exc:
            cloud.push(VaultSchema(updated_at=NOW))
        assert exc.value.server_updated_at == LATER

    def test_http_error_wraps_as_cloud_error(self) -> None:
        def boom(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        http_client = httpx.Client(transport=httpx.MockTransport(boom))
        client = CloudClient("http://test", token="t", client=http_client)
        with pytest.raises(CloudError):
            client.pull()


# ------------------------------------------------------------------
# do_push / do_pull integration with VaultStore
# ------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    from traitprint.git_ops import commit, init_repo

    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    s = VaultStore(d)
    s.save(s.create_empty())
    commit(d, "test init")
    return s


class TestDoPush:
    def test_dry_run_does_not_upload(
        self, store: VaultStore, cloud: CloudClient, server: FakeServer
    ) -> None:
        plan, result = do_push(store, cloud, dry_run=True)
        assert plan.direction == "push"
        assert result is None
        assert server.vault is None

    def test_live_push_uploads(
        self, store: VaultStore, cloud: CloudClient, server: FakeServer
    ) -> None:
        plan, result = do_push(store, cloud, dry_run=False)
        assert plan.direction == "push"
        assert result is not None
        assert result.accepted is True
        assert server.vault is not None


class TestDoPull:
    def test_dry_run_does_not_write(
        self, store: VaultStore, cloud: CloudClient, server: FakeServer
    ) -> None:
        server.vault = VaultSchema(updated_at=FAR_FUTURE).model_dump(mode="json")
        server.updated_at = FAR_FUTURE.isoformat()
        local_before = store.load().updated_at

        plan, _ = do_pull(store, cloud, dry_run=True)
        assert plan.direction == "pull"
        assert store.load().updated_at == local_before

    def test_live_pull_preserves_server_timestamp(
        self, store: VaultStore, cloud: CloudClient, server: FakeServer
    ) -> None:
        server.vault = VaultSchema(updated_at=FAR_FUTURE).model_dump(mode="json")
        server.updated_at = FAR_FUTURE.isoformat()

        plan, pull = do_pull(store, cloud, dry_run=False)
        assert plan.direction == "pull"
        # The saved vault's updated_at matches the server's, NOT "now".
        assert store.load().updated_at == FAR_FUTURE
