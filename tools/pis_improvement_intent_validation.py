from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping

from infra.controlplane import pis_improvement_entrypoints as entrypoints
from infra.controlplane import pis_improvement_session_contract as contract
from infra.webui.ledger import ledger_storage
from tools.bin import pis_improvement as cli
from tools.shared.fsio import utc_now_iso, write_json_atomic


UID = "PIS-20260412-062104Z-E5G7"
SOURCE_INTENT_UID = "IS-20260403-212914Z-AED0"
PARENT_UID = "PIS-20260412-060451Z-EB85"
IMPLEMENTATION_CHILD_UIDS = (
    "PIS-20260412-062100Z-A1C3",
    "PIS-20260412-062101Z-B2D4",
    "PIS-20260412-062102Z-C3E5",
    "PIS-20260412-062103Z-D4F6",
)
SCHEMA_VERSION = "pis_improvement_intent_validation_v1"
DEFAULT_REPORT_PATH_SUFFIX = Path("artifacts/intent_validation/pis_improvement_intent_validation.json")
VALIDATION_COMMANDS = [
    "python -m py_compile tools/pis_improvement_intent_validation.py tests/tools/test_pis_improvement_intent_validation.py",
    "python -m pytest tests/tools/test_pis_improvement_intent_validation.py -q",
    "python -m tools.pis_improvement_intent_validation --repo-root .",
    "validation artifact scenario and ledger consistency assertions",
    "git diff --check -- tools/pis_improvement_intent_validation.py tests/tools/test_pis_improvement_intent_validation.py governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260412-062104Z-E5G7__validate-pis-improvement-automation-assembled-system-intent-e2e-suite/artifacts/intent_validation/pis_improvement_intent_validation.json",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _resolve_under_repo(repo_root: Path, rel_path: Path) -> Path:
    candidate = (repo_root / rel_path).resolve()
    if candidate == repo_root or repo_root not in candidate.parents:
        raise ValueError(f"path escapes repo root: {rel_path}")
    return candidate


def _repo_rel(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


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


def _seed_pis(
    repo_root: Path,
    *,
    uid: str,
    slug: str,
    title: str,
    status: str,
    updated_utc: str,
    derived_intent_uid: str = SOURCE_INTENT_UID,
) -> Path:
    item_dir = ledger_storage.ensure_item_dir(
        ledger_storage.ledger_patch_instruction_sheets_root(repo_root),
        uid=uid,
        slug=slug,
        status=status,
        created_utc=updated_utc,
    )
    ledger_storage.atomic_write_json(
        item_dir / "meta.json",
        {
            "uid": uid,
            "slug": slug,
            "title": title,
            "status": status,
            "created_utc": updated_utc,
            "updated_utc": updated_utc,
            "release_state": "draft",
        },
    )
    ledger_storage.atomic_write_text(
        item_dir / "pis.md",
        f"# Patch Instruction Sheet (PIS)\n\n**UID:** {uid}\n\n**Derived from Intent Sheet:** {derived_intent_uid}\n\n## 1. Title\n{title}\n",
    )
    return item_dir


def _round_payload(*, critique_summary: str | None = None) -> Dict[str, Any]:
    return {
        "round_id": "round_001",
        "candidate_ref": "cand_recent_001",
        "critique_packet": {
            "summary": critique_summary or "The candidate needs explicit critique-to-resolution mapping for replay-safe audit.",
            "packet_provenance": {
                "source_pis_hash": "a" * 64,
                "cited_artifacts": [
                    "docs/contracts/contract_pis_improvement_session_v1.md",
                    "governance/pis_improvement_session_contract_v1.json",
                ],
            },
            "critique_items": [
                {
                    "critique_id": "critique_item_001",
                    "category": "resolution_gap",
                    "severity": "major",
                    "finding": "A critique without an explicit response mapping will not survive replay.",
                    "evidence": "The shared contract requires critique-to-resolution linkage in the revision ledger.",
                    "proposed_resolution": "Persist a reviser packet that answers critique_item_001 explicitly.",
                }
            ],
            "overall_verdict": "revision_required",
        },
        "reviser_packet": {
            "summary": "Linked the response back to the recorded critique item.",
            "resolution_items": [
                {
                    "critique_id": "critique_item_001",
                    "disposition": "addressed",
                    "response": "The reviser packet now cites the critique item directly.",
                    "change_summary": "Replay can prove the critique was answered.",
                }
            ],
            "overall_status": "ready_for_review",
        },
        "proposed_revision_text": "--- a/demo\n+++ b/demo\n@@\n+deterministic revision delta\n",
        "change_summary": "Persisted a replay-safe critique resolution mapping.",
    }


def _second_round_payload() -> Dict[str, Any]:
    payload = _round_payload()
    payload["round_id"] = "round_002"
    payload["critique_packet"]["summary"] = "A second round still needs to respect the deterministic iteration cap."
    payload["critique_packet"]["critique_items"][0]["critique_id"] = "critique_item_002"
    payload["critique_packet"]["critique_items"][0]["finding"] = "The loop must block instead of silently processing extra rounds."
    payload["reviser_packet"]["resolution_items"][0]["critique_id"] = "critique_item_002"
    return payload


def _final_recommendation() -> Dict[str, Any]:
    return {
        "recommendation_id": "recommendation_001",
        "outcome": "adopt_revision",
        "winning_candidate_ref": "cand_recent_001",
        "rationale_markdown": "# Final recommendation\n\nAdopt the deterministic revision with explicit critique linkage.\n",
    }


def _seed_runtime_fixture(repo_root: Path) -> None:
    _seed_pis(
        repo_root,
        uid=PARENT_UID,
        slug="parent",
        title="PIS Improvement Automation",
        status="draft",
        updated_utc="2026-04-12T06:21:06Z",
    )
    _seed_pis(
        repo_root,
        uid="PIS-20260412-062103Z-D4F6",
        slug="entrypoint",
        title="Entry points",
        status="verified",
        updated_utc="2026-04-12T06:21:04Z",
    )
    _seed_pis(
        repo_root,
        uid="PIS-20260412-062102Z-C3E5",
        slug="orchestration",
        title="Orchestration",
        status="verified",
        updated_utc="2026-04-12T06:21:03Z",
    )
    _seed_pis(
        repo_root,
        uid="PIS-20260412-062101Z-B2D4",
        slug="critique-contract",
        title="Structured critique contract",
        status="verified",
        updated_utc="2026-04-12T06:21:02Z",
    )
    _seed_pis(
        repo_root,
        uid="PIS-20260412-062100Z-A1C3",
        slug="session-contract",
        title="Session contract",
        status="verified",
        updated_utc="2026-04-12T06:21:01Z",
    )


def _run_cli_json(args: List[str], *, repo_root: Path) -> tuple[int, Dict[str, Any]]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = cli.main(args + ["--json"], repo_root=repo_root)
    payload = json.loads(stdout.getvalue())
    return exit_code, payload


def _collect_implementation_child_inputs(base_root: Path) -> List[Dict[str, Any]]:
    surface_map = {
        "PIS-20260412-062100Z-A1C3": [
            "infra/controlplane/pis_improvement_session_contract.py",
            "tests/control_plane/test_pis_improvement_session_contract.py",
            "docs/contracts/contract_pis_improvement_session_v1.md",
            "governance/pis_improvement_session_contract_v1.json",
        ],
        "PIS-20260412-062101Z-B2D4": [
            "infra/controlplane/pis_improvement_session_contract.py",
            "tests/control_plane/test_pis_improvement_session_contract.py",
            "governance/pis_improvement_session_reference_examples_v1.json",
        ],
        "PIS-20260412-062102Z-C3E5": [
            "infra/controlplane/pis_improvement_orchestration.py",
            "tests/control_plane/test_pis_improvement_orchestration.py",
        ],
        "PIS-20260412-062103Z-D4F6": [
            "infra/controlplane/pis_improvement_entrypoints.py",
            "tools/bin/pis_improvement.py",
            "tests/control_plane/test_pis_improvement_entrypoints.py",
        ],
    }
    rows: List[Dict[str, Any]] = []
    for uid in IMPLEMENTATION_CHILD_UIDS:
        item_dir = _find_item_dir(base_root, uid, kind="pis")
        meta = _read_json(item_dir / "meta.json")
        rows.append(
            {
                "uid": uid,
                "title": _clean_text(meta.get("title")) or uid,
                "status": _clean_text(meta.get("status")) or "draft",
                "release_state": _clean_text(meta.get("release_state")) or "draft",
                "pis_path": _repo_rel(base_root, item_dir / "pis.md"),
                "meta_path": _repo_rel(base_root, item_dir / "meta.json"),
                "implementation_surfaces": [
                    rel_path for rel_path in surface_map[uid] if (base_root / rel_path).exists()
                ],
            }
        )
    return rows


def _ledger_truth(base_root: Path, child_inputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    source_intent_dir = _find_item_dir(base_root, SOURCE_INTENT_UID, kind="intent")
    parent_dir = _find_item_dir(base_root, PARENT_UID, kind="pis")
    this_child_dir = _find_item_dir(base_root, UID, kind="pis")
    source_intent_meta = _read_json(source_intent_dir / "meta.json")
    parent_meta = _read_json(parent_dir / "meta.json")
    this_child_meta = _read_json(this_child_dir / "meta.json")
    verified_children = [row["uid"] for row in child_inputs if row["status"] == "verified"]
    unverified_children = [row["uid"] for row in child_inputs if row["status"] != "verified"]
    blockers: List[str] = []
    if _clean_text(source_intent_meta.get("status")) != "verified":
        blockers.append(f"source intent remains {_clean_text(source_intent_meta.get('status')) or 'draft'}")
    if _clean_text(parent_meta.get("status")) != "verified":
        blockers.append(f"parent PIS remains {_clean_text(parent_meta.get('status')) or 'draft'}")
    if unverified_children:
        blockers.append(f"implementation children still unverified: {', '.join(unverified_children)}")
    if _clean_text(this_child_meta.get("status")) != "verified":
        blockers.append(f"intent-validation child remains {_clean_text(this_child_meta.get('status')) or 'draft'} pending closeout")
    return {
        "source_intent_status": _clean_text(source_intent_meta.get("status")) or "draft",
        "parent_status": _clean_text(parent_meta.get("status")) or "draft",
        "this_child_status": _clean_text(this_child_meta.get("status")) or "draft",
        "verified_implementation_children": verified_children,
        "unverified_implementation_children": unverified_children,
        "fail_closed_status_preserved": _clean_text(this_child_meta.get("status")) == "draft" and bool(blockers),
        "blockers": blockers,
    }


def _scenario_list_recent_positive(repo_root: Path) -> Dict[str, Any]:
    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "list_recent",
            "limit": 5,
        },
        repo_root=repo_root,
    )
    ordered = [row["uid"] for row in result["recent_candidates"]]
    if ordered[:4] != [
        PARENT_UID,
        "PIS-20260412-062103Z-D4F6",
        "PIS-20260412-062102Z-C3E5",
        "PIS-20260412-062101Z-B2D4",
    ]:
        raise AssertionError(f"unexpected recent ordering: {ordered}")
    return {
        "scenario_id": "recent_selection_positive",
        "scenario_kind": "positive_path",
        "action": "entrypoint.list_recent",
        "outcome": "passed",
        "observed": {
            "recent_uids": ordered,
            "operator_summary": result["operator_summary"],
        },
        "requirements": [
            "recent_pis_selection",
            "shared_agent_contract_behavior",
        ],
    }


def _scenario_cli_launch_positive(repo_root: Path) -> Dict[str, Any]:
    exit_code, payload = _run_cli_json(
        [
            "launch",
            "--request-id",
            "req_cli_launch_001",
            "--pis",
            PARENT_UID,
            "--candidate",
            "PIS-20260412-062101Z-B2D4",
        ],
        repo_root=repo_root,
    )
    if exit_code != 0:
        raise AssertionError(f"cli launch unexpectedly failed: {payload}")
    issues = entrypoints.validate_result_payload(payload)
    if issues:
        raise AssertionError(f"cli result payload drifted: {issues}")
    return {
        "scenario_id": "cli_launch_positive",
        "scenario_kind": "positive_path",
        "action": "cli.launch",
        "outcome": "passed",
        "observed": {
            "session_id": payload["session_id"],
            "status": payload["status"],
            "selected_candidate_uids": payload["selected_candidate_uids"],
            "artifact_keys": sorted(payload["artifacts"].keys()),
            "recommendation_status": payload["recommendation"]["status"],
        },
        "requirements": [
            "shared_cli_contract_behavior",
        ],
    }


def _scenario_completed_session_contract_validation_positive(repo_root: Path) -> Dict[str, Any]:
    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_complete_positive",
            "source_pis_uid": PARENT_UID,
            "rounds": [_round_payload()],
            "final_recommendation": _final_recommendation(),
        },
        repo_root=repo_root,
    )
    if not result["ok"]:
        raise AssertionError(f"completed entrypoint path unexpectedly failed before validation: {result}")
    session_path = repo_root / result["artifacts"]["session_manifest"]
    session_payload = _read_json(session_path)
    session_validation = contract.validate_pis_improvement_session(session_payload)
    if not session_validation["ok"]:
        raise AssertionError(f"completed assembled session should satisfy the shared contract: {session_validation}")
    return {
        "scenario_id": "completed_session_contract_validation_positive",
        "scenario_kind": "positive_path",
        "action": "entrypoint.launch",
        "outcome": "passed",
        "observed": {
            "status": result["status"],
            "recommendation_outcome": result["recommendation"]["outcome"],
            "current_iteration": session_payload["workflow_state"]["current_iteration"],
            "deterministic_order": session_payload["candidate_selection"]["deterministic_order"],
            "session_contract_ok": session_validation["ok"],
            "validation_errors": session_validation["errors"],
        },
        "requirements": [
            "persistent_revision_history",
            "deterministic_iteration_policy",
            "shared_agent_contract_behavior",
        ],
    }


def _scenario_invalid_source_selection_negative(repo_root: Path) -> Dict[str, Any]:
    exit_code, payload = _run_cli_json(
        [
            "launch",
            "--request-id",
            "req_cli_invalid_source",
            "--pis",
            PARENT_UID,
            "--candidate",
            PARENT_UID,
        ],
        repo_root=repo_root,
    )
    if exit_code == 0 or payload["error_code"] != "invalid_request":
        raise AssertionError(f"cli invalid-source path did not fail closed: {payload}")
    return {
        "scenario_id": "invalid_source_selection_negative",
        "scenario_kind": "negative_path",
        "action": "cli.launch",
        "outcome": "passed",
        "observed": {
            "blocked": payload["blocked"],
            "error_code": payload["error_code"],
            "operator_summary": payload["operator_summary"],
        },
        "requirements": [
            "recent_pis_selection",
            "shared_cli_contract_behavior",
        ],
    }


def _scenario_generic_praise_negative(repo_root: Path) -> Dict[str, Any]:
    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_generic_praise",
            "source_pis_uid": PARENT_UID,
            "rounds": [_round_payload(critique_summary="Looks good")],
        },
        repo_root=repo_root,
    )
    if result["ok"] or result["error_code"] != "invalid_critique_packet":
        raise AssertionError(f"generic praise path did not fail closed: {result}")
    return {
        "scenario_id": "generic_praise_rejected_negative",
        "scenario_kind": "negative_path",
        "action": "entrypoint.launch",
        "outcome": "passed",
        "observed": {
            "blocked": result["blocked"],
            "error_code": result["error_code"],
            "result_status": result["status"],
            "operator_summary": result["operator_summary"],
        },
        "requirements": [
            "structured_critique",
        ],
    }


def _scenario_iteration_cap_negative(repo_root: Path) -> Dict[str, Any]:
    result = entrypoints.run_request(
        {
            "schema_version": entrypoints.ENTRYPOINT_REQUEST_SCHEMA_VERSION,
            "action": "launch",
            "request_id": "req_iteration_cap",
            "source_pis_uid": PARENT_UID,
            "policy_overrides": {"iteration_cap": 1},
            "rounds": [_round_payload(), _second_round_payload()],
            "final_recommendation": _final_recommendation(),
        },
        repo_root=repo_root,
    )
    if result["ok"] or result["error_code"] != "iteration_cap_exceeded":
        raise AssertionError(f"iteration cap path did not fail closed: {result}")
    return {
        "scenario_id": "iteration_cap_exceeded_negative",
        "scenario_kind": "negative_path",
        "action": "entrypoint.launch",
        "outcome": "passed",
        "observed": {
            "blocked": result["blocked"],
            "error_code": result["error_code"],
            "result_status": result["status"],
            "operator_summary": result["operator_summary"],
        },
        "requirements": [
            "deterministic_iteration_policy",
            "persistent_revision_history",
        ],
    }


def _build_requirement_coverage(scenarios: Iterable[Mapping[str, Any]], ledger_truth: Mapping[str, Any]) -> List[Dict[str, Any]]:
    by_id = {row["scenario_id"]: row for row in scenarios}
    return [
        {
            "requirement_id": "recent_pis_selection",
            "intent_clause": "CLI selection of recent PIS documents must be deterministic and explicit.",
            "status": "proven_in_fixture_runs",
            "scenario_ids": [
                "recent_selection_positive",
                "invalid_source_selection_negative",
            ],
            "observed_behavior": "The recent list returned newest-first deterministic candidates, and the CLI blocked when the source PIS was reused as a candidate.",
        },
        {
            "requirement_id": "structured_critique",
            "intent_clause": "Critiques must stay structured and reject generic praise.",
            "status": "proven_in_fixture_runs",
            "scenario_ids": ["generic_praise_rejected_negative"],
            "observed_behavior": "The assembled entrypoint blocked with invalid_critique_packet when critique text collapsed into generic praise.",
        },
        {
            "requirement_id": "persistent_revision_history",
            "intent_clause": "Revision history persistence must remain explicit and replayable.",
            "status": "proven_in_fixture_runs",
            "scenario_ids": ["completed_session_contract_validation_positive"],
            "observed_behavior": "The completed shared-entrypoint run wrote revision-ledger history and the resulting session manifest passed shared-contract validation.",
        },
        {
            "requirement_id": "deterministic_iteration_policy",
            "intent_clause": "Iteration counts and stop policy must be bounded and fail closed.",
            "status": "proven_in_fixture_runs",
            "scenario_ids": [
                "completed_session_contract_validation_positive",
                "iteration_cap_exceeded_negative",
            ],
            "observed_behavior": "A completed session respected the deterministic replay contract, and a cap of one still rejected a second round with explicit blocked state.",
        },
        {
            "requirement_id": "shared_cli_or_agent_contract_behavior",
            "intent_clause": "CLI and future agent/app callers must use the same backend contract instead of ad hoc side paths.",
            "status": "proven_in_fixture_runs",
            "scenario_ids": [
                "recent_selection_positive",
                "cli_launch_positive",
                "completed_session_contract_validation_positive",
                "invalid_source_selection_negative",
            ],
            "observed_behavior": "The shared entrypoint and CLI both emitted the canonical result envelope and preserved fail-closed behavior for invalid requests.",
        },
        {
            "requirement_id": "fail_closed_ledger_truth",
            "intent_clause": "This gate must stay blocked when proof or ledger reconciliation is incomplete.",
            "status": "blocked_by_current_ledger_truth" if ledger_truth["blockers"] else "reconciled",
            "scenario_ids": [],
            "observed_behavior": "; ".join(ledger_truth["blockers"]) if ledger_truth["blockers"] else "No ledger blockers remained.",
        },
    ]


def _assert_report(report: Mapping[str, Any]) -> None:
    scenario_ids = [row["scenario_id"] for row in report["scenario_results"]]
    if not any(row["scenario_kind"] == "positive_path" for row in report["scenario_results"]):
        raise AssertionError("report must include at least one positive scenario")
    if not any(row["scenario_kind"] == "negative_path" for row in report["scenario_results"]):
        raise AssertionError("report must include at least one negative scenario")
    if sorted(item["uid"] for item in report["implementation_child_inputs"]) != sorted(IMPLEMENTATION_CHILD_UIDS):
        raise AssertionError("report must cite all implementation children directly")
    if report["ledger_truth"]["blockers"] and not report["ledger_truth"]["fail_closed_status_preserved"]:
        raise AssertionError("report must preserve fail-closed draft truth while blockers remain")
    if "cli_launch_positive" not in scenario_ids or "generic_praise_rejected_negative" not in scenario_ids:
        raise AssertionError("report omitted required assembled scenarios")


def _fingerprint_without_self(payload: Mapping[str, Any]) -> str:
    cloned = dict(payload)
    cloned.pop("artifact_fingerprint", None)
    encoded = json.dumps(cloned, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_validation_report(
    *,
    repo_root: Path,
    report_path: Path | None = None,
    implementation_repo_root: Path | None = None,
    now_fn: Callable[[], str] = utc_now_iso,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    implementation_root = Path(implementation_repo_root).resolve() if implementation_repo_root is not None else repo_root
    resolved_report_path = report_path if report_path is not None else _default_report_path(repo_root)
    absolute_report_path = _resolve_under_repo(repo_root, resolved_report_path)
    child_inputs = _collect_implementation_child_inputs(implementation_root)
    ledger_truth = _ledger_truth(implementation_root, child_inputs)

    with tempfile.TemporaryDirectory(prefix="pis_improvement_intent_validation_") as tmp_dir:
        fixture_root = Path(tmp_dir)
        _seed_runtime_fixture(fixture_root)
        scenarios = [
            _scenario_list_recent_positive(fixture_root),
            _scenario_cli_launch_positive(fixture_root),
            _scenario_completed_session_contract_validation_positive(fixture_root),
            _scenario_invalid_source_selection_negative(fixture_root),
            _scenario_generic_praise_negative(fixture_root),
            _scenario_iteration_cap_negative(fixture_root),
        ]

    has_blockers = bool(ledger_truth["blockers"])
    validation_outcome = (
        "blocked_pending_family_reconciliation"
        if has_blockers
        else "ready_for_closeout_or_reconciled"
    )
    summary = (
        "Fixture-backed runs proved that the assembled PIS-improvement backend can list recent PIS candidates, "
        "launch the shared CLI/agent envelope, and fail closed on invalid candidate selection, generic praise, "
        "and iteration-cap overflow. A completed shared-entrypoint session now also satisfies the shared session "
        "contract, so the remaining blocker is ledger reconciliation rather than an implementation gap. The "
        "family should stay draft until closeout promotes the parent, source intent, and validating child together."
        if has_blockers
        else
        "Fixture-backed runs still prove the assembled PIS-improvement backend after family closure: recent-PIS "
        "selection is deterministic, the shared CLI and agent envelope remains canonical, malformed critique and "
        "iteration-cap paths fail closed, and completed sessions satisfy the shared session contract. Current "
        "ledger truth is already reconciled, so this report now acts as durable supporting evidence rather than a "
        "blocking gate."
    )

    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "family_parent_uid": PARENT_UID,
        "created_utc": now_fn(),
        "validation_outcome": validation_outcome,
        "summary": summary,
        "validation_commands": list(VALIDATION_COMMANDS),
        "implementation_child_inputs": child_inputs,
        "ledger_truth": ledger_truth,
        "assembled_claim": {
            "earned": (
                "The current backend contract is assembled enough to prove deterministic recent-PIS selection, "
                "shared CLI/agent result envelopes, structured critique rejection, explicit revision-history "
                "persistence, and bounded iteration-cap failure in local fixture runs without hidden context."
            ),
            "blocked_by": list(ledger_truth["blockers"]),
        },
        "scenario_results": scenarios,
        "requirement_coverage": _build_requirement_coverage(scenarios, ledger_truth),
        "non_claims": [
            "no claim that the family is already ledger-verified before closeout reconciliation runs",
            "no Android UI behavior claim beyond reuse of the shared backend contract",
            "no claim that draft ledger state is acceptable final truth",
            "no hidden transcript-dependent evidence was used",
        ],
        "evidence_paths": {
            "validation_report": _repo_rel(repo_root, absolute_report_path),
            "source_intent": _repo_rel(implementation_root, _find_item_dir(implementation_root, SOURCE_INTENT_UID, kind="intent") / "intent_sheet.md"),
            "family_parent": _repo_rel(implementation_root, _find_item_dir(implementation_root, PARENT_UID, kind="pis") / "pis.md"),
        },
    }
    report["artifact_fingerprint"] = _fingerprint_without_self(report)
    _assert_report(report)
    write_json_atomic(absolute_report_path, report)
    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the intent-validation artifact for the PIS improvement family.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--report-path", default="")
    args = parser.parse_args(argv)
    report_path = Path(args.report_path) if args.report_path else None
    report = build_validation_report(
        repo_root=Path(args.repo_root),
        report_path=report_path,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
