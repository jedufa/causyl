from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import compiled_work_order_family_proof as proof


def _bound_item_dir() -> Path:
    base = Path("governance/ledger/patch_instruction_sheets/causyl")
    rel = Path("2026") / "04" / "PIS-20260411-144241Z-7992__prove-compiled-work-order-adoption-across-stable-handoffs"
    for ledger_status in ("verified", "active"):
        candidate = base / ledger_status / rel
        if (candidate / "meta.json").exists():
            return candidate
    return base / "verified" / rel


def _bound_proof_artifact() -> Path:
    return _bound_item_dir() / "artifacts" / "verification" / "compiled_work_order_family_proof.md"


def _payload_timestamp_values(value: object) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key, nested in value.items():
            if (key == "utc" or key.endswith("_utc")) and isinstance(nested, str) and nested.endswith("Z"):
                found.append(nested)
            found.extend(_payload_timestamp_values(nested))
        return found
    if isinstance(value, list):
        found: list[str] = []
        for nested in value:
            found.extend(_payload_timestamp_values(nested))
        return found
    return []


def test_compiled_work_order_family_proof_covers_supported_and_fallback_handoffs(tmp_path: Path) -> None:
    report = proof.collect_compiled_work_order_family_proof(repo_root=tmp_path)

    assert report["schema_version"] == "compiled_work_order_family_proof_v1"
    assert report["proof_child_uid"] == "PIS-20260411-144241Z-7992"
    assert report["parent_uid"] == "PIS-20260411-142246Z-429E"
    assert report["child_uids"] == [
        "PIS-20260411-144241Z-154B",
        "PIS-20260411-144241Z-2871",
        "PIS-20260411-144241Z-EEAC",
    ]
    assert report["case_count"] == 4
    assert report["all_cases_passed"] is True
    assert report["specialized_verifier_review_required"] is True

    cases = {row["case_id"]: row for row in report["cases"]}

    fanout = cases["fanout_supported_authoritative_handoff"]
    assert fanout["producer_status"] == "compiled_emitted"
    assert fanout["consumer_mode"] == "authoritative"
    assert fanout["consumer_scope_objective"] == (
        "Compile the stable scheduler-v2 fanout child-brief handoff for orchestrator-owned child PIS emission."
    )
    assert "fanout_authoring_compile" in fanout["consumer_validation_checks"]

    campaign = cases["campaign_supported_authoritative_handoff"]
    assert campaign["producer_status"] == "compiled_emitted"
    assert campaign["consumer_mode"] == "authoritative"
    assert campaign["consumer_scope_objective"] == (
        "Compile the stable campaign phase brief handoff for advisory child-PIS preparation without granting execution authority."
    )
    assert "campaign_advisory_handoff" in campaign["consumer_validation_checks"]

    malformed = cases["malformed_envelope_fails_closed_to_fallback"]
    assert malformed["consumer_mode"] == "compatibility_fallback"
    assert malformed["consumer_fallback_reason"] == "compiled_work_order_validation_failed"

    unstable = cases["unstable_recursive_boundary_stays_explicit_fallback"]
    assert unstable["producer_status"] == "fallback_legacy"
    assert unstable["consumer_mode"] == "compatibility_fallback"
    assert unstable["consumer_fallback_reason"] == "handoff_marked_non_compiled"


def test_bound_compiled_work_order_family_proof_artifact_lists_cases_children_and_antipatterns() -> None:
    artifact = _bound_proof_artifact()
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")

    assert "PIS-20260411-144241Z-7992" in body
    assert "PIS-20260411-142246Z-429E" in body
    assert "PIS-20260411-144241Z-154B" in body
    assert "PIS-20260411-144241Z-2871" in body
    assert "PIS-20260411-144241Z-EEAC" in body
    assert "fanout_supported_authoritative_handoff" in body
    assert "campaign_supported_authoritative_handoff" in body
    assert "malformed_envelope_fails_closed_to_fallback" in body
    assert "unstable_recursive_boundary_stays_explicit_fallback" in body
    assert "unstable_boundary_forced_onto_compiled_rollout" in body
    assert "specialized verifier review is still required" in body


def test_bound_compiled_work_order_reconcile_timestamps_are_coherent() -> None:
    item_dir = _bound_item_dir()
    meta = json.loads((item_dir / "meta.json").read_text(encoding="utf-8"))
    artifact = json.loads(
        (item_dir / "artifacts" / "verification" / "status_reconcile_verification.json").read_text(encoding="utf-8")
    )
    events = [
        json.loads(line)
        for line in (item_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    latest_meta_event = next(event for event in reversed(events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(event for event in reversed(events) if event["type"] == "status_reconcile_reviewed")
    latest_verification_event = next(event for event in reversed(events) if event["type"] == "verification_checked")

    assert latest_meta_event["utc"] == latest_reconcile_event["utc"] == latest_verification_event["utc"]
    assert latest_meta_event["utc"] == meta["updated_utc"] == meta["evidence"]["validated_utc"] == artifact["reviewed_utc"]
    assert latest_reconcile_event["payload"]["verified_utc"] == artifact["reviewed_utc"]

    relevant_event_utcs = [
        event["utc"]
        for event in events
        if event["type"] in {"meta_updated", "status_reconcile_reviewed", "verification_checked", "status_changed", "ledger_status_changed"}
    ]
    assert relevant_event_utcs == sorted(relevant_event_utcs)

    for event in events:
        for payload_utc in _payload_timestamp_values(event.get("payload")):
            assert payload_utc <= event["utc"], (event["type"], payload_utc, event["utc"])
