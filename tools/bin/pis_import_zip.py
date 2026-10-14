#!/usr/bin/env python3
"""Import a zip bundle of Patch Instruction Sheets into the ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_storage
from infra.webui.ledger.ledger_manager import ChangeLedger

_SOURCE_UID_RE = re.compile(r"^(PIS-\d{8}-\d{6}Z-[A-Z0-9]{4})(?:-.+)?$")


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
        if name.endswith("/pis_manifest.json"):
            return name
    raise FileNotFoundError("pis_manifest.json not found in zip")


def _canonical_uid(source_uid: str) -> str:
    text = str(source_uid or "").strip()
    m = _SOURCE_UID_RE.match(text)
    if not m:
        raise ValueError(f"invalid source uid: {text}")
    return str(m.group(1))


def _tag_list(entry: Dict[str, Any], *, bundle_revision: str) -> List[str]:
    raw = [
        "bundle_import",
        "pis_bundle_import",
        str(entry.get("family") or ""),
        str(entry.get("pis_kind") or ""),
        str(entry.get("claim_strength_target") or ""),
        str(bundle_revision or ""),
    ]
    out: List[str] = []
    for value in raw:
        token = ledger_storage.sanitize_slug(str(value or "")).replace("-", "_")
        if not token or token in out:
            continue
        out.append(token)
        if len(out) >= 12:
            break
    return out


def _replace_uid_refs(text: str, uid_map: Dict[str, str]) -> str:
    out = str(text or "")
    for source_uid, canonical_uid in sorted(uid_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(source_uid, canonical_uid)
    return out


def _already_imported(ledger: ChangeLedger, uid: str) -> bool:
    try:
        ledger.get_item_dir(uid)
        return True
    except Exception:
        return False


def import_bundle(
    *,
    repo_root: Path,
    zip_path: Path,
    project_id: str = "causyl",
    workstream_type: str = "shared_capability",
    scope_boundary: str = "cross-project",
    dry_run: bool = False,
) -> Dict[str, Any]:
    ledger = ChangeLedger(repo_root=repo_root)
    zip_abs = Path(zip_path).resolve()
    zip_sha = _sha256_file(zip_abs)
    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    with zipfile.ZipFile(zip_abs) as zf:
        manifest_name = _find_manifest_name(zf)
        manifest = json.loads(zf.read(manifest_name).decode("utf-8", errors="replace"))
        items = [item for item in list(manifest.get("items") or []) if isinstance(item, dict)]
        bundle_revision = str(manifest.get("bundle_revision") or "").strip()
        base = Path(manifest_name).parent.as_posix()

        uid_map = {
            str(item.get("uid") or "").strip(): _canonical_uid(str(item.get("uid") or "").strip())
            for item in items
            if str(item.get("uid") or "").strip()
        }

        for sequence_index, item in enumerate(items, start=1):
            source_uid = str(item.get("uid") or "").strip()
            canonical_uid = uid_map[source_uid]
            filename = str(item.get("filename") or "").strip()
            if not filename:
                raise ValueError(f"manifest item missing filename: {source_uid}")
            member = f"{base}/{filename}"
            title = str(item.get("title") or canonical_uid).strip() or canonical_uid
            slug = filename.split("__", 2)[-1].rsplit(".", 1)[0] if "__" in filename else title
            tags = _tag_list(item, bundle_revision=bundle_revision)
            parent_uid = uid_map.get(str(item.get("parent_uid") or "").strip())
            depends_on = [uid_map.get(str(dep or "").strip(), str(dep or "").strip()) for dep in list(item.get("depends_on") or [])]
            structured_dependencies = []
            for dep in list(item.get("structured_dependencies") or []):
                if not isinstance(dep, dict):
                    continue
                norm = dict(dep)
                norm_uid = uid_map.get(str(norm.get("uid") or "").strip(), str(norm.get("uid") or "").strip())
                if norm_uid:
                    norm["uid"] = norm_uid
                structured_dependencies.append(norm)

            if _already_imported(ledger, canonical_uid):
                skipped.append({"uid": canonical_uid, "member": member, "reason": "already_imported"})
                continue

            markdown = _replace_uid_refs(zf.read(member).decode("utf-8", errors="replace"), uid_map)

            if dry_run:
                imported.append(
                    {
                        "uid": canonical_uid,
                        "source_uid": source_uid,
                        "title": title,
                        "member": member,
                        "sequence_index": sequence_index,
                        "execution_priority": sequence_index,
                    }
                )
                continue

            created = ledger.create_item(
                title=title,
                slug=slug,
                tags=tags,
                pis_markdown=markdown,
                uid_override=canonical_uid,
                project_id=project_id,
                workstream_type=workstream_type,
                scope_boundary=scope_boundary,
                artifact_roots=[],
            )
            item_dir = ledger.get_item_dir(canonical_uid)
            meta = ledger_storage.read_json(item_dir / "meta.json") or {}
            meta["execution_priority"] = sequence_index
            meta["parent_uid"] = parent_uid
            meta["integration_meta"] = {
                "import_strategy": "zip_bundle_import",
                "source_zip": str(zip_abs),
                "source_zip_sha256": zip_sha,
                "source_manifest": manifest_name,
                "source_member": member,
                "bundle_revision": bundle_revision,
                "source_uid": source_uid,
                "sequence_index": sequence_index,
                "bundle_item_filename": filename,
                "family": str(item.get("family") or ""),
                "pis_kind": str(item.get("pis_kind") or ""),
                "claim_strength_target": str(item.get("claim_strength_target") or ""),
                "proof_mode": str(item.get("proof_mode") or ""),
                "depends_on": depends_on,
                "structured_dependencies": structured_dependencies,
            }
            meta["evidence"] = {
                "source_bundle": str(zip_abs),
                "source_import": str((item_dir / "artifacts" / "source_import" / Path(filename).name).relative_to(repo_root)),
                "triage_reason": "uploaded_pis_bundle_import",
                "triage_status": "active_bundle_import",
                "triaged_utc": str(meta.get("updated_utc") or meta.get("created_utc") or ""),
            }
            ledger_storage.atomic_write_json(item_dir / "meta.json", meta)
            artifacts = item_dir / "artifacts"
            source_import_dir = artifacts / "source_import"
            source_import_dir.mkdir(parents=True, exist_ok=True)
            ledger_storage.atomic_write_text(source_import_dir / Path(filename).name, markdown)
            ledger_storage.atomic_write_json(
                artifacts / "import_record.json",
                {
                    "source_zip": str(zip_abs),
                    "source_zip_sha256": zip_sha,
                    "source_manifest": manifest_name,
                    "source_member": member,
                    "bundle_revision": bundle_revision,
                    "manifest_entry": item,
                    "canonical_uid": canonical_uid,
                },
            )
            ledger._append_event(  # noqa: SLF001
                item_dir,
                event_type="meta_updated",
                payload={
                    "execution_priority": sequence_index,
                    "integration_meta": meta["integration_meta"],
                    "evidence": meta["evidence"],
                    "parent_uid": parent_uid,
                },
            )
            imported.append(
                {
                    "uid": canonical_uid,
                    "title": title,
                    "path": str(item_dir.relative_to(repo_root)).replace("\\", "/"),
                    "sequence_index": sequence_index,
                }
            )

    ledger_storage.write_uid_registry(ledger.items_root)
    return {
        "ok": True,
        "zip_path": str(zip_abs),
        "source_zip_sha256": zip_sha,
        "imported": imported,
        "skipped": skipped,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Import a zip bundle of PIS items into the ledger.")
    ap.add_argument("zip_path", help="Path to the PIS bundle zip.")
    ap.add_argument("--project-id", default="causyl")
    ap.add_argument("--workstream-type", default="shared_capability")
    ap.add_argument("--scope-boundary", default="cross-project")
    ap.add_argument("--dry-run", action="store_true")
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
    print(f"PIS bundle {mode} complete: imported={len(imported)} skipped={len(skipped)}")
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
