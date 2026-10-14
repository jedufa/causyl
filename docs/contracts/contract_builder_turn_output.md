# Builder Turn Output Contract

Status: active
Canonical role: builder turn section, scope-fence, and result-packet reference

This contract is the authoritative v1 reference for builder-output structure in
compiled and scheduled build flows.

## Required Sections

Builder outputs may include additional prose, but the following canonical
sections are the only section names downstream tooling may rely on:

- `Goal`
- `Done`
- `Changed files`
- `Validation Commands Run`
- `Assumptions Made`
- `Residual Risks / Gaps`
- `Next Owner`
- `Continuation Point`
- `Notes`

Requiredness is explicit:

- `Changed files` is required for bounded repo-change claims
- `Validation Commands Run` is required for build turns and any turn that
  presents validation evidence
- additional sections become required only when the job contract or compiled IR
  declares them explicitly

Semantically similar prose is not a substitute for a required section. Downstream
validation must fail closed when a required section is missing.

## Scope Fence

The builder output is a bounded execution receipt, not a self-certifying
success story.

Required scope-fence posture:

- `Changed files` is the authoritative mutation inventory for the turn
- summary sections such as `Done` must not claim additional mutated files that
  are absent from `Changed files`
- mutation claims outside the allowed repo scope are contract violations
- claim strength must stay bounded to the validation commands actually listed

## Result Packet

The canonical parseable builder result packet is `builder_result_packet_v1`
with these stable fields:

- `schema_version`
- `required_sections`
- `present_sections`
- `missing_sections`
- `changed_files`
- `validation_commands`
- `assumptions`
- `residual_risks`
- `next_owner`
- `continuation_point`
- `scope_fence`
- `claim_strength`
- `contract_violations`

The packet is additive metadata. It summarizes builder structure and boundary
claims; it does not replace raw model output or proof artifacts as truth owners.

## Fail-Closed Violations

These are contract violations, not style differences:

- missing required `Changed files`
- missing required `Validation Commands Run`
- missing any other explicitly required builder section
- summary mutation claims that exceed the authoritative `Changed files` block
- changed-file entries outside the declared allowed-file scope
