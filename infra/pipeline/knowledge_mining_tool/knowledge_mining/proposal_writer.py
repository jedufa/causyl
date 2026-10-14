from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import AnalyzerOutput, KnowledgeModel, ProposalFile, RunArtifacts, ScanResult
from .utils import AUTO_MINED_DIRNAME, TOOL_VERSION, ensure_dir


class ProposalWriter:
    def __init__(self, *, logger: logging.Logger, max_report_items: int = 200) -> None:
        self.logger = logger
        self.max_report_items = max_report_items

    def write_reports(
        self,
        *,
        scan_result: ScanResult,
        knowledge_model: KnowledgeModel,
        analyzer_output: AnalyzerOutput,
        artifacts: RunArtifacts,
    ) -> None:
        ensure_dir(artifacts.reports_dir)
        self._write_json(
            artifacts.reports_dir / "scan_summary.json",
            {
                "repo_root": str(scan_result.repo_root),
                "repo_slug": scan_result.repo_slug,
                "knowledge_root": str(knowledge_model.knowledge_root),
                "total_files": scan_result.total_files,
                "python_files": scan_result.python_files,
                "parse_errors": scan_result.parse_errors,
                "top_level_dirs": dict(scan_result.top_level_dirs.most_common(50)),
                "extensions": dict(scan_result.extensions.most_common(50)),
                "knowledge_yaml_files": [str(path) for path in knowledge_model.yaml_files],
            },
        )
        self._write_json(
            artifacts.reports_dir / "token_frequencies.json",
            [
                {
                    "term": term,
                    **evidence.to_dict(limit_paths=8),
                }
                for term, evidence in scan_result.top_terms(limit=1000)
            ],
        )
        self._write_json(
            artifacts.reports_dir / "top_directory_tokens.json",
            [
                {
                    "token": token,
                    **evidence.to_dict(limit_paths=8),
                }
                for token, evidence in scan_result.top_directory_tokens(limit=500)
            ],
        )
        self._write_json(artifacts.reports_dir / "unknown_terms.json", analyzer_output.unknown_terms)
        self._write_json(
            artifacts.reports_dir / "proposal_counts.json",
            {
                "synonyms": len(analyzer_output.synonym_proposals),
                "path_patterns": len(analyzer_output.path_pattern_proposals),
                "virtual_buckets": len(analyzer_output.virtual_bucket_proposals),
                "subsystem_aliases": len(analyzer_output.subsystem_alias_proposals),
            },
        )

    def build_overlay_files(
        self,
        *,
        scan_result: ScanResult,
        knowledge_model: KnowledgeModel,
        analyzer_output: AnalyzerOutput,
        artifacts: RunArtifacts,
    ) -> list[ProposalFile]:
        base_rel_dir = Path(AUTO_MINED_DIRNAME) / scan_result.repo_slug
        generated_root = artifacts.generated_dir / "knowledge_overlay"
        ensure_dir(generated_root)
        now = datetime.now(timezone.utc).isoformat()

        proposal_files: list[ProposalFile] = []
        proposal_specs: list[tuple[str, str, list[dict[str, Any]]]] = [
            ("synonyms", "synonym_proposals", [item.to_dict() for item in analyzer_output.synonym_proposals]),
            ("path_patterns", "path_pattern_proposals", [item.to_dict() for item in analyzer_output.path_pattern_proposals]),
            ("virtual_buckets", "virtual_bucket_proposals", [item.to_dict() for item in analyzer_output.virtual_bucket_proposals]),
            ("subsystem_aliases", "subsystem_alias_proposals", [item.to_dict() for item in analyzer_output.subsystem_alias_proposals]),
        ]

        for proposal_type, payload_key, items in proposal_specs:
            if not items:
                continue
            content = {
                "metadata": {
                    "generated_at": now,
                    "generated_by": "knowledge_mining_tool",
                    "tool_version": TOOL_VERSION,
                    "repo_root": str(scan_result.repo_root),
                    "repo_slug": scan_result.repo_slug,
                    "knowledge_root": str(knowledge_model.knowledge_root),
                    "proposal_type": proposal_type,
                    "review_required": True,
                },
                payload_key: items,
            }
            relpath = base_rel_dir / f"{proposal_type}.yaml"
            text = yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
            target_path = generated_root / relpath
            ensure_dir(target_path.parent)
            target_path.write_text(text, encoding="utf-8")
            proposal_files.append(
                ProposalFile(
                    relpath=relpath,
                    content=text,
                    proposal_type=proposal_type,
                    proposal_count=len(items),
                )
            )
        return proposal_files

    def write_summary(
        self,
        *,
        scan_result: ScanResult,
        knowledge_model: KnowledgeModel,
        analyzer_output: AnalyzerOutput,
        artifacts: RunArtifacts,
        proposal_files: list[ProposalFile],
        diff_paths: list[Path],
    ) -> None:
        summary_path = artifacts.output_root / "summary.md"
        lines: list[str] = []
        lines.append("# Knowledge Mining Summary")
        lines.append("")
        lines.append("## Inputs")
        lines.append("")
        lines.append(f"- Repo root: `{scan_result.repo_root}`")
        lines.append(f"- Knowledge root: `{knowledge_model.knowledge_root}`")
        lines.append(f"- Repo slug: `{scan_result.repo_slug}`")
        lines.append("")
        lines.append("## Scan")
        lines.append("")
        lines.append(f"- Total files: {scan_result.total_files}")
        lines.append(f"- Python files: {scan_result.python_files}")
        lines.append(f"- Python parse errors: {scan_result.parse_errors}")
        lines.append(f"- Knowledge YAML files loaded: {len(knowledge_model.yaml_files)}")
        lines.append("")
        lines.append("## Proposal counts")
        lines.append("")
        lines.append(f"- Synonym additions: {len(analyzer_output.synonym_proposals)}")
        lines.append(f"- Path-pattern additions: {len(analyzer_output.path_pattern_proposals)}")
        lines.append(f"- Virtual buckets: {len(analyzer_output.virtual_bucket_proposals)}")
        lines.append(f"- Subsystem aliases: {len(analyzer_output.subsystem_alias_proposals)}")
        lines.append("")

        if analyzer_output.unknown_terms:
            lines.append("## Top unknown terms")
            lines.append("")
            for item in analyzer_output.unknown_terms[:25]:
                lines.append(
                    f"- `{item['term']}` ({item['frequency']}) via {', '.join(item['sources'])}"
                )
            lines.append("")

        if proposal_files:
            lines.append("## Generated overlay files")
            lines.append("")
            for proposal_file in proposal_files:
                lines.append(f"- `{proposal_file.relpath.as_posix()}` ({proposal_file.proposal_count} items)")
            lines.append("")

        if diff_paths:
            lines.append("## Diff files")
            lines.append("")
            for diff_path in diff_paths:
                rel = diff_path.relative_to(artifacts.output_root).as_posix()
                lines.append(f"- `{rel}`")
            lines.append("")

        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
