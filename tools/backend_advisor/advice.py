"""Deterministic backend cost/model efficiency advisor.

Last modified: 2026-03-31 00:00 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.deterministic_tooling.common import build_envelope, ensure_runtime_dir, git_head, hash_object, load_cache, normalized_list, parse_properties_text, read_json, read_text, repo_rel, store_cache, write_output
from tools.runtime_policy import runtime_policy_path

SCHEMA_VERSION = "backend_cost_advice_v1"
TOOL_NAME = "cost_model_backend_efficiency_advisor"
CACHE_TTL_S = 900.0

# Deliberately coarse local heuristics. This tool only ranks local policy options.


def _load_policy_for_repo(repo_root: Path) -> dict[str, str]:
    local_path = (Path(repo_root).resolve() / "config" / "runtime_policy.properties").resolve()
    if local_path.exists():
        return parse_properties_text(read_text(local_path))
    default_path = runtime_policy_path()
    return parse_properties_text(read_text(default_path)) if default_path.exists() else {}

_COST_HINTS = {
    "mini": 1,
    "deepseek": 2,
    "codex-mini": 2,
    "codex": 3,
    "gpt-5.4": 4,
    "reasoner": 5,
}


def _cost_class(model_name: str) -> int:
    lower = str(model_name or "").lower()
    for token, value in _COST_HINTS.items():
        if token in lower:
            return value
    return 3


def _telemetry_rows(raw: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    rows = raw.get("backends") if isinstance(raw.get("backends"), list) else raw.get("backend_ranking")
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or row.get("model_name") or "").strip()
        backend = str(row.get("backend") or row.get("backend_name") or "").strip() or "default"
        if not model:
            continue
        out.append(
            {
                "model": model,
                "backend": backend,
                "latency_ms": int(row.get("latency_ms") or 0),
                "reliability": float(row.get("reliability") or 0.0),
                "quota_ok": bool(row.get("quota_ok", True)),
            }
        )
    return out


def build_backend_advice(
    *,
    repo_root: Path,
    target_pis_uid: str,
    budget: int,
    model_requirements: Sequence[str] = (),
    runtime_policy: Mapping[str, str] | None = None,
    backend_telemetry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    policy = dict(runtime_policy or _load_policy_for_repo(repo_root))
    telemetry_rows = _telemetry_rows(backend_telemetry)
    cache_dir = ensure_runtime_dir(repo_root, "backend_advisor")
    key = hash_object(
        {
            "git_head": git_head(repo_root),
            "target_pis_uid": target_pis_uid,
            "budget": int(budget),
            "model_requirements": list(model_requirements or []),
            "policy": policy,
            "telemetry": telemetry_rows,
        }
    ).split(":", 1)[1]
    cache_path = cache_dir / f"{key}.json"
    cached = load_cache(cache_path, ttl_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    warnings: list[dict[str, Any]] = []
    candidates = normalized_list(
        [
            policy.get("mobile.chat_model", ""),
            *str(policy.get("mobile.chat_fallback_models", "")).split(","),
            policy.get("scheduler.default_model", ""),
            *str(policy.get("scheduler.default_fallback_models", "")).split(","),
        ]
    )
    if model_requirements:
        required_lower = {str(item).lower() for item in model_requirements if str(item).strip()}
        candidates = [row for row in candidates if any(req in row.lower() for req in required_lower)] or candidates
    ranked: list[dict[str, Any]] = []
    for model_name in candidates:
        telemetry = next((row for row in telemetry_rows if row["model"] == model_name), None)
        quota_ok = True if telemetry is None else bool(telemetry.get("quota_ok", True))
        latency_ms = 0 if telemetry is None else int(telemetry.get("latency_ms") or 0)
        reliability = 0.0 if telemetry is None else float(telemetry.get("reliability") or 0.0)
        score = (_cost_class(model_name) * 1000) + latency_ms - int(reliability * 100)
        ranked.append(
            {
                "model": model_name,
                "backend": str((telemetry or {}).get("backend") or "policy_default"),
                "cost_class": _cost_class(model_name),
                "latency_ms": latency_ms,
                "reliability": reliability,
                "quota_ok": quota_ok,
                "score": score + (100000 if not quota_ok else 0),
            }
        )
    ranked.sort(key=lambda row: (int(row["score"]), str(row["model"]), str(row["backend"])))
    if not telemetry_rows:
        warnings.append({"code": "telemetry_missing", "severity": "warning", "message": "No backend telemetry was provided. Ranking uses local policy heuristics only."})
    if ranked and not ranked[0]["quota_ok"]:
        warnings.append({"code": "quota_blocked", "severity": "warning", "message": "Preferred candidate is quota-blocked; fallback chain is advisory only."})

    preferred = ranked[0] if ranked else {}
    fallback = ranked[1:]
    outputs = {
        "preferred_runner": preferred,
        "fallback_sequence": fallback,
        "cost_delta": (fallback[0]["cost_class"] - preferred.get("cost_class", 0)) if fallback and preferred else 0,
        "quota_flags": [{"model": row["model"], "quota_ok": row["quota_ok"]} for row in ranked if not row["quota_ok"]],
        "meta": {"ranking_reason": "score -> model_name -> backend_name", "candidate_count": len(ranked)},
    }
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={
            "repo_root": str(repo_root),
            "target_pis_uid": str(target_pis_uid or "").strip(),
            "budget": int(budget),
            "model_requirements": normalized_list(model_requirements),
            "runtime_policy_path": repo_rel(repo_root, runtime_policy_path()),
        },
        outputs=outputs,
        warnings=warnings,
        provenance=[{"ref_type": "file", "ref": repo_rel(repo_root, runtime_policy_path()), "content_hash": hash_object(policy)}],
        artifacts=[{"path": repo_rel(repo_root, cache_path), "kind": "cache_entry"}],
    )
    store_cache(cache_path, envelope)
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank local model/backend options deterministically")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--pis", required=True)
    parser.add_argument("--budget", type=int, default=1000)
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--telemetry-file")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    telemetry = read_json(Path(args.telemetry_file)) if args.telemetry_file else None
    payload = build_backend_advice(
        repo_root=Path(args.repo_root),
        target_pis_uid=args.pis,
        budget=int(args.budget),
        model_requirements=args.requirement,
        backend_telemetry=telemetry,
    )
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
