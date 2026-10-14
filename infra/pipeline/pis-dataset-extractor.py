#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_storage

HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
UID_RE = re.compile(r"\*\*UID:\*\*\s*([^\n]+)")
BACKTICK_ID_RE = re.compile(r"`([^`]+)`")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
LEADING_NOISE_RE = re.compile(r"^(the system must|the pipeline must|ensure that|ensure|must|should|every|all)\s+", re.I)
TRAILING_PUNCT_RE = re.compile(r"[.\s]+$")

DOMAIN_RULES: List[Tuple[str, List[str]]] = [
    ("assistant_truth_and_identity", [
        "assistant identity", "source-of-truth", "source of truth", "assistant-state", "assistant state",
        "runtime state", "chat transcript", "precedence", "write arbitration", "audit event", "event log",
        "state snapshot", "operator-visible", "operator visible", "truth ownership", "identity contract",
    ]),
    ("scheduler_or_control_plane", [
        "scheduler", "watchdog", "healer", "scheduled job", "pause", "resume", "runtime policy",
        "orphan", "stale lock", "due logic", "control plane",
    ]),
    ("ui_local_changes", [
        "ui", "web page", "mobile", "button", "panel", "layout", "label", "operator shell", "page",
        "screen", "dialog",
    ]),
    ("audit_and_integrity", [
        "audit", "tamper", "integrity", "append-only", "append only", "durable evidence", "lineage",
        "canonical truth", "immutable", "proof", "unauthorized", "registry",
    ]),
    ("experiment_or_run_validation", [
        "benchmark", "compare runs", "host verification", "validation", "campaign run", "divergence",
        "regression", "checkpoint", "replay", "resume verification", "proof-oriented",
    ]),
    ("pipeline_internal_modification", [
        "retrieval", "search plan", "context bundle", "ranker", "pipeline", "patch plan", "bundle",
        "task family", "role assignment", "negative targets", "responsibility terms",
    ]),
]

TASK_MODE_RULES: List[Tuple[str, List[str]]] = [
    ("modify_existing_code", ["implement", "fix", "patch", "update", "wire", "edit", "change", "tighten", "repair"]),
    ("design_in_repo", ["define", "design", "contract", "schema", "boundary", "precedence", "state model", "decompose"]),
    ("greenfield_design", ["portable", "generalist", "from scratch", "new system", "greenfield", "reusable foundation"]),
]

OWNER_SIGNALS = [
    ("truth_ownership", ["authoritative", "source-of-truth", "source of truth", "canonical", "precedence"]),
    ("durable_state", ["state", "schema", "snapshot", "reload", "versioned", "restore"]),
    ("audit_trail", ["append-only", "append only", "audit", "event log", "mutation trail", "replayable"]),
    ("write_arbitration", ["write arbitration", "writer", "conflicting", "concurrency", "cross-surface"]),
    ("recovery_markers", ["recovery", "blocked", "incompatible", "restore", "boot"]),
]

HUB_NEGATIVE_HINTS = ["planning", "orchestrator", "runner", "routes", "benchmark", "bundle", "ranker", "summary"]


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "item"


def split_sections(md: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    current = "front_matter"
    buf: List[str] = []
    for line in md.splitlines():
        m = HEADING_RE.match(line.strip())
        if m:
            sections[current] = "\n".join(buf).strip()
            current = m.group(2).strip().lower().replace(" ", "_")
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def extract_uid(md: str, fallback: str) -> str:
    m = UID_RE.search(md)
    return m.group(1).strip() if m else fallback


def normalize_phrase(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text.strip())
    text = LEADING_NOISE_RE.sub("", text)
    text = TRAILING_PUNCT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().capitalize() if text else text


def parse_bullets(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        m = BULLET_RE.match(raw.strip())
        if m:
            item = normalize_phrase(m.group(1))
            if item:
                out.append(item)
    dedup: List[str] = []
    seen = set()
    for item in out:
        k = item.lower()
        if k not in seen:
            dedup.append(item)
            seen.add(k)
    return dedup


def parse_scope(text: str) -> Tuple[List[str], List[str]]:
    in_scope: List[str] = []
    non_goals: List[str] = []
    target: Optional[List[str]] = None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("in scope"):
            target = in_scope
            continue
        if low.startswith("explicit non-goals") or low.startswith("non-goals"):
            target = non_goals
            continue
        m = BULLET_RE.match(line)
        if m and target is not None:
            item = normalize_phrase(m.group(1))
            if item:
                target.append(item)
    return in_scope, non_goals


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def weighted_text(parts: Iterable[Tuple[str, int]]) -> str:
    chunks: List[str] = []
    for text, weight in parts:
        if not text:
            continue
        chunks.extend([text] * max(1, weight))
    return "\n\n".join(chunks)


def score_keywords(text: str, rules: List[Tuple[str, List[str]]]) -> List[Tuple[str, int, List[str]]]:
    t = text.lower()
    scores: List[Tuple[str, int, List[str]]] = []
    for name, kws in rules:
        hits = [kw for kw in kws if kw in t]
        if hits:
            scores.append((name, len(hits), hits))
    scores.sort(key=lambda x: (-x[1], x[0]))
    return scores


def pick_task_family(text: str) -> Tuple[str, List[str]]:
    scores = score_keywords(text, DOMAIN_RULES)
    return (scores[0][0], scores[0][2]) if scores else ("pipeline_internal_modification", [])


def pick_task_mode(text: str) -> Tuple[str, List[str]]:
    scores = score_keywords(text, TASK_MODE_RULES)
    return (scores[0][0], scores[0][2]) if scores else ("design_in_repo", [])


def extract_refs(text: str) -> List[str]:
    refs = BACKTICK_ID_RE.findall(text)
    out: List[str] = []
    seen = set()
    for r in refs:
        if r not in seen:
            out.append(r)
            seen.add(r)
    return out


def normalize_failure_modes(items: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in items:
        x = item.lower()
        x = re.sub(r"^(risk:|failure mode:|failure:)", "", x).strip()
        x = re.sub(r"\b(system|workflow|pipeline)\b", "", x)
        x = re.sub(r"\s+", " ", x).strip(" .-")
        if not x:
            continue
        label = x.replace(" ", "_")
        if label not in seen:
            normalized.append(label)
            seen.add(label)
    return normalized


def normalize_invariants(items: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in items:
        x = item.lower()
        x = re.sub(r"\b(must|should|shall)\b", "", x)
        x = re.sub(r"\s+", " ", x).strip(" .-")
        if not x:
            continue
        if x not in seen:
            normalized.append(x)
            seen.add(x)
    return normalized


def derive_retrieval_bias(family: str, text: str) -> Dict[str, List[str]]:
    include: List[str]
    exclude = ["benchmark_harnesses", "broad_orchestration_hubs"]
    if family == "assistant_truth_and_identity":
        include = ["assistant_runtime_state", "truth_registry", "audit_log", "serialization_and_replay", "recovery_markers", "operator_visible_state"]
        exclude.extend(["chat_projection_only", "ui_render_only"])
    elif family == "scheduler_or_control_plane":
        include = ["scheduler_core", "runtime_policy", "recovery_mechanisms", "operator_entrypoints"]
    elif family == "audit_and_integrity":
        include = ["ledger_actions", "audit_log", "state_registry", "verification_surfaces"]
    elif family == "ui_local_changes":
        include = ["ui_shell_and_mobile", "page_handlers", "local_validation"]
        exclude.extend(["scheduler_core", "planner_hubs"])
    elif family == "experiment_or_run_validation":
        include = ["validation_harnesses", "checkpoint_and_replay", "run_comparison"]
        exclude.extend(["ui_shell_and_mobile", "planner_hubs"])
    else:
        include = ["intake_and_profile", "retrieval_runtime", "policy_assets"]
    if "non-goal" in text.lower() or "non-goals" in text.lower():
        exclude.append("explicit_non_goal_surfaces")
    return {"include_subsystems": include, "exclude_subsystems": sorted(set(exclude))}


def derive_owner_hypotheses(text: str, family: str) -> Dict[str, List[str]]:
    tl = text.lower()
    owner_tags = [name for name, kws in OWNER_SIGNALS if any(kw in tl for kw in kws)]
    if family == "assistant_truth_and_identity":
        owner_domains = ["assistant_runtime_state", "truth_registry", "audit_log", "serialization_and_replay", "operator_visible_state"]
        hubs = ["planning", "orchestrator", "runner", "summary"]
    elif family == "scheduler_or_control_plane":
        owner_domains = ["scheduler_core", "watchdog", "healer", "runtime_policy"]
        hubs = ["planning", "benchmark", "webui_routes"]
    elif family == "ui_local_changes":
        owner_domains = ["ui_shell_and_mobile", "page_handlers", "local_validation"]
        hubs = ["scheduler", "planner", "benchmark"]
    else:
        owner_domains = [family]
        hubs = HUB_NEGATIVE_HINTS
    return {"owner_signal_tags": owner_tags, "likely_owner_domains": owner_domains, "likely_hub_surface_hints": hubs}


def candidate_updates(family: str) -> Dict[str, List[str]]:
    files = [
        "task_families.additions.yaml",
        "task_profile_rules.additions.yaml",
        "invariant_templates.yaml",
        "failure_taxonomy.yaml",
        "retrieval_bias_rules.yaml",
        "responsibility_terms.yaml",
        "role_assignment_rules.yaml",
        "subsystem_map.yaml",
    ]
    return {name: [family] for name in files}


def yaml_quote(s: str) -> str:
    if s == "" or re.search(r'[:#\-\[\]\{\},&*!?|>@`"\']|^\s|\s$', s):
        return json.dumps(s, ensure_ascii=False)
    return s


def dump_yaml(obj, indent: int = 0) -> str:
    sp = "  " * indent
    if isinstance(obj, dict):
        lines: List[str] = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{sp}{key}:")
                lines.append(dump_yaml(value, indent + 1))
            else:
                lines.append(f"{sp}{key}: {yaml_quote(str(value))}")
        return "\n".join(lines)
    if isinstance(obj, list):
        lines = []
        for item in obj:
            if isinstance(item, dict):
                sub = dump_yaml(item, indent + 1).splitlines()
                if sub:
                    lines.append(f"{sp}- {sub[0].lstrip()}")
                    lines.extend(sub[1:])
                else:
                    lines.append(f"{sp}- {{}}")
            elif isinstance(item, list):
                lines.append(f"{sp}-")
                lines.append(dump_yaml(item, indent + 1))
            else:
                lines.append(f"{sp}- {yaml_quote(str(item))}")
        return "\n".join(lines)
    return f"{sp}{yaml_quote(str(obj))}"


def render_summary(data: Dict) -> str:
    lines = [
        f"# {data['pis']['uid']} dataset",
        "",
        f"- Title: {data['pis']['title']}",
        f"- Task mode: {data['classification']['task_mode']}",
        f"- Task family: {data['classification']['task_family']}",
        "",
        "## Invariants",
    ]
    lines.extend(f"- {x}" for x in data["dataset"]["invariants"]) 
    lines.extend(["", "## Failure modes"])
    lines.extend(f"- {x}" for x in data["dataset"]["failure_modes"]) 
    lines.extend(["", "## Retrieval bias"])
    for group, items in data["dataset"]["retrieval_bias"].items():
        lines.append(f"- {group}:")
        lines.extend(f"  - {x}" for x in items)
    lines.append("")
    return "\n".join(lines)


def build_dataset(pis_path: Path) -> Dict:
    md = pis_path.read_text(encoding="utf-8")
    sections = split_sections(md)
    uid = extract_uid(md, pis_path.parent.name.split("__", 1)[0])
    title = first_nonempty_line(sections.get("title", "")) or pis_path.parent.name
    goal = sections.get("goal_/_user-visible_behavior", "")
    parent_relation = sections.get("parent_relation", "")
    scope_text = sections.get("scope", "")
    source_truth = sections.get("source-of-truth_decisions", "")
    dependencies = sections.get("dependencies,_boundary,_and_overlap_mapping", "")
    failures = sections.get("key_failure_modes_and_recovery_expectations", "")
    acceptance = sections.get("acceptance_criteria", "")
    validation = sections.get("narrow_validation_expectations", "")

    in_scope, non_goals = parse_scope(scope_text)
    combined = weighted_text([
        (title, 3),
        (goal, 3),
        (parent_relation, 1),
        (scope_text, 2),
        (source_truth, 3),
        (dependencies, 2),
        (failures, 3),
        (acceptance, 2),
        (validation, 2),
    ])
    family, family_hits = pick_task_family(combined)
    mode, mode_hits = pick_task_mode(combined)
    invariants_raw = parse_bullets(source_truth)
    invariants_raw.extend([x for x in parse_bullets(acceptance) if any(tok in x.lower() for tok in ["explicit", "durable", "append", "stable", "authoritative", "precedence", "write arbitration", "write-arbitration", "must"] )])
    if not invariants_raw:
        invariants_raw = parse_bullets(acceptance)
    failure_raw = parse_bullets(failures)

    return {
        "pis": {
            "uid": uid,
            "title": title,
            "source_dir": str(pis_path.parent),
            "pis_path": str(pis_path),
            "parent_refs": extract_refs(parent_relation),
            "dependency_refs": extract_refs(dependencies),
        },
        "classification": {
            "task_mode": mode,
            "task_mode_signals": mode_hits,
            "task_family": family,
            "task_family_signals": family_hits,
        },
        "source_sections": {
            "scope_in": in_scope,
            "scope_non_goals": non_goals,
            "source_of_truth_decisions": parse_bullets(source_truth),
            "failure_expectations": failure_raw,
            "acceptance_criteria": parse_bullets(acceptance),
            "narrow_validation_expectations": parse_bullets(validation),
        },
        "dataset": {
            "invariants": normalize_invariants(invariants_raw),
            "failure_modes": normalize_failure_modes(failure_raw),
            "retrieval_bias": derive_retrieval_bias(family, combined),
            "ownership_hypotheses": derive_owner_hypotheses(combined, family),
            "candidate_updates": candidate_updates(family),
        },
    }


def resolve_pis(args: argparse.Namespace) -> Path:
    if args.pis:
        p = Path(args.pis)
        return p / "pis.md" if p.is_dir() else p
    items_root = Path(args.items_root)
    item_dir = ledger_storage.item_dir_for_uid(items_root, str(args.uid or "").strip())
    if item_dir is None:
        raise FileNotFoundError(f"No ledger item found for {args.uid!r} under {items_root}")
    return item_dir / "pis.md"


def write_one(pis_path: Path, out_dir: Path, yaml_out: Optional[Path] = None, md_out: Optional[Path] = None) -> Tuple[Path, Path, Dict]:
    data = build_dataset(pis_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = slugify(pis_path.parent.name)
    yaml_path = yaml_out or out_dir / f"{stem}.dataset.yaml"
    md_path = md_out or out_dir / f"{stem}.dataset.md"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = dump_yaml(data) + "\n"
    md_text = render_summary(data)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    return yaml_path, md_path, data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract a structured dataset from one PIS ledger item.")
    parser.add_argument("--pis", help="Path to pis.md or containing item directory.")
    parser.add_argument("--uid", help="UID or prefix to find under governance/ledger/patch_instruction_sheets.")
    parser.add_argument("--items-root", default="governance/ledger/patch_instruction_sheets", help="Default ledger items root.")
    parser.add_argument("--out-dir", default="infra/pipeline/knowledge_drafts/pis_datasets", help="Output directory for dataset files.")
    parser.add_argument("--yaml-out", help="Optional explicit YAML output path.")
    parser.add_argument("--md-out", help="Optional explicit Markdown output path.")
    parser.add_argument("--stdout", action="store_true", help="Also print YAML to stdout.")
    parser.add_argument("--csv-row", action="store_true", help="Print one CSV summary row instead of file paths.")
    args = parser.parse_args(argv)
    if not args.pis and not args.uid:
        parser.error("Provide --pis or --uid")
    try:
        pis_path = resolve_pis(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not pis_path.exists():
        print(f"ERROR: pis file not found: {pis_path}", file=sys.stderr)
        return 2
    yaml_path, md_path, data = write_one(
        pis_path=pis_path,
        out_dir=Path(args.out_dir),
        yaml_out=Path(args.yaml_out) if args.yaml_out else None,
        md_out=Path(args.md_out) if args.md_out else None,
    )
    if args.stdout:
        sys.stdout.write((Path(yaml_path).read_text(encoding="utf-8")))
    elif args.csv_row:
        row = {
            "item_dir": pis_path.parent.name,
            "uid": data["pis"]["uid"],
            "task_mode": data["classification"]["task_mode"],
            "task_family": data["classification"]["task_family"],
            "dataset_yaml": str(yaml_path),
            "dataset_md": str(md_path),
        }
        writer = csv.DictWriter(sys.stdout, fieldnames=list(row.keys()))
        writer.writerow(row)
    else:
        print(str(yaml_path))
        print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
