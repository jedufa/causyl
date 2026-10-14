"""Release-channel integrity contract helpers for stabilization work."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

RELEASE_CHANNEL_INTEGRITY_UID = "PIS-20260402-063100Z-X4M1"
RELEASE_CHANNEL_INTEGRITY_SCHEMA_VERSION = "lab_assistant_release_channel_integrity_v1"


CHANNELS: List[Dict[str, Any]] = [
    {
        "channel": "debug",
        "publication_state": "published_local_artifact",
        "required_receipt_fields": [
            "channel_publication_state",
            "validation_scope",
            "proof_gap",
            "proof_gap_reason",
            "artifact_lineage",
            "source_snapshot_identity",
        ],
        "minimum_lineage_keys": [
            "published_apk_path",
            "meta_sidecar_path",
            "receipt_relpath",
        ],
    },
    {
        "channel": "release",
        "publication_state": "published_local_artifact",
        "required_receipt_fields": [
            "channel_publication_state",
            "validation_scope",
            "proof_gap",
            "proof_gap_reason",
            "artifact_lineage",
            "source_snapshot_identity",
        ],
        "minimum_lineage_keys": [
            "published_apk_path",
            "meta_sidecar_path",
            "receipt_relpath",
        ],
    },
]


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def release_channel_integrity_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": RELEASE_CHANNEL_INTEGRITY_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": RELEASE_CHANNEL_INTEGRITY_UID,
        "channels": [dict(row) for row in CHANNELS],
        "rules": [
            "Published artifacts must declare channel publication state, validation scope, and proof gaps explicitly.",
            "Artifact lineage must bind the published artifact, meta sidecar, and durable receipt together.",
            "Missing receipt-scope metadata is a proof gap even when the artifact bytes exist.",
        ],
    }


def validate_release_channel_integrity(snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(snapshot or release_channel_integrity_snapshot())
    errors: List[str] = []
    channels = [dict(row) for row in list(payload.get("channels") or []) if isinstance(row, dict)]
    if str(payload.get("uid") or "") != RELEASE_CHANNEL_INTEGRITY_UID:
        errors.append("release channel integrity uid mismatch")
    observed = [str(row.get("channel") or "") for row in channels]
    if observed != [row["channel"] for row in CHANNELS]:
        errors.append("release channel order does not match the canonical X4M1 contract")
    for row in channels:
        required_fields = [str(item).strip() for item in list(row.get("required_receipt_fields") or []) if str(item).strip()]
        if not required_fields:
            errors.append(f"{row.get('channel')}: missing required_receipt_fields")
        lineage_keys = [str(item).strip() for item in list(row.get("minimum_lineage_keys") or []) if str(item).strip()]
        if not lineage_keys:
            errors.append(f"{row.get('channel')}: missing minimum_lineage_keys")
        if not str(row.get("publication_state") or "").strip():
            errors.append(f"{row.get('channel')}: missing publication_state")
    return {
        "schema_version": "lab_assistant_release_channel_integrity_validation_v1",
        "uid": RELEASE_CHANNEL_INTEGRITY_UID,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }
