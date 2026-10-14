#!/usr/bin/env python3
"""Start recursive implementation for an Intent Sheet or PIS using lab defaults."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.bin import scheduler_batch_from_parent as _batch_from_parent
from infra.bin import scheduler_create_job as _scheduler_create_job
from infra.controlplane import intent_family_v2_workflow_contract as _intent_family_v2_workflow_contract
from infra.controlplane import mission_drift_gate
from tools.bin import intent_sheet_next as _intent_sheet_next
from infra.controlplane import scheduled_jobs as sched
from infra.webui.ledger import ledger_storage
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger.ledger_manager import ChangeLedger

DEFAULT_MAX_CHILDREN = 8
DEFAULT_LOOP_POLICY = "build_verify_repair"
DEFAULT_LOOP_MAX_ITERATIONS = 1
DEFAULT_CHILD_PASS_COUNT = 3
EXCLUDED_STATUSES = {"verified", "done", "completed", "deprecated", "superseded", "deferred"}
DEFAULT_PROOF_PROFILE = "focused"
DEFAULT_CLOSE_MODE = "auto"
DEFAULT_PARALLELISM = "sequential_by_dependency"
DEFAULT_START_NOW = True
DEFAULT_IMPLEMENTATION_CHILD_MIN = 5
DEFAULT_IMPLEMENTATION_CHILD_MAX = 6
DEFAULT_MANDATORY_TAIL = {"validation": True, "closeout": True}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(text: str, *, limit: int = 28) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value[:limit].strip("-") or "item"


def _format_decomposition_contract(guidance: Dict[str, Any]) -> List[str]:
    impl_min = int(guidance.get("implementation_min") or DEFAULT_IMPLEMENTATION_CHILD_MIN)
    impl_max = int(guidance.get("implementation_max") or DEFAULT_IMPLEMENTATION_CHILD_MAX)
    child_cap = int(guidance.get("child_cap") or DEFAULT_MAX_CHILDREN)
    decomposition_class = str(guidance.get("decomposition_class") or "").strip()
    rationale = str(guidance.get("decomposition_rationale") or "").strip()
    tail = guidance.get("mandatory_tail") if isinstance(guidance.get("mandatory_tail"), dict) else DEFAULT_MANDATORY_TAIL
    lines = [
        f"- aim for {impl_min} to {impl_max} implementation children when the scope can be expressed honestly at that granularity",
        f"- keep total child count bounded at {child_cap} unless the operator explicitly asks for a larger family",
    ]
    if bool(tail.get("validation", True)):
        lines.append("- reserve one detailed late child PIS for intent-driven assembled-system E2E validation")
        lines.append("- the intent-validation child must be a substantive validation design artifact, not a thin wrapper")
    if bool(tail.get("closeout", True)):
        lines.append("- require one separate final closeout child after the intent-validation child")
        lines.append("- the closeout child must prove the implementation children plus the intent-validation child, repair bounded findings when needed, rerun focused validation after repair, and only then close the family honestly")
    if decomposition_class:
        lines.append(f"- decomposition class: {decomposition_class}")
    if rationale:
        lines.append(f"- decomposition rationale: {rationale}")
    lines.append("- implementation order should follow dependency order")
    lines.append("- implement execution children with explicit build_or_improve pass chains by default")
    lines.append("- keep validation and closeout as explicit tail jobs instead of hidden verifier/controller branches")
    lines.append("- validation and closeout proof must be focused, scenario-driven, and honest about uncovered gaps")
    return lines


def _build_parent_pis_markdown(*, intent_uid: str, title: str, guidance: Optional[Dict[str, Any]] = None) -> str:
    contract_lines = _format_decomposition_contract(guidance or {})
    return (
        "# Patch Instruction Sheet (PIS)\n\n"
        f"**Derived from Intent Sheet:** {intent_uid}\n\n"
        f"## 1. Title\n{title}\n\n"
        "## 2. Goal / user-visible behavior\n"
        "Create the umbrella parent PIS for this intent so it can be decomposed into executable child PIS items.\n\n"
        "## 3. Scope boundaries\n"
        "- This parent exists to define the implementation family boundary.\n"
        "- Child PIS items will carry the executable implementation slices.\n"
        "- Do not treat this parent as one giant implementation ticket.\n\n"
        "## 4. Default execution contract\n"
        + "\n".join(contract_lines)
        + "\n"
    )


def _job_id(prefix: str, uid: str, title: str) -> str:
    suffix = str(uid or "").rsplit("-", 1)[-1].lower()
    raw = f"{prefix}_{suffix}_{_slugify(title, limit=36)}"
    raw = raw[:79].rstrip("._-")
    if not sched.JOB_ID_RE.match(raw):
        return f"{prefix}_{suffix}"[:79]
    return raw


def _is_excluded_status(raw: Any) -> bool:
    return str(raw or "").strip().lower() in EXCLUDED_STATUSES


def _intent_rows() -> List[Dict[str, Any]]:
    rows = IntentSheetLedger(repo_root=REPO_ROOT).list_sheets(limit=5000)
    out: List[Dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if _is_excluded_status(status):
            continue
        out.append(
            {
                "kind": "intent_sheet",
                "uid": str(row.get("uid") or "").strip().upper(),
                "slug": str(row.get("slug") or "").strip().lower(),
                "status": status,
                "title": str(row.get("title") or "").strip(),
                "execution_priority": row.get("execution_priority"),
            }
        )
    out.sort(key=lambda row: (int(row.get("execution_priority") or 10**9), str(row.get("uid") or "")))
    return out


def _pis_rows() -> List[Dict[str, Any]]:
    root = ledger_storage.ledger_patch_instruction_sheets_root(REPO_ROOT)
    out: List[Dict[str, Any]] = []
    for item_dir in ledger_storage.list_item_dirs(root):
        meta = ledger_storage.read_json(item_dir / "meta.json") or {}
        status = str(meta.get("status") or "").strip().lower()
        if _is_excluded_status(status):
            continue
        out.append(
            {
                "kind": "pis",
                "uid": str(meta.get("uid") or "").strip().upper(),
                "slug": str(meta.get("slug") or item_dir.name.split("__", 1)[-1]).strip().lower(),
                "status": status,
                "title": str(meta.get("title") or "").strip(),
                "execution_priority": meta.get("execution_priority"),
            }
        )
    out.sort(key=lambda row: (int(row.get("execution_priority") or 10**9), str(row.get("uid") or "")))
    return out


def _resolve_target_uid(raw: str) -> Tuple[str, str]:
    token = str(raw or "").strip()
    if not token:
        raise ValueError("target uid or slug is required")
    upper = token.upper()
    lower = token.lower()
    if lower in {"next_pis", "next-pis", "nextpis"}:
        rows = _pis_rows()
        if not rows:
            raise ValueError("no active implementable PIS found")
        first = rows[0]
        return str(first["uid"]), str(first["kind"])
    if lower in {"next_intent", "next-intent", "nextintent"}:
        rows = _intent_rows()
        if not rows:
            raise ValueError("no active implementable intent sheet found")
        first = rows[0]
        return str(first["uid"]), str(first["kind"])
    if lower in {"next_safe_intent", "next-safe-intent", "nextsafeintent"}:
        rows = _intent_sheet_next.next_sheets(repo_root=REPO_ROOT, limit=200)
        first = next(
            (
                row
                for row in rows
                if bool(
                    (row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}).get("safe")
                )
            ),
            None,
        )
        if first is None:
            raise ValueError("no safe implementable intent sheet found")
        return str(first["uid"]), "intent_sheet"
    if upper.startswith(("IS-", "PIS-")):
        kind = _item_kind(upper)
        rows = _intent_rows() if kind == "intent_sheet" else _pis_rows()
        for row in rows:
            if row["uid"] == upper:
                return str(row["uid"]), str(row["kind"])
        raise ValueError(f"target is not active and implementable: {upper}")
    candidates: List[Tuple[str, str]] = []
    for row in _intent_rows() + _pis_rows():
        uid_suffix = str(row["uid"]).rsplit("-", 1)[-1].lower()
        if lower == uid_suffix or lower == str(row["slug"]):
            candidates.append((str(row["uid"]), str(row["kind"])))
    if not candidates:
        raise ValueError(f"no active implementable item matched: {token}")
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 1:
        raise ValueError(
            "ambiguous implement target: "
            + ", ".join(f"{uid} [{kind}]" for uid, kind in unique[:8])
        )
    return unique[0]


def _pis_choice_from_uid(uid: str) -> _scheduler_create_job.PisChoice:
    item_dir = sched._resolve_ledger_item_dir_for_uid(uid, repo_root=REPO_ROOT)
    if item_dir is None:
        raise FileNotFoundError(uid)
    meta = json.loads((item_dir / "meta.json").read_text(encoding="utf-8")) if (item_dir / "meta.json").exists() else {}
    return _scheduler_create_job.PisChoice(
        uid=str(meta.get("uid") or uid),
        title=str(meta.get("title") or uid),
        status=str(meta.get("status") or "draft"),
        updated_utc=str(meta.get("updated_utc") or ""),
        created_utc=str(meta.get("created_utc") or ""),
        slug=str(meta.get("slug") or item_dir.name.split("__", 1)[-1]),
        dir_path=item_dir,
        pis_path=(item_dir / "pis.md").resolve(),
    )


def _ensure_parent_for_intent(uid: str) -> Tuple[str, Dict[str, Any]]:
    sheets = IntentSheetLedger(repo_root=REPO_ROOT)
    items = ChangeLedger(repo_root=REPO_ROOT)
    sheet = sheets.read_sheet(uid)
    meta = dict(sheet.get("meta") or {})
    existing = [str(x).strip() for x in list(meta.get("derived_pis_uids") or []) if str(x).strip()]
    if existing:
        return existing[0], {"created_parent": False, "created_parent_uid": existing[0]}
    title = str(meta.get("title") or uid).strip() or uid
    guidance = _intent_guidance(uid)
    created = items.create_item(
        title=title,
        slug=_slugify(title),
        tags=list(meta.get("tags") or []) + ["intent-derived", f"source-intent:{uid}"],
        pis_markdown=_build_parent_pis_markdown(intent_uid=uid, title=title, guidance=guidance),
        project_id=str(meta.get("project_id") or ""),
        workstream_type=str(meta.get("workstream_type") or ""),
        scope_boundary=str(meta.get("scope_boundary") or ""),
        artifact_roots=list(meta.get("artifact_roots") or []),
    )
    parent_uid = str(created.get("uid") or "").strip()
    admission = (
        meta.get("integration_meta", {}).get("mission_fit")
        if isinstance(meta.get("integration_meta"), dict) and isinstance(meta.get("integration_meta", {}).get("mission_fit"), dict)
        else {}
    )
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is not None and admission:
        _write_meta_patch(
            parent_dir,
            {
                "integration_meta": {
                    "mission_fit": dict(admission),
                    "intent_sheet_uid": uid,
                    "execution_mode": "explicit_pass_chain_v1",
                }
            },
        )
    sheets.update_meta(uid, {"derived_pis_uids": existing + [parent_uid]})
    return parent_uid, {"created_parent": True, "created_parent_uid": parent_uid}


def _existing_parent_for_intent(uid: str) -> Tuple[str, Dict[str, Any]]:
    sheets = IntentSheetLedger(repo_root=REPO_ROOT)
    sheet = sheets.read_sheet(uid)
    meta = dict(sheet.get("meta") or {})
    existing = [str(x).strip() for x in list(meta.get("derived_pis_uids") or []) if str(x).strip()]
    for candidate in existing:
        if sched._resolve_ledger_item_dir_for_uid(candidate, repo_root=REPO_ROOT) is not None:
            return candidate, {"created_parent": False, "created_parent_uid": candidate}
    return "", {"created_parent": False, "created_parent_uid": ""}


def _options_dict(
    *,
    max_children: int = DEFAULT_MAX_CHILDREN,
    child_pass_count: int = DEFAULT_CHILD_PASS_COUNT,
    parallel: str = "safe",
    proof_profile: str = DEFAULT_PROOF_PROFILE,
    close_mode: str = DEFAULT_CLOSE_MODE,
    start_now: bool = DEFAULT_START_NOW,
    plan_only: bool = False,
    decompose_only: bool = False,
) -> Dict[str, Any]:
    parallelism = "parallel_safe" if str(parallel or "").strip().lower() == "safe" else DEFAULT_PARALLELISM
    return {
        "child_cap": max(1, int(max_children)),
        "child_pass_count": max(1, int(child_pass_count)),
        "loop_policy": DEFAULT_LOOP_POLICY,
        "loop_alias": "1.5_iterations",
        "start_now": bool(start_now),
        "proof_profile": str(proof_profile or DEFAULT_PROOF_PROFILE).strip().lower() or DEFAULT_PROOF_PROFILE,
        "close_mode": str(close_mode or DEFAULT_CLOSE_MODE).strip().lower() or DEFAULT_CLOSE_MODE,
        "parallelism": parallelism,
        "plan_only": bool(plan_only),
        "decompose_only": bool(decompose_only),
    }


def _decomposition_readiness_state(parent_uid: str) -> str:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is None:
        return ""
    try:
        meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    integration_meta = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    readiness = integration_meta.get("decomposition_readiness") if isinstance(integration_meta.get("decomposition_readiness"), dict) else {}
    return str(readiness.get("state") or "").strip().lower()


def _intent_guidance(uid: str) -> Dict[str, Any]:
    try:
        sheet = IntentSheetLedger(repo_root=REPO_ROOT).read_sheet(uid)
    except Exception:
        sheet = {}
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
    target = meta.get("decomposition_target_range") if isinstance(meta.get("decomposition_target_range"), dict) else {}
    tail = meta.get("mandatory_tail") if isinstance(meta.get("mandatory_tail"), dict) else {}
    implementation_min = int(target.get("min") or DEFAULT_IMPLEMENTATION_CHILD_MIN)
    implementation_max = int(target.get("max") or DEFAULT_IMPLEMENTATION_CHILD_MAX)
    if implementation_min < 0:
        implementation_min = DEFAULT_IMPLEMENTATION_CHILD_MIN
    if implementation_max < implementation_min:
        implementation_max = implementation_min
    mandatory_tail = {
        "validation": bool(tail.get("validation", DEFAULT_MANDATORY_TAIL["validation"])),
        "closeout": bool(tail.get("closeout", DEFAULT_MANDATORY_TAIL["closeout"])),
    }
    child_cap = implementation_max + int(mandatory_tail["validation"]) + int(mandatory_tail["closeout"])
    return {
        "decomposition_class": str(meta.get("decomposition_class") or "").strip().lower(),
        "decomposition_target_range": {"min": implementation_min, "max": implementation_max},
        "decomposition_rationale": str(meta.get("decomposition_rationale") or "").strip(),
        "mandatory_tail": mandatory_tail,
        "implementation_min": implementation_min,
        "implementation_max": implementation_max,
        "child_cap": max(1, child_cap),
    }


def _source_intent_uid_for_parent(parent_uid: str) -> str:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is None:
        return ""
    try:
        meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    integration_meta = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    intent_uid = str(integration_meta.get("intent_sheet_uid") or "").strip().upper()
    if intent_uid.startswith("IS-"):
        return intent_uid
    for tag in list(meta.get("tags") or []):
        text = str(tag or "").strip()
        if text.lower().startswith("source-intent:is-"):
            return text.split(":", 1)[-1].strip().upper()
    return ""


def _write_meta_patch(item_dir: Path, patch: Dict[str, Any]) -> None:
    meta_path = (item_dir / "meta.json").resolve()
    meta = ledger_storage.read_json(meta_path) or {}
    if isinstance(patch.get("integration_meta"), dict):
        existing = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
        meta["integration_meta"] = {**existing, **dict(patch.get("integration_meta") or {})}
        patch = {key: value for key, value in patch.items() if key != "integration_meta"}
    meta.update(patch)
    meta["updated_utc"] = _utc_now_iso()
    ledger_storage.atomic_write_json(meta_path, meta)


def _merged_intent_integration_meta(uid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    sheet = IntentSheetLedger(repo_root=REPO_ROOT).read_sheet(uid)
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
    existing = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    return {**existing, **patch}


def _intent_mission_fit(uid: str) -> Dict[str, Any]:
    sheet = IntentSheetLedger(repo_root=REPO_ROOT).read_sheet(uid)
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
    integration_meta = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    exception_record = integration_meta.get("mission_fit_exception") if isinstance(integration_meta.get("mission_fit_exception"), dict) else {}
    admission = mission_drift_gate.assess_intent_mission_fit(
        title=str(meta.get("title") or uid),
        intent_markdown=str(sheet.get("intent_markdown") or ""),
        exception_record=exception_record,
        repo_root=REPO_ROOT,
    )
    IntentSheetLedger(repo_root=REPO_ROOT).update_meta(
        uid,
        {"integration_meta": _merged_intent_integration_meta(uid, {"mission_fit": admission})},
    )
    return admission


def _build_intent_validation_pis_markdown(*, intent_uid: str, intent_title: str, parent_uid: str) -> str:
    validation_title = f"Validate {intent_title} Assembled-System Intent E2E Suite"
    return (
        "# Patch Instruction Sheet (PIS)\n\n"
        f"**Derived from Intent Sheet:** {intent_uid}\n"
        f"**Validation Family Parent:** {parent_uid}\n\n"
        f"## 1. Title\n{validation_title}\n\n"
        "## 2. Goal / user-visible behavior\n"
        "Design and execute a detailed intent-driven assembled-system E2E validation campaign that proves the implemented family actually satisfies the original intent sheet, not just the child PIS slices in isolation.\n\n"
        "## 3. Intent requirement map\n"
        "- enumerate the concrete intent outcomes, operator-facing promises, constraints, and failure modes that this family claims to satisfy\n"
        "- map each requirement to observable system behavior, durable state transitions, or evidence surfaces before claiming coverage\n"
        "- call out any intent clauses that remain ambiguous, underspecified, or not yet executable as tests\n\n"
        "## 4. Scenario matrix\n"
        "- define the representative success-path E2E scenarios that should pass when the intent is truly implemented\n"
        "- define the negative-path, mismatch-prone, and drift-detection scenarios that should fail closed or surface contradiction when the implementation diverges from the intent\n"
        "- identify the minimum observables, artifacts, and state transitions each scenario must inspect\n\n"
        "## 5. Validation execution plan\n"
        "- run one or more assembled-system success flows through the implemented path, not just isolated sibling checks\n"
        "- run the negative-path and mismatch-detection scenarios that are most likely to expose intent-implementation drift\n"
        "- prefer scenario-driven E2E proof that tests the whole family contract across implemented contracts, the plane surfaces, and durable state transitions the intent actually cares about\n"
        "- explicitly call out any required intent behavior that is still missing, approximated, or only indirectly covered\n\n"
        "## 6. Required evidence\n"
        "- capture durable evidence for what is proven, what remains unproven, and what is contradicted\n"
        "- emit one durable validation artifact that maps scenarios and observations back to the original intent requirements\n"
        "- summarize acceptance against the original intent requirements rather than against sibling completion alone\n\n"
        "## 7. Non-goals and promotion law\n"
        "- this PIS is the detailed validation-design child for the family, not the final ledger closeout owner\n"
        "- do not spend this child on ledger-only closeout work if assembled-system intent validation is still weak\n"
        "- a passing intent-validation suite is necessary input to final family closure, but it is not itself permission to promote the family or source intent\n"
        "- state explicit non-claims for any requirement that was not exercised, was only partially evidenced, or remains contradicted\n"
    )


def _build_intent_closeout_pis_markdown(*, intent_uid: str, intent_title: str, parent_uid: str) -> str:
    closeout_title = f"Close {intent_title} Intent Boundary, Proof, and Family Reconciliation"
    return (
        "# Patch Instruction Sheet (PIS)\n\n"
        f"**Derived from Intent Sheet:** {intent_uid}\n"
        f"**Validation Family Parent:** {parent_uid}\n\n"
        f"## 1. Title\n{closeout_title}\n\n"
        "## 2. Goal / user-visible behavior\n"
        "Perform final proof, bounded repair, reconciliation, and honest closure for this intent-derived family after implementation children and the intent-validation child have produced their evidence.\n\n"
        "## 3. Proof target\n"
        "- prove that the implementation children and the intent-validation child together are sufficient to support the family-level claim\n"
        "- inspect assembled-system evidence, durable receipts, validation artifacts, ledger status truth, and required chronology before closing\n"
        "- fail closed when the implementation family or the intent-validation evidence is too weak, stale, contradictory, or incomplete\n\n"
        "## 4. Operational continuity repair mode\n"
        "- this closeout child may repair bounded findings that block honest closure when those repairs stay within the family implementation boundary\n"
        "- after repair, rerun the focused validation needed to prove that the repaired state actually resolves the finding\n"
        "- do not hide contradictions behind ledger-only promotion; if repair cannot make the family truthful, leave it blocked with explicit reasons\n\n"
        "## 5. Closure reconciliation scope\n"
        "- reconcile the truth of the source intent sheet, the parent PIS, the implementation children, and the intent-validation child as one closure set\n"
        "- emit one durable closeout artifact summarizing proof, repairs applied, reruns performed, remaining gaps, and the honest promotion decision\n"
        "- promote only when the resulting state is coherent across evidence, ledger status, and durable artifacts\n"
    )


def _ensure_intent_family_tail_children(parent_uid: str, *, intent_uid: str = "") -> Dict[str, Any]:
    source_intent_uid = str(intent_uid or "").strip().upper() or _source_intent_uid_for_parent(parent_uid)
    if not source_intent_uid:
        return {"ok": True, "created": False, "validation_uid": "", "closeout_uid": "", "reason": "no_source_intent"}
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is None:
        return {"ok": False, "created": False, "validation_uid": "", "closeout_uid": "", "reason": "missing_parent"}
    parent_meta = ledger_storage.read_json(parent_dir / "meta.json") or {}
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    if not str(parent_integration.get("execution_mode") or "").strip():
        parent_integration = dict(parent_integration)
        parent_integration["execution_mode"] = "explicit_pass_chain_v1"
        _write_meta_patch(parent_dir, {"integration_meta": parent_integration})
        parent_meta = ledger_storage.read_json(parent_dir / "meta.json") or parent_meta
        parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else parent_integration
    child_uids = [str(x).strip() for x in list(parent_integration.get("child_uids") or []) if str(x).strip()]
    if not child_uids:
        return {"ok": True, "created": False, "validation_uid": "", "closeout_uid": "", "reason": "no_existing_children"}
    validation_uid = ""
    closeout_uid = ""
    for child_uid in child_uids:
        child_dir = sched._resolve_ledger_item_dir_for_uid(child_uid, repo_root=REPO_ROOT)
        if child_dir is None:
            continue
        child_meta = ledger_storage.read_json(child_dir / "meta.json") or {}
        child_integration = child_meta.get("integration_meta") if isinstance(child_meta.get("integration_meta"), dict) else {}
        relationship = str(child_integration.get("relationship") or "").strip()
        if relationship == _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND:
            validation_uid = child_uid
        elif relationship == _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND:
            closeout_uid = child_uid
    intent_ledger = IntentSheetLedger(repo_root=REPO_ROOT)
    sheet = intent_ledger.read_sheet(source_intent_uid)
    sheet_meta = dict(sheet.get("meta") or {})
    intent_title = str(sheet_meta.get("title") or source_intent_uid).strip() or source_intent_uid
    validation_title = f"Validate {intent_title} Assembled-System Intent E2E Suite"
    closeout_title = f"Close {intent_title} Intent Boundary, Proof, and Family Reconciliation"
    items = ChangeLedger(repo_root=REPO_ROOT)
    created_validation = False
    created_closeout = False
    if not validation_uid:
        created = items.create_item(
            title=validation_title,
            slug=_slugify(f"prove {intent_title} end to end intent validation suite", limit=56),
            tags=[
                "intent-derived",
                "intent-validation-suite",
                f"source-intent:{source_intent_uid.lower()}",
            ],
            pis_markdown=_build_intent_validation_pis_markdown(
                intent_uid=source_intent_uid,
                intent_title=intent_title,
                parent_uid=parent_uid,
            ),
            project_id=str(parent_meta.get("project_id") or sheet_meta.get("project_id") or ""),
            workstream_type=str(parent_meta.get("workstream_type") or sheet_meta.get("workstream_type") or ""),
            scope_boundary=str(parent_meta.get("scope_boundary") or sheet_meta.get("scope_boundary") or ""),
            artifact_roots=list(parent_meta.get("artifact_roots") or []),
        )
        validation_uid = str(created.get("uid") or "").strip()
        validation_dir = sched._resolve_ledger_item_dir_for_uid(validation_uid, repo_root=REPO_ROOT)
        prior_child_uids = [uid for uid in child_uids if uid != validation_uid]
        validation_integration = {
            "family": str(parent_integration.get("family") or "").strip(),
            "intent_sheet_uid": source_intent_uid,
            "parent_title": str(parent_meta.get("title") or parent_uid).strip() or parent_uid,
            "parent_uid": parent_uid,
            "relationship": _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND,
            "sequence_index": len(prior_child_uids) + 1,
            "mission_fit": parent_integration.get("mission_fit"),
        }
        _write_meta_patch(
            validation_dir,
            {
                "parent_uid": parent_uid,
                "parent_title": str(parent_meta.get("title") or parent_uid).strip() or parent_uid,
                "critical_path_position": len(prior_child_uids) + 1,
                "depends_on": list(prior_child_uids),
                "implementation_ready": True,
                "integration_meta": validation_integration,
            },
        )
        items.append_event(
            validation_uid,
            event_type="decomposed_from_parent",
            payload={
                "parent_uid": parent_uid,
                "relationship": _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND,
                "critical_path_rank": len(prior_child_uids) + 1,
                "depends_on": list(prior_child_uids),
            },
        )
        child_uids = prior_child_uids + [validation_uid]
        created_validation = True
    if not closeout_uid:
        created = items.create_item(
            title=closeout_title,
            slug=_slugify(f"close {intent_title} intent boundary proof family reconciliation", limit=56),
            tags=[
                "intent-derived",
                "intent-family-closeout",
                f"source-intent:{source_intent_uid.lower()}",
            ],
            pis_markdown=_build_intent_closeout_pis_markdown(
                intent_uid=source_intent_uid,
                intent_title=intent_title,
                parent_uid=parent_uid,
            ),
            project_id=str(parent_meta.get("project_id") or sheet_meta.get("project_id") or ""),
            workstream_type=str(parent_meta.get("workstream_type") or sheet_meta.get("workstream_type") or ""),
            scope_boundary=str(parent_meta.get("scope_boundary") or sheet_meta.get("scope_boundary") or ""),
            artifact_roots=list(parent_meta.get("artifact_roots") or []),
        )
        closeout_uid = str(created.get("uid") or "").strip()
        closeout_dir = sched._resolve_ledger_item_dir_for_uid(closeout_uid, repo_root=REPO_ROOT)
        prior_child_uids = [uid for uid in child_uids if uid != closeout_uid]
        closeout_integration = {
            "family": str(parent_integration.get("family") or "").strip(),
            "intent_sheet_uid": source_intent_uid,
            "parent_title": str(parent_meta.get("title") or parent_uid).strip() or parent_uid,
            "parent_uid": parent_uid,
            "relationship": _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND,
            "sequence_index": len(prior_child_uids) + 1,
            "mission_fit": parent_integration.get("mission_fit"),
        }
        _write_meta_patch(
            closeout_dir,
            {
                "parent_uid": parent_uid,
                "parent_title": str(parent_meta.get("title") or parent_uid).strip() or parent_uid,
                "critical_path_position": len(prior_child_uids) + 1,
                "depends_on": list(prior_child_uids),
                "implementation_ready": True,
                "integration_meta": closeout_integration,
            },
        )
        items.append_event(
            closeout_uid,
            event_type="decomposed_from_parent",
            payload={
                "parent_uid": parent_uid,
                "relationship": _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND,
                "critical_path_rank": len(prior_child_uids) + 1,
                "depends_on": list(prior_child_uids),
            },
        )
        child_uids = prior_child_uids + [closeout_uid]
        created_closeout = True
    parent_integration["child_uids"] = list(child_uids)
    parent_integration["recommended_execution_order"] = list(child_uids)
    parent_integration["intent_validation_child_uid"] = validation_uid
    parent_integration["required_final_child_uid"] = closeout_uid
    parent_integration["required_final_child_kind"] = (
        _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND if closeout_uid else ""
    )
    parent_integration["intent_sheet_uid"] = source_intent_uid
    parent_integration["execution_mode"] = str(parent_integration.get("execution_mode") or "explicit_pass_chain_v1").strip() or "explicit_pass_chain_v1"
    parent_integration["relationship"] = str(parent_integration.get("relationship") or "execution_parent").strip() or "execution_parent"
    _write_meta_patch(parent_dir, {"integration_meta": parent_integration})
    items.append_event(
        parent_uid,
        event_type="meta_updated",
        payload={"integration_meta": parent_integration},
    )
    validating_pis_uids = [str(x).strip() for x in list(sheet_meta.get("validating_pis_uids") or []) if str(x).strip()]
    if validation_uid and validation_uid not in validating_pis_uids:
        intent_ledger.update_meta(source_intent_uid, {"validating_pis_uids": validating_pis_uids + [validation_uid]})
    return {
        "ok": True,
        "created": bool(created_validation or created_closeout),
        "validation_uid": validation_uid,
        "closeout_uid": closeout_uid,
    }


def _build_decompose_job(parent_uid: str, *, options: Dict[str, Any]) -> Dict[str, Any]:
    row = _pis_choice_from_uid(parent_uid)
    spec = _scheduler_create_job._default_spec(row, prompt_mode="decompose_parent", queue_child_jobs=True, child_delay_minutes=0)
    spec["job_id"] = _job_id("implement", parent_uid, row.title + "-decompose")
    spec["depends_on"] = []
    spec["schedule"]["run_at"] = _utc_now_iso()
    loop = dict(spec.get("loop") or {})
    loop["builder_only"] = True
    loop["verifier_role"] = ""
    loop["max_iterations"] = DEFAULT_LOOP_MAX_ITERATIONS
    loop["loop_policy"] = DEFAULT_LOOP_POLICY
    spec["loop"] = loop
    guidance = options.get("intent_guidance") if isinstance(options.get("intent_guidance"), dict) else {}
    contract_lines = _format_decomposition_contract(guidance)
    spec["prompt"] = (
        str(spec.get("prompt") or "").rstrip()
        + "\n\nRecursive implementation defaults:\n"
        + f"- start now: {bool(options.get('start_now'))}\n"
        + f"- cap child PIS count at {int(options.get('child_cap') or DEFAULT_MAX_CHILDREN)}\n"
        + "\n".join(contract_lines)
        + "\n"
        + "- use explicit build_or_improve pass chains as the default child implementation mode\n"
        + f"- child implementation policy: {str(options.get('parallelism') or DEFAULT_PARALLELISM)}\n"
        + f"- proof profile: {str(options.get('proof_profile') or DEFAULT_PROOF_PROFILE)}\n"
        + f"- close mode: {str(options.get('close_mode') or DEFAULT_CLOSE_MODE)}\n"
        + "Readiness contract:\n"
        + "- before creating executable children, decide whether there is enough detail for an honest qualitative decomposition\n"
        + "- persist that decision in parent integration_meta.decomposition_readiness\n"
        + "- preserve parent integration_meta.mission_fit when creating any durable child work\n"
        + "- block or require explicit exception handling if proposed child work drifts beyond the recorded mission-fit admission\n"
        + "- use state=decomposition_ready when executable child boundaries are justified\n"
        + "- use state=clarification_needed when the parent or linked intent is too vague for executable fanout\n"
        + "- when clarification_needed, persist a short summary and a missing_details list in integration_meta.decomposition_readiness\n"
        + "- when clarification_needed, do not invent executable implementation children; at most create narrow clarification_child or discovery_child items\n"
    )
    prompt_meta = dict(spec.get("prompt_meta") or {})
    queue_meta = dict(prompt_meta.get("queue_child_implementation_jobs") or {})
    queue_meta.update(
        {
            "enabled": True,
            "run_after_minutes": 0,
            "child_mode": "explicit_pass_chain_v1",
            "max_iterations": max(1, int(options.get("child_pass_count") or DEFAULT_CHILD_PASS_COUNT)),
        }
    )
    prompt_meta["queue_child_implementation_jobs"] = queue_meta
    prompt_meta["implement_recursive_defaults"] = dict(options)
    if isinstance(options.get("mission_fit"), dict):
        prompt_meta["mission_fit"] = dict(options.get("mission_fit") or {})
    spec["prompt_meta"] = prompt_meta
    return spec


def _build_leaf_job(uid: str, *, options: Dict[str, Any]) -> Dict[str, Any]:
    row = _pis_choice_from_uid(uid)
    builder_model = _batch_from_parent._default_builder_model()
    verifier_model = _batch_from_parent._default_verifier_model()
    builder_runtime = _batch_from_parent._stage_runtime(builder_model)
    verifier_runtime = _batch_from_parent._stage_runtime(verifier_model)
    builder_runtime["compiled_execution_mode"] = "soft_gate"
    verifier_runtime["compiled_execution_mode"] = "soft_gate"
    spec = {
        "job_id": sched._job_id_from_ledger_uid(row.uid, row.title),
        "job_kind": "build_verify_loop",
        "enabled": True,
        "binding": {"type": "ledger_uid", "id": row.uid},
        "display_title": f"{row.uid.split('-')[-1].lower()} {row.title[:80]}",
        "prompt": sched._implementation_prompt_for_child(
            uid=row.uid,
            title=row.title,
            pis_rel=str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            status=row.status,
            repo_root=REPO_ROOT,
        ),
        "context_refs": [str(row.pis_path.relative_to(REPO_ROOT)).replace("\\", "/")],
        "expected_output": {"kind": "report", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": _utc_now_iso(), "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 1800, "post_success_commit_tag": True},
        "runtime": sched._normalize_runtime(dict(verifier_runtime), job_kind="build_verify_loop"),
        "required_context_bundle": "lab_core_v1",
        "loop": {
            "builder_role": "builder",
            "verifier_role": "verifier",
            "loop_policy": DEFAULT_LOOP_POLICY,
            "max_iterations": DEFAULT_LOOP_MAX_ITERATIONS,
            "pass_marker": "VERDICT: PASS",
            "builder_runtime": sched._normalize_runtime(dict(builder_runtime), job_kind="build_verify_loop"),
            "verifier_runtime": sched._normalize_runtime(dict(verifier_runtime), job_kind="build_verify_loop"),
        },
        "prompt_meta": {
            "implement_recursive_defaults": dict(options)
        },
    }
    return spec


def _save_job(spec: Dict[str, Any]) -> Dict[str, Any]:
    try:
        existing = sched.load_job(str(spec.get("job_id") or ""), repo_root=REPO_ROOT)
    except FileNotFoundError:
        existing = {}
    if existing:
        merged = _batch_from_parent._merge_existing(existing, spec)
        saved = sched.save_job(merged, repo_root=REPO_ROOT)
        saved["_save_status"] = "updated"
        return saved
    saved = sched.save_job(spec, repo_root=REPO_ROOT)
    saved["_save_status"] = "created"
    return saved


def _run_now(job_id: str) -> Dict[str, Any]:
    return sched.request_run_now(
        job_id,
        repo_root=REPO_ROOT,
        requested_utc=_utc_now_iso(),
        request_source="implement_recursive",
    )


def _job_terminal_success(job_id: str) -> bool:
    state = sched.load_state(job_id, repo_root=REPO_ROOT)
    status = str(state.get("last_status") or "").strip().lower()
    return status == "ok"


def _job_currently_active(job_id: str) -> bool:
    state = sched.load_state(job_id, repo_root=REPO_ROOT)
    return bool(str(state.get("active_run_id") or "").strip())


def _child_execution_pass_count(parent_meta: Dict[str, Any], *, options: Optional[Dict[str, Any]] = None) -> int:
    if isinstance(options, dict):
        explicit = int(options.get("child_pass_count") or 0)
        if explicit > 0:
            return explicit
    integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    configured = int(integration.get("child_pass_count") or 0)
    if configured > 0:
        return configured
    execution_mode = str(integration.get("execution_mode") or "").strip().lower()
    if execution_mode in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}:
        return DEFAULT_CHILD_PASS_COUNT
    return DEFAULT_LOOP_MAX_ITERATIONS


def _deps_terminal_success(job_ids: List[str]) -> bool:
    return all(_job_terminal_success(job_id) for job_id in list(job_ids or []))


def _batch_children(parent_uid: str, *, options: Dict[str, Any]) -> Dict[str, Any]:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    parent_meta = sched._read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_status = str(parent_meta.get("status") or "").strip().lower()
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
    child_uids = [str(row.get("uid") or "").strip() for row in child_rows if str(row.get("uid") or "").strip()]
    child_statuses = {sched._ledger_status_for_uid(child_uid, repo_root=REPO_ROOT) for child_uid in child_uids}
    if parent_status == "verified" and bool(child_uids) and child_statuses == {"verified"}:
        reconcile = _reconcile_source_intent_for_verified_family(parent_uid)
        return {
            "saved_job_ids": [],
            "started_job_id": "",
            "started_result": {},
            "skipped_reason": "family_already_verified",
            "source_intent_reconciled": bool(reconcile.get("reconciled")),
            "source_intent_uid": str(reconcile.get("source_intent_uid") or ""),
        }
    planned = _batch_from_parent._planned_child_specs(
        parent_uid,
        run_at_utc=_utc_now_iso(),
        builder_model=_batch_from_parent._default_builder_model(),
        verifier_model=_batch_from_parent._default_verifier_model(),
        max_iterations=_child_execution_pass_count(parent_meta, options=options),
        repo_root=REPO_ROOT,
    )
    saved: List[str] = []
    first_ready = ""
    for spec in planned:
        job_id = str(spec.get("job_id") or "")
        if not _job_terminal_success(job_id):
            spec["schedule"]["run_at"] = _utc_now_iso()
        loop = dict(spec.get("loop") or {})
        loop["loop_policy"] = DEFAULT_LOOP_POLICY
        loop["max_iterations"] = DEFAULT_LOOP_MAX_ITERATIONS
        spec["loop"] = loop
        prompt_meta = dict(spec.get("prompt_meta") or {})
        prompt_meta["implement_recursive_defaults"] = dict(options)
        spec["prompt_meta"] = prompt_meta
        out = _save_job(spec)
        job_id = str(out.get("job_id") or job_id or "")
        saved.append(job_id)
        if first_ready:
            continue
        depends_on = [str(dep or "").strip() for dep in list(spec.get("depends_on") or []) if str(dep or "").strip()]
        if _job_terminal_success(job_id) or _job_currently_active(job_id):
            continue
        if _deps_terminal_success(depends_on):
            first_ready = job_id
    started = _run_now(first_ready) if first_ready and bool(options.get("start_now")) else {}
    return {"saved_job_ids": saved, "started_job_id": first_ready, "started_result": started}


def _plan_child_batch(parent_uid: str, *, options: Dict[str, Any]) -> Dict[str, Any]:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    parent_meta = sched._read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_status = str(parent_meta.get("status") or "").strip().lower()
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
    child_uids = [str(row.get("uid") or "").strip() for row in child_rows if str(row.get("uid") or "").strip()]
    child_statuses = {sched._ledger_status_for_uid(child_uid, repo_root=REPO_ROOT) for child_uid in child_uids}
    if parent_status == "verified" and bool(child_uids) and child_statuses == {"verified"}:
        return {
            "planned_job_ids": [],
            "first_ready_job_id": "",
            "skipped_reason": "family_already_verified",
        }
    planned = _batch_from_parent._planned_child_specs(
        parent_uid,
        run_at_utc=_utc_now_iso(),
        builder_model=_batch_from_parent._default_builder_model(),
        verifier_model=_batch_from_parent._default_verifier_model(),
        max_iterations=_child_execution_pass_count(parent_meta, options=options),
        repo_root=REPO_ROOT,
    )
    planned_job_ids: List[str] = []
    first_ready = ""
    for spec in planned:
        job_id = str(spec.get("job_id") or "")
        planned_job_ids.append(job_id)
        if first_ready:
            continue
        depends_on = [str(dep or "").strip() for dep in list(spec.get("depends_on") or []) if str(dep or "").strip()]
        if _job_terminal_success(job_id) or _job_currently_active(job_id):
            continue
        if _deps_terminal_success(depends_on):
            first_ready = job_id
    return {"planned_job_ids": planned_job_ids, "first_ready_job_id": first_ready}


def _reconcile_source_intent_for_verified_family(parent_uid: str) -> Dict[str, Any]:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is None:
        return {"ok": False, "reconciled": False, "reason": "missing_parent", "source_intent_uid": ""}
    parent_meta = ledger_storage.read_json(parent_dir / "meta.json") or {}
    parent_status = str(parent_meta.get("status") or "").strip().lower()
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = str(parent_integration.get("intent_sheet_uid") or "").strip().upper() or _source_intent_uid_for_parent(parent_uid)
    if not source_intent_uid:
        return {"ok": True, "reconciled": False, "reason": "no_source_intent", "source_intent_uid": ""}
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
    child_uids = [str(row.get("uid") or "").strip() for row in child_rows if str(row.get("uid") or "").strip()]
    if parent_status != "verified" or not child_uids:
        return {"ok": True, "reconciled": False, "reason": "family_not_verified", "source_intent_uid": source_intent_uid}
    child_statuses = {sched._ledger_status_for_uid(child_uid, repo_root=REPO_ROOT) for child_uid in child_uids}
    if child_statuses != {"verified"}:
        return {"ok": True, "reconciled": False, "reason": "children_not_verified", "source_intent_uid": source_intent_uid}
    validation_uid = str(parent_integration.get("intent_validation_child_uid") or "").strip()
    closeout_uid = str(parent_integration.get("required_final_child_uid") or "").strip()
    for child_uid in child_uids:
        child_dir = sched._resolve_ledger_item_dir_for_uid(child_uid, repo_root=REPO_ROOT)
        if child_dir is None:
            continue
        child_meta = ledger_storage.read_json(child_dir / "meta.json") or {}
        child_integration = child_meta.get("integration_meta") if isinstance(child_meta.get("integration_meta"), dict) else {}
        relationship = str(child_integration.get("relationship") or "").strip()
        if relationship == "intent_validation_suite" and not validation_uid:
            validation_uid = child_uid
        elif relationship == "intent_family_closeout" and not closeout_uid:
            closeout_uid = child_uid
    validating_uid = closeout_uid or validation_uid
    if not validating_uid:
        return {"ok": True, "reconciled": False, "reason": "missing_validating_uid", "source_intent_uid": source_intent_uid}
    intent_ledger = IntentSheetLedger(repo_root=REPO_ROOT)
    sheet = intent_ledger.read_sheet(source_intent_uid)
    sheet_meta = dict(sheet.get("meta") or {})
    current_validating = [str(x).strip() for x in list(sheet_meta.get("validating_pis_uids") or []) if str(x).strip()]
    desired_validating = [validating_uid]
    evidence = {
        "kind": "intent_status_reconcile_verification",
        "validated_utc": _utc_now_iso(),
        "verified_parent_uid": parent_uid,
    }
    patch: Dict[str, Any] = {}
    if [uid.upper() for uid in current_validating] != [uid.upper() for uid in desired_validating]:
        patch["validating_pis_uids"] = desired_validating
    derived = [str(x).strip() for x in list(sheet_meta.get("derived_pis_uids") or []) if str(x).strip()]
    if parent_uid not in derived:
        patch["derived_pis_uids"] = derived + [parent_uid]
    patch["evidence"] = evidence
    if patch:
        intent_ledger.update_meta(source_intent_uid, patch)
    if str(sheet_meta.get("status") or "").strip().lower() != "verified":
        intent_ledger.update_status(
            source_intent_uid,
            "verified",
            note=f"validated_by_verified_{parent_uid.lower()}_family_closeout_and_final_child",
        )
    return {"ok": True, "reconciled": True, "reason": "reconciled", "source_intent_uid": source_intent_uid}


def _ensure_child_link_events(parent_uid: str) -> Dict[str, Any]:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=REPO_ROOT)
    if parent_dir is None:
        return {"linked_count": 0, "discovered_count": 0, "child_uids": []}
    try:
        parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        parent_meta = {}
    integration_meta = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    child_uids = [str(x).strip() for x in list(integration_meta.get("child_uids") or []) if str(x).strip()]
    if not child_uids:
        rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
        discovered_uids = [str(row.get("uid") or "").strip() for row in rows if str(row.get("uid") or "").strip()]
        if not discovered_uids:
            return {"linked_count": 0, "discovered_count": 0, "child_uids": []}
        integration_meta = dict(integration_meta)
        integration_meta["child_uids"] = list(discovered_uids)
        integration_meta["recommended_execution_order"] = list(discovered_uids)
        _write_meta_patch(parent_dir, {"integration_meta": integration_meta})
        parent_meta = ledger_storage.read_json(parent_dir / "meta.json") or {}
        integration_meta = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
        child_uids = [str(x).strip() for x in list(integration_meta.get("child_uids") or []) if str(x).strip()]
    ledger = ChangeLedger(repo_root=REPO_ROOT)
    linked = 0
    for child_uid in child_uids:
        child_dir = sched._resolve_ledger_item_dir_for_uid(child_uid, repo_root=REPO_ROOT)
        if child_dir is None:
            continue
        try:
            child_meta = json.loads((child_dir / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            child_meta = {}
        events_path = (child_dir / "events.jsonl").resolve()
        already_linked = False
        if events_path.exists():
            for raw in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if str(event.get("type") or "") != "decomposed_from_parent":
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if str(payload.get("parent_uid") or "").strip() == parent_uid:
                    already_linked = True
                    break
        if already_linked:
            continue
        child_integration = child_meta.get("integration_meta") if isinstance(child_meta.get("integration_meta"), dict) else {}
        depends_on = [str(x).strip() for x in list(child_meta.get("depends_on") or []) if str(x).strip()]
        try:
            critical_path_rank = int(
                child_meta.get("critical_path_position")
                or child_integration.get("sequence_index")
                or 0
            )
        except Exception:
            critical_path_rank = 0
        ledger.append_event(
            child_uid,
            event_type="decomposed_from_parent",
            payload={
                "parent_uid": parent_uid,
                "relationship": str(child_integration.get("relationship") or "execution_child").strip() or "execution_child",
                "critical_path_rank": critical_path_rank,
                "depends_on": depends_on,
            },
        )
        linked += 1
    rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
    return {"linked_count": linked, "discovered_count": len(rows), "child_uids": [str(row.get("uid") or "") for row in rows]}


def _item_kind(uid: str) -> str:
    if str(uid or "").startswith("IS-"):
        return "intent_sheet"
    if str(uid or "").startswith("PIS-"):
        return "pis"
    raise ValueError("uid must start with IS- or PIS-")


def run(
    uid: str,
    *,
    plan_only: bool = False,
    decompose_only: bool = False,
    max_children: int = DEFAULT_MAX_CHILDREN,
    child_pass_count: int = DEFAULT_CHILD_PASS_COUNT,
    parallel: str = "safe",
    proof_profile: str = DEFAULT_PROOF_PROFILE,
    close_mode: str = DEFAULT_CLOSE_MODE,
    start_now: bool = DEFAULT_START_NOW,
) -> Dict[str, Any]:
    normalized_uid, kind = _resolve_target_uid(uid)
    options = _options_dict(
        max_children=max_children,
        child_pass_count=child_pass_count,
        parallel=parallel,
        proof_profile=proof_profile,
        close_mode=close_mode,
        start_now=start_now,
        plan_only=plan_only,
        decompose_only=decompose_only,
    )
    if kind == "intent_sheet":
        mission_fit = _intent_mission_fit(normalized_uid)
        options["mission_fit"] = dict(mission_fit)
        if not bool(mission_fit.get("ok")):
            return {
                "ok": False,
                "target_uid": normalized_uid,
                "resolved_kind": kind,
                "mode": "mission_fit_blocked",
                "blocked_reason": "mission_drift_blocked",
                "mission_fit": mission_fit,
                "options": dict(options),
            }
        intent_guidance = _intent_guidance(normalized_uid)
        options["intent_guidance"] = dict(intent_guidance)
        options["child_cap"] = max(1, int(intent_guidance.get("child_cap") or options.get("child_cap") or DEFAULT_MAX_CHILDREN))
        if bool(plan_only):
            parent_uid, parent_meta = _existing_parent_for_intent(normalized_uid)
            if not parent_uid:
                return {
                    "ok": True,
                    "target_uid": normalized_uid,
                    "resolved_kind": kind,
                    **parent_meta,
                    "mode": "plan_create_parent_then_queue_children",
                    "options": dict(options),
                    "would_create_parent": True,
                }
            child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
            if child_rows:
                batch_result = _plan_child_batch(parent_uid, options=options)
                return {
                    "ok": True,
                    "target_uid": normalized_uid,
                    "resolved_kind": kind,
                    "resolved_parent_uid": parent_uid,
                    **parent_meta,
                    "mode": "plan_children_existing_parent",
                    "options": dict(options),
                    **batch_result,
                }
            spec = _build_decompose_job(parent_uid, options=options)
            return {
                "ok": True,
                "target_uid": normalized_uid,
                "resolved_kind": kind,
                "resolved_parent_uid": parent_uid,
                **parent_meta,
                "mode": "plan_decompose_parent_then_queue_children",
                "options": dict(options),
                "planned_job_id": str(spec.get("job_id") or ""),
            }
        parent_uid, parent_meta = _ensure_parent_for_intent(normalized_uid)
        _ensure_child_link_events(parent_uid)
        _ensure_intent_family_tail_children(parent_uid, intent_uid=normalized_uid)
        child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=REPO_ROOT)
        if child_rows:
            if bool(decompose_only):
                return {
                    "ok": True,
                    "target_uid": normalized_uid,
                    "resolved_kind": kind,
                    "resolved_parent_uid": parent_uid,
                    **parent_meta,
                    "mode": "decompose_only_existing_parent",
                    "options": dict(options),
                }
            batch_result = _batch_children(parent_uid, options=options)
            return {
                "ok": True,
                "target_uid": normalized_uid,
                "resolved_kind": kind,
                "resolved_parent_uid": parent_uid,
                **parent_meta,
                "mode": "batch_children_existing_parent",
                "options": dict(options),
                **batch_result,
            }
        spec = _build_decompose_job(parent_uid, options=options)
        saved = _save_job(spec)
        started = _run_now(str(saved.get("job_id") or spec.get("job_id") or "")) if bool(options.get("start_now")) else {}
        child_queue = {}
        if not bool(decompose_only) and bool(started.get("ok")) and str(started.get("report_path") or ""):
            try:
                receipt = sched.load_receipt(str(saved.get("job_id") or spec.get("job_id") or ""), str(started.get("run_id") or ""), repo_root=REPO_ROOT)
                child_queue = dict(receipt.get("child_job_queue") or {})
                readiness_state = _decomposition_readiness_state(parent_uid)
                if readiness_state in {"clarification_needed", "not_ready", "blocked"}:
                    child_queue.setdefault("queue_blocked_reason", readiness_state)
                elif not list(child_queue.get("children") or []):
                    repair = _ensure_child_link_events(parent_uid)
                    if int(repair.get("discovered_count") or 0) > 0:
                        _ensure_intent_family_tail_children(parent_uid, intent_uid=normalized_uid)
                        child_queue = _batch_children(parent_uid, options=options)
                started_child = ""
                for row in list(child_queue.get("children") or []):
                    if not list(row.get("depends_on") or []):
                        started_child = str(row.get("job_id") or "")
                        break
                if started_child:
                    child_queue["started_child_job_id"] = started_child
            except Exception:
                child_queue = {}
        return {
            "ok": True,
            "target_uid": normalized_uid,
            "resolved_kind": kind,
            "resolved_parent_uid": parent_uid,
            **parent_meta,
            "mode": "decompose_only" if bool(decompose_only) else "decompose_parent_then_queue_children",
            "options": dict(options),
            "saved_job_id": str(saved.get("job_id") or spec.get("job_id") or ""),
            "saved_job_status": str(saved.get("_save_status") or ""),
            "started_result": started,
            "child_job_queue": child_queue,
        }

    if bool(plan_only):
        child_rows = sched._iter_parent_child_rows(parent_uid=normalized_uid, repo_root=REPO_ROOT)
        if child_rows:
            if bool(decompose_only):
                return {
                    "ok": True,
                    "target_uid": normalized_uid,
                    "resolved_kind": kind,
                    "mode": "decompose_only_existing_parent",
                    "options": dict(options),
                }
            batch_result = _plan_child_batch(normalized_uid, options=options)
            return {
                "ok": True,
                "target_uid": normalized_uid,
                "resolved_kind": kind,
                "mode": "plan_children_existing_parent",
                "options": dict(options),
                **batch_result,
            }
    else:
        _ensure_child_link_events(normalized_uid)
        _ensure_intent_family_tail_children(normalized_uid)
        child_rows = sched._iter_parent_child_rows(parent_uid=normalized_uid, repo_root=REPO_ROOT)
        if child_rows:
            if bool(decompose_only):
                return {
                    "ok": True,
                    "target_uid": normalized_uid,
                    "resolved_kind": kind,
                    "mode": "decompose_only_existing_parent",
                    "options": dict(options),
                }
            batch_result = _batch_children(normalized_uid, options=options)
            return {
                "ok": True,
                "target_uid": normalized_uid,
                "resolved_kind": kind,
                "mode": "batch_children_existing_parent",
                "options": dict(options),
                **batch_result,
            }
    spec = _build_leaf_job(normalized_uid, options=options)
    if bool(plan_only):
        return {
            "ok": True,
            "target_uid": normalized_uid,
            "resolved_kind": kind,
            "mode": "plan_leaf_build_verify_repair",
            "options": dict(options),
            "planned_job_id": str(spec.get("job_id") or ""),
        }
    saved = _save_job(spec)
    started = _run_now(str(saved.get("job_id") or spec.get("job_id") or "")) if bool(options.get("start_now")) else {}
    return {
        "ok": True,
        "target_uid": normalized_uid,
        "resolved_kind": kind,
        "mode": "leaf_build_verify_repair",
        "options": dict(options),
        "saved_job_id": str(saved.get("job_id") or spec.get("job_id") or ""),
        "saved_job_status": str(saved.get("_save_status") or ""),
        "started_result": started,
    }


def _render_human(result: Dict[str, Any]) -> str:
    lines = [
        f"target_uid: {str(result.get('target_uid') or '')}",
        f"resolved_kind: {str(result.get('resolved_kind') or '')}",
        f"mode: {str(result.get('mode') or '')}",
    ]
    if str(result.get("resolved_parent_uid") or "").strip():
        lines.append(f"resolved_parent_uid: {str(result.get('resolved_parent_uid') or '')}")
    if bool(result.get("created_parent")):
        lines.append(f"created_parent_uid: {str(result.get('created_parent_uid') or '')}")
    if bool(result.get("would_create_parent")):
        lines.append("would_create_parent: true")
    if str(result.get("planned_job_id") or "").strip():
        lines.append(f"planned_job_id: {str(result.get('planned_job_id') or '')}")
    planned_job_ids = list(result.get("planned_job_ids") or [])
    if planned_job_ids:
        lines.append("planned_job_ids:")
        lines.extend([f"- {job_id}" for job_id in planned_job_ids])
    if str(result.get("first_ready_job_id") or "").strip():
        lines.append(f"first_ready_job_id: {str(result.get('first_ready_job_id') or '')}")
    if str(result.get("saved_job_id") or "").strip():
        lines.append(f"saved_job_id: {str(result.get('saved_job_id') or '')}")
        if str(result.get("saved_job_status") or "").strip():
            lines.append(f"saved_job_status: {str(result.get('saved_job_status') or '')}")
    saved_job_ids = list(result.get("saved_job_ids") or [])
    if saved_job_ids:
        lines.append("saved_job_ids:")
        lines.extend([f"- {job_id}" for job_id in saved_job_ids])
    if str(result.get("started_job_id") or "").strip():
        lines.append(f"started_job_id: {str(result.get('started_job_id') or '')}")
    started = dict(result.get("started_result") or {})
    if started:
        lines.append(f"started_ok: {bool(started.get('ok'))}")
        if bool(started.get("deduped")):
            lines.append(f"started_status: deduped_existing_request ({str(started.get('reason') or 'already_requested')})")
        elif bool(started.get("queued")):
            lines.append("started_status: queued_new_request")
        if str(started.get("run_id") or "").strip():
            lines.append(f"started_run_id: {str(started.get('run_id') or '')}")
    if str(result.get("skipped_reason") or "").strip():
        lines.append(f"skipped_reason: {str(result.get('skipped_reason') or '')}")
    child_queue = dict(result.get("child_job_queue") or {})
    if child_queue:
        lines.append(f"child_jobs_created: {int(child_queue.get('created_count') or 0)}")
        if str(child_queue.get("started_child_job_id") or "").strip():
            lines.append(f"first_child_ready_job_id: {str(child_queue.get('started_child_job_id') or '')}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Start recursive implementation for an Intent Sheet or PIS.")
    parser.add_argument("--uid", required=True, help="Target IS-... or PIS-... uid, uid suffix, or slug.")
    parser.add_argument("--plan-only", action="store_true", help="Resolve and plan the implementation path without saving or starting jobs.")
    parser.add_argument("--decompose-only", action="store_true", help="Stop after parent decomposition setup; do not queue child implementation work.")
    parser.add_argument("--max-children", type=int, default=DEFAULT_MAX_CHILDREN, help="Maximum child PIS count to request during decomposition (default: 8).")
    parser.add_argument("--child-pass-count", type=int, default=DEFAULT_CHILD_PASS_COUNT, help="Number of explicit build_or_improve passes per implementation child (default: 3).")
    parser.add_argument("--parallel", choices=("safe", "off"), default="safe", help="Child implementation parallelism policy.")
    parser.add_argument("--proof-profile", choices=("focused", "full"), default=DEFAULT_PROOF_PROFILE, help="Proof/verification profile for generated implementation jobs.")
    parser.add_argument("--close", choices=("auto", "manual"), default=DEFAULT_CLOSE_MODE, help="Closure policy for verified children and parents.")
    parser.add_argument("--start-now", choices=("true", "false"), default="true", help="Whether to start saved jobs immediately.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(
        str(args.uid or "").strip(),
        plan_only=bool(args.plan_only),
        decompose_only=bool(args.decompose_only),
        max_children=max(1, int(args.max_children)),
        child_pass_count=max(1, int(args.child_pass_count)),
        parallel=str(args.parallel or "safe"),
        proof_profile=str(args.proof_profile or DEFAULT_PROOF_PROFILE),
        close_mode=str(args.close or DEFAULT_CLOSE_MODE),
        start_now=str(args.start_now or "true").strip().lower() != "false",
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_render_human(result))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
