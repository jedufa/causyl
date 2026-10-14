from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .core import PipelineError, is_path_allowed, utc_now_iso

EXCLUDED_DIR_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache", "jobs"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".js", ".ts", ".tsx", ".jsx", ".sh", ".go", ".java", ".rs"}
SEMANTIC_ACTIVE_MODES = {"rank_only", "retrieve_and_rank"}
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
LONG_WHITESPACE_RE = re.compile(r"[ \t]{3,}")


@dataclass
class SemanticResult:
    path: str
    semantic_score: float
    chunk_id: str
    provider: str
    model: str
    query_text: str
    retrieved_at: str
    normalized: bool = True
    source: str = "semantic_only"
    chunk_preview: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "semantic_score": round(float(self.semantic_score), 6),
            "chunk_id": self.chunk_id,
            "provider": self.provider,
            "model": self.model,
            "query_text": self.query_text,
            "retrieved_at": self.retrieved_at,
            "normalized": bool(self.normalized),
            "source": self.source,
            "chunk_preview": self.chunk_preview,
        }


class SemanticProvider:
    provider_name = "none"

    def __init__(self) -> None:
        self.last_embed_stats: Dict[str, Any] = {}

    def embed_texts(self, texts: Sequence[str], *, model_name: str, timeout_seconds: int) -> List[List[float]]:
        raise NotImplementedError


class StubSemanticProvider(SemanticProvider):
    provider_name = "stub"

    def __init__(self) -> None:
        super().__init__()

    def embed_texts(self, texts: Sequence[str], *, model_name: str, timeout_seconds: int) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * 64
            for token in str(text or "").lower().split():
                h = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
                vec[h % 64] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        self.last_embed_stats = {
            "provider": self.provider_name,
            "model": model_name,
            "requested_count": len(texts),
            "embedded_count": len(out),
            "failed_count": 0,
            "mode": "stub",
        }
        return out


class OllamaSemanticProvider(SemanticProvider):
    provider_name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.base_urls = self._candidate_base_urls(self.base_url)

    @staticmethod
    def _candidate_base_urls(base_url: str) -> List[str]:
        urls: List[str] = []
        primary = str(base_url or "http://localhost:11434").rstrip("/")
        if primary:
            urls.append(primary)
        if primary.startswith("http://localhost:"):
            urls.append(primary.replace("http://localhost:", "http://127.0.0.1:", 1))
        elif primary.startswith("https://localhost:"):
            urls.append(primary.replace("https://localhost:", "https://127.0.0.1:", 1))
        out: List[str] = []
        for item in urls:
            if item not in out:
                out.append(item)
        return out

    @staticmethod
    def _sanitize_text(text: str, *, max_chars: int = 2000) -> str:
        cleaned = str(text or "").encode("utf-8", "ignore").decode("utf-8", "ignore")
        cleaned = cleaned.replace("\ufffd", " ")
        cleaned = CONTROL_CHARS_RE.sub(" ", cleaned)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = LONG_WHITESPACE_RE.sub("  ", cleaned)
        compact_lines: List[str] = []
        for line in cleaned.splitlines():
            compact_lines.append(line[:600])
        cleaned = "\n".join(compact_lines)
        if len(cleaned) > max_chars:
            head = max(200, max_chars // 2)
            tail = max(120, max_chars // 4)
            middle = max(120, max_chars - head - tail - 10)
            center = max(0, len(cleaned) // 2 - middle // 2)
            cleaned = cleaned[:head] + "\n...\n" + cleaned[center : center + middle] + "\n...\n" + cleaned[-tail:]
        return cleaned

    @staticmethod
    def _normalize_vectors(emb: Any, *, expected_count: int, model_name: str) -> List[List[float]]:
        vectors: List[List[float]] = []
        if isinstance(emb, list) and emb and isinstance(emb[0], list):
            vectors = [[float(v) for v in vec] for vec in emb]
        elif isinstance(emb, list) and emb and not isinstance(emb[0], list) and expected_count == 1:
            vectors = [[float(v) for v in emb]]
        if len(vectors) != expected_count:
            raise PipelineError(
                "semantic_provider_failure",
                "ollama semantic provider returned malformed embeddings",
                details={"provider": "ollama", "model": model_name, "expected_count": expected_count, "actual_count": len(vectors)},
                retryable=True,
                exit_code=7,
            )
        out: List[List[float]] = []
        for vec in vectors:
            norm = math.sqrt(sum(float(v) * float(v) for v in vec)) or 1.0
            out.append([float(v) / norm for v in vec])
        return out

    def _post_embed(self, base_url: str, payload_obj: Dict[str, Any], *, timeout_seconds: int) -> Dict[str, Any]:
        url = f"{base_url}/api/embed"
        payload = json.dumps(payload_obj).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _per_text_embed(self, texts: Sequence[str], *, model_name: str, timeout_seconds: int) -> List[List[float]]:
        out: List[List[float]] = []
        failed_indices: List[int] = []
        failed_errors: List[Dict[str, Any]] = []
        for idx, raw_text in enumerate(texts):
            text = self._sanitize_text(raw_text)
            vec: Optional[List[float]] = None
            last_exc: Optional[Exception] = None
            for base_url in self.base_urls:
                try:
                    data = self._post_embed(base_url, {"model": model_name, "input": text}, timeout_seconds=timeout_seconds)
                    vec = self._normalize_vectors(data.get("embeddings") or data.get("embedding"), expected_count=1, model_name=model_name)[0]
                    break
                except Exception as exc:
                    last_exc = exc
            if vec is None:
                failed_indices.append(idx)
                out.append([])
                if len(failed_errors) < 5:
                    failed_errors.append(
                        {
                            "index": idx,
                            "text_chars": len(text),
                            "error": str(last_exc or "unknown ollama embed failure"),
                        }
                    )
            else:
                out.append(vec)
        self.last_embed_stats = {
            "provider": self.provider_name,
            "model": model_name,
            "requested_count": len(texts),
            "embedded_count": sum(1 for vec in out if vec),
            "failed_count": len(failed_indices),
            "failed_indices_sample": failed_indices[:10],
            "failed_error_sample": failed_errors,
            "base_urls": list(self.base_urls),
            "mode": "per_text_partial",
        }
        return out

    def embed_texts(self, texts: Sequence[str], *, model_name: str, timeout_seconds: int) -> List[List[float]]:
        normalized_texts = [self._sanitize_text(text) for text in texts]
        batch_size = max(1, min(16, len(normalized_texts) or 1))
        all_vectors: List[List[float]] = []
        batch_errors: List[str] = []
        fallback_chunks = 0
        for offset in range(0, len(normalized_texts), batch_size):
            chunk = normalized_texts[offset : offset + batch_size]
            chunk_vectors: Optional[List[List[float]]] = None
            last_error: Optional[Exception] = None
            for base_url in self.base_urls:
                try:
                    data = self._post_embed(base_url, {"model": model_name, "input": chunk}, timeout_seconds=timeout_seconds)
                    chunk_vectors = self._normalize_vectors(data.get("embeddings") or data.get("embedding"), expected_count=len(chunk), model_name=model_name)
                    break
                except Exception as exc:
                    last_error = exc
            if chunk_vectors is None:
                fallback_chunks += 1
                if last_error and len(batch_errors) < 5:
                    batch_errors.append(str(last_error))
                chunk_vectors = self._per_text_embed(chunk, model_name=model_name, timeout_seconds=timeout_seconds)
            all_vectors.extend(chunk_vectors)
        if not any(all_vectors):
            raise PipelineError(
                "semantic_provider_failure",
                "ollama semantic provider call failed for all inputs",
                details={
                    "provider": self.provider_name,
                    "model": model_name,
                    "base_urls": list(self.base_urls),
                    "batch_size": len(normalized_texts),
                    "batch_error_sample": batch_errors,
                    "per_text_stats": dict(self.last_embed_stats or {}),
                },
                retryable=True,
                exit_code=7,
            )
        failed_count = sum(1 for vec in all_vectors if not vec)
        self.last_embed_stats = {
            "provider": self.provider_name,
            "model": model_name,
            "requested_count": len(texts),
            "embedded_count": len(all_vectors) - failed_count,
            "failed_count": failed_count,
            "base_urls": list(self.base_urls),
            "batch_error_sample": batch_errors,
            "fallback_chunk_count": fallback_chunks,
            "batch_size": batch_size,
            "mode": "chunked_batch_with_fallback",
        }
        return all_vectors


def normalize_semantic_mode(value: Any) -> str:
    mode = str(value or "off").strip().lower()
    if mode in {"", "none"}:
        return "off"
    return mode


def semantic_cli_overrides(*, provider: Optional[str] = None, model: Optional[str] = None, mode: Optional[str] = None) -> Dict[str, Any]:
    semantic: Dict[str, Any] = {}
    if provider is not None:
        semantic["semantic_provider"] = str(provider)
    if model is not None:
        semantic["semantic_model_name"] = str(model)
    if mode is not None:
        normalized_mode = normalize_semantic_mode(mode)
        semantic["semantic_mode"] = normalized_mode
        semantic["enable_semantic_retrieval"] = normalized_mode != "off"
    return {"semantic": semantic} if semantic else {}


def get_semantic_provider(config: Mapping[str, Any]) -> Optional[SemanticProvider]:
    sem = dict(config.get("semantic") or {})
    provider = str(sem.get("semantic_provider") or "none").lower()
    if provider in {"none", "off"}:
        return None
    if provider == "stub":
        return StubSemanticProvider()
    if provider == "ollama":
        base_url = str(sem.get("semantic_base_url") or "http://localhost:11434")
        return OllamaSemanticProvider(base_url=base_url)
    return None


def semantic_runtime_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    sem = dict(config.get("semantic") or {})
    requested_enabled = bool(sem.get("enable_semantic_retrieval", False))
    requested_mode = normalize_semantic_mode(sem.get("semantic_mode") or "off")
    requested_provider = str(sem.get("semantic_provider") or "none").lower()
    requested_model = str(sem.get("semantic_model_name") or "all-minilm")
    runtime: Dict[str, Any] = {
        "requested_enabled": requested_enabled,
        "requested_mode": requested_mode,
        "requested_provider": requested_provider,
        "requested_model": requested_model,
        "effective_enabled": False,
        "effective_mode": "off",
        "effective_provider": "none",
        "effective_model": requested_model,
        "effective_strategy": "deterministic_only",
        "semantic_status": "disabled",
        "disabled_reason": None,
    }
    if not requested_enabled or requested_mode == "off":
        runtime["disabled_reason"] = "semantic_disabled"
        return runtime
    if requested_mode not in SEMANTIC_ACTIVE_MODES:
        runtime["semantic_status"] = "disabled"
        runtime["disabled_reason"] = "unsupported_mode"
        return runtime
    provider = get_semantic_provider(config)
    if provider is None:
        runtime["semantic_status"] = "unavailable"
        runtime["effective_strategy"] = "fallback_deterministic"
        runtime["disabled_reason"] = "provider_none"
        return runtime
    runtime["effective_enabled"] = True
    runtime["effective_mode"] = requested_mode
    runtime["effective_provider"] = provider.provider_name
    runtime["effective_model"] = requested_model
    runtime["effective_strategy"] = "semantic_rank_only" if requested_mode == "rank_only" else "semantic_retrieve_and_rank"
    runtime["semantic_status"] = "ready"
    return runtime


def _cache_root(repo_root: Path, provider: str, model_name: str, config: Mapping[str, Any]) -> Path:
    sem = dict(config.get("semantic") or {})
    rel = str(sem.get("semantic_cache_dir") or "runtime/state/pipeline_semantic")
    root = (repo_root / rel / provider / model_name).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_key(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _embed_texts_cached(provider: SemanticProvider, texts: Sequence[str], *, repo_root: Path, config: Mapping[str, Any], model_name: str, timeout_seconds: int) -> Tuple[List[List[float]], Dict[str, Any]]:
    cache_dir = _cache_root(repo_root, provider.provider_name, model_name, config)
    missing: List[str] = []
    missing_idx: List[int] = []
    out: List[Optional[List[float]]] = [None] * len(texts)
    cache_hits = 0
    for idx, text in enumerate(texts):
        key = _cache_key(text)
        cache_file = cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                vec = payload.get("embedding")
                if isinstance(vec, list) and vec:
                    out[idx] = [float(v) for v in vec]
                    cache_hits += 1
                    continue
            except Exception:
                pass
        missing.append(str(text or ""))
        missing_idx.append(idx)

    provider_stats: Dict[str, Any] = {
        "cache_hits": cache_hits,
        "cache_misses": len(missing),
        "provider": getattr(provider, "provider_name", "unknown"),
        "model": model_name,
    }
    if missing:
        embedded = provider.embed_texts(missing, model_name=model_name, timeout_seconds=timeout_seconds)
        provider_stats.update(dict(getattr(provider, "last_embed_stats", {}) or {}))
        for idx, text, vec in zip(missing_idx, missing, embedded):
            out[idx] = [float(v) for v in vec] if vec else []
            if vec:
                key = _cache_key(text)
                cache_file = cache_dir / f"{key}.json"
                try:
                    cache_file.write_text(json.dumps({"embedding": out[idx]}, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
    return [list(v or []) for v in out], provider_stats


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    return float(sum(float(a[i]) * float(b[i]) for i in range(size)))


def _candidate_files(repo_root: Path, *, allowed_paths: Sequence[str], forbidden_paths: Sequence[str], max_bytes: int = 250000) -> Iterable[Path]:
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(repo_root).as_posix()
        if any(part in EXCLUDED_DIR_PARTS for part in Path(rel).parts):
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if not is_path_allowed(repo_root, rel, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths):
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        yield p


def _snippet_for_path(path: Path, max_chars: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text.replace("\ufffd", " ")
    text = CONTROL_CHARS_RE.sub(" ", text)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    middle = max(0, max_chars // 4)
    tail = max(120, max_chars - head - middle - 10)
    center = max(0, len(text) // 2 - middle // 2)
    return text[:head] + "\n...\n" + text[center : center + middle] + "\n...\n" + text[-tail:]


def semantic_retrieve(
    repo_root: Path,
    *,
    query_text: str,
    config: Mapping[str, Any],
    allowed_paths: Sequence[str],
    forbidden_paths: Sequence[str],
    candidate_paths: Optional[Sequence[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sem = dict(config.get("semantic") or {})
    fail_open = bool(sem.get("semantic_fail_open", True))
    runtime = semantic_runtime_config(config)
    meta: Dict[str, Any] = {
        **runtime,
        "semantic_returned": 0,
        "semantic_top_k": int(sem.get("semantic_top_k") or 32),
        "query_text": str(query_text or "")[:1200],
        "candidate_pool_size": 0,
        "provider_stats": {},
    }
    if runtime["semantic_status"] == "disabled":
        return [], meta
    provider = get_semantic_provider(config)
    if provider is None:
        return [], meta
    model_name = str(sem.get("semantic_model_name") or "all-minilm")
    timeout_seconds = int(sem.get("semantic_timeout_seconds") or 20)
    top_k = int(sem.get("semantic_top_k") or 32)
    min_score = float(sem.get("semantic_min_score") or 0.2)

    paths: List[Path] = []
    if candidate_paths:
        for rel in candidate_paths:
            rel_txt = str(rel or "").replace("\\", "/").strip()
            if not rel_txt:
                continue
            if not is_path_allowed(repo_root, rel_txt, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths):
                continue
            p = repo_root / rel_txt
            if p.exists() and p.is_file():
                paths.append(p)
    else:
        if runtime.get("effective_mode") == "rank_only":
            meta["semantic_status"] = "unavailable"
            meta["effective_strategy"] = "fallback_deterministic"
            meta["disabled_reason"] = "rank_only_requires_candidates"
            return [], meta
        paths.extend(list(_candidate_files(repo_root, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths)))
    meta["candidate_pool_size"] = len(paths)

    if not paths:
        meta["semantic_status"] = "unavailable"
        meta["effective_strategy"] = "fallback_deterministic"
        meta["disabled_reason"] = "no_candidates"
        return [], meta

    max_chars = int(sem.get("semantic_snippet_chars") or 2500)
    texts = [_snippet_for_path(p, max_chars=max_chars) for p in paths]
    pairs = [(p, t) for p, t in zip(paths, texts) if t.strip()]
    if not pairs:
        meta["semantic_status"] = "unavailable"
        meta["effective_strategy"] = "fallback_deterministic"
        meta["disabled_reason"] = "no_text"
        return [], meta
    paths = [p for p, _ in pairs]
    texts = [t for _, t in pairs]
    meta["candidate_pool_size"] = len(paths)
    try:
        query_vecs, query_stats = _embed_texts_cached(provider, [query_text], repo_root=repo_root, config=config, model_name=model_name, timeout_seconds=timeout_seconds)
        doc_vecs, doc_stats = _embed_texts_cached(provider, texts, repo_root=repo_root, config=config, model_name=model_name, timeout_seconds=timeout_seconds)
        query_vec = query_vecs[0] if query_vecs else []
        meta["provider_stats"] = {"query": query_stats, "documents": doc_stats}
    except PipelineError as exc:
        if fail_open:
            meta["semantic_status"] = "soft_failed"
            meta["effective_strategy"] = "fallback_deterministic"
            meta["disabled_reason"] = "provider_failure"
            meta["effective_enabled"] = False
            meta["provider_error"] = dict(exc.details or {})
            return [], meta
        raise

    if not query_vec:
        if fail_open:
            meta["semantic_status"] = "soft_failed"
            meta["effective_strategy"] = "fallback_deterministic"
            meta["disabled_reason"] = "query_embedding_failed"
            meta["effective_enabled"] = False
            return [], meta
        raise PipelineError("semantic_provider_failure", "query embedding failed", details={"provider_stats": meta.get("provider_stats")}, retryable=True, exit_code=7)

    results: List[SemanticResult] = []
    failed_paths: List[str] = []
    for p, text, vec in zip(paths, texts, doc_vecs):
        if not vec:
            failed_paths.append(p.relative_to(repo_root).as_posix())
            continue
        score = _cosine(query_vec, vec)
        if score < min_score:
            continue
        rel = p.relative_to(repo_root).as_posix()
        chunk_id = hashlib.sha256((rel + text[:400]).encode("utf-8")).hexdigest()[:16]
        results.append(
            SemanticResult(
                path=rel,
                semantic_score=score,
                chunk_id=chunk_id,
                provider=provider.provider_name,
                model=model_name,
                query_text=query_text[:500],
                retrieved_at=utc_now_iso(),
                source="semantic_only",
                chunk_preview=text[:240].replace("\n", " "),
            )
        )
    results.sort(key=lambda x: (-x.semantic_score, x.path))
    meta["semantic_status"] = "ok" if results or not failed_paths else "soft_failed"
    meta["effective_enabled"] = True if results else False
    meta["effective_provider"] = provider.provider_name
    meta["effective_model"] = model_name
    meta["semantic_returned"] = min(top_k, len(results))
    meta["disabled_reason"] = None if results else ("all_document_embeddings_failed" if failed_paths else None)
    meta["document_embedding_failed_count"] = len(failed_paths)
    meta["document_embedding_failed_paths_sample"] = failed_paths[:10]
    return [r.to_dict() for r in results[:top_k]], meta
