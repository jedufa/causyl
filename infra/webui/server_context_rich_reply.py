"""Read-only context-rich reply helpers for implementation-strategy questions."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from infra.pipeline.core import load_pipeline_config
from infra.pipeline.planning import bundle_context, rank_context
from infra.pipeline.stages.repo_retriever import build_retrieval
from infra.pipeline.stages.search_plan import build_search_plan
from infra.pipeline.stages.task_classifier import classify as classify_task
from infra.webui.ledger import ledger_storage

_PIS_UID_RE = re.compile(r"\bPIS-\d{8}-\d{6}Z-[A-Z0-9]{4}\b")
_IMPLEMENTATION_HINT_RE = re.compile(
    r"\b(how would you implement|implementation plan|which files(?: and tests)? would change|which tests(?: and files)? would change|which modules would change|what files(?: and tests)? would change|what tests(?: and files)? would change|what modules would change|what .* would you touch|approach to build|how should this be implemented|implementation strategy)\b",
    re.IGNORECASE,
)
_EXECUTION_VERB_RE = re.compile(
    r"\b(implement this (?:pis )?now|implement it now|patch(?: the repo)?|modify(?: the repo)?|edit(?: the repo)?|apply (?:the )?patch|run (?:the )?(?:validation|tests?)|make the change|fix this now)\b",
    re.IGNORECASE,
)
_PIS_HINT_RE = re.compile(r"\bPIS\b", re.IGNORECASE)
_REPO_SCOPE_RE = re.compile(r"\b(file|files|module|modules|endpoint|endpoints|function|functions|class|classes|test|tests|validation|repo|repository|selector|route|symbol|symbols|api|apis)\b", re.IGNORECASE)
_CACHE_TTL_S = 180.0
_MAX_CACHE_ENTRIES = 64
_DEFAULT_BUDGET = {
    "max_files": 6,
    "max_symbols": 8,
    "max_tests": 3,
    "max_total_bytes": 18000,
    "max_total_tokens": 4500,
    "max_retrieval_time_ms": 4000,
}
_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_epoch() -> float:
    return float(time.time())


def _head_revision(repo_root: Path) -> str:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        text = str(cp.stdout or "").strip()
        return text or "unknown"
    except Exception:
        return "unknown"


def _message_key(message: str) -> str:
    return hashlib.sha256(str(message or "").encode("utf-8")).hexdigest()


def _extract_pis_refs(text: str) -> List[str]:
    seen: List[str] = []
    for match in _PIS_UID_RE.findall(str(text or "")):
        if match not in seen:
            seen.append(match)
    return seen


def classify_reply_mode(*, message: str, binding: Mapping[str, Any]) -> Dict[str, Any]:
    msg = _clean_text(message)
    reasons: List[str] = []
    if _EXECUTION_VERB_RE.search(msg):
        reasons.append("execution_verbs_present")
        return {"mode": "escalate_to_task", "reasons": reasons, "ambiguity_level": "low"}

    implementation_request = bool(_IMPLEMENTATION_HINT_RE.search(msg))
    pis_reference = bool(_PIS_HINT_RE.search(msg) or _extract_pis_refs(msg))
    ledger_bound = str(binding.get("type") or "").strip() == "ledger_uid"
    repo_scope = bool(_REPO_SCOPE_RE.search(msg))
    ambiguity_level = "low"

    if implementation_request:
        reasons.append("implementation_strategy_request")
    if pis_reference:
        reasons.append("pis_reference_present")
    if ledger_bound:
        reasons.append("ledger_bound_turn")
    if repo_scope:
        reasons.append("repo_scope_present")

    if implementation_request and (pis_reference or ledger_bound or repo_scope):
        return {"mode": "context_rich_reply", "reasons": reasons, "ambiguity_level": ambiguity_level}
    if implementation_request:
        ambiguity_level = "high"
        return {
            "mode": "cheap_reply",
            "reasons": ["implementation_hint_without_repo_scope", "high_ambiguity"],
            "ambiguity_level": ambiguity_level,
        }
    return {"mode": "cheap_reply", "reasons": ["default_fast_path"], "ambiguity_level": ambiguity_level}


def _find_item_dir_for_uid(repo_root: Path, uid: str) -> Optional[Path]:
    return ledger_storage.item_dir_for_uid(ledger_storage.ledger_patch_instruction_sheets_root(Path(repo_root).resolve()), uid)


def _find_title_matches(repo_root: Path, message: str, *, limit: int = 3) -> List[str]:
    text = _clean_text(message).lower()
    if not text:
        return []
    tokens = [tok for tok in re.findall(r"[a-z0-9]{4,}", text) if tok not in {"this", "that", "would", "implement", "implementation", "approach", "which", "files"}]
    if not tokens:
        return []
    root = ledger_storage.ledger_patch_instruction_sheets_root(Path(repo_root).resolve())
    matches: List[Tuple[int, str]] = []
    for meta_path in root.rglob("meta.json"):
        try:
            obj = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = _clean_text(obj.get("title")).lower()
        if not title:
            continue
        score = sum(1 for tok in tokens if tok in title)
        if score <= 0:
            continue
        matches.append((score, str(obj.get("uid") or "").strip()))
    matches.sort(key=lambda row: (-row[0], row[1]))
    out: List[str] = []
    for _, uid in matches:
        if uid and uid not in out:
            out.append(uid)
        if len(out) >= max(1, int(limit)):
            break
    return out


def resolve_pis_binding(*, repo_root: Path, binding: Mapping[str, Any], message: str) -> Dict[str, Any]:
    explicit_refs = _extract_pis_refs(message)
    candidates: List[str] = []
    bound_uid = ""
    if str(binding.get("type") or "").strip() == "ledger_uid":
        bound_uid = _clean_text(binding.get("ledger_uid") or binding.get("id"))
    if bound_uid:
        candidates.append(bound_uid)
    for uid in explicit_refs:
        if uid not in candidates:
            candidates.append(uid)
    if not candidates:
        candidates.extend(_find_title_matches(repo_root, message))
    if not candidates:
        return {"resolved": False, "reason": "no_pis_reference_detected"}
    uid = candidates[0]
    item_dir = _find_item_dir_for_uid(repo_root, uid)
    if item_dir is None:
        return {"resolved": False, "reason": "pis_not_found", "uid": uid}
    meta = ledger_storage.read_json(item_dir / "meta.json") or {}
    pis_text = ledger_storage.read_text(item_dir / "pis.md")
    refs = [ref for ref in _extract_pis_refs(pis_text) if ref != uid]
    parent_uid = refs[0] if refs else ""
    inbound: List[str] = []
    items_root = (Path(repo_root).resolve() / "governance" / "ledger" / "items").resolve()
    for other_pis in items_root.rglob("pis.md"):
        if other_pis.parent == item_dir:
            continue
        try:
            text = other_pis.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if uid not in text:
            continue
        other_meta = ledger_storage.read_json(other_pis.parent / "meta.json") or {}
        other_uid = _clean_text(other_meta.get("uid"))
        if other_uid and other_uid not in inbound:
            inbound.append(other_uid)
        if len(inbound) >= 6:
            break
    evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
    return {
        "resolved": True,
        "uid": uid,
        "title": _clean_text(meta.get("title")),
        "status": _clean_text(meta.get("status") or "draft"),
        "path": str(item_dir),
        "parent_uid": parent_uid,
        "related_uids": refs[:6],
        "referenced_by_uids": inbound,
        "supersedes_uid": _clean_text(meta.get("supersedes_uid")),
        "regression_of_uid": _clean_text(meta.get("regression_of_uid")),
        "evidence_summary": {
            "has_evidence": bool(evidence),
            "evidence_kind": _clean_text(evidence.get("kind")),
            "evidence_path": _clean_text(evidence.get("path")),
            "required_evidence_count": len(list(meta.get("required_evidence") or [])),
        },
        "pis_excerpt": " ".join(pis_text.split())[:700],
    }


def _cache_get(key: Tuple[str, str, str]) -> Optional[Dict[str, Any]]:
    row = _CACHE.get(key)
    if not isinstance(row, dict):
        return None
    if (_utc_epoch() - float(row.get("stored_at") or 0.0)) > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return dict(row.get("payload") or {})


def _cache_put(key: Tuple[str, str, str], payload: Dict[str, Any]) -> None:
    if len(_CACHE) >= _MAX_CACHE_ENTRIES:
        oldest = min(_CACHE.items(), key=lambda item: float(item[1].get("stored_at") or 0.0))[0]
        _CACHE.pop(oldest, None)
    _CACHE[key] = {"stored_at": _utc_epoch(), "payload": dict(payload or {})}


def _reply_budget() -> Dict[str, int]:
    return dict(_DEFAULT_BUDGET)


def _reply_job_contract(*, repo_root: Path, message: str, pis_binding: Mapping[str, Any], route_mode: str) -> Dict[str, Any]:
    title = _clean_text(pis_binding.get("title"))
    objective = _clean_text(message)
    if title:
        objective = f"{objective}\nTarget PIS: {title} ({_clean_text(pis_binding.get('uid'))})"
    return {
        "repo_root": str(Path(repo_root).resolve()),
        "objective": objective,
        "issue_context": {
            "summary": objective,
            "changed_files_hint": [],
            "failing_tests": [],
        },
        "constraints": [
            "read_only_reply_path",
            "no_repo_writes",
            "no_task_or_run_creation",
            f"reply_mode:{route_mode}",
        ],
    }


def _estimate_tokens(text: str) -> int:
    return max(1, (len(str(text or "")) + 3) // 4)


def _format_bundle_context(context_bundle: Mapping[str, Any], *, budget: Mapping[str, int]) -> Tuple[str, List[str], List[str]]:
    max_files = max(1, int(budget.get("max_files") or _DEFAULT_BUDGET["max_files"]))
    max_symbols = max(1, int(budget.get("max_symbols") or _DEFAULT_BUDGET["max_symbols"]))
    max_tests = max(1, int(budget.get("max_tests") or _DEFAULT_BUDGET["max_tests"]))
    max_bytes = max(4000, int(budget.get("max_total_bytes") or _DEFAULT_BUDGET["max_total_bytes"]))
    max_tokens = max(512, int(budget.get("max_total_tokens") or _DEFAULT_BUDGET["max_total_tokens"]))
    files = list(context_bundle.get("files") or [])
    selected: List[Mapping[str, Any]] = []
    test_paths: List[str] = []
    used_bytes = 0
    used_tokens = 0
    symbol_count = 0
    for entry in files:
        path = _clean_text(entry.get("path"))
        if not path:
            continue
        if len(selected) >= max_files or symbol_count >= max_symbols:
            break
        snippets = list(entry.get("snippets") or [])
        snippet_text = ""
        if snippets and isinstance(snippets[0], dict):
            snippet_text = str(snippets[0].get("content") or "")
        if not snippet_text:
            continue
        chunk_bytes = len(snippet_text.encode("utf-8"))
        chunk_tokens = _estimate_tokens(snippet_text)
        if used_bytes and (used_bytes + chunk_bytes) > max_bytes:
            continue
        if used_tokens and (used_tokens + chunk_tokens) > max_tokens:
            continue
        used_bytes += chunk_bytes
        used_tokens += chunk_tokens
        symbol_count += 1
        selected.append(entry)
        if "/test" in path or path.startswith("tests/"):
            if path not in test_paths and len(test_paths) < max_tests:
                test_paths.append(path)
    lines: List[str] = ["Ranked repo context bundle:"]
    paths: List[str] = []
    for entry in selected:
        path = _clean_text(entry.get("path"))
        paths.append(path)
        lines.append(f"- file: {path}")
        lines.append(f"  role: {_clean_text(entry.get('bundle_role') or entry.get('selection_lane') or 'context')}")
        snippets = list(entry.get("snippets") or [])
        if snippets and isinstance(snippets[0], dict):
            snip = snippets[0]
            start = int(snip.get("start_line") or 0)
            end = int(snip.get("end_line") or 0)
            lines.append(f"  lines: {start}-{end}")
            lines.append("  snippet:")
            lines.append("  ```text")
            for line in str(snip.get("content") or "").splitlines():
                lines.append(f"  {line}")
            lines.append("  ```")
    if not selected:
        lines.append("- no ranked bundle files selected within budget")
    lines.append(f"- budget_usage: tokens={used_tokens}/{max_tokens}, bytes={used_bytes}/{max_bytes}, symbols={symbol_count}/{max_symbols}")
    return ("\n".join(lines), paths, test_paths)



def retrieve_repo_context(
    retrieval_input: Mapping[str, Any],
    *,
    repo_root: Path,
    budget: Mapping[str, int],
) -> Dict[str, Any]:
    config4 = load_pipeline_config(repo_root=repo_root, stage_key="script_4_repo_retriever")
    return build_retrieval(
        retrieval_input,
        Path(repo_root).resolve(),
        shutil.which("rg") or "rg",
        max(1, int(budget["max_retrieval_time_ms"] / 1000)),
        config4,
    )


def rank_repo_context(ranked_input: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    config5 = load_pipeline_config(repo_root=repo_root, stage_key="script_5_context_ranker")
    return rank_context(dict(ranked_input), config5)


def bundle_repo_context(ranked: Mapping[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    config6 = load_pipeline_config(repo_root=repo_root, stage_key="script_6_context_bundle")
    return bundle_context(dict(ranked), config6)


def build_context_rich_reply_material(
    *,
    repo_root: Path,
    message: str,
    binding: Mapping[str, Any],
    context_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    pis_binding = resolve_pis_binding(repo_root=repo_root, binding=binding, message=message)
    route = classify_reply_mode(message=message, binding=binding)
    budget = _reply_budget()
    cache_key = (_head_revision(Path(repo_root).resolve()), _clean_text(pis_binding.get("uid")), _message_key(message))
    if route["mode"] != "cheap_reply":
        cached = _cache_get(cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}

    recent_messages: List[str] = []
    for row in list(context_rows or [])[-8:]:
        if not isinstance(row, Mapping):
            continue
        sender = _clean_text(row.get("sender") or "unknown")
        content = _clean_text(row.get("content"))
        if content:
            recent_messages.append(f"{sender}: {content}")

    material: Dict[str, Any] = {
        "route_mode": route["mode"],
        "route_reasons": list(route["reasons"]),
        "pis_binding": pis_binding,
        "budget": budget,
        "repo_context_text": "",
        "repo_context_files": [],
        "likely_tests": [],
        "cache_hit": False,
        "pipeline_context_used": False,
        "pipeline_error": "",
        "instructions_addendum": "",
        "ambiguity_level": str(route.get("ambiguity_level") or "low"),
    }
    if route["mode"] == "cheap_reply":
        return material

    job_contract = _reply_job_contract(repo_root=repo_root, message=message, pis_binding=pis_binding, route_mode=route["mode"])
    try:
        config2 = load_pipeline_config(repo_root=repo_root, stage_key="script_2_task_classifier")
        classified = classify_task(job_contract, intake_assessment={"task_families": ["generic"]}, repo_root=repo_root, config=config2)
        config3 = load_pipeline_config(repo_root=repo_root, stage_key="script_3_search_plan")
        search_plan = build_search_plan(
            job_contract,
            classified["task_profile"],
            max_candidates=max(20, budget["max_files"] * 6),
            intake_assessment=classified["intake_assessment"],
            repo_root=repo_root,
            config=config3,
        )
        retrieval_input = {
            "job_contract": job_contract,
            "task_profile": classified["task_profile"],
            "intake_assessment": classified["intake_assessment"],
            "search_plan": search_plan["search_plan"],
        }
        retrieval = retrieve_repo_context(retrieval_input, repo_root=repo_root, budget=budget)
        ranked_input = dict(retrieval_input)
        ranked_input["retrieval"] = retrieval["retrieval"]
        ranked = rank_repo_context(ranked_input, repo_root=repo_root)
        bundled = bundle_repo_context(ranked, repo_root=repo_root)
        repo_context_text, repo_context_files, likely_tests = _format_bundle_context(bundled.get("context_bundle") or {}, budget=budget)
        material["repo_context_text"] = repo_context_text
        material["repo_context_files"] = repo_context_files
        material["likely_tests"] = likely_tests
        material["pipeline_context_used"] = True
    except Exception as exc:
        material["pipeline_error"] = _clean_text(exc)

    pis_lines: List[str] = ["PIS binding:"]
    if bool(pis_binding.get("resolved")):
        pis_lines.extend(
            [
                f"- uid: {_clean_text(pis_binding.get('uid'))}",
                f"- title: {_clean_text(pis_binding.get('title'))}",
                f"- status: {_clean_text(pis_binding.get('status'))}",
                f"- parent_uid: {_clean_text(pis_binding.get('parent_uid')) or '(none)'}",
                f"- related_uids: {', '.join(list(pis_binding.get('related_uids') or [])[:6]) or '(none)'}",
                f"- referenced_by: {', '.join(list(pis_binding.get('referenced_by_uids') or [])[:6]) or '(none)'}",
                f"- supersedes_uid: {_clean_text(pis_binding.get('supersedes_uid')) or '(none)'}",
                f"- regression_of_uid: {_clean_text(pis_binding.get('regression_of_uid')) or '(none)'}",
                f"- evidence: {json.dumps(dict(pis_binding.get('evidence_summary') or {}), ensure_ascii=False, sort_keys=True)}",
                "",
                "PIS excerpt:",
                str(pis_binding.get("pis_excerpt") or ""),
            ]
        )
    else:
        pis_lines.append(f"- unresolved: {_clean_text(pis_binding.get('reason') or 'unknown')}")
    if recent_messages:
        pis_lines.extend(["", "Recent conversation context:"] + [f"- {row}" for row in recent_messages[-6:]])

    instructions = [
        "You are answering an implementation-strategy question in a strict read-only mode.",
        "Do not propose that repo changes were made.",
        "Do not imply task/run creation happened unless you explicitly recommend escalation.",
        "Use the following exact section headings in this order:",
        "Likely Files/Modules",
        "Implementation Approach",
        "Risks / Edge Cases",
        "Likely Tests / Validation",
        "Open Uncertainties",
    ]
    if route["mode"] == "escalate_to_task":
        instructions.extend(
            [
                "Add a final section heading 'Escalation Note'.",
                "State clearly that the request crosses into execution and should become a managed task/run.",
                "Provide only bounded non-executing guidance.",
            ]
        )
    else:
        instructions.append("Only add an 'Escalation Note' section if the context shows the request cannot be answered safely without execution.")
    material["instructions_addendum"] = "\n".join(instructions)
    material["model_context_text"] = "\n\n".join(
        [
            "\n".join(pis_lines),
            str(material.get("repo_context_text") or "Ranked repo context bundle:\n- unavailable"),
        ]
    )
    _cache_put(cache_key, material)
    return material
