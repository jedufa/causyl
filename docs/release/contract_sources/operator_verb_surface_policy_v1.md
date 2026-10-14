# operator_verb_surface_policy_v1

Compact operator-verb exposure policy for the plane.

The public surface should expose a small intent-first verb set while preserving
visibility into proof, uncertainty, blocked state, and safe next actions across
mobile, CLI, web, and assistant surfaces.

Default implementation contract:

- `implement <UID>` is recursive by default.
- If the target is an Intent Sheet, the plane should derive or reuse one parent PIS.
- Decomposition should prefer dependency order and cap new child PIS creation at `9` unless explicitly overridden.
- Execution should start immediately unless explicitly planned only.
- The default loop name is `build-verify-repair`.
- `1.5 iterations` is an operator synonym for the same loop.
- In scheduler/runtime terms, both names resolve to the canonical loop policy `default_conditional_repair_loop`.
- The intended meaning is one builder pass, one verifier pass, and at most one bounded repair pass before finish or honest failure.
