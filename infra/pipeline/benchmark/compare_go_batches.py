from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


UID_PATTERN = re.compile(r"pis-(\d{8}-\d{6}z-[a-z0-9]{4})", re.IGNORECASE)


@dataclass
class CaseSummary:
    case_id: str
    title: str
    status: str
    selected_files: List[str]
    implementation_targets: List[str]
    selected_modify_files: List[str]
    source_path: str


def _unique(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _uid_from_report_dir(name: str) -> str:
    match = UID_PATTERN.search(str(name or ""))
    if not match:
        return ""
    return f"PIS-{match.group(1).upper()}"


def _case_id_from_report_dir(name: str) -> str:
    inferred_uid = _uid_from_report_dir(name)
    if inferred_uid:
        return inferred_uid
    return str(name or "").strip()


def _summary_to_case(summary: Mapping[str, Any], *, case_id: str, title: str, source_path: str) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        title=str(title or "").strip(),
        status=str(summary.get("status") or "").strip(),
        selected_files=_unique(summary.get("selected_files") or []),
        implementation_targets=_unique(summary.get("implementation_targets") or []),
        selected_modify_files=_unique(summary.get("selected_modify_files") or []),
        source_path=source_path,
    )


def _load_batch_dir(batch_dir: Path) -> Dict[str, CaseSummary]:
    report_root = batch_dir / "reports"
    cases: Dict[str, CaseSummary] = {}
    manifest_path = batch_dir / "batch_manifest.json"
    title_by_case: Dict[str, str] = {}
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        for entry in list(manifest.get("entries") or []):
            case_id = str(entry.get("pis_uid") or entry.get("item_id") or "").strip()
            if case_id:
                title_by_case[case_id] = str(entry.get("title") or "").strip()

    for summary_path in sorted(report_root.glob("*/deterministic/summary.json")):
        report_dir = summary_path.parent.parent
        case_id = _case_id_from_report_dir(report_dir.name)
        summary = _read_json(summary_path)
        cases[case_id] = _summary_to_case(
            summary,
            case_id=case_id,
            title=title_by_case.get(case_id, ""),
            source_path=str(summary_path),
        )
    return cases


def _load_batch_zip(batch_zip: Path) -> Dict[str, CaseSummary]:
    cases: Dict[str, CaseSummary] = {}
    with zipfile.ZipFile(batch_zip) as zf:
        names = zf.namelist()
        manifest_name = next((name for name in names if name.endswith("/batch_manifest.json")), "")
        title_by_case: Dict[str, str] = {}
        if manifest_name:
            manifest = json.loads(zf.read(manifest_name))
            for entry in list(manifest.get("entries") or []):
                case_id = str(entry.get("pis_uid") or entry.get("item_id") or "").strip()
                if case_id:
                    title_by_case[case_id] = str(entry.get("title") or "").strip()
        for name in names:
            if not name.endswith("/deterministic/summary.json"):
                continue
            report_dir = Path(name).parent.parent
            case_id = _case_id_from_report_dir(report_dir.name)
            summary = json.loads(zf.read(name))
            cases[case_id] = _summary_to_case(
                summary,
                case_id=case_id,
                title=title_by_case.get(case_id, ""),
                source_path=name,
            )
    return cases


def load_batch(source: Path) -> Dict[str, CaseSummary]:
    resolved = source.resolve()
    if resolved.is_dir():
        return _load_batch_dir(resolved)
    if resolved.is_file() and resolved.suffix.lower() == ".zip":
        return _load_batch_zip(resolved)
    raise FileNotFoundError(f"Unsupported batch source: {resolved}")


def _set_diff(old: List[str], new: List[str]) -> Dict[str, List[str]]:
    old_set = set(old)
    new_set = set(new)
    return {
        "added": sorted(new_set - old_set),
        "removed": sorted(old_set - new_set),
        "common": sorted(old_set & new_set),
    }


def compare_batches(old_cases: Mapping[str, CaseSummary], new_cases: Mapping[str, CaseSummary]) -> Dict[str, Any]:
    old_uids = set(old_cases)
    new_uids = set(new_cases)
    shared = sorted(old_uids & new_uids)
    only_old = sorted(old_uids - new_uids)
    only_new = sorted(new_uids - old_uids)

    changed_rows: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    counts = {
        "changed_selected_files": 0,
        "changed_implementation_targets": 0,
        "changed_modify_files": 0,
        "changed_any": 0,
    }

    for uid in shared:
        old_case = old_cases[uid]
        new_case = new_cases[uid]
        selected = _set_diff(old_case.selected_files, new_case.selected_files)
        targets = _set_diff(old_case.implementation_targets, new_case.implementation_targets)
        modify = _set_diff(old_case.selected_modify_files, new_case.selected_modify_files)
        changed_selected = bool(selected["added"] or selected["removed"])
        changed_targets = bool(targets["added"] or targets["removed"])
        changed_modify = bool(modify["added"] or modify["removed"])
        changed_any = changed_selected or changed_targets or changed_modify
        if changed_selected:
            counts["changed_selected_files"] += 1
        if changed_targets:
            counts["changed_implementation_targets"] += 1
        if changed_modify:
            counts["changed_modify_files"] += 1
        if changed_any:
            counts["changed_any"] += 1
            changed_rows.append(
                {
                    "case_id": uid,
                    "title": new_case.title or old_case.title,
                    "old_status": old_case.status,
                    "new_status": new_case.status,
                    "selected_files": selected,
                    "implementation_targets": targets,
                    "selected_modify_files": modify,
                    "selected_files_old": old_case.selected_files,
                    "selected_files_new": new_case.selected_files,
                    "implementation_targets_old": old_case.implementation_targets,
                    "implementation_targets_new": new_case.implementation_targets,
                    "selected_modify_files_old": old_case.selected_modify_files,
                    "selected_modify_files_new": new_case.selected_modify_files,
                    "old_source_path": old_case.source_path,
                    "new_source_path": new_case.source_path,
                }
            )
        else:
            unchanged.append(uid)

    changed_rows.sort(key=lambda row: row["case_id"])
    unchanged.sort()

    return {
        "summary": {
            "old_case_count": len(old_cases),
            "new_case_count": len(new_cases),
            "shared_case_count": len(shared),
            "old_only_case_count": len(only_old),
            "new_only_case_count": len(only_new),
            **counts,
            "unchanged_case_count": len(unchanged),
        },
        "old_only_cases": only_old,
        "new_only_cases": only_new,
        "unchanged_cases": unchanged,
        "changed_cases": changed_rows,
    }


def render_markdown(report: Mapping[str, Any], *, old_label: str, new_label: str) -> str:
    summary = dict(report.get("summary") or {})
    changed_cases = list(report.get("changed_cases") or [])
    old_only_cases = list(report.get("old_only_cases") or [])
    new_only_cases = list(report.get("new_only_cases") or [])

    lines = [
        "# Batch Comparison Report",
        "",
        f"- Old: `{old_label}`",
        f"- New: `{new_label}`",
        f"- Shared cases: `{summary.get('shared_case_count', 0)}`",
        f"- Changed any: `{summary.get('changed_any', 0)}`",
        f"- Changed selected files: `{summary.get('changed_selected_files', 0)}`",
        f"- Changed implementation targets: `{summary.get('changed_implementation_targets', 0)}`",
        f"- Changed modify files: `{summary.get('changed_modify_files', 0)}`",
        f"- Unchanged: `{summary.get('unchanged_case_count', 0)}`",
        f"- Old only: `{summary.get('old_only_case_count', 0)}`",
        f"- New only: `{summary.get('new_only_case_count', 0)}`",
        "",
        "## Changed Cases",
        "",
        "| Case | Selected | Targets | Modify |",
        "|---|---:|---:|---:|",
    ]
    for row in changed_cases:
        sel = dict(row.get("selected_files") or {})
        tgt = dict(row.get("implementation_targets") or {})
        mod = dict(row.get("selected_modify_files") or {})
        lines.append(
            f"| `{row.get('case_id')}` | `+{len(sel.get('added') or [])}/-{len(sel.get('removed') or [])}` | `+{len(tgt.get('added') or [])}/-{len(tgt.get('removed') or [])}` | `+{len(mod.get('added') or [])}/-{len(mod.get('removed') or [])}` |"
        )

    if old_only_cases:
        lines.extend(["", "## Old Only Cases", ""])
        for uid in old_only_cases:
            lines.append(f"- `{uid}`")
    if new_only_cases:
        lines.extend(["", "## New Only Cases", ""])
        for uid in new_only_cases:
            lines.append(f"- `{uid}`")

    if changed_cases:
        lines.extend(["", "## Detailed Diffs", ""])
        for row in changed_cases:
            lines.extend(
                [
                    f"### {row.get('case_id')}",
                    f"- Title: `{row.get('title') or ''}`",
                    f"- Old status: `{row.get('old_status')}`",
                    f"- New status: `{row.get('new_status')}`",
                ]
            )
            for key, label in [
                ("selected_files", "Selected files"),
                ("implementation_targets", "Implementation targets"),
                ("selected_modify_files", "Modify files"),
            ]:
                diff = dict(row.get(key) or {})
                lines.append(f"- {label} added: `{diff.get('added') or []}`")
                lines.append(f"- {label} removed: `{diff.get('removed') or []}`")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two go batch outputs by selected files, implementation targets, and modify files.")
    parser.add_argument("old_batch")
    parser.add_argument("new_batch")
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    args = parser.parse_args(argv)

    old_source = Path(args.old_batch)
    new_source = Path(args.new_batch)
    old_cases = load_batch(old_source)
    new_cases = load_batch(new_source)
    report = {
        "old_source": str(old_source.resolve()),
        "new_source": str(new_source.resolve()),
        "comparison": compare_batches(old_cases, new_cases),
    }

    out_json = Path(args.out_json) if args.out_json else None
    out_md = Path(args.out_md) if args.out_md else None
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if out_md:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(
            render_markdown(report["comparison"], old_label=str(old_source), new_label=str(new_source)),
            encoding="utf-8",
        )
    print(json.dumps(report["comparison"]["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
