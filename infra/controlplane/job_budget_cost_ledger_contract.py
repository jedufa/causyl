"""Canonical job-budget allocation and cost-ledger helpers backed by a durable contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/job_budget_cost_ledger_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_job_budget_cost_ledger_schema_v1.md")
STORAGE_ROOT_REL = Path("runtime/state/job_budget_cost_ledger")
SCHEMA_VERSION = "job_budget_cost_ledger_contract_v1"
RECORD_SCHEMA_VERSION = "job_budget_cost_ledger_record_v1"
UID = "PIS-20260406-183206Z-AC5E"
REMAINING_BUDGET_FORMULA = (
    "sum(allocation.amount_minor_units) - sum(cost.amount_minor_units where "
    "direction=debit) + sum(cost.amount_minor_units where direction=credit)"
)
DETERMINISTIC_REPLAY_ORDER: Tuple[str, ...] = (
    "recorded_at_utc",
    "entry_uid",
    "record_kind",
)

ALLOCATION_LOG_FILENAME = "allocations.jsonl"
COST_EVENT_LOG_FILENAME = "cost_events.jsonl"

ALLOCATION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "allocation_uid",
    "job_uid",
    "recorded_at_utc",
    "amount_minor_units",
    "unit",
    "allocation_reason_code",
)

COST_EVENT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "cost_event_uid",
    "job_uid",
    "recorded_at_utc",
    "amount_minor_units",
    "unit",
    "direction",
    "cost_reason_code",
)

ALLOCATION_REASON_CODES: Tuple[str, ...] = (
    "initial_grant",
    "top_up",
    "manual_correction",
    "recovery_correction",
)

COST_REASON_CODES: Tuple[str, ...] = (
    "reservation",
    "usage",
    "refund",
    "manual_correction",
    "recovery_correction",
)

COST_DIRECTIONS: Tuple[str, ...] = ("debit", "credit")
FORBIDDEN_DERIVED_FIELDS: Tuple[str, ...] = (
    "remaining_budget_minor_units",
    "balance_after_event_minor_units",
    "cached_remaining_budget_minor_units",
)

_PATH_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_UNIT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class JobBudgetCostLedgerContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    storage_root_rel: Path
    allocation_log_filename: str
    cost_event_log_filename: str
    allocation_reason_codes: Tuple[str, ...]
    cost_reason_codes: Tuple[str, ...]
    cost_directions: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_job_budget_cost_ledger_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def job_budget_cost_ledger_contract_v1() -> JobBudgetCostLedgerContractV1:
    return JobBudgetCostLedgerContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        storage_root_rel=STORAGE_ROOT_REL,
        allocation_log_filename=ALLOCATION_LOG_FILENAME,
        cost_event_log_filename=COST_EVENT_LOG_FILENAME,
        allocation_reason_codes=ALLOCATION_REASON_CODES,
        cost_reason_codes=COST_REASON_CODES,
        cost_directions=COST_DIRECTIONS,
    )


def _validate_path_safe_identifier(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _PATH_SAFE_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a non-empty path-safe identifier")
    return normalized


def _validate_recorded_at_utc(value: Any, *, field_name: str = "recorded_at_utc") -> str:
    normalized = str(value or "").strip()
    if not normalized.endswith("Z"):
        raise ValueError(f"{field_name} must be an RFC3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an RFC3339 UTC timestamp ending in Z") from exc
    return normalized


def _validate_amount_minor_units(value: Any, *, field_name: str = "amount_minor_units") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer number of minor units")
    return value


def _validate_unit(value: Any) -> str:
    normalized = str(value or "").strip()
    if not _UNIT_RE.fullmatch(normalized):
        raise ValueError("unit must be a lowercase non-empty slug such as usd_cents")
    return normalized


def _reject_forbidden_derived_fields(record: Mapping[str, Any]) -> None:
    for field_name in FORBIDDEN_DERIVED_FIELDS:
        if field_name in record:
            raise ValueError(f"{field_name} is derived-only and must not be stored in ledger records")


def _require_fields(record: Mapping[str, Any], fields: Sequence[str], *, record_kind: str) -> None:
    missing = [field_name for field_name in fields if field_name not in record]
    if missing:
        raise ValueError(f"{record_kind} missing required fields: {', '.join(missing)}")


def validate_allocation_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    _require_fields(record, ALLOCATION_REQUIRED_FIELDS, record_kind="allocation record")
    _reject_forbidden_derived_fields(record)
    if str(record.get("schema_version") or "") != RECORD_SCHEMA_VERSION:
        raise ValueError("allocation record schema_version must remain job_budget_cost_ledger_record_v1")

    allocation_reason = str(record.get("allocation_reason_code") or "").strip()
    if allocation_reason not in ALLOCATION_REASON_CODES:
        raise ValueError(
            "allocation_reason_code must be one of: " + ", ".join(ALLOCATION_REASON_CODES)
        )

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "allocation_uid": _validate_path_safe_identifier(record.get("allocation_uid"), field_name="allocation_uid"),
        "job_uid": _validate_path_safe_identifier(record.get("job_uid"), field_name="job_uid"),
        "recorded_at_utc": _validate_recorded_at_utc(record.get("recorded_at_utc")),
        "amount_minor_units": _validate_amount_minor_units(record.get("amount_minor_units")),
        "unit": _validate_unit(record.get("unit")),
        "allocation_reason_code": allocation_reason,
    }


def validate_cost_event_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    _require_fields(record, COST_EVENT_REQUIRED_FIELDS, record_kind="cost event record")
    _reject_forbidden_derived_fields(record)
    if str(record.get("schema_version") or "") != RECORD_SCHEMA_VERSION:
        raise ValueError("cost event record schema_version must remain job_budget_cost_ledger_record_v1")

    direction = str(record.get("direction") or "").strip()
    if direction not in COST_DIRECTIONS:
        raise ValueError("direction must be one of: debit, credit")
    reason = str(record.get("cost_reason_code") or "").strip()
    if reason not in COST_REASON_CODES:
        raise ValueError("cost_reason_code must be one of: " + ", ".join(COST_REASON_CODES))

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "cost_event_uid": _validate_path_safe_identifier(record.get("cost_event_uid"), field_name="cost_event_uid"),
        "job_uid": _validate_path_safe_identifier(record.get("job_uid"), field_name="job_uid"),
        "recorded_at_utc": _validate_recorded_at_utc(record.get("recorded_at_utc")),
        "amount_minor_units": _validate_amount_minor_units(record.get("amount_minor_units")),
        "unit": _validate_unit(record.get("unit")),
        "direction": direction,
        "cost_reason_code": reason,
    }


def ledger_paths_for_job(job_uid: str) -> Dict[str, Path]:
    canonical_job_uid = _validate_path_safe_identifier(job_uid, field_name="job_uid")
    job_dir = STORAGE_ROOT_REL / "jobs" / canonical_job_uid
    return {
        "job_dir": job_dir,
        "allocation_log_rel": job_dir / ALLOCATION_LOG_FILENAME,
        "cost_event_log_rel": job_dir / COST_EVENT_LOG_FILENAME,
    }


def replay_job_budget_balance(
    allocation_records: Sequence[Mapping[str, Any]],
    cost_event_records: Sequence[Mapping[str, Any]],
    *,
    job_uid: str,
) -> Dict[str, Any]:
    canonical_job_uid = _validate_path_safe_identifier(job_uid, field_name="job_uid")
    seen_allocation_uids = set()
    seen_cost_event_uids = set()
    unit: str | None = None
    allocated_minor_units = 0
    cost_debit_minor_units = 0
    cost_credit_minor_units = 0

    for raw_record in allocation_records:
        record = validate_allocation_record(raw_record)
        if record["job_uid"] != canonical_job_uid:
            raise ValueError("allocation record job_uid does not match replay target job_uid")
        if record["allocation_uid"] in seen_allocation_uids:
            raise ValueError(f"duplicate allocation_uid encountered during replay: {record['allocation_uid']}")
        seen_allocation_uids.add(record["allocation_uid"])
        if unit is None:
            unit = record["unit"]
        elif unit != record["unit"]:
            raise ValueError("all allocation and cost records for one job_uid must share exactly one unit")
        allocated_minor_units += record["amount_minor_units"]

    for raw_record in cost_event_records:
        record = validate_cost_event_record(raw_record)
        if record["job_uid"] != canonical_job_uid:
            raise ValueError("cost event record job_uid does not match replay target job_uid")
        if record["cost_event_uid"] in seen_cost_event_uids:
            raise ValueError(f"duplicate cost_event_uid encountered during replay: {record['cost_event_uid']}")
        seen_cost_event_uids.add(record["cost_event_uid"])
        if unit is None:
            unit = record["unit"]
        elif unit != record["unit"]:
            raise ValueError("all allocation and cost records for one job_uid must share exactly one unit")
        if record["direction"] == "debit":
            cost_debit_minor_units += record["amount_minor_units"]
        else:
            cost_credit_minor_units += record["amount_minor_units"]

    return {
        "job_uid": canonical_job_uid,
        "unit": unit or "",
        "allocated_minor_units": allocated_minor_units,
        "cost_debit_minor_units": cost_debit_minor_units,
        "cost_credit_minor_units": cost_credit_minor_units,
        "remaining_minor_units": allocated_minor_units - cost_debit_minor_units + cost_credit_minor_units,
    }


def validate_job_budget_cost_ledger_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_job_budget_cost_ledger_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain job_budget_cost_ledger_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260406-183206Z-AC5E")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion docs contract")
    if str(payload.get("storage_root_rel") or "") != STORAGE_ROOT_REL.as_posix():
        issues.append("storage_root_rel must point at runtime/state/job_budget_cost_ledger")

    storage_layout = payload.get("storage_layout")
    if not isinstance(storage_layout, dict):
        issues.append("storage_layout must be an object")
    else:
        if str(storage_layout.get("allocation_log_filename") or "") != ALLOCATION_LOG_FILENAME:
            issues.append("storage_layout.allocation_log_filename drifted from the runtime helper contract")
        if str(storage_layout.get("cost_event_log_filename") or "") != COST_EVENT_LOG_FILENAME:
            issues.append("storage_layout.cost_event_log_filename drifted from the runtime helper contract")

    if tuple(payload.get("allocation_reason_codes") or []) != ALLOCATION_REASON_CODES:
        issues.append("allocation_reason_codes must match the canonical v1 order exactly")
    if tuple(payload.get("cost_reason_codes") or []) != COST_REASON_CODES:
        issues.append("cost_reason_codes must match the canonical v1 order exactly")
    if tuple(payload.get("cost_directions") or []) != COST_DIRECTIONS:
        issues.append("cost_directions must match the canonical debit/credit order exactly")

    replay_rules = payload.get("replay_rules")
    if not isinstance(replay_rules, dict):
        issues.append("replay_rules must be an object")
    else:
        if str(replay_rules.get("remaining_budget_formula") or "") != REMAINING_BUDGET_FORMULA:
            issues.append("replay_rules.remaining_budget_formula drifted from the canonical v1 contract")
        if tuple(replay_rules.get("deterministic_order") or []) != DETERMINISTIC_REPLAY_ORDER:
            issues.append("replay_rules.deterministic_order drifted from the canonical v1 order")
        if tuple(replay_rules.get("forbidden_derived_fields") or []) != FORBIDDEN_DERIVED_FIELDS:
            issues.append("replay_rules.forbidden_derived_fields drifted from the runtime helper contract")

    allocation_record = payload.get("allocation_record")
    if not isinstance(allocation_record, dict):
        issues.append("allocation_record must be an object")
    elif tuple(allocation_record.get("required_fields") or []) != ALLOCATION_REQUIRED_FIELDS:
        issues.append("allocation_record.required_fields must match the canonical v1 order exactly")

    cost_event_record = payload.get("cost_event_record")
    if not isinstance(cost_event_record, dict):
        issues.append("cost_event_record must be an object")
    elif tuple(cost_event_record.get("required_fields") or []) != COST_EVENT_REQUIRED_FIELDS:
        issues.append("cost_event_record.required_fields must match the canonical v1 order exactly")

    return tuple(issues)
