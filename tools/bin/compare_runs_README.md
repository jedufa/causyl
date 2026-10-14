# compare_runs.sh (Strict Determinism Aware)

This version additionally verifies evidence that strict determinism mode was active.

It scans both run trees for:
- DEBUG_DETERMINISM references
- Engine log mentions of determinism/strict mode

The results are written to:

notes/determinism_flag_check.txt

If both runs do not clearly indicate determinism mode was active,
the comparison should not be considered audit-grade compliant.
