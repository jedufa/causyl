from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .diff_writer import DiffWriter
from .gap_analyzer import GapAnalyzer
from .knowledge_loader import KnowledgeLoader
from .models import RunArtifacts
from .proposal_writer import ProposalWriter
from .scanner import RepoScanner
from .utils import DEFAULT_KNOWLEDGE_ROOT, TOOL_VERSION, default_output_root, ensure_dir

ALLOWED_ONLY_TYPES = {"synonyms", "path_patterns", "virtual_buckets", "subsystems"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge_miner",
        description="Scan a repository, identify likely taxonomy gaps against a YAML knowledge base, and emit reviewable diff patches.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the downloaded repository to scan. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--knowledge-root",
        default=DEFAULT_KNOWLEDGE_ROOT,
        help=f"Path to the canonical knowledge directory. Defaults to {DEFAULT_KNOWLEDGE_ROOT!r}.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory where reports, generated overlay YAML, logs, and patch files are written. Defaults to ./knowledge_mining_output/<repo-name>.",
    )
    parser.add_argument(
        "--include-glob",
        action="append",
        default=[],
        help="Optional glob filter. Can be supplied multiple times. Example: --include-glob 'src/**'",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Optional exclude glob. Can be supplied multiple times. Example: --exclude-glob 'tests/**'",
    )
    parser.add_argument(
        "--limit-files",
        type=int,
        default=None,
        help="Optional cap on the number of files scanned. Useful for dry runs.",
    )
    parser.add_argument(
        "--only-type",
        action="append",
        choices=sorted(ALLOWED_ONLY_TYPES),
        default=[],
        help="Restrict proposal generation to one or more types. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--min-term-frequency",
        type=int,
        default=6,
        help="Minimum term frequency before a term is considered for unknown-term reporting and synonym proposals. Default: 6.",
    )
    parser.add_argument(
        "--min-dir-frequency",
        type=int,
        default=4,
        help="Minimum directory-token frequency before proposing path patterns or subsystem aliases. Default: 4.",
    )
    parser.add_argument(
        "--min-bucket-hits",
        type=int,
        default=3,
        help="Minimum bucket-trigger count before proposing a virtual bucket. Default: 3.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="Emit a progress log line every N scanned files. Default: 500.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce console logging to warnings and errors.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"knowledge_miner {TOOL_VERSION}",
    )
    return parser


def configure_logging(log_path: Path, *, debug: bool, quiet: bool) -> logging.Logger:
    ensure_dir(log_path.parent)
    logger = logging.getLogger("knowledge_mining")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if quiet:
        console_level = logging.WARNING
    elif debug:
        console_level = logging.DEBUG
    else:
        console_level = logging.INFO

    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


def build_artifacts(output_root: Path) -> RunArtifacts:
    reports_dir = ensure_dir(output_root / "reports")
    generated_dir = ensure_dir(output_root / "generated")
    diffs_dir = ensure_dir(output_root / "diffs")
    logs_dir = ensure_dir(output_root / "logs")
    log_path = logs_dir / "run.log"
    return RunArtifacts(
        output_root=ensure_dir(output_root),
        reports_dir=reports_dir,
        generated_dir=generated_dir,
        diffs_dir=diffs_dir,
        logs_dir=logs_dir,
        log_path=log_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.exists():
        parser.error(f"Repo root does not exist: {repo_root}")
    if not repo_root.is_dir():
        parser.error(f"Repo root is not a directory: {repo_root}")

    knowledge_root = Path(args.knowledge_root).expanduser()
    output_root = Path(args.output_root).expanduser() if args.output_root else default_output_root(repo_root)
    artifacts = build_artifacts(output_root)
    logger = configure_logging(artifacts.log_path, debug=args.debug, quiet=args.quiet)

    only_types = set(args.only_type or ALLOWED_ONLY_TYPES)
    logger.info("Starting knowledge mining")
    logger.info("Repo root: %s", repo_root)
    logger.info("Knowledge root: %s", knowledge_root)
    logger.info("Output root: %s", output_root)
    logger.info("Proposal types: %s", ", ".join(sorted(only_types)))

    scanner = RepoScanner(
        repo_root,
        logger=logger,
        include_globs=args.include_glob,
        exclude_globs=args.exclude_glob,
        limit_files=args.limit_files,
        progress_every=args.progress_every,
    )
    logger.info("Scanning repository files...")
    scan_result = scanner.scan()

    loader = KnowledgeLoader(knowledge_root, logger=logger)
    knowledge_model = loader.load()

    analyzer = GapAnalyzer(
        scan_result,
        knowledge_model,
        logger=logger,
        min_term_frequency=args.min_term_frequency,
        min_dir_frequency=args.min_dir_frequency,
        min_bucket_hits=args.min_bucket_hits,
    )
    logger.info("Analyzing taxonomy gaps...")
    analyzer_output = analyzer.analyze(only_types=only_types)

    proposal_writer = ProposalWriter(logger=logger)
    logger.info("Writing reports...")
    proposal_writer.write_reports(
        scan_result=scan_result,
        knowledge_model=knowledge_model,
        analyzer_output=analyzer_output,
        artifacts=artifacts,
    )
    logger.info("Writing generated overlay YAML files...")
    proposal_files = proposal_writer.build_overlay_files(
        scan_result=scan_result,
        knowledge_model=knowledge_model,
        analyzer_output=analyzer_output,
        artifacts=artifacts,
    )

    diff_writer = DiffWriter(logger=logger)
    logger.info("Generating unified diffs...")
    diff_artifacts = diff_writer.write_diffs(
        knowledge_root=knowledge_root,
        proposal_files=proposal_files,
        artifacts=artifacts,
    )
    proposal_writer.write_summary(
        scan_result=scan_result,
        knowledge_model=knowledge_model,
        analyzer_output=analyzer_output,
        artifacts=artifacts,
        proposal_files=proposal_files,
        diff_paths=[artifact.patch_path for artifact in diff_artifacts],
    )

    logger.info("Done")
    logger.info("Summary: %s", artifacts.output_root / "summary.md")
    logger.info("Log file: %s", artifacts.log_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
