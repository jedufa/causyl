from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


FCEE_SCENARIOS = [
    {
        "name": "patch_pass_basic",
        "label": "PASS path",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_patch_pass_basic",
    },
    {
        "name": "broken_patch_proof_gap",
        "label": "Proof gap handling",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_broken_patch_surfaces_proof_gap",
    },
    {
        "name": "build_verify_recovery_to_pass",
        "label": "Deterministic recovery",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_build_verify_recovery_to_pass",
    },
    {
        "name": "failed_verification",
        "label": "Failed verification",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_failed_verification_is_honest",
    },
    {
        "name": "scope_rejected_status_reconcile",
        "label": "Scope rejection",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_scope_rejected_stays_bounded",
    },
    {
        "name": "economic_stop_no_new_signal",
        "label": "Economic stop",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_economic_stop_no_new_signal",
    },
    {
        "name": "full_stack_blocked_chain_with_upgrade_pressure",
        "label": "Full-stack upgrade pressure",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_full_stack_blocked_chain_with_upgrade_pressure",
    },
    {
        "name": "phase3_blocked_patch_recommendation",
        "label": "Blocked recommendation",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_phase3_blocked_patch_recommends_patch_action",
    },
    {
        "name": "phase3_contradiction_prefers_reconcile",
        "label": "Contradiction handling",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_phase3_contradiction_prefers_reconcile",
    },
    {
        "name": "phase3_pass_complete_no_recommendation",
        "label": "Quiet PASS state",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_phase3_pass_complete_is_quiet",
    },
    {
        "name": "status_reconcile_success_bounded",
        "label": "Status reconcile success",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_status_reconcile_success_bounded",
    },
    {
        "name": "full_stack_rejected_upgrade_bad_economics",
        "label": "Rejected bad economics",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_full_stack_rejected_upgrade_bad_economics",
    },
    {
        "name": "full_stack_unsafe_recurring_fallback",
        "label": "Unsafe recurring fallback",
        "nodeid": "tests/control_plane/e2e/test_action_plane_e2e.py::test_fcee_e2e_full_stack_unsafe_recurring_fallback",
    },
]

FAMILY_SCENARIOS = {
    "fcee": FCEE_SCENARIOS,
}

FAMILY_TITLE = {
    "fcee": "FCEE E2E STATUS",
}

FAST_SCENARIO_NAMES = {
    "fcee": ["patch_pass_basic", "broken_patch_proof_gap"],
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _select_scenarios(*, family: str, scenario: str, run_all: bool, fast: bool, full: bool) -> List[Dict[str, str]]:
    rows = list(FAMILY_SCENARIOS[family])
    if scenario:
        selected = [row for row in rows if str(row.get("name") or "") == scenario]
        if not selected:
            raise ValueError(f"Unknown {family} scenario: {scenario}")
        return selected
    if full or run_all:
        return rows
    if fast:
        names = set(FAST_SCENARIO_NAMES.get(family) or [])
        return [row for row in rows if str(row.get("name") or "") in names]
    names = set(FAST_SCENARIO_NAMES.get(family) or [])
    return [row for row in rows if str(row.get("name") or "") in names]


def _run_pytest_node(nodeid: str, *, root: Path) -> Dict[str, Any]:
    started = time.time()
    cp = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", nodeid],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    duration_s = round(time.time() - started, 3)
    return {
        "nodeid": nodeid,
        "ok": cp.returncode == 0,
        "exit_code": int(cp.returncode),
        "stdout": str(cp.stdout or ""),
        "stderr": str(cp.stderr or ""),
        "duration_s": duration_s,
    }


def run_suite(*, family: str, scenario: str = "", run_all: bool = False, fast: bool = False, full: bool = False, root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root or repo_root()).resolve()
    selected = _select_scenarios(family=family, scenario=scenario, run_all=run_all, fast=fast, full=full)
    scenario_results: List[Dict[str, Any]] = []
    for row in selected:
        result = _run_pytest_node(str(row["nodeid"]), root=root)
        scenario_results.append(
            {
                "name": str(row["name"]),
                "label": str(row["label"]),
                "nodeid": str(row["nodeid"]),
                **result,
            }
        )
    passed = sum(1 for row in scenario_results if bool(row.get("ok")))
    total = len(scenario_results)
    ok = passed == total
    mode = "scenario" if scenario else ("full" if (full or run_all) else "fast")
    return {
        "schema_version": "lab_e2e_result_v1",
        "family": family,
        "mode": mode,
        "passed": passed,
        "total": total,
        "ok": ok,
        "scenarios": scenario_results,
    }


def render_suite_text(result: Dict[str, Any]) -> str:
    family = str(result.get("family") or "").strip()
    title = str(FAMILY_TITLE.get(family) or f"{family.upper()} E2E STATUS")
    rows = list(result.get("scenarios") or [])
    passed = int(result.get("passed") or 0)
    total = int(result.get("total") or 0)
    ok = bool(result.get("ok"))
    lines = [title, "-" * len(title)]
    for row in rows:
        mark = "✓" if bool(row.get("ok")) else "✗"
        label = str(row.get("label") or row.get("name") or "").strip()
        lines.append(f"{label:<28} {mark}")
    lines.append("")
    overall = "TRUSTABLE" if ok else "BROKEN"
    lines.append(f"OVERALL: {overall} ({passed}/{total} scenarios)")
    failing = [row for row in rows if not bool(row.get("ok"))]
    if failing:
        first = failing[0]
        detail = str(first.get("stderr") or first.get("stdout") or "").strip()
        if detail:
            lines.append("")
            lines.append("FIRST FAILURE:")
            lines.append(str(first.get("label") or first.get("name") or "").strip())
            lines.append(detail[-1200:])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run plane E2E suites through a stable operator-facing wrapper.")
    parser.add_argument("family", choices=sorted(FAMILY_SCENARIOS), help="E2E family to run.")
    parser.add_argument("--scenario", default="", help="Run one named scenario only.")
    parser.add_argument("--all", action="store_true", help="Run the full family.")
    parser.add_argument("--full", action="store_true", help="Run the full family.")
    parser.add_argument("--fast", action="store_true", help="Run the fast subset.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the human summary.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    result = run_suite(
        family=str(args.family),
        scenario=str(args.scenario or "").strip(),
        run_all=bool(args.all),
        fast=bool(args.fast),
        full=bool(args.full),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_suite_text(result))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
