from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.pipeline.automation import run_full_auto
from infra.pipeline.core import utc_now_iso
from infra.pipeline.semantic import semantic_cli_overrides


class ProjFlowApp:
    def __init__(self, *, repo_root: str | Path = ".") -> None:
        self.repo_root = Path(repo_root).resolve()
        self._live_line_active = False

    def _clear_live_line(self) -> None:
        if self._live_line_active and sys.stderr.isatty():
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()
        self._live_line_active = False

    def _print_live_line(self, text: str) -> None:
        if not sys.stderr.isatty():
            return
        sys.stderr.write("\r\033[2K" + text[:200])
        sys.stderr.flush()
        self._live_line_active = True

    @staticmethod
    def _format_duration_ms(duration_ms: int) -> str:
        seconds = max(0, int(duration_ms // 1000))
        minutes, seconds = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    def _emit_progress(self, event: str, payload: Dict[str, Any]) -> None:
        stamp = utc_now_iso()
        stage = str(payload.get("stage") or "").strip()
        stage_index = int(payload.get("stage_index") or 0)
        total_stages = int(payload.get("total_stages") or 0)
        if event == "stage_heartbeat":
            detail = str(payload.get("detail") or "").strip() or f"running {stage}"
            elapsed = self._format_duration_ms(int(payload.get("elapsed_ms") or 0))
            self._print_live_line(f"[{stamp}] projflow stage {stage_index}/{total_stages} running: {detail} [{elapsed}]")
            return
        self._clear_live_line()
        if event == "job_prepared":
            detail = f"prepared {payload.get('job_id')}"
        elif event == "pipeline_started":
            detail = f"pipeline started run_id={payload.get('run_id')} total_stages={payload.get('total_stages')}"
        elif event == "stage_start":
            detail = f"stage {stage_index}/{total_stages} started: {stage}"
        elif event == "stage_complete":
            duration_ms = int(payload.get("execution_duration_ms") or payload.get("stage_duration_ms") or 0)
            detail = f"stage {stage_index}/{total_stages} complete: {stage} ({duration_ms} ms)"
        elif event == "stage_failed":
            detail = f"stage {stage_index}/{total_stages} failed: {stage} ({payload.get('reason') or 'error'})"
        elif event == "review_render_start":
            detail = f"rendering review for run_id={payload.get('run_id')}"
        elif event == "review_render_complete":
            detail = f"review rendered for run_id={payload.get('run_id')}"
        elif event == "operator_decision_saved":
            detail = f"operator decision saved: {payload.get('decision') or ''}"
        elif event == "summary_persisted":
            detail = f"summary saved status={payload.get('status') or ''}"
        elif event == "pipeline_complete":
            detail = f"pipeline complete status={payload.get('status') or ''}"
        else:
            detail = event
        sys.stderr.write(f"[{stamp}] projflow {detail}\n")
        sys.stderr.flush()

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="projflow", description="Direct prompt execution for the deterministic pipeline.")
        parser.add_argument("prompt", nargs="*", help="Plain-English job request. No --prompt required.")
        parser.add_argument("--repo-root", default=str(self.repo_root))
        parser.add_argument("--job-id", default="")
        parser.add_argument("--semantic-provider", default="")
        parser.add_argument("--semantic-model", default="")
        parser.add_argument("--semantic-mode", default="")
        parser.add_argument("--no-auto-approve", action="store_true")
        parser.add_argument("--json", action="store_true")
        return parser

    def run(self, argv: Optional[List[str]] = None) -> int:
        parser = self._parser()
        args = parser.parse_args(argv)
        if not args.prompt:
            parser.print_help(sys.stderr)
            return 2
        prompt_text = " ".join(str(part) for part in args.prompt).strip()
        overrides = semantic_cli_overrides(provider=args.semantic_provider or None, model=args.semantic_model or None, mode=args.semantic_mode or None)
        summary = run_full_auto(
            repo_root=args.repo_root,
            prompt_text=prompt_text,
            job_id=args.job_id or None,
            config_overrides=overrides,
            auto_approve=not args.no_auto_approve,
            progress_callback=self._emit_progress,
        )
        if args.json:
            sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
            return 0
        lines = [
            f"job_id={summary.get('job_id')}",
            f"run_id={summary.get('run_id')}",
            f"status={summary.get('status')}",
            f"summary_json={summary.get('summary_paths', {}).get('json_path')}",
            f"summary_md={summary.get('summary_paths', {}).get('markdown_path')}",
            "selected_files=",
        ]
        for path in list(summary.get("selected_files") or []):
            lines.append(f"  - {path}")
        lines.append("stage_timings_ms=")
        for stage in list(summary.get("stage_timings") or []):
            lines.append(f"  - {stage.get('stage')}: {stage.get('subprocess_duration_ms') or stage.get('stage_duration_ms')} ms")
        provenance = dict(summary.get("retrieval_provenance") or {})
        lines.extend([
            f"retrieval_strategy={provenance.get('strategy')}",
            f"semantic_status={provenance.get('status') or provenance.get('semantic_status')}",
            f"semantic_requested_mode={provenance.get('requested_mode')}",
            f"semantic_effective_mode={provenance.get('effective_mode')}",
            f"semantic_requested_provider={provenance.get('requested_provider')}",
            f"semantic_effective_provider={provenance.get('effective_provider')}",
            f"semantic_requested_model={provenance.get('requested_model')}",
            f"semantic_effective_model={provenance.get('effective_model')}",
            f"patch_plan_path={summary.get('artifacts', {}).get('patch_plan_path')}",
            f"review_markdown_path={summary.get('artifacts', {}).get('review_markdown_path')}",
            f"operator_decision_path={summary.get('artifacts', {}).get('operator_decision_path')}",
        ])
        sys.stdout.write("\n".join(lines) + "\n")
        return 0
