from __future__ import annotations

import difflib
import logging
from .models import (
    AnalyzerOutput,
    CandidatePathPattern,
    CandidateSubsystemAlias,
    CandidateSynonym,
    CandidateVirtualBucket,
    Evidence,
    KnowledgeModel,
    ScanResult,
)
from .utils import (
    BUCKET_DETECTORS,
    COMMON_IMPORT_NOISE,
    SUBSYSTEM_ALLOWLIST,
    SUBSYSTEM_FAMILY_HINTS,
    GENERIC_PATH_PATTERN_TOKENS,
    VERB_LIKE_STOPWORDS,
    is_generic_proposal_token,
    is_high_signal_path_class,
    is_noise_term,
    is_structural_canonical_display,
    lexical_variants,
    score_from_count,
    singularize,
    filter_bucket_evidence_paths,
)

GENERIC_DIR_TOKENS = {
    "src","lib","libs","bin","tools","tool","scripts","script","data","dataset","datasets","vendor","third_party","internal","external","common","core","base","shared","utils","util","helpers","helper","main","java","com","kotlin","android","mobile",
}

class GapAnalyzer:
    def __init__(self, scan_result: ScanResult, knowledge_model: KnowledgeModel, *, logger: logging.Logger, min_term_frequency: int = 6, min_dir_frequency: int = 4, min_bucket_hits: int = 3, max_unknown_terms: int = 200, max_proposals_per_type: int = 50) -> None:
        self.scan_result = scan_result
        self.knowledge_model = knowledge_model
        self.logger = logger
        self.min_term_frequency = min_term_frequency
        self.min_dir_frequency = min_dir_frequency
        self.min_bucket_hits = min_bucket_hits
        self.max_unknown_terms = max_unknown_terms
        self.max_proposals_per_type = max_proposals_per_type
        self._canonical_pool = sorted(term for term in (knowledge_model.canonical_terms or knowledge_model.known_terms) if is_generic_proposal_token(term))

    def analyze(self, only_types: set[str] | None = None) -> AnalyzerOutput:
        only_types = only_types or {"synonyms", "path_patterns", "virtual_buckets", "subsystems"}
        unknown_terms = self._build_unknown_terms()
        synonym_proposals = self._build_synonym_proposals(unknown_terms) if "synonyms" in only_types else []
        path_pattern_proposals = self._build_path_pattern_proposals() if "path_patterns" in only_types else []
        virtual_bucket_proposals = self._build_virtual_bucket_proposals() if "virtual_buckets" in only_types else []
        subsystem_alias_proposals = self._build_subsystem_alias_proposals() if "subsystems" in only_types else []
        self.logger.info("Analyzer output: %s unknown terms, %s synonym proposals, %s path patterns, %s virtual buckets, %s subsystem aliases", len(unknown_terms), len(synonym_proposals), len(path_pattern_proposals), len(virtual_bucket_proposals), len(subsystem_alias_proposals))
        return AnalyzerOutput(unknown_terms=unknown_terms, synonym_proposals=synonym_proposals, path_pattern_proposals=path_pattern_proposals, virtual_bucket_proposals=virtual_bucket_proposals, subsystem_alias_proposals=subsystem_alias_proposals)

    def _evidence_is_usable(self, evidence: Evidence) -> bool:
        structural = sum(evidence.path_class_counts.get(name, 0) for name in ("source", "default", "docs", "knowledge"))
        test_only = evidence.path_class_counts.get("test", 0) and structural == 0
        if test_only:
            return False
        if structural <= 0:
            return False
        if evidence.frequency >= 10 and structural < max(3, int(evidence.frequency * 0.2)):
            return False
        if not any(is_high_signal_path_class(name) for name in evidence.path_class_counts):
            return False
        return True

    def _is_structural_synonym_candidate(self, canonical: str, variants: list[str]) -> bool:
        if not canonical or canonical in VERB_LIKE_STOPWORDS:
            return False
        if " " in canonical or "_" in canonical or "/" in canonical:
            return False
        if canonical not in GENERIC_PATH_PATTERN_TOKENS and canonical not in self.knowledge_model.canonical_terms:
            return False
        filtered = [v for v in variants if v and v not in VERB_LIKE_STOPWORDS]
        if not filtered:
            return False
        if all(singularize(v) == singularize(canonical) for v in filtered):
            return canonical in GENERIC_PATH_PATTERN_TOKENS or canonical in {"archive", "manifest", "policy", "schema", "registry", "agent"}
        return True

    def _is_taxonomy_worthy_unknown(self, term: str, evidence: Evidence) -> bool:
        sources = set(evidence.sources)
        bucket_trigger_terms = {token for values in BUCKET_DETECTORS.values() for token in values}
        if term in bucket_trigger_terms or term in COMMON_IMPORT_NOISE:
            return False
        if sources and sources.issubset({"function", "import_name"}):
            return False
        if "path" not in sources and not ({"class", "base_class", "import_from", "import"} & sources):
            return False
        if "_" in term and "path" not in sources:
            return False
        return True

    def _choose_canonical_path_token(self, token: str) -> str | None:
        singular = singularize(token)
        if singular in GENERIC_PATH_PATTERN_TOKENS:
            return singular
        if token in GENERIC_PATH_PATTERN_TOKENS:
            return token
        return None

    def _build_unknown_terms(self) -> list[dict[str, object]]:
        unknown: list[tuple[str, Evidence]] = []
        for term, evidence in self.scan_result.term_evidence.items():
            if evidence.frequency < self.min_term_frequency:
                continue
            if term in self.knowledge_model.known_terms:
                continue
            if is_noise_term(term) or not is_generic_proposal_token(term):
                continue
            if not self._evidence_is_usable(evidence):
                continue
            if not self._is_taxonomy_worthy_unknown(term, evidence):
                continue
            unknown.append((term, evidence))
        unknown.sort(key=lambda item: (-item[1].frequency, item[0]))
        payload: list[dict[str, object]] = []
        for term, evidence in unknown[: self.max_unknown_terms]:
            payload.append({"term": term, "frequency": evidence.frequency, "sources": sorted(evidence.sources), "path_class": evidence.primary_path_class(), "evidence_paths": evidence.evidence_paths[:8]})
        return payload

    def _build_synonym_proposals(self, unknown_terms: list[dict[str, object]]) -> list[CandidateSynonym]:
        grouped: dict[str, dict[str, object]] = {}
        for item in unknown_terms:
            term = str(item["term"])
            evidence = self.scan_result.term_evidence[term]
            match_norm, rationale, confidence = self._match_known_term(term)
            if not match_norm or match_norm not in self.knowledge_model.term_to_originals:
                continue
            if term in self.knowledge_model.synonym_map.get(match_norm, set()):
                continue
            canonical_display = self.knowledge_model.canonical_display_for(match_norm)
            if not is_structural_canonical_display(canonical_display):
                canonical_display = match_norm
            if not self._is_structural_synonym_candidate(canonical_display, [term]):
                continue
            bucket = grouped.setdefault(canonical_display, {"variants": set(), "evidence_paths": set(), "confidence": confidence, "rationales": []})
            bucket["variants"].add(term)
            bucket["evidence_paths"].update(evidence.evidence_paths[:8])
            bucket["confidence"] = min(float(bucket["confidence"]), confidence)
            bucket["rationales"].append(rationale)
        proposals: list[CandidateSynonym] = []
        for canonical, payload in grouped.items():
            variants = sorted(payload["variants"])
            rationale = "; ".join(sorted(set(str(entry) for entry in payload["rationales"])))
            if not self._is_structural_synonym_candidate(canonical, variants):
                continue
            proposals.append(CandidateSynonym(canonical=canonical, variants=variants, confidence=round(float(payload["confidence"]), 2), rationale=rationale, evidence_paths=sorted(payload["evidence_paths"])[:8]))
        proposals.sort(key=lambda item: (item.canonical, item.variants))
        return proposals[: self.max_proposals_per_type]

    def _match_known_term(self, term: str) -> tuple[str | None, str | None, float]:
        variants = lexical_variants(term)
        singular = singularize(term)
        for variant in variants:
            if variant in self.knowledge_model.canonical_terms and is_generic_proposal_token(variant):
                return variant, f"{term!r} normalizes to existing canonical term {variant!r}", 0.96
        for variant in variants:
            if variant in self.knowledge_model.known_terms and is_generic_proposal_token(variant):
                return variant, f"{term!r} normalizes to existing known term {variant!r}", 0.94
        if not self._canonical_pool:
            return None, None, 0.0
        close_matches = difflib.get_close_matches(singular, self._canonical_pool, n=1, cutoff=0.93)
        if close_matches:
            matched = close_matches[0]
            return matched, f"{term!r} closely matches canonical term {matched!r}", 0.91
        return None, None, 0.0

    def _build_path_pattern_proposals(self) -> list[CandidatePathPattern]:
        grouped: dict[str, dict[str, object]] = {}
        repo_tokens = set(self.scan_result.repo_slug.split("-"))
        for token, evidence in self.scan_result.top_directory_tokens(limit=500):
            if evidence.frequency < self.min_dir_frequency:
                continue
            canonical_token = self._choose_canonical_path_token(token)
            if not canonical_token:
                continue
            if canonical_token in repo_tokens or canonical_token in GENERIC_DIR_TOKENS or canonical_token in self.knowledge_model.known_path_tokens:
                continue
            if not is_generic_proposal_token(canonical_token):
                continue
            if not self._evidence_is_usable(evidence):
                continue
            if len(evidence.unique_top_levels()) < 2 and len(evidence.unique_parent_tokens()) < 3:
                continue
            structural = sum(evidence.path_class_counts.get(name, 0) for name in ("source", "default", "docs", "knowledge"))
            if structural < max(self.min_dir_frequency, 3):
                continue
            if canonical_token not in SUBSYSTEM_ALLOWLIST and evidence.frequency < (self.min_dir_frequency * 3):
                continue
            bucket = grouped.setdefault(canonical_token, {"frequency": 0, "paths": set()})
            bucket["frequency"] += evidence.frequency
            bucket["paths"].update(evidence.evidence_paths[:8])
        proposals: list[CandidatePathPattern] = []
        for token, payload in grouped.items():
            evidence_paths = sorted(payload["paths"])[:8]
            frequency = int(payload["frequency"])
            proposals.append(CandidatePathPattern(
                pattern=f"**/{token}/**",
                token=token,
                confidence=score_from_count(frequency, base=0.58, ceiling=0.93),
                rationale=f"Repeated directory token {token!r} appears across multiple high-signal repository areas and is not covered by known path patterns.",
                evidence_paths=evidence_paths,
            ))
        proposals.sort(key=lambda item: (-len(item.evidence_paths), item.token))
        return proposals[: self.max_proposals_per_type]

    def _build_virtual_bucket_proposals(self) -> list[CandidateVirtualBucket]:
        proposals: list[CandidateVirtualBucket] = []
        for bucket, evidence in sorted(self.scan_result.bucket_evidence.items(), key=lambda item: (-item[1].frequency, item[0])):
            if evidence.frequency < self.min_bucket_hits:
                continue
            if bucket in self.knowledge_model.known_terms:
                continue
            if not self._evidence_is_usable(evidence):
                continue
            triggers = sorted(BUCKET_DETECTORS.get(bucket, []))
            evidence_paths = filter_bucket_evidence_paths(bucket, evidence.evidence_paths, limit=8)
            if bucket == "ui_assets_and_templates" and len(evidence_paths) < 3:
                continue
            if bucket == "packaging_and_distribution" and len(evidence_paths) < 2:
                continue
            proposals.append(CandidateVirtualBucket(bucket=bucket, triggers=triggers, confidence=score_from_count(len(evidence_paths), base=0.62, ceiling=0.95), rationale=f"Repository paths repeatedly trigger the virtual bucket {bucket!r} via recognisable directory and file markers in high-signal areas.", evidence_paths=evidence_paths))
        return proposals[: self.max_proposals_per_type]

    def _build_subsystem_alias_proposals(self) -> list[CandidateSubsystemAlias]:
        proposals: list[CandidateSubsystemAlias] = []
        for token, evidence in self.scan_result.top_directory_tokens(limit=500):
            if evidence.frequency < self.min_dir_frequency or token not in SUBSYSTEM_ALLOWLIST or token in self.knowledge_model.known_terms:
                continue
            if not self._evidence_is_usable(evidence):
                continue
            if len(evidence.unique_top_levels()) < 2 and len(evidence.unique_parent_tokens()) < 3:
                continue
            family_hint = SUBSYSTEM_FAMILY_HINTS.get(token)
            proposals.append(CandidateSubsystemAlias(alias=token, suggested_family=family_hint, confidence=score_from_count(evidence.frequency, base=0.6, ceiling=0.94), rationale=f"Directory token {token!r} is a frequent structural surface in multiple high-signal areas and may deserve an explicit subsystem alias.", evidence_paths=evidence.evidence_paths[:8]))
        proposals.sort(key=lambda item: (-len(item.evidence_paths), item.alias))
        return proposals[: self.max_proposals_per_type]
