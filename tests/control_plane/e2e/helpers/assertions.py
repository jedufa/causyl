from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


def assert_cross_surface_truth(evidence: Mapping[str, Any], scenario: Mapping[str, Any]) -> None:
    receipt = dict(evidence["receipt"])
    report = dict(evidence["report"])
    snapshot = dict(evidence["snapshot"])
    stimulus = dict(evidence["stimulus"])
    expected_receipt = dict(scenario.get("expected_receipt") or {})
    expected_snapshot = dict(scenario.get("expected_snapshot") or {})

    assert receipt["status"] == scenario["expected_terminal_status"]
    assert report["status"] == receipt["status"]
    assert snapshot["terminal_status"] == receipt["status"]
    assert report["proof_gap"] == receipt["proof_gap"]
    assert snapshot["proof_gap"] == receipt["proof_gap"]
    assert stimulus["scenario_name"] == scenario["name"]
    assert bool(stimulus["prompt_text"].strip()) is bool(str(scenario.get("prompt_text") or "").strip())

    for key, expected in expected_receipt.items():
        assert receipt.get(key) == expected, f"receipt[{key}] mismatch"
    for key, expected in expected_snapshot.items():
        assert snapshot.get(key) == expected, f"snapshot[{key}] mismatch"

    for key in scenario.get("expected_artifacts") or []:
        if key == "receipt":
            assert Path(receipt["receipt_path"]).exists()
        elif key == "report":
            assert Path(receipt["report_path"]).exists()
        elif key == "snapshot":
            assert Path(receipt["snapshot_path"]).exists()
        elif key == "stimulus":
            assert Path(receipt["stimulus_path"]).exists()


def assert_forbidden_mutations_absent(before: Mapping[str, str], after: Mapping[str, str], *, allowed_prefixes: Sequence[str]) -> None:
    changed = {
        path
        for path, digest in after.items()
        if before.get(path) != digest
    }
    deleted = {path for path in before if path not in after}
    unexpected = sorted(
        path
        for path in changed | deleted
        if not any(path.startswith(prefix) for prefix in allowed_prefixes)
    )
    assert unexpected == [], f"unexpected mutations outside {allowed_prefixes}: {unexpected}"


def assert_lab_cli_prompt_contract(evidence: Mapping[str, Any], scenario: Mapping[str, Any]) -> None:
    report = dict(evidence["report"])
    output = dict(report["output"])
    expected = dict(scenario.get("expected_normalization") or {})
    classification = dict(output.get("classification") or {})
    selection = dict(classification.get("selection") or {})
    decomposition = dict(dict(classification.get("decomposition") or {}).get("decomposition") or {})
    front_door = dict(output.get("front_door_payload") or {})

    if "mode" in expected:
        assert output.get("mode") == expected["mode"]
    if "action_id" in expected:
        assert selection.get("action_id") == expected["action_id"]
    if "authority_class" in expected:
        assert dict(decomposition.get("risk_authority_layer") or {}).get("authority_class") == expected["authority_class"]
    if "execution_shape" in expected:
        assert dict(decomposition.get("work_shape_layer") or {}).get("execution_shape") == expected["execution_shape"]
    if "fallback_policy" in expected:
        assert dict(decomposition.get("capability_layer") or {}).get("fallback_policy") == expected["fallback_policy"]
    if "deterministic_capability_class" in expected:
        assert dict(decomposition.get("capability_layer") or {}).get("deterministic_capability_class") == expected["deterministic_capability_class"]
    if "patch_supplied" in expected:
        assert bool(str(front_door.get("patch_path") or "").strip()) is bool(expected["patch_supplied"])
    if "allow_fallback" in expected:
        assert bool(front_door.get("allow_fallback")) is bool(expected["allow_fallback"])


def assert_lab_cli_surface_truth(evidence: Mapping[str, Any], scenario: Mapping[str, Any]) -> None:
    report = dict(evidence["report"])
    snapshot = dict(evidence["snapshot"])
    output = dict(report["output"])
    surface = dict(output.get("operator_surface") or {})
    executive = dict(surface.get("executive_surface") or {})
    recommendations = dict(surface.get("recommendations") or {})
    primary = dict(recommendations.get("primary") or executive.get("primary_next_action") or {})
    codex = dict(surface.get("codex_transparency") or output.get("codex_fallback_request") or {})
    expected = dict(scenario.get("expected_surface") or {})
    human_output = str(report.get("human_output") or "")

    if expected:
        if "state" in expected:
            assert executive.get("state") == expected["state"]
            assert f"STATE: {expected['state']}" in human_output
        if "primary_action_id" in expected:
            assert primary.get("action_id") == expected["primary_action_id"]
        if "primary_label" in expected:
            assert expected["primary_label"] in human_output
        if "confidence" in expected:
            assert executive.get("confidence") == expected["confidence"]
            assert f"CONFIDENCE: {expected['confidence']}" in human_output
        if "codex_used" in expected:
            assert bool(codex.get("codex_used")) is bool(expected["codex_used"])
        if "fallback_eligible" in expected:
            assert bool(dict(output.get("codex_fallback_request") or {}).get("eligible")) is bool(expected["fallback_eligible"])
    assert snapshot.get("operator_state")
    assert str(report.get("human_output") or "").strip()
