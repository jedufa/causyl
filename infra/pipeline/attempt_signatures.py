from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .core import normalize_repo_relative_path, sha256_payload

_PATCH_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
_PY_SYMBOL_RE = re.compile(r"^[ +\-]\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_path(repo_root: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        normalized = normalize_repo_relative_path(repo_root, raw)
    except Exception:
        normalized = raw.replace("\\", "/").strip("/")
    return normalized.lower()


def canonical_file_set(*, repo_root: str, values: Sequence[Any]) -> List[str]:
    normalized = [_normalize_path(repo_root, value) for value in values]
    return sorted(_dedupe_strings(item for item in normalized if item))


def extract_patch_targets(*, repo_root: str, patch_text: str) -> List[Dict[str, str]]:
    current_file = ""
    targets: List[Dict[str, str]] = []
    seen = set()
    for raw_line in str(patch_text or "").splitlines():
        file_match = _PATCH_FILE_RE.match(raw_line)
        if file_match:
            current_file = _normalize_path(repo_root, file_match.group(1))
            continue
        if not current_file:
            continue
        symbol_match = _PY_SYMBOL_RE.match(raw_line)
        if not symbol_match:
            continue
        symbol = str(symbol_match.group(1) or "").strip().lower()
        if not symbol:
            continue
        key = (current_file, symbol)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"file": current_file, "symbol": symbol})
    return sorted(targets, key=lambda item: (item["file"], item["symbol"]))


def build_normalized_attempt_signature(
    *,
    repo_root: str,
    branch_purpose: str,
    mutation_level: int,
    failure_signature: Mapping[str, Any],
    patch_artifact: Mapping[str, Any],
    apply_result: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    file_candidates: List[Any] = []
    file_candidates.extend(list(patch_artifact.get("target_files") or []))
    file_candidates.extend(list(apply_result.get("changed_files") or []))
    file_candidates.extend(list(execution_contract.get("allowed_edit_files") or []))
    file_candidates.extend(list(execution_contract.get("validation_target_files") or []))
    patch_text = str(patch_artifact.get("content") or "")
    signature = {
        "canonical_file_set": canonical_file_set(repo_root=repo_root, values=file_candidates),
        "canonical_patch_targets": extract_patch_targets(repo_root=repo_root, patch_text=patch_text),
        "normalized_failure_signature": dict(failure_signature or {}),
        "branch_purpose": str(branch_purpose or "").strip() or "primary",
        "mutation_level": max(1, int(mutation_level or 1)),
    }
    signature["signature_hash"] = sha256_payload(signature)
    return signature


def attempt_signatures_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    comparable_left = {
        "canonical_file_set": list(left.get("canonical_file_set") or []),
        "canonical_patch_targets": list(left.get("canonical_patch_targets") or []),
        "normalized_failure_signature": dict(left.get("normalized_failure_signature") or {}),
        "branch_purpose": str(left.get("branch_purpose") or ""),
        "mutation_level": int(left.get("mutation_level") or 0),
    }
    comparable_right = {
        "canonical_file_set": list(right.get("canonical_file_set") or []),
        "canonical_patch_targets": list(right.get("canonical_patch_targets") or []),
        "normalized_failure_signature": dict(right.get("normalized_failure_signature") or {}),
        "branch_purpose": str(right.get("branch_purpose") or ""),
        "mutation_level": int(right.get("mutation_level") or 0),
    }
    return comparable_left == comparable_right
