"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Command submission (inbox)
# -----------------------------------------------------------------------------


def _ensure_intervention_plan_exists(sim_root: Path) -> None:
    """Best-effort bootstrap of InterventionPlan v2 files for legacy/migrated runs.

    Some runs created via older harness/wrapper paths may not have plans/*
    despite ControlPlane requiring one at submit time. Creating the initial plan
    here keeps command submission functional without changing engine semantics.
    """
    root = Path(sim_root).resolve()
    plans_dir = root / "plans"
    ptr_path = plans_dir / "current_plan.json"
    if ptr_path.exists():
        return

    plans_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic plan id derived from stable run path.
    plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "EIEL_PLAN|" + str(root).replace("\\", "/")))
    plan_path = plans_dir / f"plan_{plan_id}_v000001.json"
    plan_obj = {
        "schema_version": "intervention_plan_v2",
        "plan_id": plan_id,
        "campaign_id": "",
        "version": 1,
        "parent_plan_version": None,
        "created_at_generation": 0,
        "expectation_note": "initial plan (webui bootstrap)",
        "commands": [],
        "frozen": False,
    }
    ptr_obj = {"plan_id": plan_id, "version": 1}

    try:
        if not plan_path.exists():
            atomic_write_canonical_json(plan_path, plan_obj)
        atomic_write_canonical_json(ptr_path, ptr_obj)
    except Exception:
        # If another process wins the race, that's fine.
        if not ptr_path.exists():
            raise


def _canonicalize_command_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow-copied payload with guaranteed schema_version."""
    out = dict(payload)
    if "schema_version" not in out and "command_type" in out:
        # Legacy default.
        out["schema_version"] = "cmd_v1"
    return out


def _deterministic_command_uuid(payload: Dict[str, Any]) -> str:
    """Derive a deterministic command_uuid from payload (excluding command_uuid).

    Why: idempotency.

    Policy:
    - Compute SHA256 of canonical JSON of the payload *without* command_uuid.
    - UUID5(namespace, sha256_hex)

    Note: This makes identical payload retries stable. If you need two
    intentionally-identical commands, set command_uuid explicitly.
    """
    tmp = dict(payload)
    tmp.pop("command_uuid", None)
    txt = json.dumps(tmp, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(txt.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "EIEL_COMMAND|" + h))


def _validate_trait_injection_cmd_v2(payload: Dict[str, Any], *, frozen_apply_phase: Optional[str]) -> None:
    """Validate TraitInjectionCommand cmd_v2 shape (best-effort).

    Raises ValueError on invalid payload.
    """
    if str(payload.get("schema_version")) != "cmd_v2":
        raise ValueError("TraitInjectionCommand must have schema_version 'cmd_v2'")
    if str(payload.get("command_type")) != "TraitInjectionCommand":
        raise ValueError("command_type must be 'TraitInjectionCommand'")

    try:
        int(payload.get("target_generation"))
    except Exception as e:
        raise ValueError("target_generation must be int") from e

    ti = payload.get("trait_injection")
    if not isinstance(ti, dict):
        raise ValueError("trait_injection must be an object")
    if str(ti.get("schema_version")) != "trait_injection_v2":
        raise ValueError("trait_injection.schema_version must be 'trait_injection_v2'")

    ap = str(ti.get("apply_phase", "") or "")
    if ap not in {"post-reproduction-pre-selection", "post-selection-pre-mutation"}:
        raise ValueError("trait_injection.apply_phase invalid")

    if frozen_apply_phase is not None and ap != frozen_apply_phase:
        raise ValueError(f"apply_phase '{ap}' conflicts with frozen '{frozen_apply_phase}'")

    try:
        int(ti.get("duration_generations"))
    except Exception as e:
        raise ValueError("trait_injection.duration_generations must be int") from e

    if ti.get("locus_tags") is not None and not isinstance(ti.get("locus_tags"), list):
        raise ValueError("trait_injection.locus_tags must be list")
    if ti.get("allele_values") is not None and not isinstance(ti.get("allele_values"), dict):
        raise ValueError("trait_injection.allele_values must be object")
    if ti.get("target_agent_ids") is not None and not isinstance(ti.get("target_agent_ids"), list):
        raise ValueError("trait_injection.target_agent_ids must be list")


def submit_inbox_command(sim_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Write a ControlPlane command JSON into <sim_root>/inbox/.

    This is the ONLY write path that touches the simulator side of an existing run.

    Implementation requirements:
    - tmp + os.replace atomicity
    - deterministic-ish filename ordering
    - do not touch existing engine outputs

    Returns:
        dict with: {"status": "ok", "command_uuid": ..., "path": ...}

    Raises:
        ValueError: if payload is invalid.
    """
    if not isinstance(payload, dict):
        raise ValueError("command payload must be a JSON object")

    _ensure_intervention_plan_exists(Path(sim_root))

    payload = _canonicalize_command_payload(payload)

    ctype = str(payload.get("command_type", "") or "")
    if not ctype:
        raise ValueError("missing command_type")

    try:
        tg = int(payload.get("target_generation", 0))
    except Exception as e:
        raise ValueError("target_generation must be int") from e

    # Phase 5b default: validate TraitInjectionCommand cmd_v2 if present.
    if str(payload.get("schema_version")) == "cmd_v2" and ctype == "TraitInjectionCommand":
        frozen = _detect_trait_apply_phase_frozen(sim_root)
        _validate_trait_injection_cmd_v2(payload, frozen_apply_phase=frozen)

    if not payload.get("command_uuid"):
        payload["command_uuid"] = _deterministic_command_uuid(payload)

    cu = str(payload.get("command_uuid", "") or "")
    if not cu:
        raise ValueError("missing command_uuid")

    inbox = sim_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    safe_ctype = "".join(c for c in ctype if c.isalnum() or c in "._-")[:32] or "cmd"
    fname = f"gen{tg:06d}__{safe_ctype}__{cu}.json"

    out_path = inbox / fname
    tmp_path = inbox / (fname + ".tmp")

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(tmp_path, "wb") as f:
        f.write(raw)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)

    return {
        "ok": True,
        "status": "ok",
        "command_uuid": cu,
        "target_generation": int(tg),
        "command_type": ctype,
        "path": out_path.name,
        "frozen_apply_phase": _detect_trait_apply_phase_frozen(sim_root),
    }


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
