"""Filesystem-backed ledger manager for lightweight Idea Notes.

Idea Notes are intentionally lower-rigor than Intent Sheets. They capture vague
ideas, speculative directions, and rough observations without requiring
implementation-ready structure.
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

IDEA_NOTE_SCHEMA_VERSION = "idea_note_v1"
IDEA_NOTE_EVENT_SCHEMA_VERSION = "idea_note_event_v1"

_IDEA_UUID_NAMESPACE = uuid.UUID("f0cf494e-0bb8-42ee-8d27-64c69a37ec44")
_IDEA_TO_INTENT_UUID_NAMESPACE = uuid.UUID("b70b4eb3-2b20-4f3d-91fd-4335bf9c7db8")
_IDEA_UID_COUNTER_LOCK = threading.Lock()
_IDEA_UID_COUNTER = 0
_IDEA_UID_RE = ledger_storage.re.compile(r"^IDEA-\d{8}-\d{6}Z-[A-Z0-9]{4}$")

_ACTIVE_BUCKET_STATUSES = {"draft", "active", "exploring"}
_DEFERRED_BUCKET_STATUSES = {"parked"}
_VERIFIED_BUCKET_STATUSES = {"promoted"}
_DEPRECATED_BUCKET_STATUSES = {"discarded"}
_ALLOWED_STATUSES = _ACTIVE_BUCKET_STATUSES | _DEFERRED_BUCKET_STATUSES | _VERIFIED_BUCKET_STATUSES | _DEPRECATED_BUCKET_STATUSES
_LIFECYCLE_ACTIONS = {"resume", "park", "discard"}
_ALLOWED_TRANSITIONS = {
    "draft": {"active", "exploring", "parked", "discarded", "promoted"},
    "active": {"exploring", "parked", "discarded", "promoted"},
    "exploring": {"active", "parked", "discarded", "promoted"},
    "parked": {"active", "exploring", "discarded", "promoted"},
    "promoted": set(),
    "discarded": set(),
}
_ALLOWED_META_PATCH_KEYS = {
    "title",
    "tags",
    "topic_tags",
    "project_id",
    "workstream_type",
    "scope_boundary",
    "artifact_roots",
    "related_intent_sheet_uids",
    "related_pis_uids",
    "promotion_target_uid",
    "confidence",
    "maturity",
    "why_now",
    "priority_signal",
    "resume_context",
    "resume_blockers",
}


def now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_uid_counter() -> int:
    global _IDEA_UID_COUNTER
    with _IDEA_UID_COUNTER_LOCK:
        _IDEA_UID_COUNTER += 1
        return int(_IDEA_UID_COUNTER)


def _det_uuid5(tag: str) -> str:
    return str(uuid.uuid5(_IDEA_UUID_NAMESPACE, str(tag)))


def new_uid() -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    h = _det_uuid5(f"idea|{ts}|{_next_uid_counter()}").replace("-", "").upper()
    suf = "".join([c for c in h if c.isalnum()])[:4]
    if len(suf) < 4:
        suf = (suf + "0000")[:4]
    return f"IDEA-{ts}-{suf}"


def validate_uid(uid: str) -> str:
    text = str(uid or "").strip()
    if not _IDEA_UID_RE.fullmatch(text):
        raise ValueError(f"invalid idea note uid: {text}")
    return text


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "in_progress": "active",
        "blocked": "active",
        "deferred": "parked",
        "verified": "promoted",
        "done": "promoted",
        "deprecated": "discarded",
    }
    text = aliases.get(text, text or "draft")
    if text not in _ALLOWED_STATUSES:
        raise ValueError(f"invalid idea note status: {text}")
    return text


def idea_notes_root(repo_root: Optional[Path] = None) -> Path:
    rr = Path(repo_root or ledger_storage.repo_root_from_here()).resolve()
    root = (rr / "governance" / "ledger" / "idea_notes").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _classify_bucket(status: str) -> str:
    st = normalize_status(status)
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
    return u[5:9], u[9:11]


def _idea_dir(root: Path, *, uid: str, slug: str, project_id: str, status: str, created_utc: str) -> Path:
    project = ledger_schema.normalize_project_id(project_id)
    year, month = _year_month(uid, created_utc)
    return (root / project / _classify_bucket(status) / year / month / f"{validate_uid(uid)}__{ledger_storage.sanitize_slug(slug)}").resolve()


def _looks_like_idea_dir(path: Path) -> bool:
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        meta = path / "meta.json"
        doc = path / "idea.md"
        return meta.is_file() and not meta.is_symlink() and doc.is_file() and not doc.is_symlink()
    except Exception:
        return False


def _list_idea_dirs(root: Path) -> List[Path]:
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
            if _looks_like_idea_dir(child):
                out.append(child.resolve())
                continue
            stack.append(child)
    return sorted(out, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))


def _idea_dir_for_uid(root: Path, uid: str) -> Optional[Path]:
    u = validate_uid(uid)
    matches = [p for p in _list_idea_dirs(root) if p.name.startswith(u + "__")]
    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_uid_list(value: Any, *, prefix: str) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text or not text.startswith(prefix) or text in out:
            continue
        out.append(text)
    return out


def _normalize_optional_text(value: Any, *, field_name: str, max_chars: int) -> Optional[str]:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    if len(text) > int(max_chars):
        raise ValueError(f"{field_name} exceeds {int(max_chars)} characters")
    return text


def _normalize_blocker_list(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if not isinstance(value, list):
        raise ValueError("resume_blockers must be a list")
    out: List[str] = []
    for raw in value:
        text = _normalize_optional_text(raw, field_name="resume_blockers entry", max_chars=160)
        if not text or text in out:
            continue
        out.append(text)
        if len(out) >= 8:
            break
    return out


def _normalize_priority_signal(value: Any) -> Optional[str]:
    return _normalize_optional_text(value, field_name="priority_signal", max_chars=64)


def _normalize_promotion_target_uid(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("IS-") or text.startswith("PIS-"):
        return text
    raise ValueError(f"invalid promotion_target_uid: {text}")


def _merge_unique_uid(targets: List[str], uid: Optional[str]) -> List[str]:
    out = list(targets or [])
    if uid and uid not in out:
        out.append(uid)
    return out


def _normalize_provenance(meta: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(meta or {})
    out["related_intent_sheet_uids"] = _normalize_uid_list(out.get("related_intent_sheet_uids"), prefix="IS-")
    out["related_pis_uids"] = _normalize_uid_list(out.get("related_pis_uids"), prefix="PIS-")
    target = _normalize_promotion_target_uid(out.get("promotion_target_uid"))
    out["promotion_target_uid"] = target
    if target and target.startswith("IS-"):
        out["related_intent_sheet_uids"] = _merge_unique_uid(list(out.get("related_intent_sheet_uids") or []), target)
    if target and target.startswith("PIS-"):
        out["related_pis_uids"] = _merge_unique_uid(list(out.get("related_pis_uids") or []), target)
    return out


def _normalize_pre_intent_fields(meta: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(meta or {})
    out["why_now"] = _normalize_optional_text(out.get("why_now"), field_name="why_now", max_chars=280)
    out["priority_signal"] = _normalize_priority_signal(out.get("priority_signal"))
    out["resume_context"] = _normalize_optional_text(out.get("resume_context"), field_name="resume_context", max_chars=280)
    out["resume_blockers"] = _normalize_blocker_list(out.get("resume_blockers"))
    return out


def _has_resume_breadcrumb(meta: Dict[str, Any]) -> bool:
    normalized = _normalize_pre_intent_fields(meta)
    if normalized.get("resume_context"):
        return True
    return bool(normalized.get("resume_blockers"))


def _validate_transition(*, current_status: str, target_status: str, meta: Dict[str, Any]) -> None:
    current = normalize_status(current_status)
    target = normalize_status(target_status)
    if current == target:
        if target == "promoted" and not _normalize_promotion_target_uid(meta.get("promotion_target_uid")):
            raise ValueError("promotion_target_uid is required before marking an idea note promoted")
        if target == "parked" and not _has_resume_breadcrumb(meta):
            raise ValueError("parked idea note requires resume_context or resume_blockers")
        return
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"invalid idea note transition: {current} -> {target}")
    if target == "promoted" and not _normalize_promotion_target_uid(meta.get("promotion_target_uid")):
        raise ValueError("promotion_target_uid is required before marking an idea note promoted")
    if target == "parked" and not _has_resume_breadcrumb(meta):
        raise ValueError("parked idea note requires resume_context or resume_blockers")
    if current == "parked" and target in {"active", "exploring"}:
        if not _normalize_optional_text(meta.get("why_now"), field_name="why_now", max_chars=280):
            raise ValueError("resuming a parked idea note requires why_now")


def _excerpt(text: str, *, max_chars: int = 180) -> str:
    s = " ".join(str(text or "").split())
    if len(s) > int(max_chars):
        return s[: max(0, int(max_chars) - 1)] + "…"
    return s


def _derived_intent_uid_for_note(uid: str) -> str:
    note_uid = validate_uid(uid)
    stem = note_uid[len("IDEA-") :]
    timestamp = stem[:16]
    suffix = uuid.uuid5(_IDEA_TO_INTENT_UUID_NAMESPACE, note_uid).hex.upper()[:4]
    return f"IS-{timestamp}-{suffix}"


def _readiness_missing_fields(meta: Dict[str, Any]) -> List[str]:
    title = str(meta.get("title") or "").strip()
    missing: List[str] = []
    if not title or title == "(untitled)":
        missing.append("title")
    if not _normalize_optional_text(meta.get("why_now"), field_name="why_now", max_chars=280):
        missing.append("why_now")
    if not _normalize_optional_text(meta.get("resume_context"), field_name="resume_context", max_chars=280):
        missing.append("resume_context")
    return missing


def _build_promoted_intent_markdown(*, intent_uid: str, note_uid: str, meta: Dict[str, Any], idea_markdown: str) -> str:
    title = str(meta.get("title") or "Untitled Intent Sheet").strip() or "Untitled Intent Sheet"
    why_now = str(meta.get("why_now") or "").strip()
    resume_context = str(meta.get("resume_context") or "").strip()
    priority_signal = str(meta.get("priority_signal") or "").strip()
    confidence = str(meta.get("confidence") or "").strip()
    maturity = str(meta.get("maturity") or "").strip()
    excerpt = _excerpt(idea_markdown, max_chars=320)
    bullets = [
        "- Promoted explicitly from an Idea Note after the minimal readiness gate passed.",
        f"- Source Idea Note: `{note_uid}`.",
        f"- Initial bounded next step: {resume_context}",
        "- The original Idea Note remains the historical pre-intent record.",
    ]
    if priority_signal:
        bullets.append(f"- Priority signal at promotion: {priority_signal}")
    if confidence:
        bullets.append(f"- Source confidence hint: {confidence}")
    if maturity:
        bullets.append(f"- Source maturity hint: {maturity}")
    bullet_block = "\n".join(bullets)
    return (
        "# Intent Sheet\n\n"
        f"**UID:** {intent_uid}\n\n"
        f"**Promoted From Idea Note:** {note_uid}\n\n"
        "## 1. Title\n\n"
        f"{title}\n\n"
        "## 2. Goal / intended outcome\n\n"
        f"{why_now}\n\n"
        "## 3. Scope / boundaries\n\n"
        f"{bullet_block}\n\n"
        "## 4. Promotion provenance\n\n"
        "- Promotion path: explicit idea-note to intent-sheet action\n"
        f"- Source note status at promotion: {str(meta.get('status') or 'draft').strip() or 'draft'}\n"
        "- Durable source snapshot: `artifacts/source_idea_note/`\n\n"
        "## 5. Source note snapshot\n\n"
        f"> {excerpt}\n"
    )


def _new_meta(
    *,
    uid: str,
    slug: str,
    title: str,
    created_utc: str,
    status: str,
    tags: Optional[List[str]],
    idea_markdown: str,
    project_id: Optional[str],
    workstream_type: Optional[str],
    scope_boundary: Optional[str],
    artifact_roots: Optional[List[str]],
    related_intent_sheet_uids: Optional[List[str]] = None,
    related_pis_uids: Optional[List[str]] = None,
    promotion_target_uid: Optional[str] = None,
    confidence: str = "",
    maturity: str = "",
    why_now: Optional[str] = None,
    priority_signal: Optional[str] = None,
    resume_context: Optional[str] = None,
    resume_blockers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    meta = {
        "schema_version": IDEA_NOTE_SCHEMA_VERSION,
        "uid": str(uid),
        "slug": str(slug),
        "title": str(title),
        "status": normalize_status(status),
        "created_utc": str(created_utc),
        "updated_utc": str(created_utc),
        "tags": ledger_schema.normalize_topic_tags(tags, max_tags=12),
        "topic_tags": ledger_schema.normalize_topic_tags(tags),
        "project_id": ledger_schema.normalize_project_id(project_id),
        "workstream_type": ledger_schema.normalize_workstream_type(workstream_type),
        "scope_boundary": ledger_schema.normalize_scope_boundary(scope_boundary),
        "artifact_roots": ledger_schema.normalize_artifact_roots(artifact_roots),
        "related_intent_sheet_uids": list(related_intent_sheet_uids or []),
        "related_pis_uids": list(related_pis_uids or []),
        "promotion_target_uid": promotion_target_uid,
        "confidence": str(confidence or "").strip().lower(),
        "maturity": str(maturity or "").strip().lower(),
        "why_now": why_now,
        "priority_signal": priority_signal,
        "resume_context": resume_context,
        "resume_blockers": list(resume_blockers or []),
        "idea_excerpt": _excerpt(idea_markdown),
        "links": {"artifacts": [], "exports": []},
    }
    return _normalize_provenance(_normalize_pre_intent_fields(meta))


def _event_line(*, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "schema_version": IDEA_NOTE_EVENT_SCHEMA_VERSION,
        "utc": now_utc_iso(),
        "type": str(event_type or "").strip() or "unknown",
        "payload": payload or {},
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return body


class IdeaNoteLedger:
    def __init__(self, *, repo_root: Optional[Path] = None) -> None:
        self.repo_root = Path(repo_root or ledger_storage.repo_root_from_here()).resolve()
        self.root = idea_notes_root(self.repo_root)

    def list_notes(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in _list_idea_dirs(self.root):
            meta = ledger_storage.read_json(d / "meta.json") or {}
            out.append(
                {
                    "uid": str(meta.get("uid") or ""),
                    "slug": str(meta.get("slug") or ""),
                    "title": str(meta.get("title") or d.name),
                    "status": str(meta.get("status") or "draft"),
                    "created_utc": str(meta.get("created_utc") or ""),
                    "updated_utc": str(meta.get("updated_utc") or meta.get("created_utc") or ""),
                    "project_id": str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                    "workstream_type": str(meta.get("workstream_type") or ledger_schema.DEFAULT_WORKSTREAM_TYPE),
                    "scope_boundary": str(meta.get("scope_boundary") or ledger_schema.DEFAULT_SCOPE_BOUNDARY),
                    "tags": list(meta.get("tags") or []),
                    "topic_tags": list(meta.get("topic_tags") or []),
                    "artifact_roots": list(meta.get("artifact_roots") or []),
                    "confidence": str(meta.get("confidence") or ""),
                    "maturity": str(meta.get("maturity") or ""),
                    "priority_signal": str(meta.get("priority_signal") or ""),
                    "why_now": str(meta.get("why_now") or ""),
                    "resume_context": str(meta.get("resume_context") or ""),
                    "resume_blockers": list(meta.get("resume_blockers") or []),
                    "related_intent_sheet_uids": list(meta.get("related_intent_sheet_uids") or []),
                    "related_pis_uids": list(meta.get("related_pis_uids") or []),
                    "promotion_target_uid": meta.get("promotion_target_uid"),
                    "idea_excerpt": str(meta.get("idea_excerpt") or ""),
                    "dir": d.name,
                    "backlog_kind": "idea_note",
                }
            )
        out.sort(key=lambda row: str(row.get("uid") or ""))
        out.sort(key=lambda row: str(row.get("updated_utc") or ""), reverse=True)
        return out[: max(1, min(int(limit or 200), 2000))]

    def get_note_dir(self, uid: str) -> Path:
        found = _idea_dir_for_uid(self.root, uid)
        if found is None:
            raise FileNotFoundError(uid)
        return found

    def read_note(self, uid: str) -> Dict[str, Any]:
        d = self.get_note_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        return {
            "ok": True,
            "uid": str(meta.get("uid") or uid),
            "meta": meta,
            "idea_markdown": ledger_storage.read_text(d / "idea.md"),
            "artifacts": ledger_storage.list_artifacts(d),
            "dir": d.name,
        }

    def create_note(
        self,
        *,
        title: str,
        idea_markdown: str,
        slug: Optional[str] = None,
        tags: Optional[List[str]] = None,
        uid_override: Optional[str] = None,
        project_id: Optional[str] = None,
        workstream_type: Optional[str] = None,
        scope_boundary: Optional[str] = None,
        artifact_roots: Optional[List[str]] = None,
        related_intent_sheet_uids: Optional[List[str]] = None,
        related_pis_uids: Optional[List[str]] = None,
        promotion_target_uid: Optional[str] = None,
        confidence: str = "",
        maturity: str = "",
        status: str = "draft",
        why_now: Optional[str] = None,
        priority_signal: Optional[str] = None,
        resume_context: Optional[str] = None,
        resume_blockers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        uid = validate_uid(uid_override) if uid_override else new_uid()
        if _idea_dir_for_uid(self.root, uid) is not None:
            raise FileExistsError(f"uid already exists: {uid}")
        note_slug = ledger_storage.sanitize_slug(slug or title)
        utc = now_utc_iso()
        meta = _new_meta(
            uid=uid,
            slug=note_slug,
            title=str(title or "(untitled)"),
            created_utc=utc,
            status=status,
            tags=tags,
            idea_markdown=idea_markdown,
            project_id=project_id,
            workstream_type=workstream_type,
            scope_boundary=scope_boundary,
            artifact_roots=artifact_roots,
            related_intent_sheet_uids=related_intent_sheet_uids,
            related_pis_uids=related_pis_uids,
            promotion_target_uid=promotion_target_uid,
            confidence=confidence,
            maturity=maturity,
            why_now=why_now,
            priority_signal=priority_signal,
            resume_context=resume_context,
            resume_blockers=resume_blockers,
        )
        d = _idea_dir(
            self.root,
            uid=uid,
            slug=note_slug,
            project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            status=str(meta.get("status") or "draft"),
            created_utc=str(meta.get("created_utc") or utc),
        )
        d.parent.mkdir(parents=True, exist_ok=True)
        d.mkdir(parents=False, exist_ok=False)
        (d / "artifacts").mkdir(parents=True, exist_ok=True)
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        ledger_storage.atomic_write_text(d / "idea.md", idea_markdown or "")
        self._append_event(d, event_type="created", payload={"title": meta["title"], "status": meta["status"]})
        return {"ok": True, "uid": uid, "slug": note_slug, "dir": d.name, "created_utc": utc}

    def update_status(self, uid: str, status: str, *, note: str = "", action: Optional[str] = None) -> Dict[str, Any]:
        d = self.get_note_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        current_status = str(meta.get("status") or "draft")
        target_status = normalize_status(status)
        _validate_transition(current_status=current_status, target_status=target_status, meta=meta)
        meta["status"] = target_status
        meta["updated_utc"] = now_utc_iso()
        target = _idea_dir(
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
        self._append_event(
            d,
            event_type="status_changed",
            payload={
                "from_status": current_status,
                "status": meta["status"],
                "note": str(note or ""),
                "action": str(action or "").strip() or None,
            },
        )
        return {"ok": True, "uid": uid, "status": meta["status"], "updated_utc": meta["updated_utc"]}

    def update_meta(self, uid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be a JSON object")
        unknown = sorted(str(k) for k in patch.keys() if str(k) not in _ALLOWED_META_PATCH_KEYS)
        if unknown:
            raise ValueError(f"unsupported idea note metadata keys: {', '.join(unknown)}")
        d = self.get_note_dir(uid)
        meta = ledger_storage.read_json(d / "meta.json") or {}
        current_status = str(meta.get("status") or "draft")
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
        if "related_intent_sheet_uids" in patch:
            meta["related_intent_sheet_uids"] = _normalize_uid_list(patch.get("related_intent_sheet_uids"), prefix="IS-")
        if "related_pis_uids" in patch:
            meta["related_pis_uids"] = _normalize_uid_list(patch.get("related_pis_uids"), prefix="PIS-")
        if "why_now" in patch:
            meta["why_now"] = patch.get("why_now")
        if "priority_signal" in patch:
            meta["priority_signal"] = patch.get("priority_signal")
        if "resume_context" in patch:
            meta["resume_context"] = patch.get("resume_context")
        if "resume_blockers" in patch:
            meta["resume_blockers"] = patch.get("resume_blockers")
        for key in ("promotion_target_uid", "confidence", "maturity"):
            if key in patch:
                meta[key] = str(patch.get(key) or "").strip() or None
        meta = _normalize_provenance(_normalize_pre_intent_fields(meta))
        _validate_transition(current_status=current_status, target_status=current_status, meta=meta)
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(
            d,
            event_type="meta_updated",
            payload={k: meta.get(k) for k in sorted(patch.keys())},
        )
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def update_text(self, uid: str, idea_markdown: str) -> Dict[str, Any]:
        d = self.get_note_dir(uid)
        ledger_storage.atomic_write_text(d / "idea.md", idea_markdown or "")
        meta = ledger_storage.read_json(d / "meta.json") or {}
        meta["idea_excerpt"] = _excerpt(idea_markdown)
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(d, event_type="idea_updated", payload={"bytes": len((idea_markdown or "").encode("utf-8"))})
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def promote_to_intent_sheet(self, uid: str, *, note: str = "") -> Dict[str, Any]:
        from .intent_sheet_manager import IntentSheetLedger

        note_record = self.read_note(uid)
        meta = dict(note_record.get("meta") or {})
        note_uid = str(meta.get("uid") or uid).strip()
        idea_markdown = str(note_record.get("idea_markdown") or "")
        current_status = normalize_status(meta.get("status") or "draft")
        if current_status == "discarded":
            raise ValueError("discarded idea note cannot be promoted")
        missing = _readiness_missing_fields(meta)
        if missing:
            raise ValueError(f"idea note not ready for promotion; missing: {', '.join(missing)}")
        explicit_target = _normalize_promotion_target_uid(meta.get("promotion_target_uid"))
        if explicit_target and not explicit_target.startswith("IS-"):
            raise ValueError("idea note promotion_target_uid conflicts with intent promotion target")
        target_uid = explicit_target or _derived_intent_uid_for_note(note_uid)
        intent_ledger = IntentSheetLedger(repo_root=self.repo_root)
        created = False
        repaired_backlink = False
        try:
            intent_record = intent_ledger.read_sheet(target_uid)
        except FileNotFoundError:
            created_res = intent_ledger.create_sheet(
                title=str(meta.get("title") or "Untitled Intent Sheet"),
                intent_markdown=_build_promoted_intent_markdown(
                    intent_uid=target_uid,
                    note_uid=note_uid,
                    meta=meta,
                    idea_markdown=idea_markdown,
                ),
                uid_override=target_uid,
                tags=list(meta.get("tags") or []),
                project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                workstream_type=str(meta.get("workstream_type") or ledger_schema.DEFAULT_WORKSTREAM_TYPE),
                scope_boundary=str(meta.get("scope_boundary") or ledger_schema.DEFAULT_SCOPE_BOUNDARY),
                artifact_roots=list(meta.get("artifact_roots") or []),
            )
            created = True
            intent_record = intent_ledger.read_sheet(created_res["uid"])

        intent_meta = dict(intent_record.get("meta") or {})
        integration_meta = dict(intent_meta.get("integration_meta") or {})
        source_uid = str(integration_meta.get("source_idea_note_uid") or "").strip()
        if source_uid and source_uid != note_uid:
            raise ValueError(
                f"intent sheet {target_uid} is already bound to a different idea note: {source_uid}"
            )
        promotion_meta = {
            "relationship": "promoted_from_idea_note",
            "source_idea_note_uid": note_uid,
            "source_idea_note_status_at_promotion": current_status,
            "promotion_gate": {
                "required_fields": ["title", "why_now", "resume_context"],
                "satisfied_fields": ["title", "why_now", "resume_context"],
            },
            "promotion_note": str(note or "").strip() or None,
        }
        if integration_meta != promotion_meta:
            intent_ledger.update_meta(target_uid, {"integration_meta": promotion_meta})
        intent_dir = intent_ledger.get_sheet_dir(target_uid)
        source_dir = intent_dir / "artifacts" / "source_idea_note"
        ledger_storage.atomic_write_json(
            source_dir / "meta.json",
            {
                "idea_note_uid": note_uid,
                "promoted_intent_uid": target_uid,
                "captured_utc": now_utc_iso(),
                "source_meta": meta,
            },
        )
        ledger_storage.atomic_write_text(source_dir / "idea.md", idea_markdown or "")
        ledger_storage.atomic_write_json(
            intent_dir / "artifacts" / "promotion_record.json",
            {
                "schema_version": "idea_note_promotion_v1",
                "idea_note_uid": note_uid,
                "intent_sheet_uid": target_uid,
                "created_new_intent": created,
                "promotion_note": str(note or "").strip() or None,
            },
        )

        refreshed_note = self.read_note(note_uid)
        refreshed_meta = dict(refreshed_note.get("meta") or {})
        previous_target = _normalize_promotion_target_uid(refreshed_meta.get("promotion_target_uid"))
        if previous_target != target_uid or normalize_status(refreshed_meta.get("status") or "draft") != "promoted":
            repaired_backlink = not created
            self.update_meta(
                note_uid,
                {
                    "promotion_target_uid": target_uid,
                    "related_intent_sheet_uids": _merge_unique_uid(
                        list(refreshed_meta.get("related_intent_sheet_uids") or []), target_uid
                    ),
                },
            )
            self.update_status(note_uid, "promoted", note=note or "promoted to intent sheet", action="promote_to_intent_sheet")

        final_note = self.read_note(note_uid)
        return {
            "ok": True,
            "idea_note_uid": note_uid,
            "intent_sheet_uid": target_uid,
            "created_new_intent": created,
            "repaired_backlink": repaired_backlink,
            "idea_note_status": str((final_note.get("meta") or {}).get("status") or ""),
            "intent_dir": str(intent_ledger.get_sheet_dir(target_uid).relative_to(self.repo_root)).replace("\\", "/"),
        }

    def apply_lifecycle_action(
        self,
        uid: str,
        action: str,
        *,
        note: str = "",
        why_now: Optional[str] = None,
        priority_signal: Optional[str] = None,
        resume_context: Optional[str] = None,
        resume_blockers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        op = str(action or "").strip().lower()
        if op not in _LIFECYCLE_ACTIONS:
            raise ValueError(f"unsupported idea note action: {op}")
        current_meta = dict(self.read_note(uid).get("meta") or {})
        current_status = str(current_meta.get("status") or "draft")
        patch: Dict[str, Any] = {}
        target = "active"
        if op == "resume":
            if why_now is not None:
                patch["why_now"] = why_now
            if priority_signal is not None:
                patch["priority_signal"] = priority_signal
            if resume_context is not None:
                patch["resume_context"] = resume_context
            if resume_blockers is not None:
                patch["resume_blockers"] = resume_blockers
            effective_meta = dict(current_meta)
            effective_meta.update(patch)
            effective_meta = _normalize_pre_intent_fields(effective_meta)
            _validate_transition(current_status=current_status, target_status=target, meta=effective_meta)
            if not effective_meta.get("why_now"):
                raise ValueError("resume action requires why_now")
            if not effective_meta.get("resume_context"):
                raise ValueError("resume action requires resume_context")
            target = "active"
        elif op == "park":
            if resume_context is not None:
                patch["resume_context"] = resume_context
            if resume_blockers is not None:
                patch["resume_blockers"] = resume_blockers
            effective_meta = dict(current_meta)
            effective_meta.update(patch)
            _validate_transition(current_status=current_status, target_status="parked", meta=effective_meta)
            if not _has_resume_breadcrumb(effective_meta):
                raise ValueError("park action requires resume_context or resume_blockers")
            target = "parked"
        elif op == "discard":
            target = "discarded"
        if patch:
            self.update_meta(uid, patch)
        return self.update_status(uid, target, note=note, action=op)

    def _append_event(self, d: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
        line = json.dumps(_event_line(event_type=event_type, payload=payload), sort_keys=True, ensure_ascii=False)
        path = d / "events.jsonl"
        existing = ""
        if path.exists() and path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
        ledger_storage.atomic_write_text(path, existing + line + "\n")
