# composed_playbook_registry_v1

Defines the concrete Phase 2 playbooks:
- build_verify_loop_v1
- status_reconcile_v1
- patch_apply_chain_v1

Rules:
- verification owner is always scheduler_verifier
- playbooks reference graph contracts rather than embedding ad hoc execution logic
- terminal truth comes from the final verifier gate, not intermediate steps
