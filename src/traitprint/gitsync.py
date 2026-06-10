"""Sync-v1 client: git-bundle-over-HTTP vault sync (tp-an-020).

Implements the client half of the sync-v1 protocol contract
(``docs/schema/sync-v1/README.md``): the unit of transfer is a real git
bundle carried over plain HTTPS to three endpoints under the
authenticated API base —

- ``POST /vault-git/push``  — upload a bundle advancing ``main`` to the
  sha in the ``X-Traitprint-Head`` header (fast-forward only; the
  server never merges).
- ``GET  /vault-git/fetch`` — download a bundle of commits the client
  is missing (``?since=<sha>`` for an incremental bundle, ``204`` when
  already up to date).
- ``GET  /vault-git/info``  — cheap state probe (server head + ingest
  report) powering ``traitprint sync status``.

Bundle semantics: the first push (server head ``null``) sends a full
bundle (``git bundle create out.bundle main``); every later push sends
a thin bundle against the last-known server head
(``git bundle create out.bundle <server_head>..main``). The last-known
server head is persisted in ``<gitdir>/traitprint/server-head`` and
refreshed from every successful push, fetch, and 409 response. A 422
``missing_prerequisites`` rejection triggers one automatic retry with a
full bundle.

Only ``refs/heads/main`` syncs. Local vaults may live on any branch
(``git init`` defaults vary), so before bundling the client mirrors the
vault HEAD into a local ``refs/heads/main`` ref — the bundle then
always records the ref name the server expects.

Conflict model: a 409 (non-fast-forward) push is resolved locally —
fetch the server's commits, ``git merge`` them (file-level granularity,
standard conflict markers), then push the merge commit. The CLI prints
the conflicted files and the exact commands to finish the merge; agents
resolve with their file tools and rerun.

Auth is unchanged from the legacy transport (``cloud.py``): public
Supabase ``apikey`` header plus ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from traitprint.cloud import SUPABASE_ANON_KEY
from traitprint.credentials import Credentials
from traitprint.git_ops import commit as git_commit
from traitprint.git_ops import head_sha

VAULT_GIT_INFO = "/functions/v1/vault-git/info"
VAULT_GIT_PUSH = "/functions/v1/vault-git/push"
VAULT_GIT_FETCH = "/functions/v1/vault-git/fetch"

#: Relative to the vault's git dir (``.git/traitprint/server-head``).
SERVER_HEAD_FILE = Path("traitprint") / "server-head"

#: The only ref the protocol syncs.
SYNC_REF = "refs/heads/main"

MERGE_COMMIT_MESSAGE = "Merge remote vault changes"


# ------------------------------------------------------------------
# Errors
# ------------------------------------------------------------------


class GitSyncError(Exception):
    """Base class for sync-v1 failures."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class SyncAuthError(GitSyncError):
    """401 — token missing/expired; the fix is to log in again."""


class NonFastForwardError(GitSyncError):
    """409 — the server has commits the client does not."""

    def __init__(self, message: str, server_head: str | None, hint: str) -> None:
        super().__init__(message, hint)
        self.server_head = server_head


@dataclass(frozen=True)
class Violation:
    """One entry of a 422 ``violations`` list — agent-actionable."""

    file: str
    pointer: str
    message: str
    hint: str

    def as_dict(self) -> dict[str, str]:
        return {
            "file": self.file,
            "pointer": self.pointer,
            "message": self.message,
            "hint": self.hint,
        }


class SchemaViolationError(GitSyncError):
    """422 ``schema_violation`` — ref NOT advanced; per-file errors attached."""

    def __init__(self, message: str, hint: str, violations: list[Violation]) -> None:
        super().__init__(message, hint)
        self.violations = violations


class MissingPrerequisitesError(GitSyncError):
    """422 ``missing_prerequisites`` — the thin bundle's basis is unknown."""


# ------------------------------------------------------------------
# Response shapes
# ------------------------------------------------------------------


@dataclass(frozen=True)
class IngestReport:
    """The ``ingest`` block of info/push responses."""

    status: str  # "clean" | "pending" | "quarantined" | "" (absent)
    last_ingested_sha: str | None = None
    quarantined: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ServerInfo:
    """``GET /vault-git/info`` response."""

    head: str | None
    ingest: IngestReport


@dataclass(frozen=True)
class PushResponse:
    """Accepted ``POST /vault-git/push`` response (HTTP 200)."""

    head: str
    ingest: IngestReport


@dataclass(frozen=True)
class FetchResponse:
    """``GET /vault-git/fetch`` response. ``bundle is None`` == 204."""

    bundle: bytes | None
    head: str | None
    full: bool


# ------------------------------------------------------------------
# Orchestration outcomes
# ------------------------------------------------------------------


@dataclass(frozen=True)
class PushOutcome:
    """Result of :func:`sync_push`."""

    pushed: bool
    head: str
    server_head: str | None
    ingest: IngestReport | None
    commits: int = 0
    full_bundle: bool = False
    retried_full: bool = False


@dataclass(frozen=True)
class PullOutcome:
    """Result of :func:`sync_pull`.

    ``mode`` is one of ``up_to_date``, ``fast_forward``, ``merged``,
    ``conflicts``. On ``conflicts`` the merge is left in progress with
    standard conflict markers in the listed files.
    """

    fetched: bool
    mode: str
    conflicts: list[str]
    head: str
    server_head: str | None


@dataclass(frozen=True)
class StatusOutcome:
    """Result of :func:`sync_status`."""

    local_head: str | None
    server_head: str | None
    relation: str  # empty|first-push-pending|in-sync|ahead|behind|diverged|unknown
    ingest: IngestReport


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _parse_ingest(raw: Any) -> IngestReport:
    if not isinstance(raw, dict):
        return IngestReport(status="")
    quarantined: list[dict[str, str]] = []
    for item in raw.get("quarantined") or []:
        if isinstance(item, dict):
            quarantined.append({str(k): str(v) for k, v in item.items()})
    return IngestReport(
        status=str(raw.get("status") or ""),
        last_ingested_sha=raw.get("last_ingested_sha") or None,
        quarantined=quarantined,
    )


def _parse_error(data: Any) -> tuple[str, str, str]:
    """Return (code, message, hint) from the sync-v1 error envelope."""
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return "", "", ""
    return (
        str(err.get("code") or ""),
        str(err.get("message") or ""),
        str(err.get("hint") or ""),
    )


def _parse_violations(data: Any) -> list[Violation]:
    out: list[Violation] = []
    raw = data.get("violations") if isinstance(data, dict) else None
    for item in raw or []:
        if isinstance(item, dict):
            out.append(
                Violation(
                    file=str(item.get("file") or ""),
                    pointer=str(item.get("pointer") or ""),
                    message=str(item.get("message") or ""),
                    hint=str(item.get("hint") or ""),
                )
            )
    return out


def _json_or_empty(response: httpx.Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


# ------------------------------------------------------------------
# Local git operations
# ------------------------------------------------------------------


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the vault; never raises."""
    return subprocess.run(
        ["git", *args],
        cwd=str(vault),
        capture_output=True,
        text=True,
        check=False,
    )


def _git_ok(vault: Path, *args: str) -> str:
    """Run a git command; raise :class:`GitSyncError` on failure."""
    result = _git(vault, *args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "unknown git error"
        raise GitSyncError(f"git {args[0]} failed: {detail}")
    return result.stdout


def _git_dir(vault: Path) -> Path:
    out = _git_ok(vault, "rev-parse", "--git-dir").strip()
    path = Path(out)
    return path if path.is_absolute() else vault / path


def read_server_head(vault: Path) -> str | None:
    """Return the persisted last-known server head sha, if any."""
    try:
        path = _git_dir(vault) / SERVER_HEAD_FILE
    except GitSyncError:
        return None
    if not path.is_file():
        return None
    sha = path.read_text(encoding="utf-8").strip()
    return sha or None


def write_server_head(vault: Path, sha: str) -> None:
    """Persist the last-known server head sha in the vault's git dir."""
    path = _git_dir(vault) / SERVER_HEAD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha + "\n", encoding="utf-8")


def commit_exists(vault: Path, sha: str) -> bool:
    """True when *sha* resolves to a commit in the vault repo."""
    return _git(vault, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def is_ancestor(vault: Path, ancestor: str, descendant: str) -> bool:
    """True when *ancestor* is an ancestor of *descendant* (or equal)."""
    result = _git(vault, "merge-base", "--is-ancestor", ancestor, descendant)
    return result.returncode == 0


def _commit_count(vault: Path, basis: str | None, head: str) -> int:
    rev = f"{basis}..{head}" if basis else head
    result = _git(vault, "rev-list", "--count", rev)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _mirror_main_ref(vault: Path, sha: str) -> None:
    """Point local ``refs/heads/main`` at *sha* so bundles carry the sync ref.

    Vaults created before sync-v1 may live on another default branch;
    the protocol only syncs ``refs/heads/main``, so the client keeps a
    local ``main`` ref mirroring the vault HEAD. A no-op when the vault
    is already on ``main``.
    """
    _git_ok(vault, "update-ref", SYNC_REF, sha)


def commit_if_dirty(vault: Path, message: str) -> bool:
    """Commit any uncommitted vault changes (hand edits) before syncing.

    Returns True when a commit was made. Unlike :func:`git_ops.commit`,
    a clean tree is a no-op — no empty commits.
    """
    status = _git(vault, "status", "--porcelain")
    if status.returncode != 0 or not status.stdout.strip():
        return False
    return git_commit(vault, message)


def create_bundle(vault: Path, basis: str | None) -> bytes:
    """Create a git bundle of ``main`` (full, or thin against *basis*)."""
    rev = f"{basis}..main" if basis else "main"
    with tempfile.TemporaryDirectory(prefix="traitprint-sync-") as tmp:
        bundle_path = Path(tmp) / "push.bundle"
        result = _git(vault, "bundle", "create", str(bundle_path), rev)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise GitSyncError(f"git bundle create failed: {detail}")
        return bundle_path.read_bytes()


def apply_bundle(vault: Path, bundle: bytes) -> str:
    """Verify a fetched bundle and fetch its ``main`` ref. Returns the tip sha.

    Per the contract, bundles are valid fetch remotes: ``git bundle
    verify`` checks self-containedness against the local repo, then
    ``git fetch <bundle> main`` lands the commits in ``FETCH_HEAD``.
    """
    with tempfile.TemporaryDirectory(prefix="traitprint-sync-") as tmp:
        bundle_path = Path(tmp) / "fetch.bundle"
        bundle_path.write_bytes(bundle)
        verify = _git(vault, "bundle", "verify", str(bundle_path))
        if verify.returncode != 0:
            detail = (verify.stderr or verify.stdout).strip()
            raise GitSyncError(
                f"fetched bundle failed verification: {detail}",
                hint="Re-run 'traitprint sync pull'; if it persists the "
                "server bundle basis is wrong.",
            )
        _git_ok(vault, "fetch", str(bundle_path), "main")
        return _git_ok(vault, "rev-parse", "FETCH_HEAD").strip()


def integrate_fetched(vault: Path, fetched_sha: str) -> tuple[str, list[str]]:
    """Fast-forward or merge *fetched_sha* into the current branch.

    Returns ``(mode, conflicted_files)`` where mode is one of
    ``up_to_date``, ``fast_forward``, ``merged``, ``conflicts``. On
    ``conflicts`` the merge is left in progress for local resolution.
    """
    head = head_sha(vault, short=False)
    if not head:
        # Empty local repo (clone case): adopt the fetched history.
        _git_ok(vault, "reset", "--hard", fetched_sha)
        return ("fast_forward", [])
    if is_ancestor(vault, fetched_sha, head):
        return ("up_to_date", [])
    if is_ancestor(vault, head, fetched_sha):
        _git_ok(vault, "merge", "--ff-only", fetched_sha)
        return ("fast_forward", [])
    merge = _git(
        vault,
        "merge",
        "--no-ff",
        "--allow-unrelated-histories",
        "-m",
        MERGE_COMMIT_MESSAGE,
        fetched_sha,
    )
    if merge.returncode == 0:
        return ("merged", [])
    conflicts = [
        line
        for line in _git(
            vault, "diff", "--name-only", "--diff-filter=U"
        ).stdout.splitlines()
        if line
    ]
    if not conflicts:
        detail = (merge.stderr or merge.stdout).strip()
        raise GitSyncError(f"git merge failed: {detail}")
    return ("conflicts", conflicts)


# ------------------------------------------------------------------
# HTTP client
# ------------------------------------------------------------------


class GitSyncClient:
    """HTTP wrapper around the three ``/vault-git/*`` endpoints.

    Same construction and auth plumbing as :class:`cloud.CloudClient`:
    a public Supabase ``apikey`` header plus a bearer token (Supabase
    access JWT from ``traitprint login``, or a ``TRAITPRINT_API_TOKEN``
    API key).
    """

    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def __enter__(self) -> GitSyncClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @classmethod
    def from_credentials(
        cls,
        creds: Credentials,
        *,
        client: httpx.Client | None = None,
    ) -> GitSyncClient:
        """Build a client from stored credentials (token may be empty)."""
        return cls(creds.api_url, creds.token or None, client=client)

    # -- request plumbing ------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            raise SyncAuthError("Not authenticated. Run 'traitprint login' first.")
        return {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {self.token}",
        }

    @staticmethod
    def _auth_failed() -> SyncAuthError:
        return SyncAuthError("Token expired or invalid. Run 'traitprint login' again.")

    # -- endpoints --------------------------------------------------

    def info(self) -> ServerInfo:
        """``GET /vault-git/info`` — server head + ingest report."""
        url = f"{self.api_url}{VAULT_GIT_INFO}"
        try:
            response = self._client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            raise GitSyncError(f"Info request failed: {exc}") from exc
        if response.status_code == 401:
            raise self._auth_failed()
        if response.status_code >= 400:
            raise GitSyncError(
                f"Info failed: HTTP {response.status_code} {response.text[:200]}"
            )
        data = _json_or_empty(response)
        head = data.get("head") if isinstance(data, dict) else None
        return ServerInfo(
            head=str(head) if head else None,
            ingest=_parse_ingest(data.get("ingest") if isinstance(data, dict) else {}),
        )

    def fetch(self, since: str | None) -> FetchResponse:
        """``GET /vault-git/fetch[?since=<sha>]`` — bundle of missing commits."""
        url = f"{self.api_url}{VAULT_GIT_FETCH}"
        params = {"since": since} if since else None
        try:
            response = self._client.get(
                url, params=params, headers=self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise GitSyncError(f"Fetch request failed: {exc}") from exc
        if response.status_code == 401:
            raise self._auth_failed()
        if response.status_code == 204:
            return FetchResponse(bundle=None, head=since, full=False)
        if response.status_code >= 400:
            raise GitSyncError(
                f"Fetch failed: HTTP {response.status_code} {response.text[:200]}"
            )
        head = response.headers.get("X-Traitprint-Head") or None
        full = response.headers.get("X-Traitprint-Bundle", "") == "full"
        return FetchResponse(bundle=response.content, head=head, full=full)

    def push(self, bundle: bytes, head: str) -> PushResponse:
        """``POST /vault-git/push`` — upload a bundle advancing ``main``.

        Raises :class:`SyncAuthError` (401),
        :class:`NonFastForwardError` (409),
        :class:`MissingPrerequisitesError` /
        :class:`SchemaViolationError` (422).
        """
        url = f"{self.api_url}{VAULT_GIT_PUSH}"
        headers = {
            **self._auth_headers(),
            "Content-Type": "application/octet-stream",
            "X-Traitprint-Head": head,
        }
        try:
            response = self._client.post(url, content=bundle, headers=headers)
        except httpx.HTTPError as exc:
            raise GitSyncError(f"Push request failed: {exc}") from exc

        if response.status_code == 401:
            raise self._auth_failed()
        if response.status_code == 409:
            data = _json_or_empty(response)
            _, message, hint = _parse_error(data)
            server_head = data.get("server_head") if isinstance(data, dict) else None
            raise NonFastForwardError(
                message
                or "Push rejected: the server has commits you don't have "
                "(non-fast-forward).",
                server_head=str(server_head) if server_head else None,
                hint=hint
                or "Run 'traitprint sync pull', resolve any conflicts, then "
                "run 'traitprint sync push' again.",
            )
        if response.status_code == 422:
            data = _json_or_empty(response)
            code, message, hint = _parse_error(data)
            if code == "missing_prerequisites":
                raise MissingPrerequisitesError(
                    message
                    or "Push rejected: the bundle's basis commit is "
                    "unknown to the server.",
                    hint=hint or "Retry with a full bundle.",
                )
            raise SchemaViolationError(
                message
                or "Push rejected: the vault tree violates the "
                "vault v1 contract (ref not advanced).",
                hint=hint
                or "Fix the listed files, commit, then run "
                "'traitprint sync push' again.",
                violations=_parse_violations(data),
            )
        if response.status_code >= 400:
            raise GitSyncError(
                f"Push failed: HTTP {response.status_code} {response.text[:200]}"
            )
        data = _json_or_empty(response)
        new_head = str(data.get("head") or head) if isinstance(data, dict) else head
        return PushResponse(
            head=new_head,
            ingest=_parse_ingest(data.get("ingest") if isinstance(data, dict) else {}),
        )


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------


def sync_push(vault: Path, client: GitSyncClient) -> PushOutcome:
    """Push local vault history to the hosted remote.

    Commits any uncommitted hand edits first, then sends a thin bundle
    against the last-known server head (full bundle when none is
    known). A ``missing_prerequisites`` rejection is retried once with
    a full bundle. Raises :class:`NonFastForwardError` on 409 — the
    caller routes the user/agent through ``sync pull`` + merge.
    """
    commit_if_dirty(vault, "traitprint sync push")
    head = head_sha(vault, short=False)
    if not head:
        raise GitSyncError(
            "The vault has no commits to push.",
            hint="Run 'traitprint init' (or make a vault write) first.",
        )

    basis = read_server_head(vault)
    if basis is not None and not commit_exists(vault, basis):
        basis = None
    if basis is None:
        info = client.info()
        basis = info.head
        if basis is not None and not commit_exists(vault, basis):
            # The server is ahead on history we've never fetched — a
            # push is guaranteed non-fast-forward; skip the upload.
            raise NonFastForwardError(
                "Push rejected: the server has commits you don't have "
                "(non-fast-forward).",
                server_head=basis,
                hint="Run 'traitprint sync pull', resolve any conflicts, "
                "then run 'traitprint sync push' again.",
            )
    if basis == head:
        write_server_head(vault, head)
        return PushOutcome(pushed=False, head=head, server_head=head, ingest=None)

    _mirror_main_ref(vault, head)
    retried_full = False
    full_bundle = basis is None
    bundle = create_bundle(vault, basis)
    try:
        response = client.push(bundle, head)
    except MissingPrerequisitesError:
        # The server lost our recorded basis; fall back to a full bundle.
        retried_full = True
        full_bundle = True
        response = client.push(create_bundle(vault, None), head)
    except NonFastForwardError as exc:
        if exc.server_head:
            # Remember the server head for the follow-up pull/merge.
            write_server_head(vault, exc.server_head)
        raise

    write_server_head(vault, response.head)
    return PushOutcome(
        pushed=True,
        head=head,
        server_head=response.head,
        ingest=response.ingest,
        commits=_commit_count(vault, None if full_bundle else basis, head),
        full_bundle=full_bundle,
        retried_full=retried_full,
    )


def sync_pull(vault: Path, client: GitSyncClient) -> PullOutcome:
    """Fetch server commits and fast-forward or merge them locally.

    ``since`` is the last-known server head when it exists locally
    (incremental bundle); otherwise the server sends a full bundle. On
    merge conflicts the merge is left in progress and the conflicted
    files are returned — resolve them with file tools, commit, then
    ``sync push``.
    """
    commit_if_dirty(vault, "traitprint sync pull")
    since = read_server_head(vault)
    if since is not None and not commit_exists(vault, since):
        since = None

    fetched = client.fetch(since)
    if fetched.bundle is None:
        head = head_sha(vault, short=False)
        return PullOutcome(
            fetched=False,
            mode="up_to_date",
            conflicts=[],
            head=head,
            server_head=fetched.head,
        )

    fetched_sha = apply_bundle(vault, fetched.bundle)
    server_head = fetched.head or fetched_sha
    write_server_head(vault, server_head)
    mode, conflicts = integrate_fetched(vault, fetched_sha)
    head = head_sha(vault, short=False)
    if mode != "conflicts":
        _mirror_main_ref(vault, head)
    return PullOutcome(
        fetched=True,
        mode=mode,
        conflicts=conflicts,
        head=head,
        server_head=server_head,
    )


def sync_status(vault: Path, client: GitSyncClient) -> StatusOutcome:
    """Probe local vs. server sync state (``GET /vault-git/info``)."""
    info = client.info()
    local = head_sha(vault, short=False) or None
    server = info.head

    if local is None and server is None:
        relation = "empty"
    elif server is None:
        relation = "first-push-pending"
    elif local is None:
        relation = "behind"
    elif local == server:
        relation = "in-sync"
    elif not commit_exists(vault, server):
        relation = "unknown"
    elif is_ancestor(vault, server, local):
        relation = "ahead"
    elif is_ancestor(vault, local, server):
        relation = "behind"
    else:
        relation = "diverged"

    return StatusOutcome(
        local_head=local,
        server_head=server,
        relation=relation,
        ingest=info.ingest,
    )


__all__ = [
    "FetchResponse",
    "GitSyncClient",
    "GitSyncError",
    "IngestReport",
    "MissingPrerequisitesError",
    "NonFastForwardError",
    "PullOutcome",
    "PushOutcome",
    "PushResponse",
    "SchemaViolationError",
    "ServerInfo",
    "StatusOutcome",
    "SyncAuthError",
    "Violation",
    "apply_bundle",
    "commit_exists",
    "commit_if_dirty",
    "create_bundle",
    "integrate_fetched",
    "is_ancestor",
    "read_server_head",
    "sync_pull",
    "sync_push",
    "sync_status",
    "write_server_head",
]
