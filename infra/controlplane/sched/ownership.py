from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

from tools import runtime_policy as _runtime_policy


OWNERSHIP_SCHEMA_VERSION = "sched_v2_ownership_contract_v1"

MODE_LEGACY = "legacy"
MODE_V2_SHADOW = "v2_shadow"
MODE_V2_CANARY = "v2_canary"
MODE_V2_PRIMARY = "v2_primary"
SUPPORTED_MODES = frozenset({MODE_LEGACY, MODE_V2_SHADOW, MODE_V2_CANARY, MODE_V2_PRIMARY})
DEFAULT_OWNER_MODE = MODE_V2_PRIMARY
OWNER_MODE_POLICY_KEY = "scheduler.v2.owner_mode"

WORKFLOW_KIND = "intent_family_v2"
CANONICAL_WRITE_MODES = frozenset({MODE_V2_PRIMARY})
SHADOW_ONLY_MODES = frozenset({MODE_LEGACY, MODE_V2_SHADOW, MODE_V2_CANARY})


@dataclass(frozen=True)
class SchedulerOwnership:
    schema_version: str
    mode: str
    workflow_kind: str
    canonical_owner: str
    v2_can_write_canonical: bool
    legacy_can_write_canonical: bool
    shadow_artifacts_isolated: bool
    canary_parent_uids: Tuple[str, ...]
    api_surface: str
    watch_surface: str
    watchdog_authority: str
    public_port_policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_csv(raw: Any) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(raw or "").split(",") if part.strip())


def normalize_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower() or MODE_LEGACY
    if mode == "shadow":
        mode = MODE_V2_SHADOW
    elif mode == "canary":
        mode = MODE_V2_CANARY
    elif mode == "primary":
        mode = MODE_V2_PRIMARY
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported scheduler v2 ownership mode: {mode}")
    return mode


def _default_owner_mode() -> str:
    raw_env = str(os.environ.get("EIEL_SCHED_V2_OWNER_MODE") or "").strip()
    if raw_env:
        return raw_env
    return _runtime_policy.get_runtime_policy_text(OWNER_MODE_POLICY_KEY, DEFAULT_OWNER_MODE)


def load_ownership(
    *,
    repo_root: Path | None = None,
    mode: str | None = None,
    canary_parent_uids: tuple[str, ...] | None = None,
) -> SchedulerOwnership:
    normalized_mode = normalize_mode(mode if mode is not None else _default_owner_mode())
    canary_uids = (
        canary_parent_uids
        if canary_parent_uids is not None
        else _split_csv(os.environ.get("EIEL_SCHED_V2_CANARY_PARENT_UIDS", ""))
    )
    canonical_owner = "sched_v2" if normalized_mode == MODE_V2_PRIMARY else "legacy_scheduler"
    return SchedulerOwnership(
        schema_version=OWNERSHIP_SCHEMA_VERSION,
        mode=normalized_mode,
        workflow_kind=WORKFLOW_KIND,
        canonical_owner=canonical_owner,
        v2_can_write_canonical=normalized_mode in CANONICAL_WRITE_MODES,
        legacy_can_write_canonical=normalized_mode in SHADOW_ONLY_MODES,
        shadow_artifacts_isolated=normalized_mode in SHADOW_ONLY_MODES,
        canary_parent_uids=tuple(canary_uids or ()),
        api_surface="existing_plane_schedule_api",
        watch_surface="shared_scheduler_projection_bundle",
        watchdog_authority="read_health_and_receipts_only_no_direct_scheduler_truth_mutation",
        public_port_policy="no_new_public_port; serve through existing plane API unless explicitly promoted later",
    )


def assert_v2_can_evaluate_family(ownership: SchedulerOwnership, *, parent_uid: str) -> None:
    if ownership.mode == MODE_LEGACY:
        return
    if ownership.mode == MODE_V2_SHADOW:
        return
    if ownership.mode == MODE_V2_CANARY:
        allowed = {uid.upper() for uid in ownership.canary_parent_uids}
        if str(parent_uid or "").strip().upper() not in allowed:
            raise PermissionError(f"v2 canary mode does not own parent {parent_uid}")
        return
    if ownership.mode == MODE_V2_PRIMARY:
        return
    raise PermissionError(f"unsupported ownership mode: {ownership.mode}")


def assert_canonical_write_allowed(ownership: SchedulerOwnership, *, parent_uid: str) -> None:
    assert_v2_can_evaluate_family(ownership, parent_uid=parent_uid)
    if not ownership.v2_can_write_canonical:
        raise PermissionError(f"v2 canonical writes are blocked in {ownership.mode} mode")


def ownership_receipt(ownership: SchedulerOwnership, *, parent_uid: str) -> Mapping[str, Any]:
    return {
        "schema_version": ownership.schema_version,
        "parent_uid": str(parent_uid or "").strip(),
        "mode": ownership.mode,
        "workflow_kind": ownership.workflow_kind,
        "canonical_owner": ownership.canonical_owner,
        "v2_can_write_canonical": ownership.v2_can_write_canonical,
        "legacy_can_write_canonical": ownership.legacy_can_write_canonical,
        "shadow_artifacts_isolated": ownership.shadow_artifacts_isolated,
        "watchdog_authority": ownership.watchdog_authority,
        "api_surface": ownership.api_surface,
        "watch_surface": ownership.watch_surface,
        "public_port_policy": ownership.public_port_policy,
    }
