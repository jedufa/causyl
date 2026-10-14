from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

from infra.controlplane import mission_drift_gate
from infra.controlplane import plane_mission_contract as mission_contract
from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_state as assistant_state
from infra.webui import server_lab_assistant_task_register as task_register
from infra.webui import server_lab_assistant_web_operations as web_operations
from tools.shared.fsio import utc_now_iso, write_json_atomic


UID = "PIS-20260501-114506Z-F6L8"
SOURCE_INTENT_UID = "IS-20260403-221436Z-0EDE"
PARENT_UID = "PIS-20260501-113645Z-47BA"
SCHEMA_VERSION = "mission_contract_intent_validation_v1"
VALIDATION_CASE_ID = "mission_contract_intent_e2e"
IMPLEMENTATION_CHILD_UIDS: Sequence[str] = (
    "PIS-20260501-114501Z-A1F3",
    "PIS-20260501-114502Z-B2G4",
    "PIS-20260501-114503Z-C3H5",
    "PIS-20260501-114504Z-D4J6",
    "PIS-20260501-114505Z-E5K7",
)
DEFAULT_REPORT_PATH_SUFFIX = Path("artifacts/intent_validation/mission_contract_intent_e2e.json")
VALIDATION_COMMANDS = [
    "python -m py_compile tools/mission_contract_intent_validation.py tests/tools/test_mission_contract_intent_validation.py",
    "python -m pytest tests/tools/test_mission_contract_intent_validation.py -q",
    "python -m tools.mission_contract_intent_validation --repo-root .",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _find_item_dir(base_root: Path, uid: str, *, kind: str) -> Path:
    if kind == "pis":
        glob_root = base_root / "governance/ledger/patch_instruction_sheets/causyl"
    elif kind == "intent":
        glob_root = base_root / "governance/ledger/intent_sheets/causyl"
    else:
        raise ValueError(f"unsupported kind: {kind}")
    matches = sorted(glob_root.glob(f"**/{uid}__*/meta.json"))
    if not matches:
        raise FileNotFoundError(f"could not find {kind} item for {uid}")
    return matches[0].parent


def _default_report_path(repo_root: Path) -> Path:
    return _find_item_dir(repo_root, UID, kind="pis").relative_to(repo_root) / DEFAULT_REPORT_PATH_SUFFIX


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _repo_rel(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_json_atomic(path, payload)


def _seed_runtime_fixture(repo_root: Path, *, contract_payload: Mapping[str, Any] | None = None) -> None:
    (repo_root / "governance/ledger").mkdir(parents=True, exist_ok=True)
    (repo_root / "governance/agents/runtime").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(repo_root)
    current_state = assistant_state.load_or_init_assistant_state(repo_root)
    register_obj = task_register.load_or_init_task_register(repo_root)
    if str(register_obj.get("assistant_id_ref") or "") != str(current_state.get("assistant_id") or ""):
        register_obj["assistant_id_ref"] = current_state.get("assistant_id")
        register_obj["last_writer"] = "mission_contract_intent_validation"
        _write_json(task_register._register_path(repo_root), register_obj)
        _write_json(task_register._summary_cache_path(repo_root), task_register._build_summary(register_obj))
    if contract_payload is not None:
        target = repo_root / mission_contract.CONTRACT_ARTIFACT_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(dict(contract_payload), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _load_canonical_contract_payload(implementation_repo_root: Path) -> Dict[str, Any]:
    source = implementation_repo_root / mission_contract.CONTRACT_ARTIFACT_REL
    return _read_json(source)


def _dependency_snapshot(implementation_repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = _find_item_dir(implementation_repo_root, uid, kind="pis")
    meta = _read_json(item_dir / "meta.json")
    evidence = meta.get("evidence")
    return {
        "uid": uid,
        "title": _clean_text(meta.get("title")) or uid,
        "status": _clean_text(meta.get("status")) or "draft",
        "release_state": _clean_text(meta.get("release_state")) or "draft",
        "has_evidence": isinstance(evidence, Mapping) and bool(evidence),
        "meta_path": _repo_rel(implementation_repo_root, item_dir / "meta.json"),
    }


def _evaluate_surface_consistency(
    *,
    source: Mapping[str, Any],
    bootstrap_packet: Mapping[str, Any],
    operator_payload: Mapping[str, Any],
    operations_payload: Mapping[str, Any],
) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    if not bool(source.get("ok")):
        issues.append(
            {
                "code": "mission_contract_unavailable",
                "summary": _clean_text(source.get("summary")) or "Canonical mission contract is unavailable.",
            }
        )
        return issues

    runtime_projection = dict(source.get("runtime_projection") or {})
    operator_projection = dict(source.get("operator_projection") or {})
    bootstrap_contract = dict(bootstrap_packet.get("mission_contract") or {})
    bootstrap_runtime = dict(bootstrap_contract.get("runtime_projection") or {})
    mission_surface = dict(operator_payload.get("mission_surface") or {})
    payload_contract = dict(operator_payload.get("mission_contract") or {})
    refresh_scope = dict((operations_payload.get("runtime_controls") or {}).get("refresh_scope") or {})

    runtime_fp = _clean_text(runtime_projection.get("source_fingerprint"))
    operator_fp = _clean_text(operator_projection.get("source_fingerprint"))
    if runtime_fp != operator_fp:
        issues.append(
            {
                "code": "runtime_operator_projection_fingerprint_mismatch",
                "summary": "Runtime and operator projections disagree on the loaded mission-contract fingerprint.",
            }
        )

    if _clean_text(bootstrap_runtime.get("source_fingerprint")) != runtime_fp:
        issues.append(
            {
                "code": "bootstrap_runtime_projection_mismatch",
                "summary": "Fresh-turn bootstrap mission projection does not match canonical runtime mission fingerprint.",
            }
        )

    if _clean_text(mission_surface.get("source_fingerprint")) != operator_fp:
        issues.append(
            {
                "code": "operator_surface_projection_mismatch",
                "summary": "Operator snapshot mission surface does not match canonical operator mission fingerprint.",
            }
        )

    if _clean_text(refresh_scope.get("source_fingerprint")) != runtime_fp:
        issues.append(
            {
                "code": "refresh_scope_projection_mismatch",
                "summary": "Operations refresh-scope projection does not match canonical runtime mission fingerprint.",
            }
        )

    if _clean_text(operator_projection.get("headline")) != _clean_text(runtime_projection.get("product_promise")):
        issues.append(
            {
                "code": "headline_product_promise_mismatch",
                "summary": "Operator headline does not match the canonical runtime product promise.",
            }
        )

    if _clean_text(refresh_scope.get("summary")) != _clean_text(runtime_projection.get("refresh_scope_summary")):
        issues.append(
            {
                "code": "refresh_scope_summary_mismatch",
                "summary": "Operations refresh-scope summary drifted away from the canonical runtime mission text.",
            }
        )

    if _clean_text(refresh_scope.get("claim_ceiling_summary")) != _clean_text(runtime_projection.get("claim_ceiling_summary")):
        issues.append(
            {
                "code": "claim_ceiling_summary_mismatch",
                "summary": "Operations refresh claim ceiling drifted away from the canonical runtime mission text.",
            }
        )

    if list(mission_surface.get("success_model") or []) != list(operator_projection.get("operator_success_model") or []):
        issues.append(
            {
                "code": "operator_success_model_mismatch",
                "summary": "Operator mission surface success model differs from the canonical operator projection.",
            }
        )

    if list(mission_surface.get("non_goals") or []) != list(operator_projection.get("explicit_non_goals") or []):
        issues.append(
            {
                "code": "operator_non_goal_mismatch",
                "summary": "Operator mission surface non-goals differ from the canonical operator projection.",
            }
        )

    if payload_contract and not bool(payload_contract.get("ok")):
        issues.append(
            {
                "code": "operator_snapshot_contract_unavailable",
                "summary": "Operator snapshot failed to load a valid mission contract.",
            }
        )

    return issues


def _collect_positive_surface_observation(repo_root: Path) -> Dict[str, Any]:
    contract_payload = _load_canonical_contract_payload(_repo_root())
    _seed_runtime_fixture(repo_root, contract_payload=contract_payload)
    source = mission_contract.read_plane_mission_contract(repo_root=repo_root)
    bootstrap_packet = bootstrap.build_fresh_turn_bootstrap(repo_root, recent_chat_rows=[])
    operator_payload = operator_snapshot.build_operator_snapshot(repo_root)
    operations_payload = web_operations.operations_console_snapshot(repo_root)
    issues = _evaluate_surface_consistency(
        source=source,
        bootstrap_packet=bootstrap_packet,
        operator_payload=operator_payload,
        operations_payload=operations_payload,
    )
    runtime_projection = dict(source.get("runtime_projection") or {})
    return {
        "issues": issues,
        "source_fingerprint": _clean_text(runtime_projection.get("source_fingerprint")),
        "product_promise": _clean_text(runtime_projection.get("product_promise")),
        "bootstrap_reload_status": _clean_text(bootstrap_packet.get("reload_status")),
        "operator_mission_status": _clean_text((operator_payload.get("mission_surface") or {}).get("status")),
        "refresh_scope_summary": _clean_text(((operations_payload.get("runtime_controls") or {}).get("refresh_scope") or {}).get("summary")),
    }


def _collect_invalid_contract_observation(repo_root: Path) -> Dict[str, Any]:
    invalid_payload = _load_canonical_contract_payload(_repo_root())
    invalid_payload.pop("claim_ceiling_summary", None)
    _seed_runtime_fixture(repo_root, contract_payload=invalid_payload)
    source = mission_contract.read_plane_mission_contract(repo_root=repo_root)
    bootstrap_packet = bootstrap.build_fresh_turn_bootstrap(repo_root, recent_chat_rows=[])
    operator_payload = operator_snapshot.build_operator_snapshot(repo_root)
    return {
        "mission_contract_ok": bool(source.get("ok")),
        "failure_class": _clean_text(source.get("failure_class")),
        "bootstrap_failure_class": _clean_text((bootstrap_packet.get("mission_contract") or {}).get("failure_class")),
        "operator_mission_status": _clean_text((operator_payload.get("mission_surface") or {}).get("status")),
        "operator_mission_summary": _clean_text((operator_payload.get("mission_surface") or {}).get("summary")),
    }


def _collect_off_mission_observation() -> Dict[str, Any]:
    result = mission_drift_gate.assess_intent_mission_fit(
        title="Autonomous lab director expansion",
        intent_markdown="# Intent Sheet\n\nPursue fully autonomous lab director behavior in the near term.\n",
    )
    return {
        "ok": bool(result.get("ok")),
        "classification": _clean_text(result.get("classification")),
        "decision": _clean_text(result.get("decision")),
        "reason_codes": list(result.get("reason_codes") or []),
        "matched_non_goals": list(result.get("matched_non_goals") or []),
    }


def _collect_seeded_contradiction_observation(repo_root: Path) -> Dict[str, Any]:
    base = _collect_positive_surface_observation(repo_root)
    contract_payload = _load_canonical_contract_payload(_repo_root())
    _seed_runtime_fixture(repo_root, contract_payload=contract_payload)
    source = mission_contract.read_plane_mission_contract(repo_root=repo_root)
    bootstrap_packet = bootstrap.build_fresh_turn_bootstrap(repo_root, recent_chat_rows=[])
    operator_payload = operator_snapshot.build_operator_snapshot(repo_root)
    operations_payload = web_operations.operations_console_snapshot(repo_root)
    operator_payload["mission_surface"]["source_fingerprint"] = "seeded-mismatch"
    issues = _evaluate_surface_consistency(
        source=source,
        bootstrap_packet=bootstrap_packet,
        operator_payload=operator_payload,
        operations_payload=operations_payload,
    )
    return {
        "baseline_issue_count": len(list(base.get("issues") or [])),
        "issue_codes": [row["code"] for row in issues],
    }


def _scenario(
    scenario_id: str,
    *,
    scenario_kind: str,
    outcome: str,
    mapped_requirement_ids: Sequence[str],
    intent_clause: str,
    observed: Mapping[str, Any],
    evidence_refs: Sequence[str],
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "outcome": outcome,
        "mapped_requirement_ids": list(mapped_requirement_ids),
        "intent_clause": intent_clause,
        "observed": dict(observed),
        "evidence_refs": list(evidence_refs),
    }


def _build_requirement_coverage(
    *,
    scenarios: Iterable[Mapping[str, Any]],
    dependency_snapshot: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    scenario_ids_by_requirement: Dict[str, List[str]] = {}
    scenario_lookup = {str(row.get("scenario_id") or ""): dict(row) for row in scenarios}
    for row in scenario_lookup.values():
        for requirement_id in list(row.get("mapped_requirement_ids") or []):
            scenario_ids_by_requirement.setdefault(str(requirement_id), []).append(str(row.get("scenario_id") or ""))

    dependencies_ready = all(bool(row.get("has_evidence")) and str(row.get("status")) == "verified" for row in dependency_snapshot)
    return [
        {
            "requirement_id": "mission_source_to_operator_projection",
            "intent_clause": "One assembled-system success path must prove loaded mission data projects consistently through runtime and operator-visible surfaces.",
            "status": "proven" if scenario_lookup["positive_loaded_mission_projects_consistently"]["outcome"] == "passed" else "unproven",
            "scenario_ids": scenario_ids_by_requirement.get("mission_source_to_operator_projection", []),
        },
        {
            "requirement_id": "stale_framing_fails_closed",
            "intent_clause": "Stale or malformed mission framing must fail closed instead of inventing runtime or operator mission text.",
            "status": "proven" if scenario_lookup["negative_missing_claim_ceiling_blocks_projection"]["outcome"] == "passed" else "unproven",
            "scenario_ids": scenario_ids_by_requirement.get("stale_framing_fails_closed", []),
        },
        {
            "requirement_id": "off_mission_admission_blocked",
            "intent_clause": "Off-mission work admission must be blocked rather than silently normalized into platform scope.",
            "status": "proven" if scenario_lookup["negative_off_mission_intent_rejected"]["outcome"] == "passed" else "unproven",
            "scenario_ids": scenario_ids_by_requirement.get("off_mission_admission_blocked", []),
        },
        {
            "requirement_id": "runtime_operator_contradiction_detected",
            "intent_clause": "Contradiction between runtime and operator mission views must remain observable instead of smoothing over disagreement.",
            "status": "proven" if scenario_lookup["negative_seeded_projection_contradiction_detected"]["outcome"] == "passed" else "unproven",
            "scenario_ids": scenario_ids_by_requirement.get("runtime_operator_contradiction_detected", []),
        },
        {
            "requirement_id": "family_dependency_readiness",
            "intent_clause": "Intent validation cannot claim full family proof while implementation children remain unreconciled or lack durable evidence.",
            "status": "proven" if dependencies_ready else "unproven",
            "scenario_ids": [],
        },
    ]


def build_validation_report(
    *,
    repo_root: Path | None = None,
    report_path: Path | None = None,
    implementation_repo_root: Path | None = None,
    now_fn: Callable[[], str] = utc_now_iso,
    force_dependency_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    impl_root = Path(implementation_repo_root or _repo_root()).resolve()
    report_rel = Path(report_path) if report_path is not None else _default_report_path(root)

    dependency_snapshot = [_dependency_snapshot(impl_root, uid) for uid in IMPLEMENTATION_CHILD_UIDS]
    if force_dependency_ready is True:
        dependency_snapshot = [{**row, "status": "verified", "has_evidence": True} for row in dependency_snapshot]
    elif force_dependency_ready is False:
        dependency_snapshot = [{**row, "status": row["status"], "has_evidence": False if not row["has_evidence"] else row["has_evidence"]} for row in dependency_snapshot]

    with tempfile.TemporaryDirectory(prefix="mission-contract-intent-validation-") as tmp:
        tmp_root = Path(tmp)
        positive = _collect_positive_surface_observation(tmp_root / "positive")
        invalid = _collect_invalid_contract_observation(tmp_root / "invalid")
        off_mission = _collect_off_mission_observation()
        contradiction = _collect_seeded_contradiction_observation(tmp_root / "contradiction")

    scenarios = [
        _scenario(
            "positive_loaded_mission_projects_consistently",
            scenario_kind="success_path",
            outcome="passed" if not positive["issues"] else "failed",
            mapped_requirement_ids=["mission_source_to_operator_projection"],
            intent_clause="The plane should behave like a persistent, operator-guided laboratory assistant and project the same mission contract through runtime and operator surfaces.",
            observed=positive,
            evidence_refs=[
                "governance/plane_mission_contract_v1.json",
                "infra/controlplane/plane_mission_contract.py",
                "infra/webui/server_lab_assistant_bootstrap.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
                "infra/webui/server_lab_assistant_web_operations.py",
            ],
        ),
        _scenario(
            "negative_missing_claim_ceiling_blocks_projection",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if invalid["mission_contract_ok"] is False
                and invalid["failure_class"] == "mission_contract_invalid"
                and invalid["bootstrap_failure_class"] == "mission_contract_invalid"
                and invalid["operator_mission_status"] == "degraded"
                else "failed"
            ),
            mapped_requirement_ids=["stale_framing_fails_closed"],
            intent_clause="Mission framing must fail closed when required claim-ceiling fields are missing.",
            observed=invalid,
            evidence_refs=[
                "infra/controlplane/plane_mission_contract.py",
                "infra/webui/server_lab_assistant_bootstrap.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
            ],
        ),
        _scenario(
            "negative_off_mission_intent_rejected",
            scenario_kind="negative_path",
            outcome=(
                "passed"
                if off_mission["ok"] is False
                and off_mission["decision"] == "block"
                and "explicit_non_goal_match" in off_mission["reason_codes"]
                else "failed"
            ),
            mapped_requirement_ids=["off_mission_admission_blocked"],
            intent_clause="The platform must reject seductive but off-mission work admission instead of smoothing it into scope.",
            observed=off_mission,
            evidence_refs=[
                "infra/controlplane/mission_drift_gate.py",
                "governance/plane_mission_contract_v1.json",
            ],
        ),
        _scenario(
            "negative_seeded_projection_contradiction_detected",
            scenario_kind="negative_control",
            outcome=(
                "passed"
                if contradiction["baseline_issue_count"] == 0
                and "operator_surface_projection_mismatch" in contradiction["issue_codes"]
                else "failed"
            ),
            mapped_requirement_ids=["runtime_operator_contradiction_detected"],
            intent_clause="Operator/runtime contradiction must remain detectable when projections diverge.",
            observed=contradiction,
            evidence_refs=[
                "infra/controlplane/plane_mission_contract.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
                "infra/webui/server_lab_assistant_web_operations.py",
            ],
        ),
    ]

    requirement_coverage = _build_requirement_coverage(scenarios=scenarios, dependency_snapshot=dependency_snapshot)
    all_scenarios_passed = all(row["outcome"] == "passed" for row in scenarios)
    dependencies_ready = all(row["status"] == "verified" and bool(row["has_evidence"]) for row in dependency_snapshot)
    validation_outcome = "passed" if all_scenarios_passed and dependencies_ready else "proof_gap"
    maximum_supported_claim = (
        "assembled_mission_contract_projection_validated"
        if validation_outcome == "passed"
        else "mission_contract_projection_and_drift_gates_exercised_but_family_dependencies_unverified"
    )

    residual_gaps: List[Dict[str, Any]] = []
    if not dependencies_ready:
        residual_gaps.append(
            {
                "risk_type": "validation_risk",
                "summary": "Implementation children remain draft or lack durable evidence, so this child cannot honestly promote the family beyond proof_gap.",
                "affected_uids": [row["uid"] for row in dependency_snapshot if row["status"] != "verified" or not bool(row["has_evidence"])],
            }
        )

    report = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "family_parent_uid": PARENT_UID,
        "validation_case_id": VALIDATION_CASE_ID,
        "validation_case_utc": now_fn(),
        "validation_outcome": validation_outcome,
        "maximum_supported_claim": maximum_supported_claim,
        "summary": (
            "Mission contract projections are consistent across source, runtime bootstrap, operator snapshot, and refresh-scope surfaces, and negative controls fail closed."
            if validation_outcome == "passed"
            else "Mission contract projection and drift gates are exercised with real success and negative-control coverage, but family proof is still capped by unreconciled implementation-child evidence."
        ),
        "dependency_snapshot": dependency_snapshot,
        "scenario_results": scenarios,
        "requirement_coverage": requirement_coverage,
        "coverage_summary": {
            "requirement_count": len(requirement_coverage),
            "scenario_count": len(scenarios),
            "scenario_outcome_counts": {
                "passed": sum(1 for row in scenarios if row["outcome"] == "passed"),
                "failed": sum(1 for row in scenarios if row["outcome"] != "passed"),
            },
            "covered_scenario_kinds": sorted({str(row["scenario_kind"]) for row in scenarios}),
        },
        "validation_commands": list(VALIDATION_COMMANDS),
        "evidence_paths": {"validation_report": report_rel.as_posix()},
        "non_claims": [
            "no claim that the mission-contract family is fully closed while dependency children remain draft or unreconciled",
            "no claim that a live web server or in-flight job was exercised here; this validation uses repo-native runtime builders and seeded negative controls",
            "no hidden transcript-dependent evidence was used",
        ],
        "residual_gaps": residual_gaps,
    }
    report["artifact_fingerprint"] = _fingerprint(report)
    return report


def validate_validation_report(report: Mapping[str, Any]) -> List[str]:
    issues: List[str] = []
    scenario_ids = {str(row.get("scenario_id") or "") for row in list(report.get("scenario_results") or [])}
    required_positive = {"positive_loaded_mission_projects_consistently"}
    required_negative = {
        "negative_missing_claim_ceiling_blocks_projection",
        "negative_off_mission_intent_rejected",
        "negative_seeded_projection_contradiction_detected",
    }
    missing_positive = sorted(required_positive - scenario_ids)
    missing_negative = sorted(required_negative - scenario_ids)
    if missing_positive:
        issues.append(f"missing required positive scenarios: {', '.join(missing_positive)}")
    if missing_negative:
        issues.append(f"missing required negative scenarios: {', '.join(missing_negative)}")

    payload = dict(report)
    actual_fingerprint = _clean_text(payload.pop("artifact_fingerprint", ""))
    expected_fingerprint = _fingerprint(payload)
    if actual_fingerprint != expected_fingerprint:
        issues.append("artifact_fingerprint does not match payload")

    required_fields = {
        "schema_version",
        "uid",
        "source_intent_uid",
        "family_parent_uid",
        "validation_case_id",
        "validation_case_utc",
        "validation_outcome",
        "maximum_supported_claim",
        "summary",
        "dependency_snapshot",
        "scenario_results",
        "requirement_coverage",
        "validation_commands",
        "non_claims",
    }
    missing_fields = sorted(field for field in required_fields if field not in report)
    if missing_fields:
        issues.append(f"missing required fields: {', '.join(missing_fields)}")
    return issues


def write_validation_report(
    report: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    root = Path(repo_root or _repo_root()).resolve()
    rel = Path(report_path) if report_path is not None else _default_report_path(root)
    target = (root / rel).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    issues = validate_validation_report(report)
    if issues:
        raise ValueError("; ".join(issues))
    write_json_atomic(target, dict(report))
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the mission contract assembled-system intent validation artifact.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repo root where the artifact should be written.")
    parser.add_argument("--report-path", type=Path, default=None, help="Optional report path relative to repo root.")
    parser.add_argument("--implementation-repo-root", type=Path, default=Path("."), help="Repo root used for implementation/dependency inspection.")
    parser.add_argument("--validation-case-utc", type=str, default=None, help="Optional fixed UTC timestamp for deterministic rebuilds.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    now_fn: Callable[[], str]
    if args.validation_case_utc:
        now_fn = lambda: args.validation_case_utc
    else:
        now_fn = utc_now_iso

    report = build_validation_report(
        repo_root=args.repo_root,
        report_path=args.report_path,
        implementation_repo_root=args.implementation_repo_root,
        now_fn=now_fn,
    )
    path = write_validation_report(report, repo_root=args.repo_root, report_path=args.report_path)
    print(json.dumps({"ok": True, "path": _repo_rel(args.repo_root.resolve(), path), "validation_outcome": report["validation_outcome"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
