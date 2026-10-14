from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import (
    PipelineError,
    build_envelope,
    emit_pipeline_error,
    get_repo_git_info,
    load_json,
    load_pipeline_config,
    normalize_repo_relative_path,
    save_json_atomic,
    utc_now_iso,
)
from infra.pipeline.intake_expansion import expand_intake_assessment

STAGE_KEY = "script_1_job_intake"

_NON_PATH_PATTERNS = [
    re.compile(r"^\s*#+\s+"),
    re.compile(
        r"^\s*[-*]\s*(tool_name|category|tier|determinism|failure_semantics|cache_policy|truth_boundary|audit_artifact|inputs|outputs|guarantees|non_guarantees|security_or_safety_notes|test_plan|example_invocation|example_output_envelope)\s*:",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(tool_name|category|tier|determinism|failure_semantics|cache_policy|truth_boundary|audit_artifact|inputs|outputs|guarantees|non_guarantees|security_or_safety_notes|test_plan|example_invocation|example_output_envelope)\s*:",
        re.IGNORECASE,
    ),
]

_PATHLIKE_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.-]+")
_FILELIKE_RE = re.compile(r"(?<![A-Za-z0-9_./-])[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+")
_EXCLUSIVE_SCOPE_PHRASES = (
    "only modify",
    "only edit",
    "only touch",
    "modify only",
    "edit only",
    "touch only",
    "keep changes in",
    "limit changes to",
    "confine changes to",
)
_EDIT_VERB_RE = re.compile(r"\b(edit|modify|update|change|improve|refactor|adjust|revise)\b", re.IGNORECASE)


def _looks_like_repo_path(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if len(value) > 220:
        return False
    if any(p.search(value) for p in _NON_PATH_PATTERNS):
        return False
    if value.startswith("python ") or value.startswith("python -m "):
        return False
    if "://" in value:
        return False
    if "\n" in value or "\r" in value:
        return False
    if value.count(" ") > 2:
        return False
    if "/" not in value and "." not in Path(value).name:
        return False
    return True


def _dedupe_strings(values: List[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _prompt_sections(raw: Mapping[str, Any]) -> List[str]:
    return [
        str(raw.get("title") or ""),
        str(raw.get("description") or ""),
        str(raw.get("objective") or ""),
        str(raw.get("user_notes") or ""),
    ]


def _looks_like_test_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    if not normalized:
        return False
    basename = Path(normalized).name
    return basename.startswith("test_") or normalized.startswith("tests/") or "/tests/" in f"/{normalized}"


def _text_requests_test_edits(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    mentions_tests = any(token in lowered for token in [" test", " tests", "test ", "tests/", "test_", "testing"])
    requests_change = any(token in lowered for token in ["add ", "update ", "modify ", "change ", "create ", "write "])
    soft_conditional = any(phrase in lowered for phrase in ["if needed", "if necessary", "if required", "where needed"])
    strong_specificity = any(
        phrase in lowered
        for phrase in [
            "to prove",
            "to cover",
            "covering",
            "add tests for",
            "update tests for",
            "add or update tests",
            "exact tests",
            "new test",
            "new tests",
        ]
    )
    if soft_conditional and not strong_specificity:
        return False
    return bool(mentions_tests and requests_change)


def _extract_explicit_prompt_paths(repo_path: Path, sections: List[str]) -> List[str]:
    candidates: List[str] = []
    for text in sections:
        raw_text = str(text or "")
        for match in _PATHLIKE_RE.finditer(raw_text):
            candidates.append(match.group(0))
        for match in _FILELIKE_RE.finditer(raw_text):
            candidates.append(match.group(0))
    normalized: List[str] = []
    for raw in candidates:
        hint = str(raw or "").strip().strip("`'\"()[]{}").rstrip(".,:;")
        if not _looks_like_repo_path(hint):
            continue
        try:
            rel = normalize_repo_relative_path(str(repo_path), hint)
        except Exception:
            continue
        if (repo_path / rel).exists():
            normalized.append(rel)
    return _dedupe_strings(normalized)


def _infer_targeted_allowed_paths(repo_path: Path, sections: List[str], explicit_paths: List[str]) -> List[str]:
    if not explicit_paths:
        return []
    combined = " ".join(str(text or "") for text in sections)
    lowered = combined.lower()
    explicit_test_request = _text_requests_test_edits(lowered)
    mentions_tests = any(token in lowered for token in [" test", " tests", "test ", "tests/", "test_", "testing"])
    if any(phrase in lowered for phrase in _EXCLUSIVE_SCOPE_PHRASES):
        return list(explicit_paths)
    targeted: List[str] = []
    if not _EDIT_VERB_RE.search(combined):
        return []
    for path in explicit_paths:
        path_lower = path.lower()
        basename = Path(path).name.lower()
        patterns = [
            rf"\b(?:edit|modify|update|change|improve|refactor|adjust|revise)\s+(?:the\s+file\s+)?[`'\"]?{re.escape(path_lower)}",
            rf"\b(?:edit|modify|update|change|improve|refactor|adjust|revise)\s+(?:the\s+file\s+)?[`'\"]?{re.escape(basename)}",
        ]
        if any(re.search(pattern, lowered) for pattern in patterns):
            targeted.append(path)
    targeted = _dedupe_strings(targeted)
    if not targeted:
        return []
    if mentions_tests and not any(_looks_like_test_path(path) for path in targeted):
        return []
    if explicit_test_request and len(targeted) == 1 and not _looks_like_test_path(targeted[0]):
        return []
    return targeted



def _looks_low_information_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"new job", "todo", "unknown", "n/a"}:
        return True
    if len(text) < 12 and re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return True
    words = re.findall(r"[A-Za-z0-9_]+", text)
    if len(words) <= 2 and all(len(w) <= 12 for w in words):
        return True
    return False



def _first_sentence(text: str, max_len: int = 220) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    m = re.search(r"(.+?[.!?])(?:\s|$)", raw, re.S)
    sentence = (m.group(1) if m else raw).strip()
    return sentence[:max_len]


def _normalize_path_list(repo_root: str, values: Any) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise PipelineError("input_validation_error", "Path policy fields must be arrays", details={"value": values}, exit_code=3)
    normalized: List[str] = []
    invalid_examples: List[str] = []
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        if not _looks_like_repo_path(raw):
            invalid_examples.append(raw[:120])
            continue
        try:
            normalized.append(normalize_repo_relative_path(repo_root, raw))
        except Exception:
            invalid_examples.append(raw[:120])
            continue
    if values and not normalized and invalid_examples:
        raise PipelineError(
            "input_validation_error",
            "Path policy fields contained no valid repo-relative paths",
            details={"invalid_examples": invalid_examples[:8]},
            exit_code=3,
        )
    return _dedupe_strings(normalized)



def build_job_contract(raw: Mapping[str, Any], *, repo_root: str, config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    title = str(raw.get("title") or "").strip()
    description = str(raw.get("description") or "").strip()
    objective = str(raw.get("objective") or "").strip()
    user_notes = str(raw.get("user_notes") or "").strip()
    if _looks_low_information_text(description) and user_notes:
        description = user_notes
    if _looks_low_information_text(objective) and user_notes:
        objective = _first_sentence(user_notes)
    if not any([title, description, objective, user_notes]):
        raise PipelineError("input_validation_error", "title, description, objective, and user_notes cannot all be empty", exit_code=3)

    repo_root_value = str(raw.get("repo_root") or repo_root).strip()
    repo_path = Path(repo_root_value).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise PipelineError("invalid_repository", "repo_root does not exist or is not a directory", details={"repo_root": str(repo_path)}, exit_code=4)

    sections = _prompt_sections(raw)
    inferred_explicit_paths = _extract_explicit_prompt_paths(repo_path, sections)
    inferred_targeted_paths = _infer_targeted_allowed_paths(repo_path, sections, inferred_explicit_paths)

    allowed_paths = _normalize_path_list(str(repo_path), raw.get("allowed_paths"))
    forbidden_paths = _normalize_path_list(str(repo_path), raw.get("forbidden_paths"))
    if not allowed_paths and inferred_targeted_paths:
        allowed_paths = list(inferred_targeted_paths)
    overlap = sorted(set(allowed_paths).intersection(set(forbidden_paths)))
    if overlap:
        raise PipelineError("conflicting_path_policies", "allowed_paths and forbidden_paths overlap", details={"paths": overlap}, exit_code=6)

    changed_files_hint = _normalize_path_list(str(repo_path), list(raw.get("changed_files_hint") or []))
    changed_files_hint = _dedupe_strings(changed_files_hint + inferred_explicit_paths)

    constraints = _dedupe_strings(list(raw.get("constraints") or []))
    acceptance = _dedupe_strings(list(raw.get("acceptance_criteria") or []))
    task_type_hint = str(raw.get("task_type_hint") or "unknown").strip() or "unknown"
    git_info = get_repo_git_info(repo_path)
    job_contract = {
        "job_id": str(raw.get("job_id") or os.environ.get("PIPELINE_JOB_ID") or ""),
        "title": title or (description[:120] if description else objective[:120]),
        "description": description or objective or title,
        "repo_root": str(repo_path),
        "branch": str(raw.get("branch") or git_info.get("branch") or "") or None,
        "objective": objective or description or title,
        "task_type_hint": task_type_hint,
        "issue_context": {
            "ticket_id": raw.get("ticket_id"),
            "stack_trace": raw.get("stack_trace"),
            "failing_tests": list(raw.get("failing_tests") or []),
            "user_notes": raw.get("user_notes"),
            "changed_files_hint": changed_files_hint,
        },
        "constraints": constraints,
        "forbidden_paths": forbidden_paths,
        "allowed_paths": allowed_paths,
        "acceptance_criteria": acceptance,
        "preferred_languages": list(raw.get("preferred_languages") or []),
        "test_commands": list(raw.get("test_commands") or []),
        "metadata": dict(raw.get("metadata") or {}),
    }
    intake_assessment = expand_intake_assessment(raw, job_contract, repo_root=str(repo_path), config=config)
    return {
        "job_contract": job_contract,
        "intake_assessment": intake_assessment,
        "repo_info": git_info,
        "normalized_fields": {
            "inferred_task_type_hint": task_type_hint,
            "path_policy_mode": "allowlist" if allowed_paths else ("denylist" if forbidden_paths else "unrestricted"),
        },
    }



def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MVP-1 job intake")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        raw = load_json(args.input)
        job_id = str(raw.get("job_id") or os.environ.get("PIPELINE_JOB_ID") or "").strip()
        run_id = str(raw.get("run_id") or os.environ.get("PIPELINE_RUN_ID") or "").strip()
        if not job_id or not run_id:
            raise PipelineError("missing_runtime_ids", "job_id and run_id must be available for stage execution", exit_code=3)
        result = build_job_contract(raw, repo_root=args.repo_root, config=config)
        result["job_contract"]["job_id"] = job_id
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=job_id,
            run_id=run_id,
            input_payload=raw,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
