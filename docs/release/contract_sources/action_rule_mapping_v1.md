# Action Rule Mapping v1

The phase-1 action mapper is the only allowed request-to-action classifier.

Rules:
- one versioned mapping layer
- deterministic input to output mapping
- no hidden external state
- explicit priority order
- ties must be surfaced as conflicts, not silently resolved by code order

Input schema:
- `request_text`
- `artifact_presence_flags`
- `target_identifiers`
- `authority_hints`
- `risk_hints`

Output schema:
- `action_id`
- `confidence_bucket`
- `required_evidence_gatherers`
- `fallback_eligibility_class`
- `unsupported_reason`
- `matched_rule_id`
