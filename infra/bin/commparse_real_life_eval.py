from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from experiments_shared.action_plane.operator_interface import execute_operator_request_v1, plan_operator_request_v1
from infra.bin import lab


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _labsh_history_path(repo_root: Path) -> Path:
    return repo_root / "governance" / "agents" / "runtime" / "labsh_history.txt"


def _lab_sessions_root(repo_root: Path) -> Path:
    return repo_root / "governance" / "agents" / "sessions" / "lab_front_door"


def _invented_prompt_corpus_path(repo_root: Path) -> Path:
    return repo_root / "tests" / "control_plane" / "e2e" / "fixtures" / "lab_cli" / "commparse_invented_prompt_corpus_v1.json"


def _should_skip_prompt(prompt: str) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return lowered in {"help", "exit", "quit", "source", "reload", "reexec"}


def _history_prompts(repo_root: Path) -> List[str]:
    path = _labsh_history_path(repo_root)
    if not path.exists():
        return []
    prompts: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = str(line or "").strip()
        if _should_skip_prompt(text):
            continue
        prompts.append(text)
    return prompts


def _session_records(repo_root: Path) -> List[Dict[str, Any]]:
    root = _lab_sessions_root(repo_root)
    rows: List[Dict[str, Any]] = []
    if not root.exists():
        return rows
    for session_file in sorted(root.glob("*/session__lab.jsonl")):
        try:
            for raw_line in session_file.read_text(encoding="utf-8").splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                except Exception:
                    continue
                rows.append(record)
        except Exception:
            continue
    return rows


def _session_prompts(repo_root: Path) -> List[str]:
    prompts: List[str] = []
    for record in _session_records(repo_root):
        if str(record.get("kind") or "") != "message":
            continue
        if str(record.get("sender") or "") != "user":
            continue
        prompt = str(record.get("content") or "").strip()
        if _should_skip_prompt(prompt):
            continue
        prompts.append(prompt)
    return prompts


def _historical_assistant_by_prompt(repo_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    by_prompt: Dict[str, List[Dict[str, Any]]] = {}
    current_prompt = ""
    current_session = ""
    for record in _session_records(repo_root):
        session_id = str(record.get("session_id") or "").strip()
        if str(record.get("kind") or "") != "message":
            continue
        sender = str(record.get("sender") or "").strip()
        if sender == "user":
            current_prompt = str(record.get("content") or "").strip()
            current_session = session_id
            continue
        if sender != "assistant":
            continue
        if not current_prompt or current_session != session_id:
            continue
        by_prompt.setdefault(current_prompt, []).append(
            {
                "codex_used": bool(record.get("codex_used")),
                "selection": dict(record.get("selection") or {}),
                "terminal_class": str(record.get("terminal_class") or "").strip(),
                "summary": str(record.get("summary") or "").strip(),
            }
        )
    return by_prompt


def collect_real_life_prompts(repo_root: Path, *, limit: int) -> List[str]:
    merged: List[str] = []
    seen = set()
    for prompt in reversed(_history_prompts(repo_root)):
        if prompt in seen:
            continue
        seen.add(prompt)
        merged.append(prompt)
    for prompt in reversed(_session_prompts(repo_root)):
        if prompt in seen:
            continue
        seen.add(prompt)
        merged.append(prompt)
    return list(reversed(merged[-max(1, limit):]))


def collect_invented_prompts(repo_root: Path, *, limit: int) -> List[Dict[str, Any]]:
    path = _invented_prompt_corpus_path(repo_root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for item in list(payload or []):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if _should_skip_prompt(prompt):
            continue
        rows.append(
            {
                "prompt": prompt,
                "goal": str(item.get("goal") or "").strip(),
                "source": "invented",
            }
        )
    return rows[: max(1, limit)]


def _default_args() -> argparse.Namespace:
    return lab.build_parser().parse_args([])


def _payload_for_prompt(prompt: str, *, repo_root: Path, allow_fallback: bool) -> Dict[str, Any]:
    args = _default_args()
    payload = lab._build_payload(args, prompt, repo_root)
    payload["allow_fallback"] = allow_fallback
    return payload


def _evaluate_single_prompt(prompt: str, *, repo_root: Path, execute: bool) -> Dict[str, Any]:
    payload = _payload_for_prompt(prompt, repo_root=repo_root, allow_fallback=execute)
    if execute:
        result = execute_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=repo_root)
        post_plan = plan_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=repo_root)
    else:
        result = plan_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=repo_root)
        post_plan = result
    command_parse = dict(result.get("command_parse") or {})
    selection = dict(result.get("selection") or {})
    post_selection = dict(post_plan.get("selection") or {})
    operator_surface = dict(result.get("operator_surface") or {})
    codex = dict(operator_surface.get("codex_transparency") or result.get("codex_fallback_request") or {})
    return {
        "prompt": prompt,
        "mode": "execute" if execute else "plan",
        "supported": bool(result.get("supported")),
        "selection_action_id": str(selection.get("action_id") or "").strip(),
        "selection_rule_id": str(selection.get("matched_rule_id") or "").strip(),
        "commparse_action_id": str(command_parse.get("selected_action_id") or "").strip(),
        "commparse_commands": list(command_parse.get("would_call_commands") or []),
        "commparse_shape": str(command_parse.get("execution_shape") or "").strip(),
        "commparse_agreement": bool(command_parse.get("agreement_with_final_selection", str(command_parse.get("selected_action_id") or "").strip() == str(selection.get("action_id") or "").strip())),
        "codex_used": bool(codex.get("codex_used")),
        "post_plan_action_id": str(post_selection.get("action_id") or "").strip(),
        "post_plan_supported": bool(post_plan.get("supported")),
        "summary": str(result.get("summary") or "").strip(),
    }


def _evaluate_prompt(prompt: str, *, repo_root: Path, execute: bool) -> Dict[str, Any]:
    args = _default_args()
    args.plan = not execute
    args.json = False
    args.no_fallback = not execute
    args.allow_fallback = bool(execute)
    try:
        results, _exit_code = lab.execute_front_door_prompt(
            args,
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
            contract_repo_root=repo_root,
        )
    except ValueError as exc:
        return {
            "prompt": prompt,
            "mode": "execute" if execute else "plan",
            "supported": False,
            "selection_action_id": "",
            "selection_rule_id": "front_door_validation_error",
            "commparse_action_id": "",
            "commparse_commands": [],
            "commparse_shape": "front_door_validation_error",
            "commparse_agreement": True,
            "codex_used": False,
            "post_plan_action_id": "",
            "post_plan_supported": False,
            "summary": str(exc).strip() or "front door validation failed",
            "front_door_builtin": False,
        }
    if len(results) <= 1:
        result = dict(results[0] if results else {})
        command_parse = dict(result.get("command_parse") or {})
        selection = dict(result.get("selection") or {})
        operator_surface = dict(result.get("operator_surface") or {})
        codex = dict(operator_surface.get("codex_transparency") or result.get("codex_fallback_request") or {})
        return {
            "prompt": prompt,
            "mode": "execute" if execute else "plan",
            "supported": bool(result.get("supported")),
            "selection_action_id": str(selection.get("action_id") or "").strip(),
            "selection_rule_id": str(selection.get("matched_rule_id") or "").strip(),
            "commparse_action_id": str(command_parse.get("selected_action_id") or "").strip(),
            "commparse_commands": list(command_parse.get("would_call_commands") or []),
            "commparse_shape": str(command_parse.get("execution_shape") or "").strip(),
            "commparse_agreement": bool(command_parse.get("agreement_with_final_selection", str(command_parse.get("selected_action_id") or "").strip() == str(selection.get("action_id") or "").strip())),
            "codex_used": bool(codex.get("codex_used")),
            "post_plan_action_id": str(selection.get("action_id") or "").strip(),
            "post_plan_supported": bool(result.get("supported")),
            "summary": str(result.get("summary") or "").strip(),
            "front_door_builtin": str(selection.get("matched_rule_id") or "").startswith("front_door_"),
        }
    evaluated_steps = []
    for step_result in results:
        step_payload = dict(step_result)
        command_parse = dict(step_payload.get("command_parse") or {})
        selection = dict(step_payload.get("selection") or {})
        operator_surface = dict(step_payload.get("operator_surface") or {})
        codex = dict(operator_surface.get("codex_transparency") or step_payload.get("codex_fallback_request") or {})
        evaluated_steps.append(
            {
                "prompt": str(step_payload.get("request_text") or "").strip(),
                "mode": "execute" if execute else "plan",
                "supported": bool(step_payload.get("supported")),
                "selection_action_id": str(selection.get("action_id") or "").strip(),
                "selection_rule_id": str(selection.get("matched_rule_id") or "").strip(),
                "commparse_action_id": str(command_parse.get("selected_action_id") or "").strip(),
                "commparse_commands": list(command_parse.get("would_call_commands") or []),
                "commparse_shape": str(command_parse.get("execution_shape") or "").strip(),
                "commparse_agreement": bool(command_parse.get("agreement_with_final_selection", str(command_parse.get("selected_action_id") or "").strip() == str(selection.get("action_id") or "").strip())),
                "codex_used": bool(codex.get("codex_used")),
                "post_plan_action_id": str(selection.get("action_id") or "").strip(),
                "post_plan_supported": bool(step_payload.get("supported")),
                "summary": str(step_payload.get("summary") or "").strip(),
            }
        )
    supported = all(bool(step.get("supported")) for step in evaluated_steps)
    codex_used = any(bool(step.get("codex_used")) for step in evaluated_steps)
    commparse_agreement = all(bool(step.get("commparse_agreement")) for step in evaluated_steps)
    return {
        "prompt": prompt,
        "mode": "execute" if execute else "plan",
        "supported": supported,
        "selection_action_id": "chain_sequence_v1" if supported else "",
        "selection_rule_id": "front_door_chain_split",
        "commparse_action_id": "chain_sequence_v1" if supported else "",
        "commparse_commands": [command for step in evaluated_steps for command in list(step.get("commparse_commands") or [])],
        "commparse_shape": "sequence_request",
        "commparse_agreement": commparse_agreement,
        "codex_used": codex_used,
        "post_plan_action_id": "chain_sequence_v1" if supported else "",
        "post_plan_supported": supported,
        "summary": f"Chained request with {len(evaluated_steps)} steps.",
        "chain_steps": evaluated_steps,
    }


def _evaluate_prompt_rows(prompt_rows: Iterable[Mapping[str, Any]], *, repo_root: Path, execute: bool) -> List[Dict[str, Any]]:
    historical = _historical_assistant_by_prompt(repo_root)
    rows: List[Dict[str, Any]] = []
    for prompt_row in prompt_rows:
        prompt = str(prompt_row.get("prompt") or "").strip()
        if not prompt:
            continue
        row = _evaluate_prompt(prompt, repo_root=repo_root, execute=execute)
        row["source"] = str(prompt_row.get("source") or "").strip() or "unknown"
        row["goal"] = str(prompt_row.get("goal") or "").strip()
        prior = list(historical.get(prompt) or [])
        row["historical_matches"] = prior
        row["historical_codex_used"] = any(bool(item.get("codex_used")) for item in prior)
        row["historical_action_ids"] = sorted({str(dict(item.get("selection") or {}).get("action_id") or "").strip() for item in prior if str(dict(item.get("selection") or {}).get("action_id") or "").strip()})
        if row["historical_codex_used"]:
            row["improvement_after_codex"] = bool(row["post_plan_supported"] and row["post_plan_action_id"])
        else:
            row["improvement_after_codex"] = None
        rows.append(row)
    return rows


def _build_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    action_counter = Counter(str(row.get("selection_action_id") or "unsupported") for row in rows)
    source_counter = Counter(str(row.get("source") or "unknown") for row in rows)
    return {
        "prompt_count": len(rows),
        "supported_count": sum(1 for row in rows if row.get("supported")),
        "unsupported_count": sum(1 for row in rows if not row.get("supported")),
        "commparse_agreement_count": sum(1 for row in rows if row.get("commparse_agreement")),
        "historical_codex_case_count": sum(1 for row in rows if row.get("historical_codex_used")),
        "historical_codex_improved_count": sum(1 for row in rows if row.get("improvement_after_codex") is True),
        "source_counts": dict(source_counter),
        "top_actions": [{"action_id": action_id, "count": count} for action_id, count in action_counter.most_common(10)],
    }


def evaluate_commparse_prompts(repo_root: Path, *, limit: int = 30, execute: bool = False, prompt_set: str = "real") -> Dict[str, Any]:
    prompt_rows: List[Dict[str, Any]] = []
    if prompt_set in {"real", "both"}:
        prompt_rows.extend({"prompt": prompt, "source": "real"} for prompt in collect_real_life_prompts(repo_root, limit=limit))
    if prompt_set in {"invented", "both"}:
        prompt_rows.extend(collect_invented_prompts(repo_root, limit=limit))
    rows = _evaluate_prompt_rows(prompt_rows, repo_root=repo_root, execute=execute)
    summary = _build_summary(rows)
    return {
        "schema_version": "commparse_real_life_eval_v1",
        "mode": "execute" if execute else "plan",
        "prompt_set": prompt_set,
        "summary": summary,
        "rows": rows,
    }


def render_eval_text(payload: Mapping[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "COMMPARSE REAL-LIFE EVAL",
        "------------------------",
        f"mode: {payload.get('mode')}",
        f"prompt set: {payload.get('prompt_set')}",
        f"prompts: {summary.get('prompt_count', 0)}",
        f"supported now: {summary.get('supported_count', 0)}",
        f"unsupported now: {summary.get('unsupported_count', 0)}",
        f"commparse agreement: {summary.get('commparse_agreement_count', 0)}",
        f"historical codex cases: {summary.get('historical_codex_case_count', 0)}",
        f"historical codex improved: {summary.get('historical_codex_improved_count', 0)}",
    ]
    source_counts = dict(summary.get("source_counts") or {})
    if source_counts:
        lines.append(f"sources: {source_counts}")
    lines.extend(["", "Top actions:"])
    for row in list(summary.get("top_actions") or []):
        lines.append(f"- {row.get('action_id')}: {row.get('count')}")
    lines.append("")
    lines.append("Prompt rows:")
    for row in list(payload.get("rows") or []):
        prompt = str(row.get("prompt") or "").strip()
        selected = str(row.get("selection_action_id") or "unsupported").strip()
        commparse = str(row.get("commparse_action_id") or "unsupported").strip()
        suffix_bits = []
        if row.get("historical_codex_used"):
            suffix_bits.append("historical_codex")
        if row.get("improvement_after_codex") is True:
            suffix_bits.append("improved")
        suffix = f" [{' '.join(suffix_bits)}]" if suffix_bits else ""
        lines.append(f"- {prompt}")
        lines.append(f"  final={selected} | commparse={commparse} | agree={'yes' if row.get('commparse_agreement') else 'no'}{suffix}")
        commands = list(row.get("commparse_commands") or [])
        if commands:
            lines.append(f"  commands: {commands[0]}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate commparse on real-life plane prompts.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of unique recent prompts to evaluate.")
    parser.add_argument("--execute", action="store_true", help="Replay prompts through execute mode with fallback enabled. Unsafe for mutating prompts.")
    parser.add_argument("--prompt-set", choices=("real", "invented", "both"), default="real", help="Prompt source to evaluate.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    repo_root = _repo_root()
    payload = evaluate_commparse_prompts(
        repo_root,
        limit=max(1, int(args.limit)),
        execute=bool(args.execute),
        prompt_set=str(args.prompt_set or "real"),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_eval_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
