from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import re

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.artifact_lineage import build_artifact_lineage
from infra.pipeline.artifact_normalization import normalize_patch_artifact
from infra.pipeline.diff_utils import repair_unified_diff_bare_hunks, repair_unified_diff_hunk_headers
from infra.pipeline.core import PipelineError, build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, save_text_atomic, utc_now_iso
from infra.pipeline.model_runtime import resolve_stage8_runtime
from infra.pipeline.schemas import (
    validate_execution_contract_payload,
    validate_patch_generate_result,
    validate_patch_plan_result,
    validate_stage_input,
)

STAGE_KEY = "script_8_patch_generate"
FIXTURE_ENV = "PIPELINE_STAGE8_PATCH_RESPONSE_FILE"
EXPLICIT_EXECUTION_MODE_ENV_KEYS = (
    "PIPELINE_STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE",
    "PIPELINE__STAGES__SCRIPT_8_PATCH_GENERATE__EXECUTION_MODE",
)


def count_tokens_from_messages(messages: Sequence[Mapping[str, Any]], model: str) -> Optional[int]:
    enc = None
    try:
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.encoding_for_model(str(model or "").strip())
        except Exception:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                enc = None
    except Exception:
        enc = None
    tokens = 0
    for msg in messages:
        tokens += 4
        content = str(msg.get("content") or "")
        if enc is not None:
            tokens += len(enc.encode(content))
        else:
            tokens += max(1, len(re.findall(r"\w+|[^\w\s]", content, re.UNICODE)))
    tokens += 2
    return int(tokens)


def _splitlines_for_patch(text: str) -> Tuple[List[str], bool]:
    if not text:
        return ([], False)
    return (text.splitlines(), text.endswith("\n"))


def _strip_prefixed_line(line: str) -> Tuple[str, str]:
    if not line:
        raise PipelineError("invalid_patch_artifact", "apply_patch line must not be empty", exit_code=11)
    prefix = line[0]
    if prefix not in {" ", "+", "-"}:
        raise PipelineError("invalid_patch_artifact", "apply_patch line has invalid prefix", details={"line": line}, exit_code=11)
    return prefix, line[1:]


def _parse_hunk_header(line: str) -> Optional[int]:
    if not line.startswith("@@"):
        return None
    parts = line.split()
    for token in parts:
        if token.startswith("-"):
            number = token[1:].split(",", 1)[0].strip()
            if number.isdigit():
                value = int(number)
                return max(0, value - 1)
    return None


def _find_hunk_start(old_lines: Sequence[str], cursor: int, hunk_lines: Sequence[str], hinted_start: Optional[int]) -> int:
    anchor: List[str] = []
    for entry in hunk_lines:
        prefix, payload = _strip_prefixed_line(entry)
        if prefix in {" ", "-"}:
            anchor.append(payload)
    if hinted_start is not None and 0 <= hinted_start <= len(old_lines):
        if old_lines[hinted_start : hinted_start + len(anchor)] == list(anchor):
            return hinted_start
    if not anchor:
        return cursor
    limit = len(old_lines) - len(anchor) + 1
    for idx in range(cursor, max(cursor, limit)):
        if old_lines[idx : idx + len(anchor)] == list(anchor):
            return idx
    first_anchor = anchor[0]
    for idx in range(cursor, len(old_lines)):
        if old_lines[idx] == first_anchor:
            return idx
    raise PipelineError("malformed_diff", "apply_patch hunk could not be aligned to source file", exit_code=11)


def _apply_update_hunks(*, original_text: str, hunks: Sequence[Tuple[Optional[int], List[str]]]) -> str:
    old_lines, had_trailing_newline = _splitlines_for_patch(original_text)
    new_lines: List[str] = []
    cursor = 0
    for hinted_start, hunk_lines in hunks:
        hunk_start = _find_hunk_start(old_lines, cursor, hunk_lines, hinted_start)
        new_lines.extend(old_lines[cursor:hunk_start])
        scan = hunk_start
        for entry in hunk_lines:
            prefix, payload = _strip_prefixed_line(entry)
            if prefix == " ":
                if scan >= len(old_lines) or old_lines[scan] != payload:
                    try:
                        found_at = old_lines.index(payload, scan)
                    except ValueError as exc:
                        raise PipelineError("malformed_diff", "apply_patch context line does not match source file", details={"line": entry}, exit_code=11) from exc
                    new_lines.extend(old_lines[scan:found_at])
                    scan = found_at
                new_lines.append(payload)
                scan += 1
            elif prefix == "-":
                if scan >= len(old_lines) or old_lines[scan] != payload:
                    try:
                        found_at = old_lines.index(payload, scan)
                    except ValueError as exc:
                        raise PipelineError("malformed_diff", "apply_patch deletion line does not match source file", details={"line": entry}, exit_code=11) from exc
                    new_lines.extend(old_lines[scan:found_at])
                    scan = found_at
                scan += 1
            else:
                new_lines.append(payload)
        cursor = scan
    new_lines.extend(old_lines[cursor:])
    rebuilt = "\n".join(new_lines)
    if new_lines and had_trailing_newline:
        rebuilt += "\n"
    return rebuilt


def _to_unified_diff(*, path_before: str, path_after: str, before_text: str, after_text: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{path_before}" if path_before != "/dev/null" else "/dev/null",
            tofile=f"b/{path_after}" if path_after != "/dev/null" else "/dev/null",
            lineterm="",
        )
    )
    return "\n".join(diff_lines) + ("\n" if diff_lines else "")


def _apply_hunks_via_snippets(
    *,
    before_text: str,
    hunks: Sequence[Tuple[Optional[int], List[str]]],
    snippets: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    before_lines, before_had_trailing_newline = _splitlines_for_patch(before_text)
    for snippet in snippets:
        snippet_text = str(snippet.get("content") or "")
        if not snippet_text:
            continue
        start_line = int(snippet.get("start_line") or 1)
        end_line = int(snippet.get("end_line") or start_line)
        start_idx = max(0, start_line - 1)
        end_idx = max(start_idx, end_line)
        snippet_lines, _ = _splitlines_for_patch(snippet_text)
        if before_lines[start_idx:end_idx] != snippet_lines:
            continue
        try:
            updated_snippet = _apply_update_hunks(original_text=snippet_text, hunks=hunks)
        except PipelineError:
            continue
        updated_lines, _ = _splitlines_for_patch(updated_snippet)
        rebuilt_lines = list(before_lines[:start_idx]) + list(updated_lines) + list(before_lines[end_idx:])
        rebuilt = "\n".join(rebuilt_lines)
        if rebuilt_lines and before_had_trailing_newline:
            rebuilt += "\n"
        return rebuilt
    return None




def _extract_patch_candidate(raw_text: str) -> str:
    text = str(raw_text or "")
    if not text.strip():
        raise PipelineError("empty_model_output", "patch generation output must not be empty", exit_code=11)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    apply_matches = list(re.finditer(r"(?ms)^\*\*\* Begin Patch\n.*?^\*\*\* End Patch\s*$", normalized))
    if len(apply_matches) > 1:
        raise PipelineError("ambiguous_patch_output", "multiple apply_patch candidates detected", exit_code=11)
    if len(apply_matches) == 1:
        return apply_matches[0].group(0).strip() + "\n"
    diff_headers = list(re.finditer(r"(?m)^diff --git ", normalized))
    if len(diff_headers) > 1:
        raise PipelineError("ambiguous_patch_output", "multiple unified diff candidates detected", exit_code=11)
    if len(diff_headers) == 1:
        return normalized[diff_headers[0].start():].lstrip()
    triple_header = re.search(r"(?m)^--- (?:a/|/dev/null).+?^\+\+\+ (?:b/|/dev/null)", normalized, re.DOTALL)
    if triple_header:
        return normalized[triple_header.start():].lstrip()
    return normalized

def _convert_apply_patch_to_unified_diff(raw_patch: str, *, repo_root: str, generation_files: Optional[Sequence[Mapping[str, Any]]] = None) -> Optional[str]:
    text = str(raw_patch or "")
    if not text.lstrip().startswith("*** Begin Patch"):
        return None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PipelineError("invalid_patch_artifact", "apply_patch artifact must begin with *** Begin Patch", exit_code=11)
    idx = 1
    sections: List[str] = []
    repo = Path(repo_root)
    generation_file_map = {
        str(entry.get("path") or "").strip(): dict(entry)
        for entry in list(generation_files or [])
        if isinstance(entry, Mapping) and str(entry.get("path") or "").strip()
    }
    while idx < len(lines):
        line = lines[idx]
        if not line:
            idx += 1
            continue
        if line == "*** End Patch":
            break
        if line.startswith("*** Update File: "):
            old_path = str(line[len("*** Update File: ") :].strip())
            new_path = old_path
            idx += 1
            if idx < len(lines) and lines[idx].startswith("*** Move to: "):
                new_path = str(lines[idx][len("*** Move to: ") :].strip())
                idx += 1
            hunks: List[Tuple[Optional[int], List[str]]] = []
            current_hunk: List[str] = []
            current_hint: Optional[int] = None
            while idx < len(lines):
                current = lines[idx]
                if current in {"*** End Patch", "*** End of File"} or current.startswith("*** Update File: ") or current.startswith("*** Add File: ") or current.startswith("*** Delete File: "):
                    break
                if current.startswith("@@"):
                    if current_hunk:
                        hunks.append((current_hint, current_hunk))
                    current_hunk = []
                    current_hint = _parse_hunk_header(current)
                    idx += 1
                    continue
                if current and current[0] in {" ", "+", "-"}:
                    current_hunk.append(current)
                    idx += 1
                    continue
                raise PipelineError("invalid_patch_artifact", "Unexpected apply_patch content in update section", details={"line": current}, exit_code=11)
            if current_hunk:
                hunks.append((current_hint, current_hunk))
            source_path = repo / old_path
            before_text = source_path.read_text(encoding="utf-8")
            generation_entry = generation_file_map.get(old_path, {})
            generation_snippets = list(generation_entry.get("snippets") or [])
            generation_mode = str(generation_entry.get("include_mode") or "")
            if generation_mode == "snippets_only" and generation_snippets:
                snippet_fallback = _apply_hunks_via_snippets(
                    before_text=before_text,
                    hunks=hunks,
                    snippets=generation_snippets,
                )
                if snippet_fallback is not None:
                    after_text = snippet_fallback
                else:
                    after_text = _apply_update_hunks(original_text=before_text, hunks=hunks)
            else:
                try:
                    after_text = _apply_update_hunks(original_text=before_text, hunks=hunks)
                except PipelineError as exc:
                    snippet_fallback = _apply_hunks_via_snippets(
                        before_text=before_text,
                        hunks=hunks,
                        snippets=generation_snippets,
                    )
                    if snippet_fallback is None:
                        raise exc
                    after_text = snippet_fallback
            diff_text = _to_unified_diff(path_before=old_path, path_after=new_path, before_text=before_text, after_text=after_text)
            if not diff_text:
                raise PipelineError("empty_diff", "apply_patch update produced no diff", details={"path": old_path}, exit_code=11)
            sections.append(diff_text.rstrip("\n"))
            if idx < len(lines) and lines[idx] == "*** End of File":
                idx += 1
            continue
        if line.startswith("*** Add File: "):
            new_path = str(line[len("*** Add File: ") :].strip())
            idx += 1
            added_lines: List[str] = []
            while idx < len(lines):
                current = lines[idx]
                if current in {"*** End Patch", "*** End of File"} or current.startswith("*** Update File: ") or current.startswith("*** Add File: ") or current.startswith("*** Delete File: "):
                    break
                if not current.startswith("+"):
                    raise PipelineError("invalid_patch_artifact", "Add File section may only contain added lines", details={"line": current}, exit_code=11)
                added_lines.append(current[1:])
                idx += 1
            after_text = "\n".join(added_lines) + ("\n" if added_lines else "")
            diff_text = _to_unified_diff(path_before="/dev/null", path_after=new_path, before_text="", after_text=after_text)
            if not diff_text:
                raise PipelineError("empty_diff", "apply_patch add produced no diff", details={"path": new_path}, exit_code=11)
            sections.append(diff_text.rstrip("\n"))
            if idx < len(lines) and lines[idx] == "*** End of File":
                idx += 1
            continue
        if line.startswith("*** Delete File: "):
            old_path = str(line[len("*** Delete File: ") :].strip())
            idx += 1
            before_text = (repo / old_path).read_text(encoding="utf-8")
            diff_text = _to_unified_diff(path_before=old_path, path_after="/dev/null", before_text=before_text, after_text="")
            if not diff_text:
                raise PipelineError("empty_diff", "apply_patch delete produced no diff", details={"path": old_path}, exit_code=11)
            sections.append(diff_text.rstrip("\n"))
            continue
        raise PipelineError("invalid_patch_artifact", "Unrecognized apply_patch section header", details={"line": line}, exit_code=11)
    if not sections:
        raise PipelineError("empty_diff", "apply_patch artifact did not contain any file sections", exit_code=11)
    return "\n".join(sections) + "\n"


class OpenAIPatchGenerator:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: int,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        self.provider = str(provider or "").strip().lower()
        self.model = str(model or "").strip()
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_seconds = int(timeout_seconds)
        self.reasoning_effort = str(reasoning_effort or "").strip() or None
        if self.provider not in {"openai", "deepseek"}:
            raise PipelineError("unsupported_patch_provider", f"unsupported patch generation provider: {self.provider}", exit_code=4)
        if not self.model:
            raise PipelineError("missing_patch_model", "model name is required for patch generation", exit_code=4)

    def _supports_temperature(self) -> bool:
        model = self.model.strip().lower()
        if not model:
            return False
        if model.startswith("gpt-5") or "codex" in model:
            return False
        return True

    def generate(self, prompt: str) -> Dict[str, Any]:
        api_key_env = "DEEPSEEK_API_KEY" if self.provider == "deepseek" else "OPENAI_API_KEY"
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise PipelineError("missing_provider_credentials", f"{api_key_env} is required for patch generation", exit_code=4)
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise PipelineError("missing_provider_dependency", "openai package is unavailable", exit_code=4) from exc

        started = dt.datetime.now(dt.timezone.utc)
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if self.provider == "deepseek":
            client_kwargs["base_url"] = str(os.environ.get("EIEL_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
        client = OpenAI(**client_kwargs)
        try:
            if self.provider == "deepseek":
                request_kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.max_output_tokens,
                    "timeout": self.timeout_seconds,
                }
                if self._supports_temperature():
                    request_kwargs["temperature"] = self.temperature
                response = client.chat.completions.create(**request_kwargs)
                choices = list(getattr(response, "choices", []) or [])
                message = getattr(choices[0], "message", None) if choices else None
                text = getattr(message, "content", "") or ""
                finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
                output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
                total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
            else:
                request_kwargs = {
                    "model": self.model,
                    "input": prompt,
                    "max_output_tokens": self.max_output_tokens,
                    "timeout": self.timeout_seconds,
                }
                if self.reasoning_effort:
                    request_kwargs["reasoning"] = {"effort": self.reasoning_effort}
                if self._supports_temperature():
                    request_kwargs["temperature"] = self.temperature
                response = client.responses.create(**request_kwargs)
                text = getattr(response, "output_text", "") or ""
                finish_reason = getattr(response, "finish_reason", None)
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
                output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
                total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
        except Exception as exc:
            message = str(exc).lower()
            source = "timeout" if "timeout" in message else "provider"
            raise PipelineError("runtime_execution_error", f"provider call failed: {exc}", details={"failure_source": source}, exit_code=7) from exc
        finished = dt.datetime.now(dt.timezone.utc)
        if not text.strip():
            raise PipelineError("empty_patch_output", "provider returned empty patch output", exit_code=11)
        return {
            "raw_text": text,
            "selected_model": getattr(response, "model", None) or self.model,
            "selected_agent_tech": "",
            "finish_reason": finish_reason,
            "usage_input_tokens": input_tokens,
            "usage_output_tokens": output_tokens,
            "usage_total_tokens": total_tokens,
            "duration_ms": max(0, int((finished - started).total_seconds() * 1000)),
            "response_id": getattr(response, "id", None),
        }


def _load_execution_contract(run_dir: Path) -> Dict[str, Any]:
    payload = load_json(run_dir / "07_execution_contract.json")
    validate_execution_contract_payload(payload)
    return payload


def _build_file_section(entry: Mapping[str, Any]) -> str:
    path = str(entry.get("path") or "")
    include_mode = str(entry.get("include_mode") or "reference_only")
    lines = [f"FILE: {path}", f"INCLUDE_MODE: {include_mode}"]
    if include_mode == "full_file":
        lines.extend(["CONTENT:", str(entry.get("content") or "")])
    elif include_mode == "snippets_only":
        snippets = list(entry.get("snippets") or [])
        if snippets:
            lines.append("SNIPPETS:")
        for idx, snippet in enumerate(snippets, start=1):
            start_line = int(snippet.get("start_line") or 1)
            end_line = int(snippet.get("end_line") or start_line)
            lines.append(f"[{idx}] lines {start_line}-{end_line}")
            lines.append(str(snippet.get("content") or ""))
    else:
        lines.append("REFERENCE_ONLY: true")
    return "\n".join(lines).rstrip()


def _extract_verification_claim(raw_text: str) -> Optional[Dict[str, Any]]:
    normalized = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    marker = "VERIFICATION_CLAIM_JSON:"
    patch_marker = "\nPATCH:\n"
    start = normalized.find(marker)
    if start < 0:
        return None
    json_start = start + len(marker)
    patch_idx = normalized.find(patch_marker, json_start)
    if patch_idx < 0:
        return None
    claim_text = normalized[json_start:patch_idx].strip()
    if not claim_text:
        return None
    try:
        payload = json.loads(claim_text)
    except Exception as exc:
        raise PipelineError("invalid_verification_claim", "verification claim JSON could not be parsed", details={"claim_text": claim_text[:240]}, exit_code=11) from exc
    if not isinstance(payload, Mapping):
        raise PipelineError("invalid_verification_claim", "verification claim must decode to a mapping", exit_code=11)
    return dict(payload)


def _extract_patch_body(raw_text: str) -> str:
    normalized = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    patch_marker = "\nPATCH:\n"
    patch_idx = normalized.find(patch_marker)
    if patch_idx >= 0:
        return normalized[patch_idx + len(patch_marker):].lstrip()
    return normalized


def _build_prompt(
    *,
    execution_contract: Mapping[str, Any],
    stage7_result: Mapping[str, Any],
) -> str:
    constraints = dict(execution_contract.get("constraints") or {})
    allowed_edit_files = [str(item) for item in list(execution_contract.get("allowed_edit_files") or []) if str(item).strip()]
    validation_target_files = [str(item) for item in list(execution_contract.get("validation_target_files") or []) if str(item).strip()]
    editable_validation_target_files = [path for path in validation_target_files if path in set(allowed_edit_files)]
    retry_contract = dict(stage7_result.get("retry_contract") or execution_contract.get("retry_contract") or {})
    prompt_sections = [
        "SYSTEM:\nReturn EXACTLY two sections and no extra prose: VERIFICATION_CLAIM_JSON followed by PATCH.",
        "OBJECTIVE + CONSTRAINTS:\n"
        + json.dumps(
            {
                "objective": str(execution_contract.get("objective") or ""),
                "acceptance_criteria": list(constraints.get("acceptance_criteria") or []),
                "required_tests": list(constraints.get("required_tests") or []),
                "must_not_change": list(constraints.get("must_not_change") or []),
                "assumptions": list(constraints.get("assumptions") or []),
                "unresolved_uncertainties": list(constraints.get("unresolved_uncertainties") or []),
                "execution_risks": list(constraints.get("execution_risks") or []),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
        "EDIT SCOPE:\n"
        + json.dumps(
            {
                "allowed_edit_files": list(execution_contract.get("allowed_edit_files") or []),
                "forbidden_edit_files": list(execution_contract.get("forbidden_edit_files") or []),
                "must_not_create_files": bool(execution_contract.get("must_not_create_files", False)),
                "may_create_files": bool(execution_contract.get("may_create_files", False)),
                "expected_new_files": list(execution_contract.get("expected_new_files") or []),
                "output_contract": dict(execution_contract.get("stage8_output_contract") or {}),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
        "TASK CONTEXT:\n"
        + json.dumps(
            {
                "job_title": str(stage7_result.get("job_contract", {}).get("title") or ""),
                "task_archetype": str(stage7_result.get("task_profile", {}).get("task_archetype") or ""),
                "output_contract_mode": str(stage7_result.get("patch_plan", {}).get("output_contract_mode") or ""),
                "implementation_target_files": list(execution_contract.get("implementation_target_files") or []),
                "validation_target_files": list(execution_contract.get("validation_target_files") or []),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
    ]
    if retry_contract:
        prompt_sections.append(
            "RETRY CONTRACT:\n"
            + json.dumps(
                {
                    "selected_strategy": str(retry_contract.get("selected_strategy") or ""),
                    "mutation_level": int(retry_contract.get("mutation_level") or 1),
                    "strategy_note": str(retry_contract.get("strategy_note") or ""),
                    "negative_constraints": list(retry_contract.get("negative_constraints") or []),
                    "upstream_mutations": list(retry_contract.get("upstream_mutations") or []),
                    "previous_failure_signature": dict(retry_contract.get("failure_signature") or {}),
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    if validation_target_files:
        prompt_sections.append(
            "VALIDATION CONTRACT:\n"
            + json.dumps(
                {
                    "validation_target_files": validation_target_files,
                    "editable_validation_target_files": editable_validation_target_files,
                    "must_update_validation_targets": bool(editable_validation_target_files),
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    generation_files = list(execution_contract.get("generation_files") or [])
    file_sections = [_build_file_section(entry) for entry in generation_files]
    prompt_sections.append("FILE CONTENT:\n" + "\n\n".join(section for section in file_sections if section))
    prompt_sections.append(
        "OUTPUT CONTRACT:\n"
        "- Return EXACTLY two sections in this order:\n"
        "  VERIFICATION_CLAIM_JSON:\n"
        "  <strict JSON object>\n"
        "  PATCH:\n"
        "  <valid unified diff>\n"
        "- The verification claim JSON must contain implementation_files, changed_test_files, new_or_updated_tests, and behavior_claims.\n"
        "- Each new_or_updated_tests entry must include path, name, and proves.\n"
        "- Only claim tests that are actually added or modified by the patch.\n"
        + (
            "- Because editable validation targets were provided, new_or_updated_tests must not be empty.\n"
            "- The PATCH must modify at least one file from editable_validation_target_files.\n"
            "- changed_test_files must include at least one file from editable_validation_target_files.\n"
            "- Every new_or_updated_tests.path must be inside validation_target_files.\n"
            if editable_validation_target_files else ""
        )
        +
        "- The PATCH section must contain ONLY a valid unified diff.\n"
        "- Every diff section MUST include a real unified-diff hunk header in the form @@ -old_start,old_count +new_start,new_count @@.\n"
        "- Do not include markdown fences, prose, or explanations.\n"
        "- Do not modify files outside allowed_edit_files.\n"
        "- Do not create files unless may_create_files is true and the path is listed in expected_new_files.\n"
        "- Do not introduce new parameters, exception types, validation branches, or behavior changes unless they are explicitly required by the objective or demanded by existing tests.\n"
        "- Keep the implementation as small as possible and avoid speculative hardening beyond the stated objective.\n"
        "- If a RETRY CONTRACT is present, you MUST satisfy its negative_constraints and avoid repeating the previous failure pattern.\n"
        "- Ensure every new or updated test assertion is consistent with the implementation in PATCH; mentally execute the named tests against your code before responding.\n"
        "- Do not add stronger behavioral expectations in tests than the objective or existing tests require.\n"
        "- When the objective describes chained normalization steps such as trim, collapse, lowercase, and truncate, ensure the final returned string still satisfies the stated contract after every step and does not leave accidental leading or trailing separator artifacts unless explicitly required.\n"
        "- When updating pytest-style test files with exception assertions, prefer pytest.raises(...) over manual try/except assertion patterns.\n"
        "- If the objective names exact test functions, add or update those exact test names.\n"
        "- Keep the patch bounded to the stated objective and constraints.\n"
        "- Example format:\n"
        "  VERIFICATION_CLAIM_JSON:\n"
        "  {\"implementation_files\":[\"app.py\"],\"changed_test_files\":[\"tests/test_app.py\"],\"new_or_updated_tests\":[{\"path\":\"tests/test_app.py\",\"name\":\"test_none_returns_empty_string\",\"proves\":\"None returns an empty string\"}],\"behavior_claims\":[\"normalize_text returns an empty string for None\"]}\n"
        "  PATCH:\n"
        "  --- a/app.py\n"
        "  +++ b/app.py\n"
        "  @@ -1,2 +1,4 @@\n"
        "   def normalize_text(s):\n"
        "  -    return s.strip()\n"
        "  +    if s is None:\n"
        "  +        return \"\"\n"
        "  +    return s.strip()"
    )
    return "\n\n".join(prompt_sections).rstrip() + "\n"


def _generation_block_reason(execution_contract: Mapping[str, Any]) -> Optional[str]:
    if not bool(execution_contract.get("generation_applicable", False)):
        return str(execution_contract.get("generation_blocked_reason") or "execution contract is not generation_applicable")
    if not list(execution_contract.get("allowed_edit_files") or []):
        return "execution contract did not authorize any allowed_edit_files"
    execution_mode = str(execution_contract.get("execution_mode") or "").strip().lower()
    if execution_mode in {"review_only", "repo_assessment", "retrieval_only", "design_contract_only"}:
        return f"execution contract mode {execution_mode} does not authorize generation"
    return None


def _failure_result(*, runtime: Mapping[str, Any], artifact_lineage: Mapping[str, Any], reason: str, failure_class: str, failure_source: str, execution_source: str = "live", retryable_hint: bool = False, raw_output_path: str = "", extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    metadata = {
        "result_kind": "generation_failed",
        "requested_model": runtime.get("requested_model"),
        "requested_agent_tech": runtime.get("requested_agent_tech"),
        "selected_model": runtime.get("selected_model"),
        "selected_agent_tech": runtime.get("selected_agent_tech"),
        "model": runtime.get("requested_model"),
        "agent_tech": runtime.get("requested_agent_tech") or runtime.get("selected_agent_tech"),
        "provider": runtime.get("provider"),
        "reasoning_effort": runtime.get("reasoning_effort"),
        "duration_ms": 0,
        "raw_output_path": raw_output_path,
        "output_format_requested": runtime.get("preferred_artifact"),
        "output_format_received": "failure",
        "selected_via": runtime.get("selected_via"),
        "execution_mode": runtime.get("execution_mode"),
        "execution_source": execution_source,
        "simulation_mode": execution_source == "fixture",
        "runtime_summary": runtime.get("runtime_summary"),
        "failure_class": failure_class,
        "failure_source": failure_source,
        "retryable_hint": bool(retryable_hint),
    }
    if extra:
        metadata.update(dict(extra))
    return {
        "generation_status": "generation_failed",
        "result_kind": "generation_failed",
        "generation_failed_reason": str(reason),
        "source_execution_contract_artifact": artifact_lineage["execution_contract"],
        "artifact_lineage": dict(artifact_lineage),
        "generation_metadata": metadata,
    }


def _estimated_token_extra(*, model: str, prompt: str, raw_text: str = "") -> Dict[str, Any]:
    estimated_input_tokens = count_tokens_from_messages(
        [{"role": "user", "content": prompt}],
        model,
    )
    estimated_output_tokens = count_tokens_from_messages(
        [{"role": "assistant", "content": raw_text}],
        model,
    )
    estimated_total_tokens = (
        int(estimated_input_tokens) + int(estimated_output_tokens)
        if estimated_input_tokens is not None and estimated_output_tokens is not None
        else None
    )
    return {
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_total_tokens": estimated_total_tokens,
    }


def _retry_prompt_for_empty_output(prompt: str, attempt_index: int) -> str:
    if attempt_index <= 0:
        return prompt
    return (
        str(prompt or "").rstrip()
        + "\n\nRETRY NOTE:\n"
        + f"Previous attempt {attempt_index} returned empty output. Return a non-empty VERIFICATION_CLAIM_JSON section and a non-empty PATCH section.\n"
    )


def _looks_retryable_invalid_model_output(raw_text: str, exc: PipelineError) -> bool:
    text = str(raw_text or "").strip()
    if not text:
        return False
    if exc.code not in {"invalid_verification_claim", "invalid_patch_artifact", "malformed_diff"}:
        return False
    has_patch_marker = "PATCH:" in text
    has_diff_marker = ("--- a/" in text and "+++ b/" in text) or "*** Begin Patch" in text
    unbalanced_json = text.count("{") > text.count("}")
    suspiciously_short = len(text) < 1200
    return bool((not has_patch_marker) or (not has_diff_marker) or unbalanced_json or suspiciously_short)


def _retry_prompt_for_invalid_output(prompt: str, attempt_index: int, exc: PipelineError) -> str:
    return (
        str(prompt or "").rstrip()
        + "\n\nRETRY NOTE:\n"
        + f"Previous attempt {attempt_index} returned incomplete or invalid output ({exc.code}). "
        + "Return a COMPLETE VERIFICATION_CLAIM_JSON object followed by a COMPLETE PATCH unified diff. "
        + "Do not stop after the JSON section and do not omit the PATCH section.\n"
    )


def _explicit_execution_mode_override_present() -> bool:
    return any(os.environ.get(env_key, "").strip() for env_key in EXPLICIT_EXECUTION_MODE_ENV_KEYS)


def _provisional_patch_artifact(
    *,
    provider_cfg: Mapping[str, Any],
    raw_patch: str,
    execution_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    fallback_targets = [
        str(path)
        for path in list(execution_contract.get("allowed_edit_files") or []) + list(execution_contract.get("expected_new_files") or [])
        if str(path or "").strip()
    ]
    return {
        "artifact_type": "unified_diff",
        "producer": {"provider": str(provider_cfg["provider"]), "model": str(provider_cfg["model"])},
        "target_files": fallback_targets,
        "content": str(raw_patch or ""),
    }


def _output_artifact_ref(path_value: str | Path) -> str:
    return Path(path_value).name


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execution MVP stage 8 patch generator")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        runtime = resolve_stage8_runtime(config)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract", "task_profile", "context_bundle", "patch_plan"])
        validate_patch_plan_result(env.result)
        run_dir = Path(args.input).resolve().parent
        execution_contract_payload = _load_execution_contract(run_dir)
        execution_contract = dict(execution_contract_payload.get("execution_contract") or {})
        artifact_lineage = build_artifact_lineage(additions={
            "patch_plan": _output_artifact_ref(args.input),
            "execution_contract": "07_execution_contract.json",
            "patch_artifact": _output_artifact_ref(args.output),
        })
        generation_blocked_reason = _generation_block_reason(execution_contract)
        if generation_blocked_reason:
            result = {
                "generation_status": "not_applicable_due_to_execution_contract",
                "result_kind": "generation_skipped",
                "generation_skipped": True,
                "generation_blocked_reason": generation_blocked_reason,
                "source_execution_contract_artifact": artifact_lineage["execution_contract"],
                "artifact_lineage": artifact_lineage,
            }
            validate_patch_generate_result(result)
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        prompt = _build_prompt(execution_contract=execution_contract, stage7_result=env.result)
        repo_root = str(env.result.get("job_contract", {}).get("repo_root") or "").strip()
        if not repo_root:
            raise PipelineError("missing_repo_root", "job_contract.repo_root is required for patch generation", exit_code=3)
        raw_output_path = run_dir / "08_patch_artifact.raw.txt"
        execution_mode = str(runtime.get("execution_mode") or "fixture")
        fixture_path = os.environ.get(FIXTURE_ENV, "").strip()
        if fixture_path and not _explicit_execution_mode_override_present():
            execution_mode = "fixture"
            runtime = {**runtime, "execution_mode": execution_mode}

        if execution_mode == "disabled":
            result = {
                "generation_status": "not_applicable_due_to_execution_contract",
                "result_kind": "generation_skipped",
                "generation_skipped": True,
                "generation_blocked_reason": "stage 8 execution_mode=disabled",
                "source_execution_contract_artifact": artifact_lineage["execution_contract"],
                "artifact_lineage": artifact_lineage,
            }
            validate_patch_generate_result(result)
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        raw_text = ""
        provider_result: Dict[str, Any] = {}
        execution_source = "live"
        generation_attempt_count = 0
        adapter: Optional[OpenAIPatchGenerator] = None
        token_extra = _estimated_token_extra(
            model=str(runtime.get("selected_model") or runtime.get("requested_model") or ""),
            prompt=prompt,
        )
        if execution_mode == "fixture":
            if not fixture_path:
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason="fixture mode requested but PIPELINE_STAGE8_PATCH_RESPONSE_FILE is missing", failure_class="runtime_execution_error", failure_source="fixture", execution_source="fixture", retryable_hint=False, extra=token_extra)
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0
            try:
                raw_text = Path(fixture_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"fixture file not found: {fixture_path}", failure_class="runtime_execution_error", failure_source="fixture", execution_source="fixture", retryable_hint=False, extra={**token_extra, "fixture_path": str(fixture_path)})
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0
            except OSError as exc:
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"fixture file unreadable: {exc}", failure_class="runtime_execution_error", failure_source="fixture", execution_source="fixture", retryable_hint=False, extra={**token_extra, "fixture_path": str(fixture_path)})
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0
            save_text_atomic(raw_output_path, raw_text)
            execution_source = "fixture"
            provider_result = {
                "raw_text": raw_text,
                "selected_model": runtime.get("selected_model"),
                "selected_agent_tech": runtime.get("selected_agent_tech"),
                "finish_reason": None,
                "usage_input_tokens": None,
                "usage_output_tokens": None,
                "usage_total_tokens": None,
                "duration_ms": 0,
                "response_id": None,
            }
        else:
            if not bool(runtime.get("allow_live_model_calls")):
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason="live model call requested but allow_live_model_calls is false", failure_class="policy_block", failure_source="policy", execution_source="live", retryable_hint=False, extra=token_extra)
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0
            adapter = OpenAIPatchGenerator(provider=str(runtime.get("provider")), model=str(runtime.get("selected_model")), temperature=float(runtime.get("temperature") or 0), max_output_tokens=int(runtime.get("max_output_tokens") or 4000), timeout_seconds=int(runtime.get("timeout_seconds") or 3000), reasoning_effort=runtime.get("reasoning_effort"))
            max_empty_output_retries = max(0, int(dict(config.get("_stage") or {}).get("empty_output_retry_limit") or 1))
            last_exc: Optional[PipelineError] = None
            for attempt_index in range(max_empty_output_retries + 1):
                generation_attempt_count = attempt_index + 1
                attempt_prompt = _retry_prompt_for_empty_output(prompt, attempt_index)
                try:
                    provider_result = adapter.generate(attempt_prompt)
                    break
                except PipelineError as exc:
                    last_exc = exc
                    if exc.code == "empty_patch_output" and attempt_index < max_empty_output_retries:
                        continue
                    failure_source = str(exc.details.get("failure_source") or ("credentials" if exc.code == "missing_provider_credentials" else "provider"))
                    result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"{exc.code}: {exc.message}", failure_class="runtime_execution_error" if exc.code != "unsupported_patch_provider" else "runtime_resolution_error", failure_source=failure_source, execution_source="live", retryable_hint=failure_source in {"provider", "timeout"} or exc.code == "empty_patch_output", raw_output_path=str(raw_output_path.resolve()), extra={**token_extra, "attempt_count": generation_attempt_count})
                    validate_patch_generate_result(result)
                    envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                    save_json_atomic(args.output, envelope)
                    return 0
            if last_exc is not None and not provider_result:
                failure_source = str(last_exc.details.get("failure_source") or ("credentials" if last_exc.code == "missing_provider_credentials" else "provider"))
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"{last_exc.code}: {last_exc.message}", failure_class="runtime_execution_error" if last_exc.code != "unsupported_patch_provider" else "runtime_resolution_error", failure_source=failure_source, execution_source="live", retryable_hint=failure_source in {"provider", "timeout"} or last_exc.code == "empty_patch_output", raw_output_path=str(raw_output_path.resolve()), extra={**token_extra, "attempt_count": generation_attempt_count})
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0
            raw_text = str(provider_result.get("raw_text") or "")
            save_text_atomic(raw_output_path, raw_text)
        token_extra = _estimated_token_extra(
            model=str(provider_result.get("selected_model") or runtime.get("selected_model") or runtime.get("requested_model") or ""),
            prompt=prompt,
            raw_text=raw_text,
        )

        allowed_scope = list(execution_contract.get("allowed_edit_files") or []) + list(execution_contract.get("expected_new_files") or [])
        if not allowed_scope:
            result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason="execution contract did not provide an allowed edit scope", failure_class="runtime_resolution_error", failure_source="config", execution_source=execution_source, retryable_hint=False, raw_output_path=str(raw_output_path.resolve()), extra=token_extra)
            validate_patch_generate_result(result)
            envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
            save_json_atomic(args.output, envelope)
            return 0

        max_invalid_output_retries = max(0, int(dict(config.get("_stage") or {}).get("invalid_output_retry_limit") or 1))
        invalid_output_retry_count = 0
        while True:
            try:
                verification_claim = _extract_verification_claim(raw_text)
                extracted_patch_text = _extract_patch_candidate(_extract_patch_body(raw_text))
                normalized_patch_text = _convert_apply_patch_to_unified_diff(extracted_patch_text, repo_root=repo_root, generation_files=list(execution_contract.get("generation_files") or [])) or extracted_patch_text
                normalized_patch_text = repair_unified_diff_bare_hunks(normalized_patch_text)
                normalized_patch_text = repair_unified_diff_hunk_headers(normalized_patch_text)
                patch_artifact = normalize_patch_artifact({
                    "artifact_type": str(runtime.get("preferred_artifact") or "unified_diff"),
                    "producer": {"provider": runtime.get("provider"), "model": runtime.get("selected_model")},
                    "target_files": [],
                    "content": normalized_patch_text,
                }, repo_root=repo_root, allowed_paths=allowed_scope, forbidden_paths=list(execution_contract.get("forbidden_edit_files") or []))
                break
            except PipelineError as exc:
                if execution_source == "live" and adapter is not None and invalid_output_retry_count < max_invalid_output_retries and _looks_retryable_invalid_model_output(raw_text, exc):
                    invalid_output_retry_count += 1
                    generation_attempt_count += 1
                    retry_prompt = _retry_prompt_for_invalid_output(prompt, generation_attempt_count, exc)
                    try:
                        provider_result = adapter.generate(retry_prompt)
                    except PipelineError as retry_exc:
                        failure_source = str(retry_exc.details.get("failure_source") or ("credentials" if retry_exc.code == "missing_provider_credentials" else "provider"))
                        result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"{retry_exc.code}: {retry_exc.message}", failure_class="runtime_execution_error" if retry_exc.code != "unsupported_patch_provider" else "runtime_resolution_error", failure_source=failure_source, execution_source="live", retryable_hint=failure_source in {"provider", "timeout"} or retry_exc.code == "empty_patch_output", raw_output_path=str(raw_output_path.resolve()), extra={**token_extra, "attempt_count": generation_attempt_count})
                        validate_patch_generate_result(result)
                        envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                        save_json_atomic(args.output, envelope)
                        return 0
                    raw_text = str(provider_result.get("raw_text") or "")
                    save_text_atomic(raw_output_path, raw_text)
                    token_extra = _estimated_token_extra(
                        model=str(provider_result.get("selected_model") or runtime.get("selected_model") or runtime.get("requested_model") or ""),
                        prompt=prompt,
                        raw_text=raw_text,
                    )
                    continue
                result = _failure_result(runtime=runtime, artifact_lineage=artifact_lineage, reason=f"{exc.code}: {exc.message}", failure_class="normalization_error" if exc.code not in {"unsupported_patch_provider"} else "runtime_resolution_error", failure_source="normalization", execution_source=execution_source, retryable_hint=False, raw_output_path=str(raw_output_path.resolve()), extra={**token_extra, "output_format_received": "invalid_model_output", "fixture_path": str(fixture_path) if fixture_path else None, "attempt_count": generation_attempt_count})
                validate_patch_generate_result(result)
                envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
                save_json_atomic(args.output, envelope)
                return 0

        metadata = {
            "result_kind": "generation_succeeded",
            "requested_model": runtime.get("requested_model"),
            "requested_agent_tech": runtime.get("requested_agent_tech"),
            "selected_model": provider_result.get("selected_model") or runtime.get("selected_model"),
            "selected_agent_tech": runtime.get("selected_agent_tech"),
            "model": runtime.get("requested_model"),
            "agent_tech": runtime.get("requested_agent_tech") or runtime.get("selected_agent_tech"),
            "provider": runtime.get("provider"),
            "reasoning_effort": runtime.get("reasoning_effort"),
            "duration_ms": int(provider_result.get("duration_ms") or 0),
            "raw_output_path": str(raw_output_path.resolve()),
            "output_format_requested": str(runtime.get("preferred_artifact") or "unified_diff"),
            "output_format_received": str(patch_artifact.get("artifact_type") or "unified_diff"),
            "finish_reason": provider_result.get("finish_reason"),
            "usage_input_tokens": provider_result.get("usage_input_tokens"),
            "usage_output_tokens": provider_result.get("usage_output_tokens"),
            "usage_total_tokens": provider_result.get("usage_total_tokens"),
            "estimated_input_tokens": token_extra.get("estimated_input_tokens"),
            "estimated_output_tokens": token_extra.get("estimated_output_tokens"),
            "estimated_total_tokens": token_extra.get("estimated_total_tokens"),
            "selected_via": runtime.get("selected_via"),
            "execution_mode": runtime.get("execution_mode"),
            "execution_source": execution_source,
            "simulation_mode": execution_source == "fixture",
            "response_id": provider_result.get("response_id"),
            "runtime_summary": runtime.get("runtime_summary"),
            "attempt_count": generation_attempt_count,
        }
        if execution_source == "fixture":
            metadata["fixture_basename"] = Path(fixture_path).name
        result = {
            "generation_status": "generated",
            "result_kind": "generation_succeeded",
            "patch_artifact": patch_artifact,
            "verification_claim": verification_claim,
            "source_execution_contract_artifact": artifact_lineage["execution_contract"],
            "artifact_lineage": artifact_lineage,
            "generation_metadata": metadata,
        }
        validate_patch_generate_result(result)
        envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
