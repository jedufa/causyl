"""infra.webui.ledger.ledger_schema

Schema definitions and helpers for Change Ledger.

Design goals
------------
- Keep schemas small and stable (governance-friendly).
- Use explicit schema_version fields.
- Avoid importing heavy WebUI server modules (no circular deps).

Notes
-----
This module defines the canonical v1 schema shapes for:
- ledger_item_v1 (meta.json)
- ledger_event_v1 (events.jsonl line entries)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


LEDGER_ITEM_SCHEMA_VERSION = "ledger_item_v1"
LEDGER_EVENT_SCHEMA_VERSION = "ledger_event_v1"

DEFAULT_PROJECT_ID = "causyl"
DEFAULT_WORKSTREAM_TYPE = "platform"
DEFAULT_SCOPE_BOUNDARY = "platform-local"

ALLOWED_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
ALLOWED_WORKSTREAM_TYPES = {
    "platform",
    "project",
    "shared_capability",
    "ops",
}
ALLOWED_SCOPE_BOUNDARIES = {
    "platform-local",
    "project-local",
    "cross-project",
}


ALLOWED_STATUSES: List[str] = [
    "draft",
    "active",
    "deferred",
    "ready_for_implement",
    "in_progress",
    "ready_for_assure",
    "ready",
    "in_integration",
    "implemented",
    "verified",
    "done",
    "blocked",
    "abandoned",
    "deprecated",
    "reopened",
]


ALLOWED_RELEASE_STATES: List[str] = [
    "draft",
    "candidate",
    "released",
]

_TAG_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,47}$")
_PIS_UID_TAG_RE = re.compile(r"^pis-\d{8}-\d{6}z-[a-z0-9]{4}$")


def normalize_topic_tags(raw: Any, *, max_tags: int = 8) -> List[str]:
    """Normalize durable topic tags into a small, stable set."""

    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for value in raw:
        token = str(value or "").strip().lower()
        if not token:
            continue
        if _PIS_UID_TAG_RE.match(token):
            continue
        if not _TAG_TOKEN_RE.fullmatch(token):
            continue
        if token in out:
            continue
        out.append(token)
        if len(out) >= max(1, min(32, int(max_tags))):
            break
    return out


def normalize_status(s: Any) -> str:
    """Normalize a status string into canonical form.

    Args:
        s: Any user-provided status.

    Returns:
        Canonical status.

    Raises:
        ValueError if status is invalid.
    """

    st = str(s or "").strip().lower()
    # Common aliases.
    alias = {
        "integration": "in_integration",
        "in-integration": "in_integration",
        "currently_in_integration": "in_integration",
        "planned": "ready",
        "ready_for_builder": "ready_for_implement",
        "ready_for_assurer": "ready_for_assure",
    }
    st = alias.get(st, st)
    if st not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {st}")
    return st


def normalize_release_state(s: Any) -> str:
    """Normalize release_state.

    Raises ValueError if invalid.
    """

    rs = str(s or "").strip().lower()
    if not rs:
        rs = "draft"
    if rs not in ALLOWED_RELEASE_STATES:
        raise ValueError(f"invalid release_state: {rs}")
    return rs


def normalize_project_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_PROJECT_ID
    if not ALLOWED_PROJECT_ID_RE.fullmatch(text):
        raise ValueError(f"invalid project_id: {text}")
    return text


def normalize_workstream_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_WORKSTREAM_TYPE
    if text not in ALLOWED_WORKSTREAM_TYPES:
        raise ValueError(f"invalid workstream_type: {text}")
    return text


def normalize_scope_boundary(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_SCOPE_BOUNDARY
    if text not in ALLOWED_SCOPE_BOUNDARIES:
        raise ValueError(f"invalid scope_boundary: {text}")
    return text


def normalize_artifact_roots(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text or text in out:
            continue
        out.append(text)
    return out


def with_meta_defaults(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    obj = dict(meta or {})
    tags = normalize_topic_tags(obj.get("tags"), max_tags=12)
    topic_tags_source = obj.get("topic_tags") if isinstance(obj.get("topic_tags"), list) else tags
    obj["tags"] = tags
    obj["topic_tags"] = normalize_topic_tags(topic_tags_source)
    obj["project_id"] = normalize_project_id(obj.get("project_id"))
    obj["workstream_type"] = normalize_workstream_type(obj.get("workstream_type"))
    obj["scope_boundary"] = normalize_scope_boundary(obj.get("scope_boundary"))
    obj["artifact_roots"] = normalize_artifact_roots(obj.get("artifact_roots"))
    return obj


def new_item_meta(
    *,
    uid: str,
    slug: str,
    title: str,
    created_utc: str,
    status: str = "draft",
    tags: Optional[List[str]] = None,
    pis_markdown: Optional[str] = None,
    supersedes_uid: Optional[str] = None,
    regression_of_uid: Optional[str] = None,
    project_id: Optional[str] = None,
    workstream_type: Optional[str] = None,
    scope_boundary: Optional[str] = None,
    artifact_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new ledger_item_v1 meta document."""

    return with_meta_defaults({
        "schema_version": LEDGER_ITEM_SCHEMA_VERSION,
        "uid": str(uid),
        "slug": str(slug),
        "title": str(title),
        "status": normalize_status(status),
        "release_state": "draft",
        "created_utc": str(created_utc),
        "updated_utc": str(created_utc),
        "execution_priority": None,
        "tags": normalize_topic_tags(tags, max_tags=12),
        "topic_tags": normalize_topic_tags(tags),
        "project_id": normalize_project_id(project_id),
        "workstream_type": normalize_workstream_type(workstream_type),
        "scope_boundary": normalize_scope_boundary(scope_boundary),
        "artifact_roots": normalize_artifact_roots(artifact_roots),
        "supersedes_uid": (str(supersedes_uid) if supersedes_uid else None),
        "regression_of_uid": (str(regression_of_uid) if regression_of_uid else None),
        # Optional evidence pointer; front-end can treat as a green check.
        "evidence": None,
        # Optional derived links (runs/exports/etc). Keep small.
        "links": {"runs": [], "exports": []},
        # Optional metadata for integration tooling.
        "integration_meta": None,
        # Optional executable action specs (AG01).
        "actions": [],
        # Optional evidence requirements used by mark_verified gate.
        "required_evidence": [],
        # Optional linked changeset ids (MVP-2 integration helper).
        "changesets": [],
        # Convenience: keep a short excerpt for list views (UI may override).
        "pis_excerpt": _excerpt(pis_markdown or ""),
    })


def _excerpt(text: str, *, max_chars: int = 180) -> str:
    """Return a single-line excerpt."""

    s = " ".join(str(text or "").split())
    if len(s) > int(max_chars):
        return s[: max(0, int(max_chars) - 1)] + "…"
    return s
