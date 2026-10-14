# Action Plane Phase 1 Boundary

Phase 1 is intentionally narrow.

In-scope action families:
- `patch_ingest_repair`
- `pis_assess_proof_gap`
- `job_inspect_bounded_recovery`

First concrete implementation anchor:
- `patch_ingest_repair_v1`

Allowed decomposition style:
- rule-based intent classification
- explicit mappings
- one versioned mapping layer
- no domain-general inference engine

Allowed execution ladder:
- deterministic attempt
- deterministic recovery
- pre-escalation checklist
- bounded fallback
- verifier-bound validation

Concrete-first rule:
- registry and contract work is not complete unless at least one real action family is implemented against real executable tools

Phase-1 anti-goals:
- general planning
- domain-general decomposition
- broad multi-domain action coverage
- substrate-wide execution unification
- broad automatic surface generation

Later work may extend phase 1, but it may not redefine these constraints retroactively.
