from __future__ import annotations

import json
from pathlib import Path

from tools.keychain_probe_intent_validation import build_validation_report


def test_keychain_probe_intent_validation_writes_redacted_scenario_report(tmp_path: Path) -> None:
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/validation/keychain_probe_intent_validation.json"),
        now_fn=lambda: "2026-04-10T08:00:00Z",
    )

    report_path = tmp_path / report["evidence_paths"]["validation_report"]
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    scenario_by_id = {row["scenario_id"]: row for row in loaded["scenario_results"]}

    assert loaded["schema_version"] == "keychain_probe_intent_validation_v1"
    assert loaded["uid"] == "PIS-20260410-073008Z-1D22"
    assert loaded["validation_outcome"] == "passed_with_non_claims"
    assert scenario_by_id["available"]["observed_probe_outcome"] == "available"
    assert scenario_by_id["missing_credential"]["observed_probe_outcome"] == "missing_credential"
    assert scenario_by_id["backend_unavailable"]["observed_probe_outcome"] == "backend_unavailable"
    assert scenario_by_id["access_denied"]["observed_probe_outcome"] == "access_denied"
    assert scenario_by_id["invalid_reference_rejected"]["observed_probe_outcome"] == "rejected_before_receipt"
    assert loaded["redaction_scan"]["status"] == "pass"
    assert loaded["redaction_scan"]["inspected_receipt_count"] == 4
    assert loaded["validation_commands"] == [
        "python -m py_compile tools/keychain_probe.py tools/keychain_probe_intent_validation.py "
        "tests/tools/test_keychain_probe.py tests/tools/test_keychain_probe_intent_validation.py",
        "python -m pytest tests/tools/test_keychain_probe.py tests/tools/test_keychain_probe_intent_validation.py -q",
        "python -m tools.keychain_probe_intent_validation --repo-root .",
        "metadata/report consistency Python assertion",
        "validation artifact forbidden backend-detail scan",
        "git diff --check -- <touched files>",
    ]
    assert loaded["proof_summary"]["contradicted"] == []
    assert "real operator keychain/backend availability" in loaded["proof_summary"]["unproven"]
    assert "no real operator keychain/backend integration claimed" in loaded["non_claims"]
    assert loaded["proof_summary"]["non_claims"] == loaded["non_claims"]


def test_keychain_probe_intent_validation_receipts_keep_allowed_shape(tmp_path: Path) -> None:
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/validation/keychain_probe_intent_validation.json"),
        now_fn=lambda: "2026-04-10T08:01:00Z",
    )
    receipt_paths = [
        tmp_path / Path(row["receipt_path"])
        for row in report["scenario_results"]
        if row.get("receipt_path")
    ]

    assert len(receipt_paths) == 4
    for path in receipt_paths:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        assert set(receipt) == {
            "schema_version",
            "created_utc",
            "credential_ref",
            "backend_class",
            "outcome",
            "success",
            "boundary_attempt",
        }
        assert receipt["boundary_attempt"]["credential_value_observed"] is False
        assert receipt["boundary_attempt"]["credential_value_persisted"] is False
        assert receipt["boundary_attempt"]["credential_value_returned"] is False
