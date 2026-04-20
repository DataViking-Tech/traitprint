"""High-level push/pull orchestration built on top of ``CloudClient``.

These helpers compute a decision (push / pull / noop / conflict) from local
and server timestamps so the CLI can report the same plan in ``--dry-run``
mode and in live execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from traitprint.cloud import CloudClient, ConflictError, PullResult, PushResult
from traitprint.schema import VaultSchema
from traitprint.vault import VaultStore

Direction = Literal["push", "pull", "noop", "conflict"]


@dataclass(frozen=True)
class SyncPlan:
    """Decision produced by ``plan_push`` / ``plan_pull``."""

    direction: Direction
    local_updated_at: datetime | None
    server_updated_at: datetime | None
    reason: str


def _fmt(ts: datetime | None) -> str:
    return ts.isoformat() if ts else "never"


def plan_push(local: VaultSchema, server_updated_at: datetime | None) -> SyncPlan:
    """Decide whether ``local`` should be pushed."""
    local_ts = local.updated_at
    if server_updated_at is None:
        return SyncPlan(
            direction="push",
            local_updated_at=local_ts,
            server_updated_at=None,
            reason="No server vault yet; local will be uploaded.",
        )
    if local_ts > server_updated_at:
        return SyncPlan(
            direction="push",
            local_updated_at=local_ts,
            server_updated_at=server_updated_at,
            reason=(
                f"Local ({_fmt(local_ts)}) is newer than server "
                f"({_fmt(server_updated_at)})."
            ),
        )
    if local_ts == server_updated_at:
        return SyncPlan(
            direction="noop",
            local_updated_at=local_ts,
            server_updated_at=server_updated_at,
            reason="Local and server are already in sync.",
        )
    return SyncPlan(
        direction="conflict",
        local_updated_at=local_ts,
        server_updated_at=server_updated_at,
        reason=(
            f"Server ({_fmt(server_updated_at)}) is newer than local "
            f"({_fmt(local_ts)}). Run 'traitprint pull' first."
        ),
    )


def plan_pull(local: VaultSchema | None, pull: PullResult) -> SyncPlan:
    """Decide whether ``pull.vault`` should overwrite ``local``."""
    local_ts = local.updated_at if local else None
    server_ts = pull.server_updated_at
    if pull.vault is None or server_ts is None:
        return SyncPlan(
            direction="noop",
            local_updated_at=local_ts,
            server_updated_at=None,
            reason="No server vault exists. Nothing to pull.",
        )
    if local is None:
        return SyncPlan(
            direction="pull",
            local_updated_at=None,
            server_updated_at=server_ts,
            reason="No local vault; server vault will be installed.",
        )
    if local_ts is None or server_ts > local_ts:
        return SyncPlan(
            direction="pull",
            local_updated_at=local_ts,
            server_updated_at=server_ts,
            reason=(
                f"Server ({_fmt(server_ts)}) is newer than local ({_fmt(local_ts)})."
            ),
        )
    if server_ts == local_ts:
        return SyncPlan(
            direction="noop",
            local_updated_at=local_ts,
            server_updated_at=server_ts,
            reason="Local and server are already in sync.",
        )
    return SyncPlan(
        direction="conflict",
        local_updated_at=local_ts,
        server_updated_at=server_ts,
        reason=(
            f"Local ({_fmt(local_ts)}) is newer than server "
            f"({_fmt(server_ts)}). Run 'traitprint push' to upload."
        ),
    )


def do_push(
    store: VaultStore, client: CloudClient, *, dry_run: bool
) -> tuple[SyncPlan, PushResult | None]:
    """Plan and optionally execute a push."""
    local = store.load()
    pull = client.pull()
    plan = plan_push(local, pull.server_updated_at)
    if dry_run or plan.direction != "push":
        return plan, None
    result = client.push(local)
    return plan, result


def do_pull(
    store: VaultStore, client: CloudClient, *, dry_run: bool
) -> tuple[SyncPlan, PullResult | None]:
    """Plan and optionally execute a pull."""
    local = store.load() if store.exists() else None
    pull = client.pull()
    plan = plan_pull(local, pull)
    if dry_run or plan.direction != "pull":
        return plan, pull
    if pull.vault is not None:
        # ``bump_updated_at=False`` so we preserve the server timestamp the
        # push protocol relies on for LWW.
        store.save(pull.vault, bump_updated_at=False)
    return plan, pull


__all__ = [
    "SyncPlan",
    "ConflictError",
    "plan_push",
    "plan_pull",
    "do_push",
    "do_pull",
]
