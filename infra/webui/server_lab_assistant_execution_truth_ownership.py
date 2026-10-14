"""M9R2 execution truth ownership contract and write-boundary guards."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List


EXECUTION_TRUTH_OWNERSHIP_SCHEMA_VERSION = "lab_assistant_execution_truth_ownership_v1"
M9R2_UID = "PIS-20260313-100601Z-M9R2"
DEPENDENCY_UIDS = [
    "PIS-20260312-161622Z-2848",
    "PIS-20260312-161622Z-BD05",
]

OWNER_EXECUTION_BACKEND = "execution_backend"
OWNER_BD05_TASK_REGISTER = "bd05_task_register"
OWNER_WEB_CONSOLE_PROJECTION = "web_console_projection"

DOMAIN_SCHEDULER_TRUTH = "scheduler_truth"
DOMAIN_RUNTIME_TRUTH = "runtime_truth"
DOMAIN_EXECUTION_RECEIPTS = "execution_receipts"
DOMAIN_TASK_WRITEBACK = "task_writeback"
DOMAIN_OPERATOR_PROJECTION = "operator_projection"
DOMAIN_PROCESS_LOCAL_ADVISORY = "process_local_advisory"

_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_WRITER_PROJECTION_PREFIXES = {
    "2db7",
    "mobile",
    "operator",
    "operator_snapshot",
    "projection",
    "ui",
    "web",
    "web_console",
    "webui",
}
_WRITER_EXECUTION_PREFIXES = {
    "backend",
    "detached_job",
    "e2f8",
    "execution",
    "mailbox",
    "recovery",
    "scheduler",
    "workflow",
    "writeback",
    "y5z7",
}


class ExecutionTruthOwnershipError(RuntimeError):
    """Raised when a caller violates the M9R2 truth ownership contract."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def ownership_domains() -> List[Dict[str, Any]]:
    return [
        {
            "domain_id": DOMAIN_SCHEDULER_TRUTH,
            "canonical_owner": OWNER_EXECUTION_BACKEND,
            "canonical_store": "governance/agents/runtime/scheduled_jobs/state/*.json",
            "write_via": "execution backend scheduler loop",
            "arbitration": "2848 cross-surface precedence",
        },
        {
            "domain_id": DOMAIN_RUNTIME_TRUTH,
            "canonical_owner": OWNER_EXECUTION_BACKEND,
            "canonical_store": "runtime/state/lab_assistant/detached_jobs/jobs/*.json",
            "write_via": "execution backend runtime workers/recovery",
            "arbitration": "2848 cross-surface precedence",
        },
        {
            "domain_id": DOMAIN_EXECUTION_RECEIPTS,
            "canonical_owner": OWNER_EXECUTION_BACKEND,
            "canonical_store": "runtime/state/lab_assistant/execution_reconciliation/{reports,receipts}",
            "write_via": "execution backend reconciliation writer",
            "arbitration": "2848 cross-surface precedence",
        },
        {
            "domain_id": DOMAIN_TASK_WRITEBACK,
            "canonical_owner": OWNER_BD05_TASK_REGISTER,
            "canonical_store": "runtime/state/lab_assistant/task_register/task_register.json",
            "write_via": "BD05 guarded writeback path only",
            "arbitration": "BD05 generation + 2848 write arbitration",
        },
        {
            "domain_id": DOMAIN_OPERATOR_PROJECTION,
            "canonical_owner": OWNER_WEB_CONSOLE_PROJECTION,
            "canonical_store": "read-only projection payloads",
            "write_via": "web console projection build on durable truth",
            "arbitration": "projection only; never canonical execution owner",
        },
        {
            "domain_id": DOMAIN_PROCESS_LOCAL_ADVISORY,
            "canonical_owner": "none",
            "canonical_store": "process memory / transport-local state",
            "write_via": "advisory only",
            "arbitration": "must not be treated as canonical truth",
        },
    ]


def ownership_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": EXECUTION_TRUTH_OWNERSHIP_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": M9R2_UID,
        "depends_on": list(DEPENDENCY_UIDS),
        "domains": ownership_domains(),
        "rules": [
            "Canonical execution truth belongs to backend-owned durable stores.",
            "Web console owns projection only and cannot claim canonical execution ownership.",
            "Process-local state is advisory until durably written and referenced.",
            "Task truth writeback must pass BD05 generation arbitration.",
        ],
    }


def classify_writer_plane(writer: Any) -> str:
    text = _clean_text(writer).lower()
    head = text.split(".", 1)[0] if text else ""
    if head in _WRITER_PROJECTION_PREFIXES:
        return OWNER_WEB_CONSOLE_PROJECTION
    if head in _WRITER_EXECUTION_PREFIXES:
        return OWNER_EXECUTION_BACKEND
    return OWNER_EXECUTION_BACKEND


def assert_truth_write_allowed(*, domain_id: str, writer: Any) -> None:
    domain = _clean_text(domain_id)
    row = next((item for item in ownership_domains() if _clean_text(item.get("domain_id")) == domain), None)
    if row is None:
        raise ExecutionTruthOwnershipError(f"unknown truth domain: {domain or '<missing>'}")
    owner = _clean_text(row.get("canonical_owner"))
    plane = classify_writer_plane(writer)
    if owner in {OWNER_EXECUTION_BACKEND, OWNER_BD05_TASK_REGISTER} and plane == OWNER_WEB_CONSOLE_PROJECTION:
        raise ExecutionTruthOwnershipError(
            f"M9R2 write rejected: writer `{_clean_text(writer) or '<missing>'}` from `{plane}` "
            f"cannot own canonical `{domain}` truth."
        )


def is_durable_evidence_ref(raw: Any) -> bool:
    value = _clean_text(raw)
    if not value:
        return False
    lowered = value.lower()
    if _URI_SCHEME_RE.match(value):
        return False
    if lowered.startswith(("memory:", "inproc:", "socket:", "ws:", "wss:")):
        return False
    return True


def filter_durable_evidence_refs(values: List[Any]) -> List[str]:
    out: List[str] = []
    for raw in list(values or []):
        text = _clean_text(raw)
        if not is_durable_evidence_ref(text):
            continue
        if text not in out:
            out.append(text)
    return out
