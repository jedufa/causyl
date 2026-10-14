from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .core import normalize_repo_relative_path, sha256_payload, sha256_text

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{3,}")
_STOP_TOKENS = {
    "artifact",
    "artifacts",
    "branch",
    "because",
    "contract",
    "detail",
    "details",
    "failed",
    "failure",
    "from",
    "job",
    "jobs",
    "json",
    "lineage",
    "message",
    "patch",
    "recorded",
    "run",
    "script",
    "stage",
    "status",
}
_VOLATILE_TEXT_PATTERNS = [
    re.compile(r"\bRUN-[A-Za-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\bJOB-[A-Za-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\bBRANCH-[A-Za-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\bART-[A-Za-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\b\d{8}-\d{6}\b"),
]


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


def _tokenize(values: Sequence[Any]) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for value in values:
        sanitized = _sanitize_token_source(value)
        for token in _TOKEN_RE.findall(sanitized):
            if token in _STOP_TOKENS or _is_volatile_token(token) or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return sorted(tokens)


def _sanitize_token_source(value: Any) -> str:
    text = str(value or "")
    for pattern in _VOLATILE_TEXT_PATTERNS:
        text = pattern.sub(" ", text)
    return text.lower()


def _is_volatile_token(token: str) -> bool:
    text = str(token or "").strip().lower()
    if not text:
        return True
    if text.isdigit() and len(text) >= 6:
        return True
    if re.fullmatch(r"[0-9a-f]{6,}", text):
        return True
    if len(text) >= 6 and sum(ch.isdigit() for ch in text) >= 4:
        return True
    return False


def _jaccard(left: Sequence[Any], right: Sequence[Any]) -> float:
    left_set = {str(item) for item in list(left or []) if str(item).strip()}
    right_set = {str(item) for item in list(right or []) if str(item).strip()}
    if not left_set and not right_set:
        return 1.0
    union = left_set | right_set
    if not union:
        return 1.0
    return len(left_set & right_set) / float(len(union))


def build_failure_signature(
    *,
    repo_root: str,
    primary_hypothesis: str,
    failure_summary: str,
    blocking_reason_codes: Sequence[Any],
    feedback_bundle: Mapping[str, Any],
    apply_result: Mapping[str, Any],
    diagnostics: Sequence[Any],
) -> Dict[str, Any]:
    affected_files = [_normalize_path(repo_root, item) for item in list(feedback_bundle.get("affected_files") or [])]
    if not any(affected_files):
        affected_files = [_normalize_path(repo_root, item) for item in list(apply_result.get("changed_files") or [])]
    failing_tests = []
    for item in list(feedback_bundle.get("failing_tests") or []):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            failing_tests.append(name)
        for test_name in list(item.get("failing_tests") or []):
            text = str(test_name or "").strip()
            if text:
                failing_tests.append(text)
    token_sources: List[Any] = [failure_summary, *list(blocking_reason_codes or [])]
    token_sources.extend(list(feedback_bundle.get("excerpts") or []))
    token_sources.extend(list(diagnostics or []))
    key_error_tokens = _tokenize(token_sources)[:24]
    normalized_message_hash = sha256_text(
        "|".join(
            [
                str(primary_hypothesis or "").strip().lower(),
                ",".join(sorted(_dedupe_strings(item for item in affected_files if item))),
                ",".join(sorted(_dedupe_strings(failing_tests))),
                ",".join(key_error_tokens),
            ]
        )
    )
    return {
        "error_type": str(primary_hypothesis or "").strip() or "unknown_failure",
        "affected_files": sorted(_dedupe_strings(item for item in affected_files if item)),
        "failing_tests": sorted(_dedupe_strings(failing_tests)),
        "key_error_tokens": key_error_tokens,
        "normalized_message_hash": normalized_message_hash,
    }


def similarity_score(
    current: Mapping[str, Any],
    prior: Mapping[str, Any],
    *,
    weights: Mapping[str, Any],
) -> float:
    error_type_score = 1.0 if str(current.get("error_type") or "") == str(prior.get("error_type") or "") else 0.0
    file_overlap_score = _jaccard(current.get("affected_files") or [], prior.get("affected_files") or [])
    test_overlap_score = _jaccard(current.get("failing_tests") or [], prior.get("failing_tests") or [])
    token_overlap_score = _jaccard(current.get("key_error_tokens") or [], prior.get("key_error_tokens") or [])
    message_score = 1.0 if str(current.get("normalized_message_hash") or "") == str(prior.get("normalized_message_hash") or "") else 0.0
    return (
        float(weights.get("error_type") or 0.0) * error_type_score
        + float(weights.get("file_overlap") or 0.0) * file_overlap_score
        + float(weights.get("test_overlap") or 0.0) * test_overlap_score
        + float(weights.get("token_overlap") or 0.0) * token_overlap_score
        + float(weights.get("message") or 0.0) * message_score
    )


def find_similar_failures(
    *,
    current_signature: Mapping[str, Any],
    prior_attempts: Sequence[Mapping[str, Any]],
    weights: Mapping[str, Any],
    similarity_threshold: float,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for attempt in list(prior_attempts or []):
        prior_signature = dict(attempt.get("failure_signature") or {})
        if not prior_signature:
            continue
        score = similarity_score(current_signature, prior_signature, weights=weights)
        if score + 1e-9 < float(similarity_threshold):
            continue
        matches.append(
            {
                "run_id": str(attempt.get("run_id") or ""),
                "branch_id": str(attempt.get("branch_id") or ""),
                "cluster_id": str(attempt.get("failure_cluster_id") or ""),
                "score": score,
                "strategy": str(attempt.get("strategy") or ""),
            }
        )
    return sorted(matches, key=lambda item: (-float(item.get("score") or 0.0), str(item.get("run_id") or "")))


def failure_cluster_id(signature: Mapping[str, Any]) -> str:
    stable = {
        "error_type": str(signature.get("error_type") or ""),
        "affected_files": list(signature.get("affected_files") or []),
        "failing_tests": list(signature.get("failing_tests") or []),
        "key_error_tokens": list(signature.get("key_error_tokens") or [])[:8],
    }
    return f"CLUSTER-{sha256_payload(stable)[:12]}"
