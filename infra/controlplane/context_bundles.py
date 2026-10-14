"""Context bundle registry for scheduled agent jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from infra.webui.ledger import ledger_storage


REPO_ROOT = Path(__file__).resolve().parents[2]
ITEMS_ROOT = ledger_storage.ledger_patch_instruction_sheets_root(REPO_ROOT)


LAB_CORE_V1: List[str] = [
    "governance/agents/prompts/_all_codex.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
]

LAB_ARCH_GOV_V1: List[str] = LAB_CORE_V1 + [
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_taxonomy.md",
    "governance/ledger/patch_instruction_sheets/PIS-20260312-150610Z-D821__persistent-lab-assistant-v1-operational-contract/pis.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/governance.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_scheduler_verification_directives.md",
]

LAB_DELIVERY_SAFETY_V1: List[str] = LAB_CORE_V1 + [
    "docs/90_archive/markdown_source/persistent_files_chatgpt/test_delivery_contract.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/output_redaction.md",
]

CONTEXT_BUNDLES: Dict[str, List[str]] = {
    "lab_core_v1": LAB_CORE_V1,
    "lab_arch_gov_v1": LAB_ARCH_GOV_V1,
    "lab_delivery_safety_v1": LAB_DELIVERY_SAFETY_V1,
}


def bundle_refs(bundle_id: str) -> List[str]:
    refs = list(CONTEXT_BUNDLES.get(str(bundle_id or "").strip(), []))
    out: List[str] = []
    for ref in refs:
        text = str(ref or "").strip()
        if text.startswith("governance/ledger/patch_instruction_sheets/PIS-") and text.endswith("/pis.md"):
            parts = Path(text).parts
            uid = parts[3].split("__", 1)[0] if len(parts) >= 4 else ""
            item_dir = ledger_storage.item_dir_for_uid(ITEMS_ROOT, uid)
            if item_dir is not None:
                out.append(str((item_dir / "pis.md").relative_to(REPO_ROOT)).replace("\\", "/"))
                continue
        out.append(text)
    return out


def available_bundle_ids() -> List[str]:
    return sorted(CONTEXT_BUNDLES.keys())
