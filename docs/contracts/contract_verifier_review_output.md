# Verifier Review Output Contract

Status: active
Canonical role: verifier verdict, findings, and validation-review reference

This contract is the authoritative v1 reference for verifier-output structure in
strict compiled build/verify flows.

## Canonical Verdict Law

Allowed verdict tokens under this contract are:

- `PASS`
- `FAIL`
- `UNKNOWN`
- `PASS_PENDING_ACTIONS`

These tokens are part of the contract, not stylistic suggestions. Unsupported
verdict spellings fail closed.

## Required Blocks

Verifier outputs may include additional prose after the canonical review packet,
but downstream tooling may rely only on these canonical blocks:

- `VERDICT: <token>` on the first line
- `Findings`
- `Boundary Review`
- `Proof Review`
- `Validation Review`
- `Confidence` (optional advisory-only block)

Requiredness is explicit:

- `Findings`, `Boundary Review`, `Proof Review`, and `Validation Review` are
  always required when this contract is active
- `Confidence` is optional and advisory only
- `PASS_PENDING_ACTIONS` additionally requires `Pending Actions` and `Stop Rule`
  companion blocks
- loop-policy blocks such as `Pending Actions`, `Stop Rule`, and
  `Terminal Blockers` may still be required by a higher-level loop contract, but
  they do not replace the canonical review blocks above

Scheduled high-impact verifier prompts may use exact compatibility aliases such
as `Findings by Severity` and `Validation Run Review`. Those aliases normalize
into the canonical packet; they do not create a second contract.

Semantically similar prose is not a substitute for a required block. Downstream
validation must fail closed when a required block is missing.

## Canonical Review Fields

The review blocks must remain structurally parseable and diffable. Required
fields:

- `Boundary Review`
  - `scope_alignment: <value>`
  - `contract_alignment: <value>`
- `Proof Review`
  - `evidence_status: <value>`
  - `pass_fail_closed_check: <value>`
- `Validation Review`
  - `commands_reviewed: <value>`
  - `outcome_support: <value>`
- `Confidence` (optional)
  - `advisory_only: <value>`

Scheduled high-impact verifier prompts may emit these exact alias fields and
still satisfy the same canonical contract:

- `Boundary Review`
  - `in_scope_only` -> `scope_alignment`
  - `sibling_overlap_found` / `boundary_notes` -> `contract_alignment`
- `Proof Review`
  - `validation_evidence_sufficient` -> `evidence_status`
  - `remaining_proof_gaps` -> `pass_fail_closed_check`
- `Validation Run Review`
  - `commands_checked` -> `commands_reviewed`
  - `results_checked` -> `outcome_support`

A PASS-equivalent verdict requires explicit proof review. Confidence language
must never satisfy proof requirements by itself.

The `Findings` block must be explicit:

- `- none` or a close compatibility alias such as `no actionable findings` is
  valid when there are genuinely no findings
- otherwise findings must name concrete negative observations, typically via
  severity rows or direct problem statements
- vague reassurance such as `looks good` or `seems fine` is not a valid
  findings block

## Review Packet

The canonical parseable verifier result packet is
`verifier_review_packet_v1` with these stable fields:

- `schema_version`
- `verdict`
- `required_sections`
- `present_sections`
- `missing_sections`
- `findings`
- `boundary_review`
- `proof_review`
- `validation_review`
- `confidence_hints`
- `pending_actions`
- `stop_rule`
- `terminal_blockers`
- `one_iteration_assessment`
- `contract_violations`

The packet is additive metadata. It summarizes verifier structure and evidence
review posture; it does not replace raw verifier output or the underlying proof.

## Fail-Closed Violations

These are contract violations, not style differences:

- unsupported verdict tokens
- missing `VERDICT:` on the first line
- missing required `Findings`, `Boundary Review`, `Proof Review`, or
  `Validation Review`
- vague `Findings` content that does not state either explicit `none` or a
  concrete negative finding
- missing required review fields inside those blocks
- PASS-equivalent verdicts without explicit satisfactory proof and validation
  review fields
- `PASS_PENDING_ACTIONS` without explicit `Pending Actions` and `Stop Rule`
  companion blocks
- confidence or convergence language presented without the required proof review
