from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping

from tools.keychain_probe import (
    FakeCredentialAvailabilityBackend,
    probe_credential_reference,
)
from tools.shared.fsio import utc_now_iso, write_json_atomic


UID = "PIS-20260410-073008Z-1D22"
SOURCE_INTENT_UID = "IS-20260410-072118Z-2088"
PARENT_UID = "PIS-20260410-072123Z-7113"
IMPLEMENTATION_CHILD_UID = "PIS-20260410-073007Z-CE4D"
SCHEMA_VERSION = "keychain_probe_intent_validation_v1"
DEFAULT_REPORT_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260410-073008Z-1D22__validate-keychain-access-boundary-probe-intent-e2e/"
    "artifacts/validation/keychain_probe_intent_validation.json"
)
VALIDATION_COMMANDS = [
    "python -m py_compile tools/keychain_probe.py tools/keychain_probe_intent_validation.py "
    "tests/tools/test_keychain_probe.py tests/tools/test_keychain_probe_intent_validation.py",
    "python -m pytest tests/tools/test_keychain_probe.py tests/tools/test_keychain_probe_intent_validation.py -q",
    "python -m tools.keychain_probe_intent_validation --repo-root .",
    "metadata/report consistency Python assertion",
    "validation artifact forbidden backend-detail scan",
    "git diff --check -- <touched files>",
]

_EXPECTED_OUTCOMES = {
    "available": "available",
    "missing_credential": "missing_credential",
    "backend_unavailable": "backend_unavailable",
    "access_denied": "access_denied",
}
_ALLOWED_RECEIPT_KEYS = {
    "schema_version",
    "created_utc",
    "credential_ref",
    "backend_class",
    "outcome",
    "success",
    "boundary_attempt",
}
_DENIED_DETAIL = "denied detail must stay out of durable receipts"
_UNAVAILABLE_DETAIL = "backend detail must stay out of durable receipts"


class _UnavailableBackend:
    backend_class = "intent_validation_backend"

    def has_credential_reference(self, credential_ref: str) -> bool:
        raise RuntimeError(_UNAVAILABLE_DETAIL)


class _DeniedBackend:
    backend_class = "intent_validation_backend"

    def has_credential_reference(self, credential_ref: str) -> bool:
        from tools.keychain_probe import KeychainAccessDenied

        raise KeychainAccessDenied(_DENIED_DETAIL)


def build_validation_report(
    *,
    repo_root: Path,
    report_path: Path = DEFAULT_REPORT_PATH,
    now_fn: Callable[[], str] = utc_now_iso,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    absolute_report_path = _resolve_under_repo(repo_root, report_path)
    receipts_dir = absolute_report_path.parent / "receipts"
    created_utc = now_fn()

    scenario_results = [
        _run_probe_scenario(
            scenario_id="available",
            scenario_kind="success_path",
            backend=FakeCredentialAvailabilityBackend(available_refs={"operator/github"}),
            expected_outcome="available",
            receipts_dir=receipts_dir,
            now_fn=now_fn,
        ),
        _run_probe_scenario(
            scenario_id="missing_credential",
            scenario_kind="negative_path",
            backend=FakeCredentialAvailabilityBackend(),
            expected_outcome="missing_credential",
            receipts_dir=receipts_dir,
            now_fn=now_fn,
        ),
        _run_probe_scenario(
            scenario_id="backend_unavailable",
            scenario_kind="negative_path",
            backend=_UnavailableBackend(),
            expected_outcome="backend_unavailable",
            receipts_dir=receipts_dir,
            now_fn=now_fn,
        ),
        _run_probe_scenario(
            scenario_id="access_denied",
            scenario_kind="negative_path",
            backend=_DeniedBackend(),
            expected_outcome="access_denied",
            receipts_dir=receipts_dir,
            now_fn=now_fn,
        ),
        _run_invalid_reference_scenario(receipts_dir=receipts_dir, now_fn=now_fn),
    ]

    redaction_scan = _build_redaction_scan(
        repo_root=repo_root,
        receipt_paths=sorted(receipts_dir.glob("*.json")),
        forbidden_terms=[_DENIED_DETAIL, _UNAVAILABLE_DETAIL],
    )
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "family_parent_uid": PARENT_UID,
        "implementation_child_uid": IMPLEMENTATION_CHILD_UID,
        "created_utc": created_utc,
        "validation_outcome": "passed_with_non_claims",
        "validation_commands": list(VALIDATION_COMMANDS),
        "summary": (
            "The implemented credential-reference probe was exercised through a fake/test backend for "
            "success, missing credential, unavailable backend, denied access, and invalid reference outcomes. "
            "Receipts stayed redacted by construction. Real operator keychain integration and watchdog closeout "
            "remain outside this child."
        ),
        "proof_summary": {
            "proven": [
                "fake-backend success path reaches the implemented credential-reference probe and writes a redacted success receipt",
                "missing credential, backend unavailable, and access denied paths write explicit redacted non-success receipts",
                "unsafe traversal-like references are rejected before receipt creation",
                "generated validation receipts omit backend exception details and keep the allowlisted receipt shape",
            ],
            "unproven": [
                "real operator keychain/backend availability",
                "scheduler receipt reconciliation for the full family",
                "watchdog observation or mutation review",
                "final parent/source-intent promotion",
            ],
            "contradicted": [],
            "non_claims": [
                "no real operator keychain/backend integration claimed",
                "no scheduler receipt reconciliation claimed",
                "no watchdog observation or mutation review claimed",
                "no final parent/source-intent promotion claimed",
            ],
        },
        "requirement_coverage": [
            {
                "requirement_id": "reference_only_boundary",
                "intent_clause": (
                    "A named keychain credential reference can be checked without exposing the secret value."
                ),
                "status": "proven_for_fake_backend",
                "scenario_ids": ["available"],
                "observed_behavior": (
                    "The probe accepted a named reference and persisted only the reference, outcome, backend "
                    "class, timestamp, and boundary flags."
                ),
            },
            {
                "requirement_id": "explicit_negative_outcomes",
                "intent_clause": (
                    "Missing/unavailable/denied cases fail closed with redacted evidence."
                ),
                "status": "proven_for_fake_backend",
                "scenario_ids": ["missing_credential", "backend_unavailable", "access_denied"],
                "observed_behavior": (
                    "Required negative outcomes persisted as explicit non-success states instead of success."
                ),
            },
            {
                "requirement_id": "invalid_reference_rejection",
                "intent_clause": "Repo-owned state must not accept unsafe credential identifiers.",
                "status": "proven_for_fake_backend",
                "scenario_ids": ["invalid_reference_rejected"],
                "observed_behavior": "A traversal-like reference raised before a receipt could be written.",
            },
            {
                "requirement_id": "durable_redacted_receipts",
                "intent_clause": (
                    "No raw secret-like value is written to tracked files, runtime receipts, benchmark artifacts, "
                    "ledger evidence, or validation reports."
                ),
                "status": "proven_for_generated_validation_artifacts",
                "scenario_ids": [row["scenario_id"] for row in scenario_results],
                "observed_behavior": (
                    "Generated receipts used an allowlisted receipt shape and excluded backend exception detail."
                ),
            },
            {
                "requirement_id": "watchdog_closeout",
                "intent_clause": (
                    "Closeout verifies scheduler receipts and watchdog observation for this real family."
                ),
                "status": "non_claim",
                "scenario_ids": [],
                "observed_behavior": (
                    "Watchdog and family promotion evidence belongs to PIS-20260410-073009Z-887D, not this "
                    "validation child."
                ),
            },
        ],
        "scenario_results": _relativize_scenario_paths(repo_root, scenario_results),
        "redaction_scan": redaction_scan,
        "evidence_paths": {
            "validation_report": _repo_rel(repo_root, absolute_report_path),
            "receipts_dir": _repo_rel(repo_root, receipts_dir),
        },
        "non_claims": [
            "no real operator keychain/backend integration claimed",
            "no scheduler receipt reconciliation claimed",
            "no watchdog observation or mutation review claimed",
            "no final parent/source-intent promotion claimed",
        ],
    }
    report["artifact_fingerprint"] = _fingerprint_without_self(report)
    _assert_report(report)
    write_json_atomic(absolute_report_path, report)
    return report


def _run_probe_scenario(
    *,
    scenario_id: str,
    scenario_kind: str,
    backend: object,
    expected_outcome: str,
    receipts_dir: Path,
    now_fn: Callable[[], str],
) -> Dict[str, Any]:
    result = probe_credential_reference(
        "operator/github",
        backend=backend,  # type: ignore[arg-type]
        receipt_dir=receipts_dir,
        now_fn=now_fn,
    )
    receipt = json.loads(Path(result.receipt_path).read_text(encoding="utf-8"))
    _assert_receipt(receipt, expected_outcome=expected_outcome)
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "outcome": "passed",
        "expected_probe_outcome": expected_outcome,
        "observed_probe_outcome": receipt["outcome"],
        "success": bool(receipt["success"]),
        "receipt_path": str(Path(result.receipt_path)),
        "observations": [
            "Probe path returned the expected outcome.",
            "Receipt shape remained within the redacted allowlist.",
        ],
    }


def _run_invalid_reference_scenario(*, receipts_dir: Path, now_fn: Callable[[], str]) -> Dict[str, Any]:
    before = set(receipts_dir.glob("*.json"))
    try:
        probe_credential_reference(
            "../operator/github",
            backend=FakeCredentialAvailabilityBackend(),
            receipt_dir=receipts_dir,
            now_fn=now_fn,
        )
    except ValueError:
        after = set(receipts_dir.glob("*.json"))
        return {
            "scenario_id": "invalid_reference_rejected",
            "scenario_kind": "negative_path",
            "outcome": "passed",
            "expected_probe_outcome": "rejected_before_receipt",
            "observed_probe_outcome": "rejected_before_receipt",
            "success": False,
            "receipt_path": None,
            "observations": [
                "Unsafe traversal-like reference raised before boundary receipt creation.",
                f"receipt_count_delta={len(after) - len(before)}",
            ],
        }
    raise AssertionError("invalid credential reference was accepted")


def _assert_receipt(receipt: Mapping[str, Any], *, expected_outcome: str) -> None:
    keys = set(receipt.keys())
    if keys != _ALLOWED_RECEIPT_KEYS:
        raise AssertionError(f"unexpected receipt keys: {sorted(keys - _ALLOWED_RECEIPT_KEYS)}")
    if receipt.get("outcome") != expected_outcome:
        raise AssertionError(f"expected {expected_outcome}, got {receipt.get('outcome')}")
    if bool(receipt.get("success")) != (expected_outcome == "available"):
        raise AssertionError("receipt success flag does not match outcome")
    boundary = receipt.get("boundary_attempt")
    if not isinstance(boundary, dict):
        raise AssertionError("receipt boundary_attempt must be an object")
    expected_boundary = {
        "checked_reference_only": True,
        "credential_value_observed": False,
        "credential_value_persisted": False,
        "credential_value_returned": False,
        "source_of_truth": "external_keychain_or_operator_backend",
    }
    if boundary != expected_boundary:
        raise AssertionError("receipt boundary flags drifted")


def _build_redaction_scan(
    *,
    repo_root: Path,
    receipt_paths: Iterable[Path],
    forbidden_terms: Iterable[str],
) -> Dict[str, Any]:
    inspected_paths = [Path(path) for path in receipt_paths]
    findings: List[Dict[str, str]] = []
    for path in inspected_paths:
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            if term and term in text:
                findings.append({"path": _safe_display_path(repo_root, path), "term": term})
    return {
        "status": "pass" if not findings else "fail",
        "inspected_receipt_count": len(inspected_paths),
        "inspected_paths": [_safe_display_path(repo_root, path) for path in inspected_paths],
        "forbidden_terms_found": findings,
        "receipt_allowed_top_level_fields": sorted(_ALLOWED_RECEIPT_KEYS),
    }


def _assert_report(report: Mapping[str, Any]) -> None:
    scenario_ids = {str(row.get("scenario_id") or "") for row in list(report.get("scenario_results") or [])}
    expected = set(_EXPECTED_OUTCOMES) | {"invalid_reference_rejected"}
    if not expected.issubset(scenario_ids):
        raise AssertionError(f"missing required scenarios: {sorted(expected - scenario_ids)}")
    if dict(report.get("redaction_scan") or {}).get("status") != "pass":
        raise AssertionError("redaction scan did not pass")


def _fingerprint_without_self(report: Mapping[str, Any]) -> str:
    payload = {k: v for k, v in report.items() if k != "artifact_fingerprint"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _relativize_scenario_paths(repo_root: Path, scenario_results: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in scenario_results:
        copied = dict(row)
        receipt_path = copied.get("receipt_path")
        if receipt_path:
            copied["receipt_path"] = _safe_display_path(repo_root, Path(str(receipt_path)))
        rows.append(copied)
    return rows


def _safe_display_path(repo_root: Path, path: Path) -> str:
    try:
        return _repo_rel(repo_root, path)
    except ValueError:
        return str(path)


def _resolve_under_repo(repo_root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else repo_root / path
    resolved = candidate.resolve()
    resolved.relative_to(repo_root)
    return resolved


def _repo_rel(repo_root: Path, path: Path) -> str:
    return str(Path(path).resolve().relative_to(repo_root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run keychain probe intent validation scenarios.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)
    report = build_validation_report(repo_root=args.repo_root, report_path=args.report_path)
    print(json.dumps({"ok": True, "report": report["evidence_paths"]["validation_report"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
