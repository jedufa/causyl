"""Read-only retrieval, diff, and report helpers for research evidence artifacts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from infra.controlplane import literature_compendium_contract as compendium_contract
from infra.controlplane import research_evidence_pack_contract as pack_contract


REPORTING_HELPER_UID = "PIS-20260410-102636Z-C6F3"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def _rows(value: Any) -> List[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _canonical_row(row: Mapping[str, Any]) -> str:
    return json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _index_by_id(rows: Iterable[Mapping[str, Any]], id_field: str) -> Dict[str, Mapping[str, Any]]:
    out: Dict[str, Mapping[str, Any]] = {}
    for row in rows:
        row_id = _text(row.get(id_field))
        if row_id:
            out[row_id] = dict(row)
    return out


def _changed_ids(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]) -> List[str]:
    changed: List[str] = []
    for row_id in sorted(set(before) | set(after)):
        if _canonical_row(before.get(row_id, {})) != _canonical_row(after.get(row_id, {})):
            changed.append(row_id)
    return changed


def _id_diff(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]) -> Dict[str, List[str]]:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": _changed_ids(before, after),
    }


def _resolve_repo_path(path: Path | str, *, repo_root: Path | None) -> Path:
    candidate = Path(path)
    if repo_root is None:
        return candidate
    if candidate.is_absolute():
        try:
            candidate.resolve().relative_to(Path(repo_root).resolve())
        except ValueError as exc:
            raise ValueError("artifact path must stay under repo_root") from exc
        return candidate.resolve()
    if ".." in candidate.parts:
        raise ValueError("artifact path must be repo-relative without parent traversal")
    root = Path(repo_root).resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path escapes repo_root") from exc
    return resolved


def _relative_artifact_ref(path: Path, *, repo_root: Path | None) -> str:
    if repo_root is None:
        return Path(path).as_posix()
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _read_json_object(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _bounded_errors(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(error) for error in result.get("errors") or []][:20]


def _pack_report_from_result(result: Mapping[str, Any], *, artifact_ref: str = "") -> Dict[str, Any]:
    pack = result.get("normalized_pack") if isinstance(result.get("normalized_pack"), Mapping) else {}
    return {
        "schema_version": "research_evidence_pack_report_v1",
        "reporting_helper_uid": REPORTING_HELPER_UID,
        "artifact_kind": "evidence_pack",
        "artifact_ref": artifact_ref,
        "validation_status": "ok" if result.get("ok") else "failed",
        "ok": bool(result.get("ok")),
        "pack_id": _text(pack.get("pack_id")),
        "pack_schema_version": _text(pack.get("schema_version")),
        "pack_version": _text(pack.get("pack_version")),
        "computed_fingerprint": _text(result.get("computed_fingerprint")),
        "source_ids": [_text(row.get("source_id")) for row in _rows(pack.get("sources")) if _text(row.get("source_id"))],
        "claim_ids": [_text(row.get("claim_id")) for row in _rows(pack.get("claims")) if _text(row.get("claim_id"))],
        "claim_statuses": {_text(row.get("claim_id")): _text(row.get("status")) for row in _rows(pack.get("claims")) if _text(row.get("claim_id"))},
        "claim_source_link_ids": [_text(row.get("link_id")) for row in _rows(pack.get("claim_source_links")) if _text(row.get("link_id"))],
        "contradiction_ids": [_text(row.get("contradiction_id")) for row in _rows(pack.get("contradictions")) if _text(row.get("contradiction_id"))],
        "contradiction_statuses": {
            _text(row.get("contradiction_id")): _text(row.get("status")) for row in _rows(pack.get("contradictions")) if _text(row.get("contradiction_id"))
        },
        "weak_claim_ids": [_text(row.get("claim_id")) for row in _rows(pack.get("claims")) if _text(row.get("status")) == "weak_evidence"],
        "contested_claim_ids": [_text(row.get("claim_id")) for row in _rows(pack.get("claims")) if _text(row.get("status")) == "contested"],
        "unresolved_contradiction_ids": [
            _text(row.get("contradiction_id")) for row in _rows(pack.get("contradictions")) if _text(row.get("status")) == "unresolved"
        ],
        "error_count": len(result.get("errors") or []),
        "warning_count": len(result.get("warnings") or []),
        "errors": _bounded_errors(result),
    }


def build_evidence_pack_report(raw_pack: Mapping[str, Any], *, artifact_ref: str = "") -> Dict[str, Any]:
    result = pack_contract.validate_research_evidence_pack(copy.deepcopy(dict(raw_pack or {})))
    return _pack_report_from_result(result, artifact_ref=artifact_ref)


def build_compendium_report(
    raw_compendium: Mapping[str, Any],
    *,
    evidence_packs: Sequence[Mapping[str, Any]] | None = None,
    artifact_ref: str = "",
) -> Dict[str, Any]:
    result = compendium_contract.validate_literature_compendium(
        copy.deepcopy(dict(raw_compendium or {})),
        evidence_packs=[copy.deepcopy(dict(pack)) for pack in evidence_packs or ()],
    )
    compendium = result.get("normalized_compendium") if isinstance(result.get("normalized_compendium"), Mapping) else {}
    entries = _rows(compendium.get("entries"))
    conflicts = _rows(compendium.get("unresolved_conflicts"))

    def summarize_refs(raw_refs: Any) -> Dict[str, List[str]]:
        pack_ids: List[str] = []
        claim_ids: List[str] = []
        source_ids: List[str] = []
        contradiction_ids: List[str] = []
        for ref in _rows(raw_refs):
            pack_id = _text(ref.get("pack_id"))
            if pack_id and pack_id not in pack_ids:
                pack_ids.append(pack_id)
            for claim_id in _text_list(ref.get("claim_ids")):
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
            for source_id in _text_list(ref.get("source_ids")):
                if source_id not in source_ids:
                    source_ids.append(source_id)
            for contradiction_id in _text_list(ref.get("contradiction_ids")):
                if contradiction_id not in contradiction_ids:
                    contradiction_ids.append(contradiction_id)
        return {
            "pack_ids": pack_ids,
            "claim_ids": claim_ids,
            "source_ids": source_ids,
            "contradiction_ids": contradiction_ids,
        }

    return {
        "schema_version": "literature_compendium_report_v1",
        "reporting_helper_uid": REPORTING_HELPER_UID,
        "artifact_kind": "literature_compendium",
        "artifact_ref": artifact_ref,
        "validation_status": "ok" if result.get("ok") else "failed",
        "ok": bool(result.get("ok")),
        "compendium_id": _text(compendium.get("compendium_id")),
        "compendium_schema_version": _text(compendium.get("schema_version")),
        "compendium_version": _text(compendium.get("compendium_version")),
        "computed_fingerprint": _text(result.get("computed_fingerprint")),
        "evidence_pack_ids": [_text(row.get("pack_id")) for row in _rows(compendium.get("evidence_pack_refs")) if _text(row.get("pack_id"))],
        "entry_ids": [_text(row.get("entry_id")) for row in entries if _text(row.get("entry_id"))],
        "entry_refs": {_text(row.get("entry_id")): summarize_refs(row.get("evidence_refs")) for row in entries if _text(row.get("entry_id"))},
        "unresolved_conflict_ids": [_text(row.get("conflict_id")) for row in conflicts if _text(row.get("conflict_id"))],
        "unresolved_conflict_refs": {_text(row.get("conflict_id")): summarize_refs(row.get("evidence_refs")) for row in conflicts if _text(row.get("conflict_id"))},
        "error_count": len(result.get("errors") or []),
        "warning_count": len(result.get("warnings") or []),
        "errors": _bounded_errors(result),
    }


def load_evidence_pack_report(path: Path | str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    resolved = _resolve_repo_path(path, repo_root=root)
    return build_evidence_pack_report(_read_json_object(resolved), artifact_ref=_relative_artifact_ref(resolved, repo_root=root))


def load_compendium_report(path: Path | str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    resolved = _resolve_repo_path(path, repo_root=root)
    raw = _read_json_object(resolved)
    packs: List[Dict[str, Any]] = []
    link_errors: List[Dict[str, str]] = []
    normalized = compendium_contract.normalize_literature_compendium(copy.deepcopy(raw))
    for index, pack_ref in enumerate(_rows(normalized.get("evidence_pack_refs"))):
        artifact_ref = _text(pack_ref.get("pack_artifact_ref"))
        if not artifact_ref:
            continue
        try:
            packs.append(_read_json_object(_resolve_repo_path(artifact_ref, repo_root=root)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            link_errors.append(
                {
                    "field": f"evidence_pack_refs[{index}].pack_artifact_ref",
                    "code": "unreadable_evidence_pack_ref",
                    "message": f"referenced evidence pack could not be loaded: {exc}",
                }
            )
    report = build_compendium_report(raw, evidence_packs=packs, artifact_ref=_relative_artifact_ref(resolved, repo_root=root))
    if link_errors:
        report = dict(report)
        report["ok"] = False
        report["validation_status"] = "failed"
        report["errors"] = [*list(report.get("errors") or []), *link_errors][:20]
        report["error_count"] = len(report["errors"])
    return report


def list_research_evidence_artifacts(
    *,
    repo_root: Path | None = None,
    artifact_refs: Sequence[Path | str] | None = None,
) -> List[Dict[str, Any]]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    refs: Sequence[Path | str]
    if artifact_refs is None:
        refs = (
            pack_contract.REFERENCE_PACK_REL,
            compendium_contract.REFERENCE_COMPENDIUM_REL,
        )
    else:
        refs = artifact_refs

    reports: List[Dict[str, Any]] = []
    for ref in refs:
        resolved = _resolve_repo_path(ref, repo_root=root)
        raw = _read_json_object(resolved)
        schema = _text(raw.get("schema_version"))
        if schema == pack_contract.PACK_SCHEMA_VERSION:
            reports.append(build_evidence_pack_report(raw, artifact_ref=_relative_artifact_ref(resolved, repo_root=root)))
        elif schema == compendium_contract.COMPENDIUM_SCHEMA_VERSION:
            reports.append(load_compendium_report(resolved, repo_root=root))
        else:
            reports.append(
                {
                    "schema_version": "research_evidence_artifact_report_v1",
                    "reporting_helper_uid": REPORTING_HELPER_UID,
                    "artifact_kind": "unknown",
                    "artifact_ref": _relative_artifact_ref(resolved, repo_root=root),
                    "validation_status": "failed",
                    "ok": False,
                    "errors": [{"field": "schema_version", "code": "unknown_schema", "message": "artifact is not an evidence pack or compendium"}],
                }
            )
    return reports


def diff_evidence_packs(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    before_pack = pack_contract.normalize_research_evidence_pack(copy.deepcopy(dict(before or {})))
    after_pack = pack_contract.normalize_research_evidence_pack(copy.deepcopy(dict(after or {})))
    source_diff = _id_diff(_index_by_id(_rows(before_pack.get("sources")), "source_id"), _index_by_id(_rows(after_pack.get("sources")), "source_id"))
    claim_diff = _id_diff(_index_by_id(_rows(before_pack.get("claims")), "claim_id"), _index_by_id(_rows(after_pack.get("claims")), "claim_id"))
    link_diff = _id_diff(
        _index_by_id(_rows(before_pack.get("claim_source_links")), "link_id"),
        _index_by_id(_rows(after_pack.get("claim_source_links")), "link_id"),
    )
    contradiction_diff = _id_diff(
        _index_by_id(_rows(before_pack.get("contradictions")), "contradiction_id"),
        _index_by_id(_rows(after_pack.get("contradictions")), "contradiction_id"),
    )
    return {
        "schema_version": "research_evidence_pack_diff_v1",
        "reporting_helper_uid": REPORTING_HELPER_UID,
        "artifact_kind": "evidence_pack_diff",
        "pack_ids": {"before": _text(before_pack.get("pack_id")), "after": _text(after_pack.get("pack_id"))},
        "source_diff": source_diff,
        "claim_diff": claim_diff,
        "claim_source_link_diff": link_diff,
        "contradiction_diff": contradiction_diff,
        "changed_sources": source_diff["changed"],
        "changed_claims": claim_diff["changed"],
        "changed_claim_source_links": link_diff["changed"],
        "changed_contradictions": contradiction_diff["changed"],
    }


def diff_literature_compendia(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    before_compendium = compendium_contract.normalize_literature_compendium(copy.deepcopy(dict(before or {})))
    after_compendium = compendium_contract.normalize_literature_compendium(copy.deepcopy(dict(after or {})))
    pack_ref_diff = _id_diff(
        _index_by_id(_rows(before_compendium.get("evidence_pack_refs")), "pack_id"),
        _index_by_id(_rows(after_compendium.get("evidence_pack_refs")), "pack_id"),
    )
    entry_diff = _id_diff(_index_by_id(_rows(before_compendium.get("entries")), "entry_id"), _index_by_id(_rows(after_compendium.get("entries")), "entry_id"))
    conflict_diff = _id_diff(
        _index_by_id(_rows(before_compendium.get("unresolved_conflicts")), "conflict_id"),
        _index_by_id(_rows(after_compendium.get("unresolved_conflicts")), "conflict_id"),
    )
    return {
        "schema_version": "literature_compendium_diff_v1",
        "reporting_helper_uid": REPORTING_HELPER_UID,
        "artifact_kind": "literature_compendium_diff",
        "compendium_ids": {"before": _text(before_compendium.get("compendium_id")), "after": _text(after_compendium.get("compendium_id"))},
        "evidence_pack_ref_diff": pack_ref_diff,
        "entry_diff": entry_diff,
        "unresolved_conflict_diff": conflict_diff,
        "changed_evidence_pack_refs": pack_ref_diff["changed"],
        "changed_entries": entry_diff["changed"],
        "changed_unresolved_conflicts": conflict_diff["changed"],
    }


def render_operator_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"artifact_kind: {_text(report.get('artifact_kind'))}",
        f"validation_status: {_text(report.get('validation_status'))}",
    ]
    if _text(report.get("pack_id")):
        lines.append(f"pack_id: {_text(report.get('pack_id'))}")
    if _text(report.get("compendium_id")):
        lines.append(f"compendium_id: {_text(report.get('compendium_id'))}")
    if report.get("source_ids"):
        lines.append("source_ids: " + ", ".join(_text_list(report.get("source_ids"))))
    if report.get("claim_statuses"):
        lines.append("claims: " + ", ".join(f"{claim_id}={status}" for claim_id, status in dict(report.get("claim_statuses") or {}).items()))
    if report.get("contradiction_statuses"):
        lines.append(
            "contradictions: " + ", ".join(f"{contradiction_id}={status}" for contradiction_id, status in dict(report.get("contradiction_statuses") or {}).items())
        )
    if report.get("unresolved_conflict_ids"):
        lines.append("unresolved_conflicts: " + ", ".join(_text_list(report.get("unresolved_conflict_ids"))))
    if report.get("errors"):
        error_bits = []
        for error in list(report.get("errors") or [])[:5]:
            if isinstance(error, Mapping):
                error_bits.append(f"{_text(error.get('field'))}:{_text(error.get('code'))}")
        if error_bits:
            lines.append("errors: " + ", ".join(error_bits))
    return "\n".join(lines) + "\n"
