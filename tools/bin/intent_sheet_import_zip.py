#!/usr/bin/env python3
"""Import an Intent Sheet zip bundle into the ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_schema, ledger_storage
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


def _read_json_member(zf: zipfile.ZipFile, name: str) -> Dict[str, Any]:
    return json.loads(zf.read(name).decode("utf-8", errors="replace"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _find_manifest_name(zf: zipfile.ZipFile) -> str:
    for name in zf.namelist():
        if name.endswith("/intent_sheet_manifest.json"):
            return name
    raise FileNotFoundError("intent_sheet_manifest.json not found in zip")


def _sheet_wave_text(entry: Dict[str, Any]) -> str:
    raw = entry.get("wave")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    execution_wave = entry.get("execution_wave")
    try:
        return f"W{int(execution_wave)}"
    except Exception:
        return ""


def _sheet_member_path(entry: Dict[str, Any]) -> str:
    raw = entry.get("filename")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raw = entry.get("path")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise KeyError("manifest entry missing both 'filename' and 'path'")


def _normalize_depends_on(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = entry.get("depends_on")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            ident = str(item.get("id") or "").strip()
            if not ident:
                continue
            payload = dict(item)
            payload["id"] = ident
            payload.setdefault("kind", "hard")
            out.append(payload)
            continue
        ident = str(item or "").strip()
        if ident:
            out.append({"id": ident, "kind": "hard"})
    return out


def _tag_list_for_sheet(entry: Dict[str, Any], *, bundle_revision: str) -> List[str]:
    raw = [
        "intent_sheet",
        "bundle_import",
        str(entry.get("naming_class") or ""),
        str(entry.get("intent_kind") or ""),
        str(entry.get("portfolio_group") or ""),
        _sheet_wave_text(entry).lower(),
        str(bundle_revision or ""),
        str(entry.get("id") or "").lower(),
    ]
    return ledger_schema.normalize_topic_tags(raw, max_tags=12)


def _execution_priority_for_sheet(entry: Dict[str, Any], *, sequence_index: int, recommended_order_in_wave: int) -> int:
    wave = _sheet_wave_text(entry)
    order = int(entry.get("recommended_order_in_wave") or recommended_order_in_wave or sequence_index or 999)
    wave_num = 99
    if len(wave) >= 2 and wave[0] == "W" and wave[1:].isdigit():
        wave_num = int(wave[1:])
    return (wave_num * 100) + order


def _ordering_patch(entry: Dict[str, Any], *, sequence_index: int, recommended_order_in_wave: int) -> Dict[str, Any]:
    return {
        "execution_priority": _execution_priority_for_sheet(
            entry,
            sequence_index=sequence_index,
            recommended_order_in_wave=recommended_order_in_wave,
        ),
        "priority_tier": str(entry.get("priority") or ""),
        "recommended_wave": _sheet_wave_text(entry),
        "wave_label": str(entry.get("wave_label") or ""),
        "recommended_order_in_wave": int(entry.get("recommended_order_in_wave") or recommended_order_in_wave),
        "sequence_index": int(entry.get("sequence_index") or sequence_index),
        "critical_path": bool(entry.get("critical_path", False)),
        "naming_class": str(entry.get("naming_class") or ""),
        "intent_kind": str(entry.get("intent_kind") or ""),
        "portfolio_group": str(entry.get("portfolio_group") or ""),
        "depends_on": _normalize_depends_on(entry),
        "downstream_dependents": list(entry.get("downstream_dependents") or []),
        "pis_drift_risk": str(entry.get("pis_drift_risk") or ""),
        "pis_drift_note": str(entry.get("pis_drift_note") or ""),
    }


def _already_imported(
    ledger: IntentSheetLedger,
    *,
    source_zip_sha256: str,
    source_member: str,
) -> Optional[str]:
    for row in ledger.list_sheets(limit=5000):
        try:
            d = ledger.get_sheet_dir(str(row.get("uid") or ""))
        except Exception:
            continue
        marker = d / "artifacts" / "import_record.json"
        data = ledger_storage.read_json(marker)
        if not isinstance(data, dict):
            continue
        if str(data.get("source_zip_sha256") or "") == source_zip_sha256 and str(data.get("source_member") or "") == source_member:
            return str(row.get("uid") or "")
    return None


def import_bundle(
    *,
    repo_root: Path,
    zip_path: Path,
    project_id: str,
    workstream_type: str,
    scope_boundary: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    ledger = IntentSheetLedger(repo_root=repo_root)
    zip_abs = Path(zip_path).resolve()
    zip_sha = _sha256_file(zip_abs)
    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    with zipfile.ZipFile(zip_abs) as zf:
        manifest_name = _find_manifest_name(zf)
        manifest = _read_json_member(zf, manifest_name)
        bundle_revision = str(manifest.get("bundle_revision") or "").strip()
        wave_order: Dict[str, int] = {}
        for sequence_index, entry in enumerate(list(manifest.get("sheets") or []), start=1):
            if not isinstance(entry, dict):
                continue
            member_rel = _sheet_member_path(entry)
            member = f"{Path(manifest_name).parent.as_posix()}/{member_rel}"
            wave_key = _sheet_wave_text(entry) or "__unwaved__"
            wave_order[wave_key] = int(wave_order.get(wave_key, 0)) + 1
            recommended_order_in_wave = int(entry.get("recommended_order_in_wave") or wave_order[wave_key])
            existing = _already_imported(ledger, source_zip_sha256=zip_sha, source_member=member)
            if existing:
                if not dry_run:
                    d = ledger.get_sheet_dir(existing)
                    meta = ledger_storage.read_json(d / "meta.json") or {}
                    meta.update(
                        _ordering_patch(
                            entry,
                            sequence_index=sequence_index,
                            recommended_order_in_wave=recommended_order_in_wave,
                        )
                    )
                    integ = dict(meta.get("integration_meta") or {})
                    integ.update(
                        {
                            "import_strategy": "zip_bundle_import",
                            "source_zip": str(zip_abs),
                            "source_zip_sha256": zip_sha,
                            "source_manifest": manifest_name,
                            "source_member": member,
                            "bundle_revision": bundle_revision,
                            "sheet_id": str(entry.get("id") or ""),
                            "legacy_title": str(entry.get("legacy_title") or ""),
                            "sequence_index": int(entry.get("sequence_index") or sequence_index),
                            "wave": _sheet_wave_text(entry),
                            "wave_label": str(entry.get("wave_label") or ""),
                        }
                    )
                    meta["integration_meta"] = integ
                    ledger_storage.atomic_write_json(d / "meta.json", meta)
                skipped.append({"member": member, "reason": "already_imported", "uid": existing})
                continue

            text = zf.read(member).decode("utf-8", errors="replace")
            title = str(entry.get("title") or entry.get("legacy_title") or entry.get("filename") or "Untitled Intent Sheet").strip()
            slug = str(entry.get("slug") or title).strip()
            tags = _tag_list_for_sheet(entry, bundle_revision=bundle_revision)

            if dry_run:
                imported.append(
                    {
                        "member": member,
                        "title": title,
                        "slug": slug,
                        "tags": tags,
                        "dry_run": True,
                        "sequence_index": int(entry.get("sequence_index") or sequence_index),
                        "recommended_order_in_wave": recommended_order_in_wave,
                        "recommended_wave": _sheet_wave_text(entry),
                    }
                )
                continue

            created = ledger.create_sheet(
                title=title,
                intent_markdown=text,
                slug=slug,
                tags=tags,
                project_id=project_id,
                workstream_type=workstream_type,
                scope_boundary=scope_boundary,
                artifact_roots=[],
                status="draft",
            )
            uid = str(created["uid"])
            d = ledger.get_sheet_dir(uid)
            meta = ledger_storage.read_json(d / "meta.json") or {}
            meta.update(
                _ordering_patch(
                    entry,
                    sequence_index=sequence_index,
                    recommended_order_in_wave=recommended_order_in_wave,
                )
            )
            meta["integration_meta"] = {
                "import_strategy": "zip_bundle_import",
                "source_zip": str(zip_abs),
                "source_zip_sha256": zip_sha,
                "source_manifest": manifest_name,
                "source_member": member,
                "bundle_revision": bundle_revision,
                "sheet_id": str(entry.get("id") or ""),
                "legacy_title": str(entry.get("legacy_title") or ""),
                "sequence_index": int(entry.get("sequence_index") or sequence_index),
                "wave": _sheet_wave_text(entry),
                "wave_label": str(entry.get("wave_label") or ""),
            }
            ledger_storage.atomic_write_json(d / "meta.json", meta)
            artifacts = d / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            ledger_storage.atomic_write_json(
                artifacts / "import_record.json",
                {
                    "source_zip": str(zip_abs),
                    "source_zip_sha256": zip_sha,
                    "source_manifest": manifest_name,
                    "source_member": member,
                    "bundle_revision": bundle_revision,
                    "manifest_entry": entry,
                },
            )
            ledger._append_event(  # noqa: SLF001
                d,
                event_type="bundle_imported",
                payload={
                    "source_zip": str(zip_abs),
                    "source_member": member,
                    "sheet_id": str(entry.get("id") or ""),
                    "bundle_revision": bundle_revision,
                },
            )
            imported.append({"member": member, "uid": uid, "title": title, "path": str(d.relative_to(repo_root)).replace("\\", "/")})

    return {
        "ok": True,
        "zip_path": str(zip_abs),
        "source_zip_sha256": zip_sha,
        "imported": imported,
        "skipped": skipped,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Import a zip bundle of Intent Sheets into the ledger.")
    ap.add_argument("zip_path", help="Path to the intent-sheet bundle zip.")
    ap.add_argument("--project-id", default="causyl", help="Project id for imported sheets (default: causyl).")
    ap.add_argument(
        "--workstream-type",
        default="shared_capability",
        help="Workstream type for imported sheets (default: shared_capability).",
    )
    ap.add_argument(
        "--scope-boundary",
        default="cross-project",
        help="Scope boundary for imported sheets (default: cross-project).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Inspect bundle and planned imports without writing.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = ap.parse_args(argv)

    result = import_bundle(
        repo_root=REPO_ROOT,
        zip_path=Path(str(args.zip_path)),
        project_id=str(args.project_id),
        workstream_type=str(args.workstream_type),
        scope_boundary=str(args.scope_boundary),
        dry_run=bool(args.dry_run),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    imported = list(result.get("imported") or [])
    skipped = list(result.get("skipped") or [])
    mode = "dry-run" if bool(args.dry_run) else "import"
    print(f"Intent Sheet bundle {mode} complete: imported={len(imported)} skipped={len(skipped)}")
    for idx, row in enumerate(imported, start=1):
        uid = str(row.get("uid") or "").strip()
        title = str(row.get("title") or "").strip()
        member = str(row.get("member") or "").strip()
        print(f"{idx}. {uid} | {title} | {member}")
    if skipped:
        print("Skipped:")
        for row in skipped[:10]:
            uid = str(row.get("uid") or "").strip()
            reason = str(row.get("reason") or "").strip()
            print(f"- {uid or '(unbound)'} | {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
