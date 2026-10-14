"""Governance, PIS, and roadmap hygiene lint tool.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from tools.deterministic_tooling.common import build_envelope, find_item_dir_for_uid, normalized_list, read_json, read_text, repo_rel, write_output

SCHEMA_VERSION = "governance_hygiene_report_v1"
TOOL_NAME = "governance_pis_roadmap_hygiene"
_REQUIRED_META_FIELDS = ("uid", "title", "status", "schema_version")
_REQUIRED_PROFILE_TOKENS = ("tool_name", "category", "tier", "determinism", "failure_semantics")


def _child_rows(repo_root: Path, child_set: Sequence[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for uid in sorted(normalized_list(child_set)):
        item_dir = find_item_dir_for_uid(repo_root, uid)
        if item_dir is None:
            out.append({"uid": uid, "missing": True})
            continue
        meta = read_json(item_dir / "meta.json") if (item_dir / "meta.json").exists() else {}
        pis_text = read_text(item_dir / "pis.md") if (item_dir / "pis.md").exists() else ""
        out.append({"uid": uid, "missing": False, "meta": meta, "pis_text": pis_text, "path": repo_rel(repo_root, item_dir)})
    return out


def lint_governance(*, repo_root: Path, parent_uid: str, child_set: Sequence[str], context_refs: Sequence[str] = (), required_fields: Sequence[str] = ()) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    required = normalized_list(required_fields) or list(_REQUIRED_META_FIELDS)
    children = _child_rows(repo_root, child_set)
    lint_report: list[dict[str, Any]] = []
    missing_dependencies: list[dict[str, Any]] = []
    roadmap_alignment_summary: list[dict[str, Any]] = []
    suggested_actions: list[str] = []
    seen_titles: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []

    for row in children:
        uid = str(row.get("uid") or "")
        if row.get("missing"):
            lint_report.append({"uid": uid, "severity": "error", "issue": "missing_child", "detail": "Child UID could not be resolved in ledger."})
            missing_dependencies.append({"uid": uid, "reason": "missing_child"})
            continue
        meta = dict(row.get("meta") or {})
        pis_text = str(row.get("pis_text") or "")
        missing_fields = [name for name in required if not str(meta.get(name) or "").strip()]
        if missing_fields:
            lint_report.append({"uid": uid, "severity": "error", "issue": "missing_meta_fields", "detail": ", ".join(missing_fields)})
        missing_profile = [token for token in _REQUIRED_PROFILE_TOKENS if token not in pis_text]
        if missing_profile:
            lint_report.append({"uid": uid, "severity": "warning", "issue": "missing_profile_tokens", "detail": ", ".join(missing_profile)})
        title = str(meta.get("title") or "").strip().lower()
        if title:
            previous_uid = seen_titles.get(title)
            if previous_uid is not None:
                lint_report.append({"uid": uid, "severity": "warning", "issue": "title_overlap", "detail": f"duplicate title with {previous_uid}"})
            else:
                seen_titles[title] = uid
        roadmap_alignment_summary.append({"uid": uid, "title": str(meta.get("title") or ""), "status": str(meta.get("status") or "")})

    if context_refs:
        for ref in normalized_list(context_refs):
            path = (repo_root / ref).resolve()
            if not path.exists():
                lint_report.append({"uid": parent_uid, "severity": "warning", "issue": "missing_context_ref", "detail": ref})

    if not child_set:
        warnings.append({"code": "empty_child_set", "severity": "warning", "message": "No child_set was provided. Coverage cannot be evaluated honestly."})
    if any(row.get("severity") == "error" for row in lint_report):
        suggested_actions.append("Fix missing ledger children or required meta fields before closure.")
    if any(row.get("issue") == "title_overlap" for row in lint_report):
        suggested_actions.append("Clarify overlapping child ownership or merge duplicate scopes.")

    lint_report.sort(key=lambda row: ({"error": 0, "warning": 1}.get(str(row.get("severity") or "warning"), 2), str(row.get("uid") or ""), str(row.get("issue") or "")))
    outputs = {
        "lint_report": lint_report,
        "missing_dependencies": missing_dependencies,
        "roadmap_alignment_summary": roadmap_alignment_summary,
        "suggested_actions": suggested_actions,
    }
    return build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={"repo_root": str(repo_root), "parent_uid": parent_uid, "child_set": normalized_list(child_set), "context_refs": normalized_list(context_refs), "required_fields": required},
        outputs=outputs,
        warnings=warnings,
        provenance=[{"ref_type": "ledger_parent", "ref": parent_uid, "content_hash": "sha256:derived"}],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint governance/PIS hygiene")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--child", action="append", default=[])
    parser.add_argument("--context-ref", action="append", default=[])
    parser.add_argument("--required-field", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = lint_governance(
        repo_root=Path(args.repo_root),
        parent_uid=args.parent,
        child_set=args.child,
        context_refs=args.context_ref,
        required_fields=args.required_field,
    )
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
