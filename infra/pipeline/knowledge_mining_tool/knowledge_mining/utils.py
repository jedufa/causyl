from __future__ import annotations

import keyword
import math
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

DEFAULT_KNOWLEDGE_ROOT = "/home/user/causyl/infra/pipeline/knowledge"
DEFAULT_OUTPUT_ROOT_NAME = "knowledge_mining_output"
AUTO_MINED_DIRNAME = "zz_auto_mined"
TOOL_VERSION = "0.4.1"

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "__pycache__",
    ".idea",
    ".vscode",
    "node_modules",
    ".cache",
    ".eggs",
}

GENERIC_META_KEYS = {
    "metadata",
    "generated_at",
    "generated_by",
    "tool_version",
    "repo_root",
    "repo_slug",
    "knowledge_root",
    "proposal_type",
    "review_required",
    "confidence",
    "rationale",
    "evidence_paths",
    "evidence",
    "frequency",
    "count",
    "counts",
    "paths",
    "source",
    "sources",
    "notes",
    "description",
    "display_name",
    "kind",
    "canonical",
    "canonical_terms",
    "path",
    "paths",
    "path_patterns",
    "pattern",
    "patterns",
    "alias",
    "aliases",
    "synonym",
    "synonyms",
    "variant",
    "variants",
    "term",
    "terms",
    "key",
    "keys",
    "family",
    "families",
    "subsystem",
    "subsystems",
    "bucket",
    "buckets",
    "synonym_proposals",
    "path_pattern_proposals",
    "virtual_bucket_proposals",
    "subsystem_alias_proposals",
}

SHORT_TOKEN_ALLOWLIST = {
    "api", "app", "cli", "db", "ci", "cd", "ml", "ai", "io", "fs", "ui", "ux", "etl", "sdk", "sql", "orm", "aws", "gcp", "tls", "ssl", "jwt", "rpc", "dag",
}

STOPWORDS = {
    "the","and","for","with","from","that","this","these","those","into","onto","about","over","under","after","before","during","while","where","when","which","their","there","here","then","than","have","has","had","will","would","could","should","make","made","many","more","most","some","such","very","also","only","each","every","other","another","much","even","well","just","like","than","your","ours","ourselves","you","they","them","their","repo","repos","repository","repositories","module","modules","package","packages","python","py","code","file","files","folder","folders","path","paths","name","names","item","items","value","values","type","types","kind","kinds","data","info","true","false","none","null","tmp","temp","misc","stuff",
}
STOPWORDS.update(keyword.kwlist)
STOPWORDS.update({
    "typing","pathlib","datetime","annotations","json","yaml","sys","os","re","uuid","typing_extensions",
    "meta","events","result","results","report","reports","artifact","artifacts","draft","drafts",
    "dict","tuple","list","set","int","str","bool","float","dir","get","cmd","src","main","java","com",
    "fake","header","arg","args","iterable","dataclass","runtimeerror","common","script","scripts","static",
})

BUCKET_DETECTORS = {
    "tests_and_specs": {"tests","test","testing","spec","specs"},
    "docs_and_reference": {"docs","doc","documentation","reference","references","mkdocs","sphinx","readme"},
    "migrations_and_schema": {"migration","migrations","alembic","versions","schema","schemas"},
    "examples_and_samples": {"examples","example","samples","sample","demos","demo","tutorial","tutorials"},
    "generated_and_build_artifacts": {"generated","generator","build","dist","out","coverage","htmlcov","cache","compiled","artifacts","artifact","reports"},
    "ci_and_release": {"github","gitlab","ci","cd","release","releases","changelog"},
    "ui_assets_and_templates": {"template","templates","static","assets","asset","public","frontend"},
    "packaging_and_distribution": {"pyproject","setup","requirements","constraints","tox","nox","poetry","pdm","hatch","wheel","sdist"},
}

SUBSYSTEM_ALLOWLIST = {
    "adapters","adapter","api","apis","backend","backends","cli","client","clients","connector","connectors","controller","controllers","dag","dags","endpoint","endpoints","entity","entities","executor","executors","frontend","frontends","handler","handlers","hook","hooks","integration","integrations","job","jobs","model","models","operator","operators","pipeline","pipelines","plugin","plugins","provider","providers","queue","queues","registry","registries","router","routers","scheduler","schedulers","schema","schemas","serializer","serializers","service","services","task","tasks","view","views","worker","workers",
}

SUBSYSTEM_FAMILY_HINTS = {
    "api": "api_surface",
    "apis": "api_surface",
    "backend": "backend_logic",
    "backends": "backend_logic",
    "cli": "operator_interface",
    "dag": "workflow_orchestration",
    "dags": "workflow_orchestration",
    "entity": "domain_model",
    "entities": "domain_model",
    "executor": "workflow_orchestration",
    "executors": "workflow_orchestration",
    "hook": "integration_surface",
    "hooks": "integration_surface",
    "integration": "integration_surface",
    "integrations": "integration_surface",
    "job": "workflow_orchestration",
    "jobs": "workflow_orchestration",
    "model": "domain_model",
    "models": "domain_model",
    "operator": "workflow_orchestration",
    "operators": "workflow_orchestration",
    "provider": "integration_surface",
    "providers": "integration_surface",
    "queue": "workflow_orchestration",
    "queues": "workflow_orchestration",
    "registry": "registry_or_catalog",
    "registries": "registry_or_catalog",
    "router": "routing_and_dispatch",
    "routers": "routing_and_dispatch",
    "scheduler": "workflow_orchestration",
    "schedulers": "workflow_orchestration",
    "serializer": "api_surface",
    "serializers": "api_surface",
    "service": "service_layer",
    "services": "service_layer",
    "task": "workflow_orchestration",
    "tasks": "workflow_orchestration",
    "view": "ui_or_projection",
    "views": "ui_or_projection",
    "worker": "workflow_orchestration",
    "workers": "workflow_orchestration",
}

GENERIC_PATH_PATTERN_TOKENS = SUBSYSTEM_ALLOWLIST | {
    "agent", "agents", "contract", "contracts", "manifest", "manifests",
    "prompt", "prompts", "policy", "policies", "schema", "schemas",
    "session", "sessions", "registry", "registries",
}

PROPOSAL_ROOT_PREFIXES = {
    "tools", "tests", "docs", "server", "webui", "controlplane", "pipeline",
    "ledger", "mobile", "android", "governance", "lab", "assistant", "runtime",
}

VERB_LIKE_STOPWORDS = {
    "keep", "keeps", "look", "looks", "stay", "stays", "line", "lines",
    "clean", "build", "apply", "emit", "record", "format", "update", "updates",
    "read", "write", "reads", "writes", "classify", "classifies",
}

DEFAULT_LOW_SIGNAL_GLOBS = [
    "**/knowledge_drafts/**",
    "**/zz_auto_mined/**",
    "**/runtime/reports/**",
    "**/runtime/artifacts/**",
    "**/governance/ledger/**",
    "**/artifacts/**",
    "**/*.jsonl",
    "**/*.log",
    "**/*.result.json",
    "**/*.orig",
    "**/*.rej",
    "**/*Zone.Identifier",
    "**/tools/env_clone/manifests/**",
    "**/tools/benchmarks/fixtures/**",
]

LOW_SIGNAL_DIR_NAMES = {
    "artifacts","artifact","drafts","runtime","reports","report","tmp","temp","cache","coverage","htmlcov","knowledge_drafts",
}

LOW_SIGNAL_FILE_NAMES = {
    "meta.json","events.jsonl","jobs.jsonl","batch_summary.csv",
}

STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", set()))
COMMON_IMPORT_NOISE = STDLIB_MODULES | {"pytest", "pydantic", "yaml", "json", "typing_extensions"}

INSTANCE_TOKEN_RE = re.compile(
    r"^(?:\d{4}[_.-]?\d{2}[_.-]?\d{2}(?:[_.-]?\d{2,6})?|[a-f0-9]{7,}|[a-z]{0,3}\d[a-z0-9_]{1,}|\d+[a-z][a-z0-9_]*|pis_\d{8}.*)$",
    re.IGNORECASE,
)
DATEISH_RE = re.compile(r"\d{4}[_.-]?\d{2}[_.-]?\d{2}")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_root(repo_root: Path) -> Path:
    return Path.cwd() / DEFAULT_OUTPUT_ROOT_NAME / repo_slug_from_path(repo_root)


def repo_slug_from_path(repo_root: Path) -> str:
    slug = normalize_compound(repo_root.name or "repo")
    return slug or "repo"


def list_yaml_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted([path for path in root.rglob("*.yaml") if path.is_file()] + [path for path in root.rglob("*.yml") if path.is_file()])


def read_text_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def should_skip_directory(dirname: str) -> bool:
    return dirname in IGNORED_DIRS


def normalize_compound(value: str) -> str:
    value = value.strip().replace("-", "_").replace("/", "_").replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value


def split_identifier(value: str) -> list[str]:
    if not value:
        return []
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    parts = [part.lower() for part in value.split("_") if part]
    return parts


def singularize(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def lexical_variants(token: str) -> set[str]:
    forms = {token}
    singular = singularize(token)
    forms.add(singular)
    if singular.endswith("y"):
        forms.add(singular[:-1] + "ies")
    else:
        forms.add(singular + "s")
    return {form for form in forms if form}


def iter_term_forms(raw_value: str) -> Iterator[str]:
    seen: set[str] = set()
    normalized = normalize_compound(raw_value)
    if normalized and normalized not in seen:
        seen.add(normalized)
        yield normalized
    for token in split_identifier(raw_value):
        if token not in seen:
            seen.add(token)
            yield token
        singular = singularize(token)
        if singular not in seen:
            seen.add(singular)
            yield singular


def iter_identifier_forms(raw_value: str) -> Iterator[str]:
    seen: set[str] = set()
    pieces = split_identifier(raw_value)
    if "." not in raw_value and "/" not in raw_value and "\\" not in raw_value:
        normalized = normalize_compound(raw_value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            yield normalized
    for token in pieces:
        if token not in seen:
            seen.add(token)
            yield token
        singular = singularize(token)
        if singular not in seen:
            seen.add(singular)
            yield singular


def is_noise_term(term: str) -> bool:
    if not term:
        return True
    if term in STOPWORDS or term in VERB_LIKE_STOPWORDS:
        return True
    if len(term) < 2 and term not in SHORT_TOKEN_ALLOWLIST:
        return True
    if len(term) < 3 and term not in SHORT_TOKEN_ALLOWLIST:
        return True
    if term.isdigit():
        return True
    if INSTANCE_TOKEN_RE.match(term):
        return True
    if DATEISH_RE.search(term) and len(re.sub(r"\D", "", term)) >= 6:
        return True
    if term.startswith("test_") or term.endswith("_test"):
        return True
    if term.count("_") >= 4 and any(char.isdigit() for char in term):
        return True
    if "_" in term:
        parts = [part for part in term.split("_") if part]
        if len(parts) >= 3:
            return True
        if parts and parts[0] in PROPOSAL_ROOT_PREFIXES:
            return True
        if any(part in {"json", "yaml", "yml", "xml", "html", "md", "txt"} for part in parts):
            return True
    return False


def looks_like_path_pattern(value: str) -> bool:
    return any(marker in value for marker in ("*", "/", "\\"))




UI_BUCKET_DIR_TOKENS = {"static", "templates", "template", "assets", "asset", "public", "frontend"}
UI_BUCKET_EXTENSIONS = {".html", ".css", ".scss", ".sass", ".js", ".jsx", ".ts", ".tsx", ".svg"}
PACKAGING_FILENAMES = {
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "requirements.in",
    "requirements-dev.in", "setup.py", "setup.cfg", "tox.ini", "noxfile.py",
    "poetry.lock", "pdm.lock", "hatch.toml",
}
PACKAGING_STEMS = {"pyproject", "requirements", "setup", "tox", "noxfile", "constraints"}

SPECIAL_BUCKET_FILE_TRIGGERS = {
    "docs_and_reference": {"readme", "mkdocs"},
    "packaging_and_distribution": {"pyproject", "setup", "requirements", "constraints"},
}


def detect_bucket_hits(parts: Iterable[str], filename: str) -> set[str]:
    tokens = set()
    for part in parts:
        tokens.update(iter_term_forms(part))
    stem_tokens = set(iter_term_forms(Path(filename).stem))
    hits = set()
    for bucket, triggers in BUCKET_DETECTORS.items():
        bucket_tokens = set(tokens)
        bucket_tokens.update(stem_tokens.intersection(SPECIAL_BUCKET_FILE_TRIGGERS.get(bucket, set())))
        if bucket_tokens.intersection(triggers):
            hits.add(bucket)
    return hits


def is_low_signal_path(relpath: str) -> bool:
    norm = relpath.replace("\\", "/")
    parts = Path(norm).parts
    if Path(norm).name in LOW_SIGNAL_FILE_NAMES:
        return True
    if any(part in LOW_SIGNAL_DIR_NAMES for part in parts):
        return True
    lowered = norm.lower()
    if any(fragment in lowered for fragment in (
        "governance/ledger/", "knowledge_drafts/", "runtime/reports/", "runtime/artifacts/",
        "/artifacts/", "zz_auto_mined/", "tools/env_clone/manifests/", "tools/benchmarks/fixtures/"
    )):
        return True
    for pattern in DEFAULT_LOW_SIGNAL_GLOBS:
        if Path(norm).match(pattern):
            return True
    return False


def classify_path_signal(relpath: str) -> str:
    norm = relpath.replace("\\", "/")
    parts = Path(norm).parts
    if is_low_signal_path(norm):
        return "low_signal"
    if parts[:2] == ("tools", "pipeline") and len(parts) >= 3 and parts[2] == "knowledge":
        return "knowledge"
    if parts and parts[0] == "tests":
        return "test"
    if parts and parts[0] == "docs":
        return "docs"
    if any(part in {"src", "app", "agents", "tools"} for part in parts[:2]):
        return "source"
    return "default"


def is_high_signal_path_class(path_class: str) -> bool:
    return path_class in {"source", "test", "docs", "default"}


def is_noise_import_root(module_root: str) -> bool:
    return module_root in COMMON_IMPORT_NOISE


def is_structural_canonical_display(display: str) -> bool:
    tokens = [tok for tok in split_identifier(display) if tok]
    if not tokens:
        return False
    if len(tokens) > 3:
        return False
    if len(display) > 32 or "/" in display or display.startswith("_"):
        return False
    if any(tok in {"update", "apply", "look", "format", "build", "emit", "record"} for tok in tokens):
        return False
    return True


def is_generic_proposal_token(token: str) -> bool:
    if is_noise_term(token):
        return False
    if token in GENERIC_META_KEYS:
        return False
    if INSTANCE_TOKEN_RE.match(token):
        return False
    parts = [part for part in token.split("_") if part]
    if parts and parts[0] in PROPOSAL_ROOT_PREFIXES:
        return False
    return True




def filter_bucket_evidence_paths(bucket: str, paths: Iterable[str], *, limit: int = 8) -> list[str]:
    ordered = sorted(dict.fromkeys(paths))
    if bucket == "ui_assets_and_templates":
        filtered = []
        for relpath in ordered:
            p = Path(relpath)
            if any(part in UI_BUCKET_DIR_TOKENS for part in p.parts) or p.suffix.lower() in UI_BUCKET_EXTENSIONS:
                filtered.append(relpath)
        return filtered[:limit] or ordered[:limit]
    if bucket == "packaging_and_distribution":
        filtered = []
        for relpath in ordered:
            p = Path(relpath)
            name = p.name.lower()
            stem = p.stem.lower()
            if name in PACKAGING_FILENAMES or stem in PACKAGING_STEMS:
                filtered.append(relpath)
        return filtered[:limit] or ordered[:limit]
    return ordered[:limit]

def score_from_count(count: int, *, base: float, ceiling: float) -> float:
    if count <= 0:
        return base
    value = base + (ceiling - base) * (1.0 - math.exp(-count / 10.0))
    return round(min(max(value, base), ceiling), 2)
