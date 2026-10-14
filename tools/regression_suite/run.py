"""Deterministic local tooling regression suite.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from tools.backend_advisor.advice import build_backend_advice
from tools.context_suppliers.compress import build_context_bundle
from tools.deterministic_tooling.common import build_envelope, normalized_list, write_output
from tools.governance_hygiene.lint import lint_governance
from tools.lesson_index.build import build_lesson_index
from tools.operator_digest.build import build_operator_digest
from tools.repo_intel.map import build_impact_map
from tools.verification_bundle.build import build_verification_bundle

SCHEMA_VERSION = "regression_scorecard_v1"
TOOL_NAME = "deterministic_tooling_regression_suite"
_FAIL_CLOSED_CHILDREN = {"context_bundle", "verification_bundle", "git_guard"}


def run_regression_suite(*, repo_root: Path, parent_uid: str, child_pis_ids: Sequence[str]) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    context = build_context_bundle(repo_root=repo_root, target_pis_uid=parent_uid, target_files=["config/runtime_policy.properties"])
    impact = build_impact_map(repo_root=repo_root, target_files=["tools/git/support/commit_journal.py"], policy_path="config/runtime_policy.properties")
    verification = build_verification_bundle(
        repo_root=repo_root,
        target_pis_uid=parent_uid,
        impact_map=impact,
        selectors=[],
        require_evidence=True,
    )
    status_rows: list[dict[str, Any]] = [
        {"child": "context_bundle", "status": "pass" if not context.get("errors") else "fail", "output_hash": context["output_hash"]},
        {"child": "impact_map", "status": "pass" if not impact.get("errors") else "fail", "output_hash": impact["output_hash"]},
        {"child": "verification_bundle", "status": "pass" if not verification.get("errors") else "fail", "output_hash": verification["output_hash"]},
    ]
    blocking_steps = [row["child"] for row in status_rows if row["child"] in _FAIL_CLOSED_CHILDREN and row["status"] != "pass"]
    if blocking_steps:
        outputs = {
            "regression_result": "fail",
            "child_results": status_rows,
            "blocking_steps": blocking_steps,
            "evidence_manifest": [context["output_hash"], impact["output_hash"], verification["output_hash"]],
            "risks": ["FAIL_CLOSED child blocked downstream flow."],
        }
        return build_envelope(schema_version=SCHEMA_VERSION, tool_name=TOOL_NAME, inputs={"repo_root": str(repo_root), "parent_uid": parent_uid, "child_pis_ids": normalized_list(child_pis_ids)}, outputs=outputs)

    backend = build_backend_advice(repo_root=repo_root, target_pis_uid=parent_uid, budget=1000)
    governance = lint_governance(repo_root=repo_root, parent_uid=parent_uid, child_set=child_pis_ids)
    digest = build_operator_digest(repo_root=repo_root, job_ids=["overnight_20260315_context_compression"])
    lessons = build_lesson_index(repo_root=repo_root, parent_uid=parent_uid, job_digests=[digest])
    status_rows.extend(
        [
            {"child": "backend_advisor", "status": "pass" if not backend.get("errors") else "fail", "output_hash": backend["output_hash"]},
            {"child": "governance_hygiene", "status": "pass" if not governance.get("errors") else "fail", "output_hash": governance["output_hash"]},
            {"child": "operator_digest", "status": "pass" if not digest.get("errors") else "fail", "output_hash": digest["output_hash"]},
            {"child": "lesson_index", "status": "pass" if not lessons.get("errors") else "fail", "output_hash": lessons["output_hash"]},
        ]
    )
    outputs = {
        "regression_result": "pass" if all(row["status"] == "pass" for row in status_rows) else "fail",
        "child_results": status_rows,
        "blocking_steps": [],
        "evidence_manifest": [row["output_hash"] for row in status_rows],
        "risks": [row["child"] for row in status_rows if row["status"] != "pass"],
    }
    return build_envelope(schema_version=SCHEMA_VERSION, tool_name=TOOL_NAME, inputs={"repo_root": str(repo_root), "parent_uid": parent_uid, "child_pis_ids": normalized_list(child_pis_ids)}, outputs=outputs)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic tooling regression suite")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--child-pis", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = run_regression_suite(repo_root=Path(args.repo_root), parent_uid=args.parent, child_pis_ids=args.child_pis)
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
