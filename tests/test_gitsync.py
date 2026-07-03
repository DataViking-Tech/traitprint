"""Tests for the sync-v1 git-bundle client (gitsync.py + ``traitprint sync``).

A :class:`FakeGitServer` implements the server half of the contract
(``docs/schema/sync-v1/README.md``) against a real bare git repo in a tmp
dir, served through ``httpx.MockTransport``: bundle verification (422
``missing_prerequisites``), fast-forward enforcement (409), incremental
fetch bundles, and the info probe. All vault-side git operations run
against real repos (``init_repo`` configures identity/gpgsign).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from traitprint import gitsync as gitsync_module
from traitprint.cli import cli
from traitprint.credentials import Credentials, CredentialsStore
from traitprint.git_ops import commit, head_sha, init_repo
from traitprint.gitsync import (
    GitSyncClient,
    GitSyncError,
    NonFastForwardError,
    SchemaViolationError,
    SyncAuthError,
    read_server_head,
    sync_pull,
    sync_push,
    sync_status,
)
from traitprint.vault import VaultStore

TOKEN = "sync-token"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False
    )


# ------------------------------------------------------------------
# Fake sync-v1 server over a real bare repo
# ------------------------------------------------------------------


class FakeGitServer:
    """In-memory stand-in for the /vault-git/* edge functions.

    Holds one bare repo and enforces the contract with real git:
    bundles must verify (prerequisites present), pushes must
    fast-forward ``main``, fetches return full or ``since..main``
    bundles.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "server.git"
        self.token = TOKEN
        self.ingest: dict[str, object] = {"status": "clean", "last_ingested_sha": None}
        #: when set, every push is rejected 422 schema_violation with these.
        self.violations: list[dict[str, str]] | None = None
        #: non-blocking proposal contract warnings echoed on push + info (Q4).
        self.warnings: list[dict[str, str]] = []
        self.push_count = 0
        self._init_repo()

    def _init_repo(self) -> None:
        self.repo.mkdir(parents=True, exist_ok=True)
        result = _run(["git", "init", "--bare", str(self.repo)])
        assert result.returncode == 0, result.stderr

    def reset(self) -> None:
        """Wipe the hosted repo (simulates server-side data loss)."""
        shutil.rmtree(self.repo)
        self._init_repo()

    def head(self) -> str | None:
        result = _run(["git", "rev-parse", "refs/heads/main"], cwd=self.repo)
        return result.stdout.strip() if result.returncode == 0 else None

    # -- handler -----------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": "auth_expired",
                        "message": "token expired",
                        "hint": "Run 'traitprint login' again.",
                    }
                },
            )
        path = request.url.path
        if path == "/functions/v1/vault-git/info" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "head": self.head(),
                    "ingest": self.ingest,
                    "warnings": self.warnings,
                },
            )
        if path == "/functions/v1/vault-git/push" and request.method == "POST":
            return self._push(request)
        if path == "/functions/v1/vault-git/fetch" and request.method == "GET":
            return self._fetch(request)
        return httpx.Response(404)

    def _push(self, request: httpx.Request) -> httpx.Response:
        self.push_count += 1
        head_hdr = request.headers.get("x-traitprint-head", "")
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "in.bundle"
            bundle.write_bytes(request.content)

            verify = _run(["git", "bundle", "verify", str(bundle)], cwd=self.repo)
            if verify.returncode != 0:
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "missing_prerequisites",
                            "message": "bundle prerequisites not present",
                            "hint": "Retry with a full bundle.",
                        }
                    },
                )
            heads = _run(["git", "bundle", "list-heads", str(bundle)], cwd=self.repo)
            tip = ""
            for line in heads.stdout.splitlines():
                sha, _, ref = line.partition(" ")
                if ref.strip() == "refs/heads/main":
                    tip = sha
            if not tip or tip != head_hdr:
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "bundle_invalid",
                            "message": "bundle tip does not match X-Traitprint-Head",
                            "hint": "",
                        }
                    },
                )
            if self.violations is not None:
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "schema_violation",
                            "message": "vault tree violates the vault v1 contract",
                            "hint": "Fix the listed files and push again.",
                        },
                        "violations": self.violations,
                    },
                )
            fetch = _run(
                [
                    "git",
                    "fetch",
                    str(bundle),
                    "+refs/heads/main:refs/traitprint/incoming",
                ],
                cwd=self.repo,
            )
            assert fetch.returncode == 0, fetch.stderr
            current = self.head()
            if current is not None:
                ff = _run(
                    ["git", "merge-base", "--is-ancestor", current, tip],
                    cwd=self.repo,
                )
                if ff.returncode != 0:
                    return httpx.Response(
                        409,
                        json={
                            "error": {
                                "code": "non_fast_forward",
                                "message": "push is not a fast-forward of main",
                                "hint": "Run 'traitprint sync pull', resolve any "
                                "conflicts, then push again.",
                            },
                            "server_head": current,
                        },
                    )
            _run(["git", "update-ref", "refs/heads/main", tip], cwd=self.repo)
            return httpx.Response(
                200,
                json={"head": tip, "ingest": self.ingest, "warnings": self.warnings},
            )

    def _fetch(self, request: httpx.Request) -> httpx.Response:
        since = request.url.params.get("since")
        head = self.head()
        if head is None or since == head:
            return httpx.Response(204)
        rev = "main"
        full = False
        if since:
            known = _run(["git", "cat-file", "-e", f"{since}^{{commit}}"], self.repo)
            ancestor = _run(
                ["git", "merge-base", "--is-ancestor", since, head], cwd=self.repo
            )
            if known.returncode == 0 and ancestor.returncode == 0:
                rev = f"{since}..main"
            else:
                full = True
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "out.bundle"
            create = _run(["git", "bundle", "create", str(bundle), rev], cwd=self.repo)
            assert create.returncode == 0, create.stderr
            headers = {"X-Traitprint-Head": head}
            if full:
                headers["X-Traitprint-Bundle"] = "full"
            return httpx.Response(200, content=bundle.read_bytes(), headers=headers)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path: Path) -> FakeGitServer:
    return FakeGitServer(tmp_path / "server")


@pytest.fixture()
def client(server: FakeGitServer) -> GitSyncClient:
    transport = httpx.MockTransport(server.handler)
    http = httpx.Client(transport=transport, base_url="http://test")
    return GitSyncClient("http://test", token=TOKEN, client=http)


def _make_vault(path: Path) -> Path:
    """Create a real vault repo with one initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    init_repo(path)
    store = VaultStore(path)
    store.save(store.create_empty())
    commit(path, "traitprint init")
    return path


def _write_and_commit(vault: Path, name: str, text: str, message: str) -> str:
    (vault / name).write_text(text, encoding="utf-8")
    commit(vault, message)
    return head_sha(vault, short=False)


def _clone(src: Path, dst: Path) -> Path:
    result = _run(["git", "clone", str(src), str(dst)])
    assert result.returncode == 0, result.stderr
    init_repo(dst)  # repo-local identity is not copied by clone
    return dst


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    return _make_vault(tmp_path / "vault-a")


# ------------------------------------------------------------------
# Push
# ------------------------------------------------------------------


class TestSyncPush:
    def test_first_push_sends_full_bundle(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        outcome = sync_push(vault, client)
        assert outcome.pushed is True
        assert outcome.full_bundle is True
        assert outcome.retried_full is False
        assert server.head() == head_sha(vault, short=False)
        assert read_server_head(vault) == server.head()

    def test_second_push_sends_thin_bundle(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        sync_push(vault, client)
        _write_and_commit(vault, "notes.md", "one\n", "add notes")
        _write_and_commit(vault, "notes.md", "two\n", "edit notes")
        outcome = sync_push(vault, client)
        assert outcome.pushed is True
        assert outcome.full_bundle is False
        assert outcome.commits == 2
        assert server.head() == head_sha(vault, short=False)

    def test_push_when_up_to_date_is_a_noop(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        sync_push(vault, client)
        pushes_before = server.push_count
        outcome = sync_push(vault, client)
        assert outcome.pushed is False
        assert server.push_count == pushes_before  # no bundle was uploaded

    def test_push_commits_uncommitted_hand_edits_first(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        before = head_sha(vault, short=False)
        (vault / "hand-edit.md").write_text("edited by hand\n", encoding="utf-8")
        outcome = sync_push(vault, client)
        assert outcome.pushed is True
        assert head_sha(vault, short=False) != before
        assert server.head() == head_sha(vault, short=False)

    def test_push_reports_quarantined_ingest(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        server.ingest = {
            "status": "quarantined",
            "last_ingested_sha": None,
            "quarantined": [
                {
                    "entity_id": "0c5a4f6e-9a1d-4a44-9c80-1a2b3c4d5e6f",
                    "file": "stories/foo.md",
                    "reason": "dangling reference: skill_ids[1] does not resolve",
                }
            ],
        }
        outcome = sync_push(vault, client)
        assert outcome.ingest is not None
        assert outcome.ingest.status == "quarantined"
        assert outcome.ingest.quarantined[0]["file"] == "stories/foo.md"

    def test_push_carries_proposal_contract_warnings(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        # The push succeeds (warnings never fail a push); the outcome carries
        # the server's non-blocking proposal contract warnings.
        server.warnings = [
            {
                "file": "proposals/add-skill-bad.json",
                "pointer": "/payload/proficiency",
                "message": "payload.proficiency is required for add_skill",
                "hint": "add_skill needs a non-empty proficiency.",
            }
        ]
        outcome = sync_push(vault, client)
        assert outcome.pushed is True
        assert len(outcome.warnings) == 1
        warning = outcome.warnings[0]
        assert warning.file == "proposals/add-skill-bad.json"
        assert warning.pointer == "/payload/proficiency"
        assert "required for add_skill" in warning.message
        assert server.head() == head_sha(vault, short=False)  # ref advanced

    def test_push_without_warnings_reports_empty_list(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        outcome = sync_push(vault, client)
        assert outcome.warnings == []

    def test_missing_prerequisites_retries_with_full_bundle(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        sync_push(vault, client)
        server.reset()  # server loses our recorded basis
        _write_and_commit(vault, "notes.md", "after reset\n", "add notes")
        outcome = sync_push(vault, client)
        assert outcome.pushed is True
        assert outcome.retried_full is True
        assert outcome.full_bundle is True
        assert server.head() == head_sha(vault, short=False)

    def test_schema_violation_carries_per_file_errors(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        server.violations = [
            {
                "file": "stories/launch.md",
                "pointer": "/outcome",
                "message": "must be one of win, failure, learning",
                "hint": "Edit the frontmatter 'outcome' key.",
            }
        ]
        with pytest.raises(SchemaViolationError) as exc:
            sync_push(vault, client)
        violation = exc.value.violations[0]
        assert violation.file == "stories/launch.md"
        assert violation.pointer == "/outcome"
        assert "win, failure, learning" in violation.message
        assert server.head() is None  # ref NOT advanced

    def test_bad_token_raises_auth_error(self, vault: Path, server: FakeGitServer):
        transport = httpx.MockTransport(server.handler)
        http = httpx.Client(transport=transport, base_url="http://test")
        bad = GitSyncClient("http://test", token="wrong", client=http)
        with pytest.raises(SyncAuthError, match="login"):
            sync_push(vault, bad)

    def test_push_against_unknown_server_history_is_non_fast_forward(
        self, tmp_path: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        # Someone else populated the server; our fresh vault never fetched.
        other = _make_vault(tmp_path / "vault-other")
        sync_push(other, client)
        mine = _make_vault(tmp_path / "vault-mine")
        with pytest.raises(NonFastForwardError) as exc:
            sync_push(mine, client)
        assert exc.value.server_head == server.head()
        assert "sync pull" in exc.value.hint


# ------------------------------------------------------------------
# Pull
# ------------------------------------------------------------------


class TestSyncPull:
    def test_pull_up_to_date_204(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer
    ) -> None:
        sync_push(vault, client)
        outcome = sync_pull(vault, client)
        assert outcome.fetched is False
        assert outcome.mode == "up_to_date"

    def test_pull_fast_forwards_a_clone(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        clone = _clone(vault, tmp_path / "vault-b")
        _write_and_commit(vault, "notes.md", "from A\n", "add notes")
        sync_push(vault, client)

        outcome = sync_pull(clone, client)
        assert outcome.fetched is True
        assert outcome.mode == "fast_forward"
        assert outcome.head == server.head()
        assert (clone / "notes.md").read_text(encoding="utf-8") == "from A\n"
        assert read_server_head(clone) == server.head()

    def test_pull_into_empty_repo_adopts_history(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        sync_push(vault, client)
        empty = tmp_path / "vault-empty"
        empty.mkdir()
        assert _run(["git", "init"], cwd=empty).returncode == 0
        outcome = sync_pull(empty, client)
        assert outcome.mode == "fast_forward"
        assert head_sha(empty, short=False) == server.head()
        assert (empty / "traitprint.json").is_file()

    def test_divergence_merges_cleanly_across_different_files(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        sync_push(vault, client)
        clone = _clone(vault, tmp_path / "vault-b")

        _write_and_commit(vault, "from-a.md", "A\n", "A's change")
        sync_push(vault, client)
        _write_and_commit(clone, "from-b.md", "B\n", "B's change")

        with pytest.raises(NonFastForwardError):
            sync_push(clone, client)

        outcome = sync_pull(clone, client)
        assert outcome.mode == "merged"
        assert outcome.conflicts == []
        assert (clone / "from-a.md").is_file()
        assert (clone / "from-b.md").is_file()

        pushed = sync_push(clone, client)
        assert pushed.pushed is True
        assert pushed.full_bundle is False
        assert server.head() == head_sha(clone, short=False)

    def test_conflicting_edits_surface_as_git_conflicts(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        sync_push(vault, client)
        clone = _clone(vault, tmp_path / "vault-b")

        _write_and_commit(vault, "headline.md", "A's headline\n", "A edit")
        sync_push(vault, client)
        _write_and_commit(clone, "headline.md", "B's headline\n", "B edit")

        outcome = sync_pull(clone, client)
        assert outcome.fetched is True
        assert outcome.mode == "conflicts"
        assert outcome.conflicts == ["headline.md"]
        # The merge is left in progress with standard conflict markers.
        assert "<<<<<<<" in (clone / "headline.md").read_text(encoding="utf-8")

        # Re-running pull mid-conflict repeats the report — it must NOT
        # auto-commit the conflict markers.
        again = sync_pull(clone, client)
        assert again.mode == "conflicts"
        assert again.conflicts == ["headline.md"]
        assert "<<<<<<<" in (clone / "headline.md").read_text(encoding="utf-8")
        # Pushing mid-conflict is refused with resolution guidance.
        with pytest.raises(GitSyncError, match="unresolved conflicts"):
            sync_push(clone, client)

        # Agent resolution flow: fix the file, commit, push.
        (clone / "headline.md").write_text("merged headline\n", encoding="utf-8")
        assert _run(["git", "add", "-A"], cwd=clone).returncode == 0
        assert (
            _run(
                ["git", "commit", "-m", "Merge remote vault changes"], cwd=clone
            ).returncode
            == 0
        )
        pushed = sync_push(clone, client)
        assert pushed.pushed is True
        assert server.head() == head_sha(clone, short=False)

    def test_merge_abort_can_still_repull(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        # Codex P2 on #42: the server head must not be persisted before
        # integration succeeds, or a post-abort retry pull would send
        # since=<server_head>, get 204, and strand the remote changes.
        from traitprint.gitsync import read_server_head

        sync_push(vault, client)
        clone = _clone(vault, tmp_path / "vault-b")
        basis = read_server_head(clone)

        _write_and_commit(vault, "headline.md", "A's headline\n", "A edit")
        sync_push(vault, client)
        _write_and_commit(clone, "headline.md", "B's headline\n", "B edit")

        outcome = sync_pull(clone, client)
        assert outcome.mode == "conflicts"
        # Server head NOT recorded while the merge is unresolved.
        assert read_server_head(clone) == basis

        assert _run(["git", "merge", "--abort"], cwd=clone).returncode == 0
        retry = sync_pull(clone, client)
        assert retry.fetched is True
        assert retry.mode == "conflicts"  # the divergence is re-fetched, not 204'd

    def test_push_409_does_not_record_server_head(
        self, vault: Path, client: GitSyncClient, server: FakeGitServer, tmp_path: Path
    ) -> None:
        from traitprint.gitsync import NonFastForwardError, read_server_head

        sync_push(vault, client)
        clone = _clone(vault, tmp_path / "vault-b")
        basis = read_server_head(clone)

        _write_and_commit(vault, "a.md", "x\n", "A edit")
        sync_push(vault, client)
        _write_and_commit(clone, "b.md", "y\n", "B edit")

        with pytest.raises(NonFastForwardError):
            sync_push(clone, client)
        # The divergent server commits are not integrated locally yet.
        assert read_server_head(clone) == basis
        # The follow-up pull still fetches them.
        pulled = sync_pull(clone, client)
        assert pulled.fetched is True
        assert pulled.mode in ("merged", "fast_forward")


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------


class TestSyncStatus:
    def test_first_push_pending(self, vault: Path, client: GitSyncClient) -> None:
        outcome = sync_status(vault, client)
        assert outcome.server_head is None
        assert outcome.relation == "first-push-pending"

    def test_in_sync_after_push(self, vault: Path, client: GitSyncClient) -> None:
        sync_push(vault, client)
        outcome = sync_status(vault, client)
        assert outcome.local_head == outcome.server_head
        assert outcome.relation == "in-sync"
        assert outcome.ingest.status == "clean"

    def test_ahead_after_local_commit(self, vault: Path, client: GitSyncClient) -> None:
        sync_push(vault, client)
        _write_and_commit(vault, "notes.md", "local only\n", "local commit")
        outcome = sync_status(vault, client)
        assert outcome.relation == "ahead"

    def test_unknown_when_server_history_never_fetched(
        self, vault: Path, client: GitSyncClient, tmp_path: Path
    ) -> None:
        sync_push(vault, client)
        stranger = _make_vault(tmp_path / "vault-stranger")
        outcome = sync_status(stranger, client)
        assert outcome.relation == "unknown"


# ------------------------------------------------------------------
# CLI: traitprint sync push|pull|status
# ------------------------------------------------------------------


@pytest.fixture()
def patched_http(
    server: FakeGitServer, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeGitServer]:
    """Route every httpx.Client built by gitsync through the fake server."""
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(server.handler)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(gitsync_module.httpx, "Client", patched)
    yield server


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _login(vault: Path) -> None:
    CredentialsStore(vault).save(
        Credentials(api_url="http://test", email="ada@example.test", token=TOKEN)
    )


class TestSyncCli:
    def test_push_requires_login(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push"])
        assert result.exit_code != 0
        assert "Not logged in" in result.output

    def test_push_json_shape(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert set(data) == {
            "pushed",
            "head",
            "server_head",
            "ingest_status",
            "warnings",
        }
        assert data["pushed"] is True
        assert data["head"] == data["server_head"] == patched_http.head()
        assert data["ingest_status"] == "clean"
        assert data["warnings"] == []

    def test_push_human_output(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push"])
        assert result.exit_code == 0, result.output
        assert "Pushed" in result.output
        assert "full bundle" in result.output
        assert "Ingest: clean" in result.output

    def test_push_renders_proposal_warnings(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        patched_http.warnings = [
            {
                "file": "proposals/add-lens-bad.json",
                "pointer": "/payload",
                "message": "payload keys outside the add_lens entity shape: color",
                "hint": "Allowed keys for add_lens: id, slug, name, ….",
            }
        ]
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push"])
        assert result.exit_code == 0, result.output
        assert "Proposal warnings (1)" in result.output
        assert "proposals/add-lens-bad.json @ /payload" in result.output
        assert "outside the add_lens entity shape" in result.output
        assert "hint:" in result.output

    def test_push_json_includes_warnings(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        patched_http.warnings = [
            {
                "file": "proposals/update-skill.json",
                "pointer": "/target_id",
                "message": "target_id abc does not resolve to an existing skill",
                "hint": "Approving this proposal would fail — re-point target_id.",
            }
        ]
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["pushed"] is True
        assert data["warnings"][0]["file"] == "proposals/update-skill.json"
        assert data["warnings"][0]["pointer"] == "/target_id"

    def test_push_409_prints_pull_guidance(
        self,
        runner: CliRunner,
        vault: Path,
        patched_http: FakeGitServer,
        tmp_path: Path,
    ) -> None:
        other = _make_vault(tmp_path / "vault-other")
        _login(other)
        assert runner.invoke(cli, ["--path", str(other), "sync", "push"]).exit_code == 0
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push"])
        assert result.exit_code == 1
        assert "non-fast-forward" in result.output
        assert "traitprint sync pull" in result.output

    def test_push_422_renders_violations_verbatim(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        patched_http.violations = [
            {
                "file": "stories/launch.md",
                "pointer": "/outcome",
                "message": "must be one of win, failure, learning",
                "hint": "Edit the frontmatter 'outcome' key.",
            },
            {
                "file": "skills.json",
                "pointer": "/0/proficiency",
                "message": "must be an integer between 1 and 5",
                "hint": "Re-rate the skill on the 1-5 scale.",
            },
        ]
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push"])
        assert result.exit_code == 1
        for violation in patched_http.violations:
            assert violation["file"] in result.output
            assert violation["pointer"] in result.output
            assert violation["message"] in result.output
            assert violation["hint"] in result.output

    def test_push_422_json_includes_violations(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        patched_http.violations = [
            {"file": "skills.json", "pointer": "/0", "message": "bad", "hint": "fix"}
        ]
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "push", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["pushed"] is False
        assert data["error"]["code"] == "schema_violation"
        assert data["violations"][0]["file"] == "skills.json"

    def test_pull_json_shape_fast_forward(
        self,
        runner: CliRunner,
        vault: Path,
        patched_http: FakeGitServer,
        tmp_path: Path,
    ) -> None:
        _login(vault)
        clone = _clone(vault, tmp_path / "vault-b")
        _login(clone)
        _write_and_commit(vault, "notes.md", "hello\n", "add notes")
        assert runner.invoke(cli, ["--path", str(vault), "sync", "push"]).exit_code == 0
        result = runner.invoke(cli, ["--path", str(clone), "sync", "pull", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert set(data) == {"fetched", "result", "conflicts", "head"}
        assert data["fetched"] is True
        assert data["result"] == "fast_forward"
        assert data["conflicts"] == []
        assert data["head"] == patched_http.head()

    def test_pull_up_to_date(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        _login(vault)
        assert runner.invoke(cli, ["--path", str(vault), "sync", "push"]).exit_code == 0
        result = runner.invoke(cli, ["--path", str(vault), "sync", "pull"])
        assert result.exit_code == 0, result.output
        assert "Already up to date" in result.output

    def test_pull_conflicts_lists_files_and_commands(
        self,
        runner: CliRunner,
        vault: Path,
        patched_http: FakeGitServer,
        tmp_path: Path,
    ) -> None:
        _login(vault)
        assert runner.invoke(cli, ["--path", str(vault), "sync", "push"]).exit_code == 0
        clone = _clone(vault, tmp_path / "vault-b")
        _login(clone)
        _write_and_commit(vault, "headline.md", "A's headline\n", "A edit")
        assert runner.invoke(cli, ["--path", str(vault), "sync", "push"]).exit_code == 0
        _write_and_commit(clone, "headline.md", "B's headline\n", "B edit")

        result = runner.invoke(cli, ["--path", str(clone), "sync", "pull"])
        assert result.exit_code == 1
        assert "Merge conflicts in 1 file(s):" in result.output
        assert "headline.md" in result.output
        assert f"git -C {clone} add -A" in result.output
        assert "traitprint sync push" in result.output

        json_result = runner.invoke(
            cli, ["--path", str(clone), "sync", "pull", "--json"]
        )
        assert json_result.exit_code == 1
        data = json.loads(json_result.output)
        assert data["result"] == "conflicts"
        assert data["conflicts"] == ["headline.md"]

    def test_status_json_shape(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        _login(vault)
        assert runner.invoke(cli, ["--path", str(vault), "sync", "push"]).exit_code == 0
        result = runner.invoke(cli, ["--path", str(vault), "sync", "status", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert set(data) == {
            "local_head",
            "server_head",
            "ingest_status",
            "quarantine_summary",
            "relation",
            "taxonomy",
        }
        assert data["local_head"] == data["server_head"] == patched_http.head()
        assert data["ingest_status"] == "clean"
        assert data["quarantine_summary"] == {"count": 0, "items": []}
        assert data["relation"] == "in-sync"
        assert data["taxonomy"]["server_version"] is None
        assert data["taxonomy"]["advisory"] is None

    def test_status_renders_quarantined_entities(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        patched_http.ingest = {
            "status": "quarantined",
            "last_ingested_sha": None,
            "quarantined": [
                {
                    "entity_id": "0c5a4f6e-9a1d-4a44-9c80-1a2b3c4d5e6f",
                    "file": "stories/foo.md",
                    "reason": "dangling reference: skill_ids[1] does not resolve",
                }
            ],
        }
        _login(vault)
        result = runner.invoke(cli, ["--path", str(vault), "sync", "status"])
        assert result.exit_code == 0, result.output
        assert "quarantined (1 entities)" in result.output
        assert "stories/foo.md" in result.output
        assert "dangling reference" in result.output

    def test_status_expired_token_hints_relogin(
        self, runner: CliRunner, vault: Path, patched_http: FakeGitServer
    ) -> None:
        CredentialsStore(vault).save(
            Credentials(api_url="http://test", email="a@b.c", token="expired")
        )
        result = runner.invoke(cli, ["--path", str(vault), "sync", "status"])
        assert result.exit_code != 0
        assert "traitprint login" in result.output

    def test_legacy_push_help_points_at_sync(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["push", "--help"])
        assert "traitprint sync push" in result.output
        legacy_pull = runner.invoke(cli, ["pull", "--help"])
        assert "traitprint sync pull" in legacy_pull.output


# ------------------------------------------------------------------
# download_taxonomy (GET /vault-git/taxonomy)
# ------------------------------------------------------------------


def _taxonomy_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> GitSyncClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://test")
    return GitSyncClient("http://test", token=TOKEN, client=http)


def test_download_taxonomy_returns_envelope() -> None:
    artifact = {
        "version": 3,
        "lineage": "canonical",
        "skills": [
            {
                "id": "a1b2c3d4-0001-4000-8000-000000000001",
                "name": "Python",
                "category": "technical",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/functions/v1/vault-git/taxonomy"
        # The local (version, lineage) are sent so the server can 204 when current.
        assert request.url.params.get("since") == "2"
        assert request.url.params.get("lineage") == "canonical"
        return httpx.Response(200, json=artifact)

    with _taxonomy_client(handler) as client:
        assert client.download_taxonomy(2, "canonical") == artifact


def test_download_taxonomy_204_returns_none() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with _taxonomy_client(handler) as client:
        assert client.download_taxonomy(2, "canonical") is None


def test_download_taxonomy_401_raises_auth_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "auth_expired"}})

    with _taxonomy_client(handler) as client, pytest.raises(SyncAuthError):
        client.download_taxonomy(2, "canonical")


def test_download_taxonomy_rejects_non_envelope() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nope": True})

    with _taxonomy_client(handler) as client, pytest.raises(GitSyncError):
        client.download_taxonomy(2, "canonical")
