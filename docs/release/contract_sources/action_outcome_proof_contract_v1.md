# Action Outcome Proof Contract v1

Terminal outcomes:
- `success_deterministic`
- `success_after_deterministic_recovery`
- `success_after_codex_fallback`
- `partial_success`
- `proof_gap_present`
- `blocked_requires_review`
- `failed_with_known_gap`
- `failed_unknown_class`

Strict PASS rule:
- no `success_*` outcome is valid unless validation commands were executed and recorded
- required evidence artifacts must be present
- verifier-compatible verdict must be explicit `PASS`
- unresolved proof gaps automatically downgrade the result

Codex fallback never self-certifies success. It must satisfy the same PASS contract as deterministic execution.
