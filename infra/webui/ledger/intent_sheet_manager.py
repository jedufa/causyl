"""Filesystem-backed ledger manager for Intent Sheets.

Intent Sheets are durable planning/reference documents that can derive many PIS
items and remain the validation anchor for later implementation work.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ledger_schema
from . import ledger_storage
from .ledger_manager import ChangeLedger

INTENT_SHEET_SCHEMA_VERSION = "intent_sheet_v1"
INTENT_SHEET_EVENT_SCHEMA_VERSION = "intent_sheet_event_v1"

_INTENT_UUID_NAMESPACE = uuid.UUID("54af1c47-5039-44fb-b0fb-2805732d0f63")
_INTENT_UID_COUNTER_LOCK = threading.Lock()
_INTENT_UID_COUNTER = 0
_INTENT_UID_RE = ledger_storage.re.compile(r"^IS-\d{8}-\d{6}Z-[A-Z0-9]{4}$")
_ACTIVE_BUCKET_STATUSES = {"draft", "active", "in_progress", "blocked"}
_DEFERRED_BUCKET_STATUSES = {"deferred"}
_VERIFIED_BUCKET_STATUSES = {"verified", "done", "completed"}
_DEPRECATED_BUCKET_STATUSES = {"deprecated", "superseded"}
_DECOMPOSITION_CLASSES = {"law", "seam", "registry", "workflow", "subsystem", "program"}


def now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_uid_counter() -> int:
    global _INTENT_UID_COUNTER
    with _INTENT_UID_COUNTER_LOCK:
        _INTENT_UID_COUNTER += 1
        return int(_INTENT_UID_COUNTER)


def _det_uuid5(tag: str) -> str:
    return str(uuid.uuid5(_INTENT_UUID_NAMESPACE, str(tag)))


def new_uid() -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    h = _det_uuid5(f"is|{ts}|{_next_uid_counter()}").replace("-", "").upper()
    suf = "".join([c for c in h if c.isalnum()])[:4]
    if len(suf) < 4:
        suf = (suf + "0000")[:4]
    return f"IS-{ts}-{suf}"


def validate_uid(uid: str) -> str:
    text = str(uid or "").strip()
    if not _INTENT_UID_RE.fullmatch(text):
        raise ValueError(f"invalid intent sheet uid: {text}")
    return text


def intent_sheets_root(repo_root: Optional[Path] = None) -> Path:
    rr = Path(repo_root or ledger_storage.repo_root_from_here()).resolve()
    root = (rr / "governance" / "ledger" / "intent_sheets").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _classify_bucket(status: str) -> str:
    st = str(status or "").strip().lower()
    if st in _DEFERRED_BUCKET_STATUSES:
        return "deferred"
    if st in _VERIFIED_BUCKET_STATUSES:
        return "verified"
    if st in _DEPRECATED_BUCKET_STATUSES:
        return "deprecated"
    return "active"


def _year_month(uid: str, created_utc: str = "") -> tuple[str, str]:
    text = str(created_utc or "").strip()
    if text:
        try:
            dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.strftime("%Y"), dt.strftime("%m")
        except Exception:
            pass
    u = validate_uid(uid)
    return u[3:7], u[7:9]


def _sheet_dir(root: Path, *, uid: str, slug: str, project_id: str, status: str, created_utc: str) -> Path:
    project = ledger_schema.normalize_project_id(project_id)
    year, month = _year_month(uid, created_utc)
    return (root / project / _classify_bucket(status) / year / month / f"{validate_uid(uid)}__{ledger_storage.sanitize_slug(slug)}").resolve()


def _looks_like_sheet_dir(path: Path) -> bool:
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        meta = path / "meta.json"
        doc = path / "intent_sheet.md"
        return meta.is_file() and not meta.is_symlink() and doc.is_file() and not doc.is_symlink()
    except Exception:
        return False


def _list_sheet_dirs(root: Path) -> List[Path]:
    out: List[Path] = []
    if not root.exists() or not root.is_dir():
        return out
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            children = sorted(cur.iterdir(), key=lambda x: x.name, reverse=True)
        except Exception:
            continue
        for child in children:
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
            except Exception:
                continue
            if _looks_like_sheet_dir(child):
                out.append(child.resolve())
                continue
            stack.append(child)
    return sorted(out, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))


def _sheet_dir_for_uid(root: Path, uid: str) -> Optional[Path]:
    u = validate_uid(uid)
    matches = [p for p in _list_sheet_dirs(root) if p.name.startswith(u + "__")]
    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_uid_list(value: Any, *, prefix: str) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text:
            continue
        if not text.startswith(prefix):
            continue
        if text in out:
            continue
        out.append(text)
    return out


def _excerpt(text: str, *, max_chars: int = 180) -> str:
    s = " ".join(str(text or "").split())
    if len(s) > int(max_chars):
        return s[: max(0, int(max_chars) - 1)] + "…"
    return s


def _normalize_decomposition_class(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in _DECOMPOSITION_CLASSES:
        raise ValueError(f"invalid decomposition_class: {text}")
    return text


def _normalize_target_range(value: Any) -> Optional[Dict[str, int]]:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("decomposition_target_range must be an object with min/max integers")
    min_raw = value.get("min")
    max_raw = value.get("max")
    if min_raw is None or max_raw is None:
        raise ValueError("decomposition_target_range requires min and max")
    try:
        min_int = int(min_raw)
        max_int = int(max_raw)
    except Exception as exc:
        raise ValueError("decomposition_target_range min/max must be integers") from exc
    if min_int < 0 or max_int < 0 or min_int > max_int:
        raise ValueError("decomposition_target_range must satisfy 0 <= min <= max")
    return {"min": min_int, "max": max_int}


def _normalize_mandatory_tail(value: Any) -> Optional[Dict[str, bool]]:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("mandatory_tail must be an object")
    return {
        "proof": bool(value.get("proof")),
        "closure": bool(value.get("closure")),
    }


def _new_meta(
    *,
    uid: str,
    slug: str,
    title: str,
    created_utc: str,
    status: str,
    tags: Optional[List[str]],
    intent_markdown: str,
    project_id: Optional[str],
    workstream_type: Optional[str],
    scope_boundary: Optional[str],
    artifact_roots: Optional[List[str]],
    derived_pis_uids: Optional[List[str]] = None,
    validating_pis_uids: Optional[List[str]] = None,
    supersedes_uid: Optional[str] = None,
    decomposition_class: Optional[str] = None,
    decomposition_target_range: Optional[Dict[str, int]] = None,
    decomposition_rationale: Optional[str] = None,
    mandatory_tail: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    return ledger_schema.with_meta_defaults(
        {
            "schema_version": INTENT_SHEET_SCHEMA_VERSION,
            "uid": str(uid),
            "slug": str(slug),
            "title": str(title),
            "status": ledger_schema.normalize_status(status),
            "created_utc": str(created_utc),
            "updated_utc": str(created_utc),
            "tags": ledger_schema.normalize_topic_tags(tags, max_tags=12),
            "topic_tags": ledger_schema.normalize_topic_tags(tags),
            "project_id": ledger_schema.normalize_project_id(project_id),
            "workstream_type": ledger_schema.normalize_workstream_type(workstream_type),
            "scope_boundary": ledger_schema.normalize_scope_boundary(scope_boundary),
            "artifact_roots": ledger_schema.normalize_artifact_roots(artifact_roots),
            "derived_pis_uids": _normalize_uid_list(derived_pis_uids, prefix="PIS-"),
            "validating_pis_uids": _normalize_uid_list(validating_pis_uids, prefix="PIS-"),
            "supersedes_uid": str(supersedes_uid).strip() if supersedes_uid else None,
            "decomposition_class": _normalize_decomposition_class(decomposition_class),
            "decomposition_target_range": _normalize_target_range(decomposition_target_range),
            "decomposition_rationale": str(decomposition_rationale or "").strip() or None,
            "mandatory_tail": _normalize_mandatory_tail(mandatory_tail),
            "intent_excerpt": _excerpt(intent_markdown),
            "links": {"artifacts": [], "exports": []},
        }
    )


def _event_line(*, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "schema_version": INTENT_SHEET_EVENT_SCHEMA_VERSION,
        "utc": now_utc_iso(),
        "type": str(event_type or "").strip() or "unknown",
        "payload": payload or {},
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return body


class IntentSheetLedger:
    def __init__(self, *, repo_root: Optional[Path] = None) -> None:
        self.repo_root = Path(repo_root or ledger_storage.repo_root_from_here()).resolve()
        self.root = intent_sheets_root(self.repo_root)

    def list_sheets(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in _list_sheet_dirs(self.root):
            meta = ledger_storage.read_json(d / "meta.json") or {}
            out.append(
                {
                    "uid": str(meta.get("uid") or ""),
                    "slug": str(meta.get("slug") or ""),
                    "title": str(meta.get("title") or d.name),
                    "status": str(meta.get("status") or "draft"),
                    "updated_utc": str(meta.get("updated_utc") or meta.get("created_utc") or ""),
                    "execution_priority": meta.get("execution_priority"),
                    "priority_tier": str(meta.get("priority_tier") or ""),
                    "recommended_wave": str(meta.get("recommended_wave") or ""),
                    "wave_label": str(meta.get("wave_label") or ""),
                    "recommended_order_in_wave": meta.get("recommended_order_in_wave"),
                    "sequence_index": meta.get("sequence_index"),
                    "critical_path": bool(meta.get("critical_path", False)),
                    "project_id": str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                    "workstream_type": str(meta.get("workstream_type") or ledger_schema.DEFAULT_WORKSTREAM_TYPE),
                    "scope_boundary": str(meta.get("scope_boundary") or ledger_schema.DEFAULT_SCOPE_BOUNDARY),
                    "derived_pis_uids": list(meta.get("derived_pis_uids") or []),
                    "validating_pis_uids": list(meta.get("validating_pis_uids") or []),
                    "decomposition_class": str(meta.get("decomposition_class") or ""),
                    "decomposition_target_range": dict(meta.get("decomposition_target_range") or {}),
                    "decomposition_rationale": str(meta.get("decomposition_rationale") or ""),
                    "mandatory_tail": dict(meta.get("mandatory_tail") or {}),
                    "intent_excerpt": str(meta.get("intent_excerpt") or ""),
                }
            )
        out.sort(key=lambda row: str(row.get("uid") or ""))
        out.sort(key=lambda row: str(row.get("updated_utc") or ""), reverse=True)
        out.sort(key=lambda row: int(row.get("sequence_index")) if str(row.get("sequence_index") or "").isdigit() else 10**9)
        out.sort(
            key=lambda row: int(row.get("recommended_order_in_wave"))
            if str(row.get("recommended_order_in_wave") or "").isdigit()
            else 10**9
        )
        out.sort(key=lambda row: (0 if row.get("critical_path") else 1))
        out.sort(key=lambda row: int(row.get("execution_priority")) if str(row.get("execution_priority") or "").isdigit() else 10**9)
        return out[: max(1, min(int(limit or 200), 2000))]

    def get_sheet_dir(self, uid: str) -> Path:
        found = _sheet_dir_for_uid(self.root, uid)
        if found is None:
            raise FileNotFoundError(uid)
        return found

    def read_sheet(self, uid: str) -> Dict[str, Any]:
        d = self.get_sheet_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        return {
            "ok": True,
            "uid": str(meta.get("uid") or uid),
            "meta": ledger_schema.with_meta_defaults(meta),
            "intent_markdown": ledger_storage.read_text(d / "intent_sheet.md"),
            "artifacts": ledger_storage.list_artifacts(d),
            "dir": d.name,
        }

    def create_sheet(
        self,
        *,
        title: str,
        intent_markdown: str,
        slug: Optional[str] = None,
        tags: Optional[List[str]] = None,
        uid_override: Optional[str] = None,
        project_id: Optional[str] = None,
        workstream_type: Optional[str] = None,
        scope_boundary: Optional[str] = None,
        artifact_roots: Optional[List[str]] = None,
        derived_pis_uids: Optional[List[str]] = None,
        validating_pis_uids: Optional[List[str]] = None,
        supersedes_uid: Optional[str] = None,
        decomposition_class: Optional[str] = None,
        decomposition_target_range: Optional[Dict[str, int]] = None,
        decomposition_rationale: Optional[str] = None,
        mandatory_tail: Optional[Dict[str, bool]] = None,
        status: str = "draft",
    ) -> Dict[str, Any]:
        uid = validate_uid(uid_override) if uid_override else new_uid()
        if _sheet_dir_for_uid(self.root, uid) is not None:
            raise FileExistsError(f"uid already exists: {uid}")
        sheet_slug = ledger_storage.sanitize_slug(slug or title)
        utc = now_utc_iso()
        meta = _new_meta(
            uid=uid,
            slug=sheet_slug,
            title=str(title or "(untitled)"),
            created_utc=utc,
            status=status,
            tags=tags,
            intent_markdown=intent_markdown,
            project_id=project_id,
            workstream_type=workstream_type,
            scope_boundary=scope_boundary,
            artifact_roots=artifact_roots,
            derived_pis_uids=derived_pis_uids,
            validating_pis_uids=validating_pis_uids,
            supersedes_uid=supersedes_uid,
            decomposition_class=decomposition_class,
            decomposition_target_range=decomposition_target_range,
            decomposition_rationale=decomposition_rationale,
            mandatory_tail=mandatory_tail,
        )
        d = _sheet_dir(
            self.root,
            uid=uid,
            slug=sheet_slug,
            project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            status=str(meta.get("status") or "draft"),
            created_utc=str(meta.get("created_utc") or utc),
        )
        d.parent.mkdir(parents=True, exist_ok=True)
        d.mkdir(parents=False, exist_ok=False)
        (d / "artifacts").mkdir(parents=True, exist_ok=True)
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        ledger_storage.atomic_write_text(d / "intent_sheet.md", intent_markdown or "")
        self._append_event(d, event_type="created", payload={"title": meta["title"], "status": meta["status"]})
        return {"ok": True, "uid": uid, "slug": sheet_slug, "dir": d.name, "created_utc": utc}

    def update_status(self, uid: str, status: str, *, note: str = "") -> Dict[str, Any]:
        d = self.get_sheet_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        normalized_status = ledger_schema.normalize_status(status)
        if normalized_status == "verified":
            validating_pis_uids = _normalize_uid_list(meta.get("validating_pis_uids"), prefix="PIS-")
            if not validating_pis_uids:
                raise ValueError("intent sheet cannot be verified without a validating PIS")
            items = ChangeLedger(repo_root=self.repo_root)
            for validating_uid in validating_pis_uids:
                item = items.read_item(validating_uid)
                item_status = str((item.get("meta") or {}).get("status") or "").strip().lower()
                if item_status != "verified":
                    raise ValueError(
                        f"intent sheet cannot be verified until validating PIS is verified: {validating_uid}"
                    )
        meta["status"] = normalized_status
        meta["updated_utc"] = now_utc_iso()
        target = _sheet_dir(
            self.root,
            uid=str(meta.get("uid") or uid),
            slug=str(meta.get("slug") or d.name.split("__", 1)[-1]),
            project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            status=str(meta.get("status") or ""),
            created_utc=str(meta.get("created_utc") or ""),
        )
        if target != d:
            target.parent.mkdir(parents=True, exist_ok=True)
            d.rename(target)
            d = target
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(d, event_type="status_changed", payload={"status": meta["status"], "note": str(note or "")})
        return {"ok": True, "uid": uid, "status": meta["status"], "updated_utc": meta["updated_utc"]}

    def update_meta(self, uid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        d = self.get_sheet_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        if "title" in patch and patch.get("title") is not None:
            meta["title"] = str(patch.get("title") or "").strip() or meta.get("title")
        if "tags" in patch:
            normalized = ledger_schema.normalize_topic_tags(patch.get("tags"), max_tags=12)
            meta["tags"] = normalized
            if "topic_tags" not in patch:
                meta["topic_tags"] = ledger_schema.normalize_topic_tags(normalized)
        if "topic_tags" in patch:
            meta["topic_tags"] = ledger_schema.normalize_topic_tags(patch.get("topic_tags"))
        if "project_id" in patch:
            meta["project_id"] = ledger_schema.normalize_project_id(patch.get("project_id"))
        if "workstream_type" in patch:
            meta["workstream_type"] = ledger_schema.normalize_workstream_type(patch.get("workstream_type"))
        if "scope_boundary" in patch:
            meta["scope_boundary"] = ledger_schema.normalize_scope_boundary(patch.get("scope_boundary"))
        if "artifact_roots" in patch:
            meta["artifact_roots"] = ledger_schema.normalize_artifact_roots(patch.get("artifact_roots"))
        if "derived_pis_uids" in patch:
            meta["derived_pis_uids"] = _normalize_uid_list(patch.get("derived_pis_uids"), prefix="PIS-")
        if "validating_pis_uids" in patch:
            meta["validating_pis_uids"] = _normalize_uid_list(patch.get("validating_pis_uids"), prefix="PIS-")
        for key in (
            "execution_priority",
            "priority_tier",
            "recommended_wave",
            "wave_label",
            "recommended_order_in_wave",
            "sequence_index",
            "critical_path",
            "naming_class",
            "intent_kind",
            "portfolio_group",
            "depends_on",
            "downstream_dependents",
            "pis_drift_risk",
            "pis_drift_note",
            "integration_meta",
            "evidence",
        ):
            if key in patch:
                meta[key] = patch.get(key)
        if "decomposition_class" in patch:
            meta["decomposition_class"] = _normalize_decomposition_class(patch.get("decomposition_class"))
        if "decomposition_target_range" in patch:
            meta["decomposition_target_range"] = _normalize_target_range(patch.get("decomposition_target_range"))
        if "decomposition_rationale" in patch:
            meta["decomposition_rationale"] = str(patch.get("decomposition_rationale") or "").strip() or None
        if "mandatory_tail" in patch:
            meta["mandatory_tail"] = _normalize_mandatory_tail(patch.get("mandatory_tail"))
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(d, event_type="meta_updated", payload={k: patch.get(k) for k in patch.keys()})
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def update_text(self, uid: str, intent_markdown: str) -> Dict[str, Any]:
        d = self.get_sheet_dir(uid)
        ledger_storage.atomic_write_text(d / "intent_sheet.md", intent_markdown or "")
        meta = ledger_storage.read_json(d / "meta.json") or {}
        meta["intent_excerpt"] = _excerpt(intent_markdown)
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(d, event_type="intent_updated", payload={"bytes": len((intent_markdown or "").encode("utf-8"))})
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def _append_event(self, d: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
        line = json.dumps(_event_line(event_type=event_type, payload=payload), sort_keys=True, ensure_ascii=False)
        path = d / "events.jsonl"
        existing = ""
        if path.exists() and path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
        ledger_storage.atomic_write_text(path, existing + line + "\n")
