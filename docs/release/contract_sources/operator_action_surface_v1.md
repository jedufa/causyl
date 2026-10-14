# operator_action_surface_v1

Decision-first operator surface contract for the plane.

The canonical payload has three UX layers:
- `executive_surface`
- `explanation`
- `truth / evidence`

The executive surface is always visible and contains exactly these signals in
this order:
1. `STATE`
2. `NEXT ACTION`
3. `CONFIDENCE`
4. `SHORT REASON` (optional)

Allowed executive states:
- `PASS`
- `BLOCKED`
- `DEGRADED`
- `RUNNING`
- `REVIEW_REQUIRED`

Allowed executive confidence values:
- `High`
- `Medium`
- `Low`
- `Unknown`

Recommendation hierarchy rules:
- exactly one primary recommendation
- optional lower-ranked alternatives
- if no safe approved step exists, primary becomes `Manual review required`

Truth and uncertainty remain available through the explanation, uncertainty,
Codex transparency, and evidence-reference sections. The legacy surface fields
remain present for compatibility, but the decision-first hierarchy is the
canonical rendering order.
