"""Shared GPU request, discovery, and assignment helpers for experiment projects."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class GpuInfo:
    device: str
    index: int
    name: str
    driver_version: str
    cuda_version: str


def _run_cmd(cmd: List[str], *, timeout_sec: int = 5) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return int(p.returncode), str(p.stdout or "")
    except Exception as e:
        return 1, f"<cmd_failed: {type(e).__name__}: {e}>"


def has_nvidia_smi() -> bool:
    rc, _out = _run_cmd(["nvidia-smi", "-h"], timeout_sec=2)
    return rc == 0


def list_gpus_via_nvidia_smi() -> List[GpuInfo]:
    if not has_nvidia_smi():
        return []
    rc, out = _run_cmd(
        ["nvidia-smi", "--query-gpu=index,name,driver_version,cuda_version", "--format=csv,noheader"],
        timeout_sec=5,
    )
    if rc != 0:
        return []
    gpus: List[GpuInfo] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
        except Exception:
            continue
        gpus.append(
            GpuInfo(
                device=f"cuda:{idx}",
                index=idx,
                name=parts[1],
                driver_version=parts[2],
                cuda_version=parts[3],
            )
        )
    return gpus


_OOM_PATTERNS = [
    r"CUDA out of memory",
    r"cudaErrorMemoryAllocation",
    r"cublas.*ALLOC_FAILED",
    r"out of memory",
    r"hipErrorOutOfMemory",
]


def looks_like_oom(text: str) -> bool:
    t = (text or "").lower()
    for pat in _OOM_PATTERNS:
        if re.search(pat.lower(), t):
            return True
    return False


def parse_cuda_index(device: str) -> Optional[int]:
    m = re.match(r"^cuda:(\d+)$", (device or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def enrich_run_meta(
    run_meta: Dict[str, Any],
    *,
    gpu_info: Optional[GpuInfo],
    device: str,
    packing_used: int,
    oom_suspected: bool,
) -> Dict[str, Any]:
    rm = dict(run_meta or {})
    rm.setdefault("phase3", {})
    ph3 = dict(rm.get("phase3") or {})
    ph3["device"] = str(device or "")
    ph3["packing_used"] = int(packing_used)
    ph3["oom_suspected"] = bool(oom_suspected)
    if gpu_info is not None:
        ph3["gpu_name"] = str(gpu_info.name)
        ph3["driver_version"] = str(gpu_info.driver_version)
        ph3["cuda_runtime"] = str(gpu_info.cuda_version)
    rm["phase3"] = ph3
    return rm


def resolve_gpu_execution_plan(
    *,
    raw_gpu_request: str,
    sims_compute_unit: int,
    legacy_used: bool,
    legacy_packing_devices_raw: str,
    legacy_sims_per_gpu: int,
    legacy_allow_3_per_gpu: bool,
    legacy_concurrency: int,
    job_resource_devices: Sequence[str],
) -> Dict[str, Any]:
    packing_devices: List[str] = []
    if legacy_used:
        if str(legacy_packing_devices_raw or "").strip():
            packing_devices = [d.strip() for d in str(legacy_packing_devices_raw).split(",") if d.strip()]
        else:
            for d in job_resource_devices:
                if d.startswith("cuda:") and d not in packing_devices:
                    packing_devices.append(d)
        if not packing_devices:
            packing_devices = [g.device for g in list_gpus_via_nvidia_smi()]
        sims_per_gpu = int(legacy_sims_per_gpu or 2)
        if sims_per_gpu < 1:
            sims_per_gpu = 1
        if sims_per_gpu > 2 and not bool(legacy_allow_3_per_gpu):
            sims_per_gpu = 2
        if sims_per_gpu > 3:
            sims_per_gpu = 3
        if packing_devices:
            gpus_to_use = int(legacy_concurrency or 1)
            if gpus_to_use < 1:
                gpus_to_use = 1
            packing_devices = packing_devices[: min(gpus_to_use, len(packing_devices))]
            conc = len(packing_devices) * int(sims_per_gpu)
        else:
            conc = max(1, int(legacy_concurrency or 1))
        return {
            "packing_devices": packing_devices,
            "concurrency": int(conc),
            "packing_used_per_device": int(sims_per_gpu),
            "mode": "gpu_packing" if bool(packing_devices) else "cpu_only",
            "semantics": "legacy",
            "gpu_request": str(legacy_packing_devices_raw or "").strip() or "(auto)",
        }

    v = str(raw_gpu_request or "").strip().lower()
    if not v:
        v = "cuda:0"
    if v in ("cpu", "none"):
        packing_devices = []
    elif v == "auto":
        packing_devices = [g.device for g in list_gpus_via_nvidia_smi()]
    else:
        packing_devices = [d.strip() for d in str(raw_gpu_request).split(",") if d.strip()]
        bad = [d for d in packing_devices if not d.startswith("cuda:")]
        if bad:
            raise RuntimeError(
                "RUN_MANIFEST_INVALID_ARG: --gpus_available_for_sims must be 'cpu', 'auto', or a comma-separated "
                f"cuda device list like 'cuda:0,cuda:1' (bad entries: {bad})"
            )
    scu = int(sims_compute_unit or 2)
    if scu < 1:
        scu = 1
    if scu > 3:
        scu = 3
    conc = len(packing_devices) * int(scu) if packing_devices else int(scu)
    return {
        "packing_devices": packing_devices,
        "concurrency": int(conc),
        "packing_used_per_device": int(scu),
        "mode": "gpu_packing" if bool(packing_devices) else "cpu_only",
        "semantics": "v2",
        "gpu_request": str(raw_gpu_request or v),
    }


def build_slot_devices(*, packing_devices: Sequence[str], packing_used_per_device: int, concurrency: int) -> List[str]:
    if packing_devices:
        slot_devices: List[str] = []
        for d in packing_devices:
            for _ in range(int(packing_used_per_device)):
                slot_devices.append(str(d))
        return slot_devices
    return [""] * int(concurrency)
