# Tool Capability Registry v1

The tool capability registry is descriptive only. It is not runtime truth.

Each entry declares:
- `tool_id`
- `title`
- `summary`
- `kind`
- `invocation_contract`
- `argument_schema`
- `read_only`
- `mutates_state`
- `authority_class`
- `evidence_checks`
- operator and mobile eligibility flags
- `status`
- `descriptive_only`

Derivation rule:
- active entries must be generated from executable inspection or validated against a runnable invocation
- manual-only active entries are invalid
- planned entries must be marked `planned`, not surfaced as active capability

Phase-1 seed set includes executable anchors for:
- patch ingestion / bounded mutation helpers
- repo preflight and safe command execution
- scheduler and job inspection evidence sources
