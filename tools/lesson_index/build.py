"""Deterministic reusable knowledge and lessons-learned index.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.deterministic_tooling.common import build_envelope, ensure_runtime_dir, hash_object, normalized_list, read_json, repo_rel, store_cache, write_output

SCHEMA_VERSION = "lesson_index_v1"
TOOL_NAME = "reusable_knowledge_lessons_index"
_SEVERITY_RANK = {"critical": 0, "high": 1, "warning": 2, "info": 3}


def _severity_for_status(status: str) -> str:
    lower = str(status or "").lower()
    if lower in {"blocked", "failed", "error"}:
        return "high"
    if lower in {"in_progress", "pending", "unknown"}:
        return "warning"
    return "info"


def build_lesson_index(
    *,
    repo_root: Path,
    parent_uid: str,
    evidence_bundles: Sequence[Mapping[str, Any]] = (),
    job_digests: Sequence[Mapping[str, Any]] = (),
    incident_reports: Sequence[Mapping[str, Any]] = (),
    operator_notes: Sequence[str] = (),
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    lesson_dir = ensure_runtime_dir(repo_root, "lesson_index", parent_uid or "unscoped")
    entries: list[dict[str, Any]] = []
    linked_pis: list[str] = normalized_list([parent_uid])
    for digest in job_digests:
        outputs = digest.get("outputs") if isinstance(digest, Mapping) else {}
        for risk in list(outputs.get("risk_list") or []):
            if not isinstance(risk, Mapping):
                continue
            lesson_id = hash_object({"parent_uid": parent_uid, "risk": dict(risk)}).split(":", 1)[1][:16]
            entries.append(
                {
                    "lesson_id": lesson_id,
                    "severity": _severity_for_status(str(risk.get("status") or "")),
                    "trigger": str(risk.get("reason") or risk.get("status") or "unknown"),
                    "fix": "Inspect the referenced job/report and close the missing proof gap.",
                    "owner_tags": [str(risk.get("job_id") or "unknown")],
                    "source_kind": "job_digest",
                }
            )
    for note in normalized_list(operator_notes):
        lesson_id = hash_object({"parent_uid": parent_uid, "note": note}).split(":", 1)[1][:16]
        entries.append({"lesson_id": lesson_id, "severity": "info", "trigger": note, "fix": "Review operator note before rerunning the same flow.", "owner_tags": ["operator"], "source_kind": "operator_note"})
    entries.sort(key=lambda row: (_SEVERITY_RANK.get(str(row["severity"]), 9), str(row["lesson_id"])))
    index_ref = lesson_dir / "index.json"
    outputs = {"lesson_entries": entries, "lesson_index_ref": repo_rel(repo_root, index_ref), "linked_pis": linked_pis}
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={"repo_root": str(repo_root), "parent_uid": parent_uid, "operator_notes": normalized_list(operator_notes)},
        outputs=outputs,
        provenance=[{"ref_type": "parent_uid", "ref": parent_uid, "content_hash": hash_object(parent_uid)}],
    )
    store_cache(index_ref, envelope)
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic lessons-learned index")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--job-digest", action="append", default=[])
    parser.add_argument("--operator-note", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    job_digests = [read_json(Path(path)) for path in args.job_digest]
    payload = build_lesson_index(repo_root=Path(args.repo_root), parent_uid=args.parent, job_digests=job_digests, operator_notes=args.operator_note)
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
