"""infra.webui.ledger.ledger_manager

ChangeLedger manager: filesystem-backed governance ledger for PIS items.

This module implements the business logic used by WebUI API endpoints.
It intentionally avoids importing infra.webui.server to prevent circular
imports.

Key features
------------
- Create items (UID + slug)
- Read/list items
- Update status/meta/pis text
- Create revisions (supersedes chain)
- Optional append-only event chain (events.jsonl) with hash chaining
- Best-effort git commit linkage (grep by UID)
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------
# Deterministic identifiers (P0-A hard gate)
# ---------------------------------------------------------------------
#
# The determinism static scanner forbids uuid4() (random).
# This WebUI/ledger code still needs unique-ish IDs, but uniqueness is
# a tooling concern, not a randomness concern.
#
# We therefore derive UUIDs deterministically using uuid5 over a fixed
# namespace and a monotonic process-local counter.
#
_LEDGER_UUID_NAMESPACE = uuid.UUID("7c2a7a8b-3d8b-4f6a-a4a2-9b6d7f7c6a11")
_UID_COUNTER_LOCK = threading.Lock()
_UID_COUNTER = 0
_EVENT_COUNTER_LOCK = threading.Lock()
_EVENT_COUNTER = 0


def _next_uid_counter() -> int:
    """Return next process-local UID counter (monotonic)."""
    global _UID_COUNTER
    with _UID_COUNTER_LOCK:
        _UID_COUNTER += 1
        return int(_UID_COUNTER)


def _next_event_counter() -> int:
    """Return next process-local event counter (monotonic)."""
    global _EVENT_COUNTER
    with _EVENT_COUNTER_LOCK:
        _EVENT_COUNTER += 1
        return int(_EVENT_COUNTER)


def _det_uuid5(tag: str) -> str:
    """Return deterministic UUID string for a tag (uuid5)."""
    return str(uuid.uuid5(_LEDGER_UUID_NAMESPACE, str(tag)))

from . import ledger_git_links
from . import ledger_schema
from . import ledger_storage


def now_utc_iso() -> str:
    """Return UTC ISO timestamp (seconds resolution)."""

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_uid() -> str:
    """Create a new PIS UID of the form PIS-YYYYMMDD-HHMMSSZ-ABCD."""

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    # 4 chars from uuid hex, mapped to base32-ish.
    h = _det_uuid5(f"uid|{ts}|{_next_uid_counter()}").replace("-", "").upper()
    suf = "".join([c for c in h if c.isalnum()])[:4]
    if len(suf) < 4:
        suf = (suf + "0000")[:4]
    return f"PIS-{ts}-{suf}"


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _coerce_execution_priority(value: Any) -> Optional[int]:
    """Normalize execution priority into an integer or None."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("execution_priority must be an integer or null")
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except Exception as exc:
        raise ValueError("execution_priority must be an integer or null") from exc


def _execution_priority_sort_key(value: Any) -> Tuple[int, int]:
    """Return a stable sort key that keeps prioritized items first."""

    prio = _coerce_execution_priority(value)
    if prio is None:
        return (1, 0)
    return (0, int(prio))


class ChangeLedger:
    """Filesystem-backed Change Ledger for PIS items."""

    def __init__(self, *, repo_root: Optional[Path] = None) -> None:
        self.repo_root = Path(repo_root or ledger_storage.repo_root_from_here()).resolve()
        self.items_root = ledger_storage.ledger_patch_instruction_sheets_root(self.repo_root)
        self.default_storage_layout = "status_sharded"

    # ---------------------------------------------------------------------
    # Core item operations
    # ---------------------------------------------------------------------

    def list_items(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        """List ledger items (summary)."""

        lim = int(limit) if int(limit) > 0 else 200
        lim = max(1, min(2000, lim))

        out: List[Dict[str, Any]] = []
        for d in ledger_storage.list_item_dirs(self.items_root):
            meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(d / "meta.json") or {})
            uid = str(meta.get("uid") or "")
            slug = str(meta.get("slug") or "")
            title = str(meta.get("title") or d.name)
            status = str(meta.get("status") or "draft")
            updated = str(meta.get("updated_utc") or meta.get("created_utc") or "")
            tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
            topic_tags = meta.get("topic_tags") if isinstance(meta.get("topic_tags"), list) else []
            release_state = str(meta.get("release_state") or "draft")
            try:
                execution_priority = _coerce_execution_priority(meta.get("execution_priority"))
            except ValueError:
                execution_priority = None
            supersedes_uid = meta.get("supersedes_uid")
            regression_of_uid = meta.get("regression_of_uid")
            evidence = meta.get("evidence")
            pis_excerpt = str(meta.get("pis_excerpt") or "")
            out.append(
                {
                    "uid": uid,
                    "slug": slug,
                    "title": title,
                    "status": status,
                    "release_state": release_state,
                    "updated_utc": updated,
                    "execution_priority": execution_priority,
                    "tags": [str(t) for t in tags if str(t).strip()],
                    "topic_tags": ledger_schema.normalize_topic_tags(topic_tags or tags),
                    "supersedes_uid": supersedes_uid,
                    "regression_of_uid": regression_of_uid,
                    "project_id": str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                    "workstream_type": str(meta.get("workstream_type") or ledger_schema.DEFAULT_WORKSTREAM_TYPE),
                    "scope_boundary": str(meta.get("scope_boundary") or ledger_schema.DEFAULT_SCOPE_BOUNDARY),
                    "artifact_roots": list(meta.get("artifact_roots") or []),
                    "has_evidence": bool(evidence),
                    "pis_excerpt": pis_excerpt,
                    "dir": d.name,
                }
            )

        # Keep the execution queue order durable across clients while leaving
        # unprioritized items newest-first below any explicitly ranked items.
        out.sort(key=lambda r: str(r.get("uid") or ""), reverse=True)
        out.sort(key=lambda r: str(r.get("updated_utc") or ""), reverse=True)
        out.sort(key=lambda r: _execution_priority_sort_key(r.get("execution_priority")))
        return out[:lim]

    def get_item_dir(self, uid: str) -> Path:
        """Return item directory for uid."""

        duplicates = ledger_storage.duplicate_paths_for_uid(self.items_root, uid)
        if duplicates:
            raise RuntimeError(
                f"ambiguous uid {uid}: duplicate ledger directories exist: "
                + ", ".join(str(path.relative_to(self.items_root)).replace("\\", "/") for path in duplicates)
            )
        d = ledger_storage.item_dir_for_uid(self.items_root, uid)
        if d is None:
            raise FileNotFoundError(uid)
        return d

    def read_item(self, uid: str) -> Dict[str, Any]:
        """Read full item (meta + pis + artifacts)."""

        d = self.get_item_dir(uid)
        meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(d / "meta.json") or {})
        pis = ledger_storage.read_text(d / "pis.md")
        notes = ledger_storage.read_text(d / "notes.md")
        artifacts = ledger_storage.list_artifacts(d)
        return {
            "ok": True,
            "uid": str(meta.get("uid") or uid),
            "dir": d.name,
            "meta": meta,
            "pis_markdown": pis,
            "notes": notes,
            "artifacts": artifacts,
        }

    def create_item(
        self,
        *,
        title: str,
        slug: Optional[str] = None,
        tags: Optional[List[str]] = None,
        pis_markdown: str = "",
        supersedes_uid: Optional[str] = None,
        regression_of_uid: Optional[str] = None,
        uid_override: Optional[str] = None,
        project_id: Optional[str] = None,
        workstream_type: Optional[str] = None,
        scope_boundary: Optional[str] = None,
        artifact_roots: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new ledger item folder + meta + pis.md + events.jsonl."""

        uid = ledger_storage.validate_uid(uid_override) if uid_override else new_uid()
        if ledger_storage.item_dir_for_uid(self.items_root, uid) is not None or ledger_storage.duplicate_paths_for_uid(self.items_root, uid):
            raise FileExistsError(f"uid already exists: {uid}")
        s = ledger_storage.sanitize_slug(slug or title)
        utc = now_utc_iso()

        d: Optional[Path] = None
        try:
            d = ledger_storage.ensure_item_dir(
                self.items_root,
                uid=uid,
                slug=s,
                project_id=project_id,
                status="draft",
                created_utc=utc,
                storage_layout=self.default_storage_layout,
            )

            meta = ledger_schema.new_item_meta(
                uid=uid,
                slug=s,
                title=str(title or "(untitled)"),
                created_utc=utc,
                status="draft",
                tags=tags,
                pis_markdown=pis_markdown,
                supersedes_uid=supersedes_uid,
                regression_of_uid=regression_of_uid,
                project_id=project_id,
                workstream_type=workstream_type,
                scope_boundary=scope_boundary,
                artifact_roots=artifact_roots,
            )
            ledger_storage.atomic_write_json(d / "meta.json", meta)
            ledger_storage.atomic_write_text(d / "pis.md", pis_markdown or "")

            # Normalize into the canonical lifecycle-sharded path even if an
            # older or misconfigured caller created the directory flat first.
            d = ledger_storage.relocate_item_dir(
                self.items_root,
                item_dir=d,
                uid=uid,
                slug=s,
                project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                status=str(meta.get("status") or ""),
                created_utc=str(meta.get("created_utc") or utc),
                storage_layout=self.default_storage_layout,
            )

            # Add initial event.
            self._append_event(d, event_type="created", payload={"title": meta.get("title"), "status": meta.get("status")})
            ledger_storage.write_uid_registry(self.items_root)

            return {"ok": True, "uid": uid, "slug": s, "dir": d.name, "created_utc": utc}
        except Exception as e:
            # Cleanup on failure.
            if d is not None and d.exists():
                ledger_storage.safe_remove_dir(d)
            raise

    def update_status(self, uid: str, status: str, *, note: str = "") -> Dict[str, Any]:
        """Update item status."""

        original_dir = self.get_item_dir(uid)
        original_meta = ledger_storage.read_json(original_dir / "meta.json") or {}
        original_status = str(original_meta.get("status") or "")
        updated_meta = dict(original_meta)
        updated_meta["status"] = ledger_schema.normalize_status(status)
        updated_meta["updated_utc"] = now_utc_iso()
        moved_dir = ledger_storage.relocate_item_dir(
            self.items_root,
            item_dir=original_dir,
            uid=str(updated_meta.get("uid") or uid),
            slug=str(updated_meta.get("slug") or original_dir.name.split("__", 1)[-1]),
            project_id=str(updated_meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            status=str(updated_meta.get("status") or ""),
            created_utc=str(updated_meta.get("created_utc") or ""),
            storage_layout=self.default_storage_layout,
        )
        try:
            ledger_storage.atomic_write_json(moved_dir / "meta.json", updated_meta)
        except Exception:
            rollback_dir = ledger_storage.relocate_item_dir(
                self.items_root,
                item_dir=moved_dir,
                uid=str(original_meta.get("uid") or uid),
                slug=str(original_meta.get("slug") or original_dir.name.split("__", 1)[-1]),
                project_id=str(original_meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                status=original_status,
                created_utc=str(original_meta.get("created_utc") or ""),
                storage_layout=self.default_storage_layout,
            )
            ledger_storage.atomic_write_json(rollback_dir / "meta.json", original_meta)
            raise
        self._append_event(
            moved_dir,
            event_type="status_changed",
            payload={"status": updated_meta["status"], "note": str(note or "")},
        )
        ledger_storage.write_uid_registry(self.items_root)
        return {"ok": True, "uid": uid, "status": updated_meta["status"], "updated_utc": updated_meta["updated_utc"]}

    def update_meta(self, uid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Patch meta.json with selected fields."""

        d = self.get_item_dir(uid)
        meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(d / "meta.json") or {})
        original_meta = dict(meta)

        if "title" in patch and patch.get("title") is not None:
            meta["title"] = str(patch.get("title") or "").strip() or meta.get("title")
        if "tags" in patch and isinstance(patch.get("tags"), list):
            normalized = ledger_schema.normalize_topic_tags(patch.get("tags"), max_tags=12)
            meta["tags"] = list(normalized)
            if "topic_tags" not in patch:
                meta["topic_tags"] = ledger_schema.normalize_topic_tags(normalized)
        if "topic_tags" in patch:
            meta["topic_tags"] = ledger_schema.normalize_topic_tags(patch.get("topic_tags"))
        if "release_state" in patch and patch.get("release_state") is not None:
            meta["release_state"] = ledger_schema.normalize_release_state(patch.get("release_state"))
        if "execution_priority" in patch:
            meta["execution_priority"] = _coerce_execution_priority(patch.get("execution_priority"))
        if "supersedes_uid" in patch:
            su = patch.get("supersedes_uid")
            meta["supersedes_uid"] = str(su).strip() if su else None
        if "regression_of_uid" in patch:
            ru = patch.get("regression_of_uid")
            meta["regression_of_uid"] = str(ru).strip() if ru else None
        if "evidence" in patch:
            ev = patch.get("evidence")
            meta["evidence"] = ev if (ev is None or isinstance(ev, dict)) else meta.get("evidence")
        if "actions" in patch:
            acts = patch.get("actions")
            meta["actions"] = acts if (acts is None or isinstance(acts, list)) else meta.get("actions")
        if "required_evidence" in patch:
            req = patch.get("required_evidence")
            meta["required_evidence"] = req if (req is None or isinstance(req, list)) else meta.get("required_evidence")
        if "changesets" in patch:
            cs = patch.get("changesets")
            meta["changesets"] = cs if (cs is None or isinstance(cs, list)) else meta.get("changesets")
        if "integration_meta" in patch:
            integration = patch.get("integration_meta")
            meta["integration_meta"] = integration if (integration is None or isinstance(integration, dict)) else meta.get("integration_meta")
        if "project_id" in patch:
            meta["project_id"] = ledger_schema.normalize_project_id(patch.get("project_id"))
        if "workstream_type" in patch:
            meta["workstream_type"] = ledger_schema.normalize_workstream_type(patch.get("workstream_type"))
        if "scope_boundary" in patch:
            meta["scope_boundary"] = ledger_schema.normalize_scope_boundary(patch.get("scope_boundary"))
        if "artifact_roots" in patch:
            meta["artifact_roots"] = ledger_schema.normalize_artifact_roots(patch.get("artifact_roots"))

        # Refresh excerpt if pis updated separately.
        meta["updated_utc"] = now_utc_iso()
        moved_dir = ledger_storage.relocate_item_dir(
            self.items_root,
            item_dir=d,
            uid=str(meta.get("uid") or uid),
            slug=str(meta.get("slug") or d.name.split("__", 1)[-1]),
            project_id=str(meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            status=str(meta.get("status") or ""),
            created_utc=str(meta.get("created_utc") or ""),
            storage_layout=self.default_storage_layout,
        )
        try:
            ledger_storage.atomic_write_json(moved_dir / "meta.json", meta)
        except Exception:
            rollback_dir = ledger_storage.relocate_item_dir(
                self.items_root,
                item_dir=moved_dir,
                uid=str(original_meta.get("uid") or uid),
                slug=str(original_meta.get("slug") or d.name.split("__", 1)[-1]),
                project_id=str(original_meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
                status=str(original_meta.get("status") or ""),
                created_utc=str(original_meta.get("created_utc") or ""),
                storage_layout=self.default_storage_layout,
            )
            ledger_storage.atomic_write_json(rollback_dir / "meta.json", original_meta)
            raise
        self._append_event(moved_dir, event_type="meta_updated", payload={k: patch.get(k) for k in patch.keys()})
        ledger_storage.write_uid_registry(self.items_root)
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def prioritize_item(self, uid: str) -> Dict[str, Any]:
        """Promote an item to the front of the execution queue."""

        d = self.get_item_dir(uid)
        meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(d / "meta.json") or {})

        priorities: List[int] = []
        for item_dir in ledger_storage.list_item_dirs(self.items_root):
            item_meta = ledger_storage.read_json(item_dir / "meta.json") or {}
            prio = _coerce_execution_priority(item_meta.get("execution_priority"))
            if prio is not None:
                priorities.append(prio)

        current_priority = _coerce_execution_priority(meta.get("execution_priority"))
        new_priority = (min(priorities) - 1000) if priorities else 0
        if current_priority is not None and current_priority < new_priority:
            new_priority = current_priority

        meta["execution_priority"] = int(new_priority)
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(
            d,
            event_type="execution_priority_changed",
            payload={"execution_priority": int(new_priority), "reason": "mobile_backlog_prioritize"},
        )
        ledger_storage.write_uid_registry(self.items_root)
        return {
            "ok": True,
            "uid": uid,
            "execution_priority": int(new_priority),
            "updated_utc": meta["updated_utc"],
        }

    def update_pis(self, uid: str, pis_markdown: str) -> Dict[str, Any]:
        """Overwrite pis.md."""

        d = self.get_item_dir(uid)
        ledger_storage.atomic_write_text(d / "pis.md", pis_markdown or "")
        meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(d / "meta.json") or {})
        meta["pis_excerpt"] = " ".join(str(pis_markdown or "").split())[:180]
        meta["updated_utc"] = now_utc_iso()
        ledger_storage.atomic_write_json(d / "meta.json", meta)
        self._append_event(d, event_type="pis_updated", payload={"bytes": len((pis_markdown or "").encode("utf-8"))})
        ledger_storage.write_uid_registry(self.items_root)
        return {"ok": True, "uid": uid, "updated_utc": meta["updated_utc"]}

    def create_revision(
        self,
        uid: str,
        *,
        title: Optional[str] = None,
        slug: Optional[str] = None,
        deprecate_old: bool = True,
    ) -> Dict[str, Any]:
        """Create a new item that supersedes uid, copying the PIS text."""

        old_dir = self.get_item_dir(uid)
        old_meta = ledger_schema.with_meta_defaults(ledger_storage.read_json(old_dir / "meta.json") or {})
        old_pis = ledger_storage.read_text(old_dir / "pis.md")

        new_title = str(title or old_meta.get("title") or "(untitled)")
        new_slug = slug or old_meta.get("slug") or ledger_storage.sanitize_slug(new_title)
        new_tags = old_meta.get("tags") if isinstance(old_meta.get("tags"), list) else []

        created = self.create_item(
            title=new_title,
            slug=new_slug,
            tags=[str(t) for t in new_tags],
            pis_markdown=old_pis,
            supersedes_uid=str(uid),
            regression_of_uid=None,
            project_id=str(old_meta.get("project_id") or ledger_schema.DEFAULT_PROJECT_ID),
            workstream_type=str(old_meta.get("workstream_type") or ledger_schema.DEFAULT_WORKSTREAM_TYPE),
            scope_boundary=str(old_meta.get("scope_boundary") or ledger_schema.DEFAULT_SCOPE_BOUNDARY),
            artifact_roots=list(old_meta.get("artifact_roots") or []),
        )

        if deprecate_old:
            try:
                self.update_status(uid, "deprecated", note=f"superseded_by={created.get('uid')}")
            except Exception as exc:
                created_dir = ledger_storage.item_dir_for_uid(self.items_root, str(created.get("uid") or ""))
                if created_dir is not None and created_dir.exists():
                    ledger_storage.safe_remove_dir(created_dir)
                    ledger_storage.write_uid_registry(self.items_root)
                raise RuntimeError(f"failed to deprecate prior revision {uid}") from exc

        return {"ok": True, "old_uid": uid, "new": created}

    # ---------------------------------------------------------------------
    # Git linkage
    # ---------------------------------------------------------------------

    def list_commits(self, uid: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Return best-effort commits referencing uid."""

        # Validate uid shape early.
        ledger_storage.validate_uid(uid)
        return ledger_git_links.list_commits_for_uid(self.repo_root, uid, limit=limit)

    # ---------------------------------------------------------------------
    # Event chain (optional)
    # ---------------------------------------------------------------------

    def append_event(self, uid: str, *, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append an event for an existing UID and return minimal event metadata."""

        d = self.get_item_dir(uid)
        self._append_event(d, event_type=str(event_type), payload=(payload if isinstance(payload, dict) else {"value": str(payload)}))
        return {"ok": True, "uid": uid, "event_type": str(event_type), "utc": now_utc_iso()}

    def _append_event(self, item_dir: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
        """Append an event to events.jsonl with hash chaining."""

        d = Path(item_dir)
        p = d / "events.jsonl"

        prev_hash = ""
        try:
            if p.exists() and p.is_file() and (not p.is_symlink()):
                # Chain only to the last hashed ledger event row.
                data = p.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in reversed(data[-50:]):
                    s = (line or "").strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        if isinstance(obj, dict):
                            row_hash = str(obj.get("hash") or "").strip()
                            if str(obj.get("schema_version") or "").strip() == ledger_schema.LEDGER_EVENT_SCHEMA_VERSION and row_hash:
                                prev_hash = row_hash
                                break
                    except Exception:
                        continue
        except Exception:
            prev_hash = ""

        ev: Dict[str, Any] = {
            "schema_version": ledger_schema.LEDGER_EVENT_SCHEMA_VERSION,
            "event_id": _det_uuid5(f"event|{d.name}|{event_type}|{_next_event_counter()}|{now_utc_iso()}"),
            "utc": now_utc_iso(),
            "type": str(event_type),
            "payload": payload if isinstance(payload, dict) else {"value": str(payload)},
            "prev_hash": prev_hash,
        }
        # Hash is computed over canonical JSON of event-without-hash, prefixed by prev_hash.
        body = _canonical_json_bytes(ev)
        ev_hash = _sha256_hex((prev_hash or "").encode("utf-8") + body)
        ev["hash"] = ev_hash

        line = _canonical_json_bytes(ev).decode("utf-8")
        # Append (best-effort). Not atomic, but append-only; corruption is detectable.
        try:
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.write("\n")
        except Exception:
            return
