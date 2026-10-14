# Execution Routing v1

Deterministic routing rule:
- deterministic families use the pipeline/controller path first
- analysis-heavy families may route to Codex first
- controller-only actions remain controller-only

Single verification owner:
- scheduler/verifier loop owns final PASS/FAIL and proof
- pipeline/playbooks may emit candidate evidence but not final truth

Fallback policy is explicit per family and must not be inferred ad hoc.
