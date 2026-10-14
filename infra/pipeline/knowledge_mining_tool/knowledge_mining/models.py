from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Evidence:
    frequency: int = 0
    sources: set[str] = field(default_factory=set)
    _path_hits: set[str] = field(default_factory=set, repr=False)
    path_class_counts: Counter[str] = field(default_factory=Counter, repr=False)

    def add(self, path: str, source: str, increment: int = 1, *, path_class: str = "default") -> None:
        self.frequency += increment
        self.sources.add(source)
        self._path_hits.add(path)
        self.path_class_counts[path_class] += increment

    @property
    def evidence_paths(self) -> list[str]:
        return sorted(self._path_hits)

    def primary_path_class(self) -> str:
        if not self.path_class_counts:
            return "default"
        return sorted(self.path_class_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def unique_top_levels(self) -> set[str]:
        tops: set[str] = set()
        for path in self._path_hits:
            parts = Path(path).parts
            if parts:
                tops.add(parts[0])
        return tops

    def unique_parent_tokens(self) -> set[str]:
        parents: set[str] = set()
        for path in self._path_hits:
            parts = Path(path).parts[:-1]
            if parts:
                parents.add('/'.join(parts[-2:]) if len(parts) >= 2 else parts[0])
        return parents

    def to_dict(self, limit_paths: int = 8) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "sources": sorted(self.sources),
            "path_classes": dict(self.path_class_counts.most_common()),
            "evidence_paths": self.evidence_paths[:limit_paths],
        }


@dataclass
class ScanResult:
    repo_root: Path
    repo_slug: str
    total_files: int = 0
    python_files: int = 0
    parse_errors: int = 0
    top_level_dirs: Counter[str] = field(default_factory=Counter)
    extensions: Counter[str] = field(default_factory=Counter)
    term_evidence: dict[str, Evidence] = field(default_factory=dict)
    directory_token_evidence: dict[str, Evidence] = field(default_factory=dict)
    bucket_evidence: dict[str, Evidence] = field(default_factory=dict)

    def top_terms(self, limit: int = 200) -> list[tuple[str, Evidence]]:
        return sorted(
            self.term_evidence.items(),
            key=lambda item: (-item[1].frequency, item[0]),
        )[:limit]

    def top_directory_tokens(self, limit: int = 200) -> list[tuple[str, Evidence]]:
        return sorted(
            self.directory_token_evidence.items(),
            key=lambda item: (-item[1].frequency, item[0]),
        )[:limit]


@dataclass
class KnowledgeModel:
    knowledge_root: Path
    yaml_files: list[Path] = field(default_factory=list)
    known_terms: set[str] = field(default_factory=set)
    canonical_terms: set[str] = field(default_factory=set)
    term_to_originals: dict[str, set[str]] = field(default_factory=dict)
    synonym_map: dict[str, set[str]] = field(default_factory=dict)
    known_path_patterns: set[str] = field(default_factory=set)
    known_path_tokens: set[str] = field(default_factory=set)

    def register_known_term(self, normalized_term: str, original: str, *, canonical: bool = False) -> None:
        if not normalized_term:
            return
        self.known_terms.add(normalized_term)
        originals = self.term_to_originals.setdefault(normalized_term, set())
        originals.add(original)
        if canonical:
            self.canonical_terms.add(normalized_term)

    def register_synonym(self, canonical: str, variant: str) -> None:
        values = self.synonym_map.setdefault(canonical, set())
        values.add(variant)

    def canonical_display_for(self, normalized_term: str) -> str:
        originals = sorted(self.term_to_originals.get(normalized_term, {normalized_term}))
        return originals[0]


@dataclass
class CandidateSynonym:
    canonical: str
    variants: list[str]
    confidence: float
    rationale: str
    evidence_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "variants": self.variants,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence_paths": self.evidence_paths,
        }


@dataclass
class CandidatePathPattern:
    pattern: str
    token: str
    confidence: float
    rationale: str
    evidence_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "token": self.token,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence_paths": self.evidence_paths,
        }


@dataclass
class CandidateVirtualBucket:
    bucket: str
    triggers: list[str]
    confidence: float
    rationale: str
    evidence_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "triggers": self.triggers,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence_paths": self.evidence_paths,
        }


@dataclass
class CandidateSubsystemAlias:
    alias: str
    suggested_family: str | None
    confidence: float
    rationale: str
    evidence_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "suggested_family": self.suggested_family,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence_paths": self.evidence_paths,
        }


@dataclass
class AnalyzerOutput:
    unknown_terms: list[dict[str, Any]]
    synonym_proposals: list[CandidateSynonym]
    path_pattern_proposals: list[CandidatePathPattern]
    virtual_bucket_proposals: list[CandidateVirtualBucket]
    subsystem_alias_proposals: list[CandidateSubsystemAlias]


@dataclass
class ProposalFile:
    relpath: Path
    content: str
    proposal_type: str
    proposal_count: int


@dataclass
class DiffArtifact:
    relpath: Path
    patch_path: Path
    unified_diff: str
    proposal_count: int


@dataclass
class RunArtifacts:
    output_root: Path
    reports_dir: Path
    generated_dir: Path
    diffs_dir: Path
    logs_dir: Path
    log_path: Path
