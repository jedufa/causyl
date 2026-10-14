# Source contract definitions

These 37 definitions are reconstructed source definitions, not recovered copies
of the missing historical `runtime/contracts` files. The original JSON assets
were absent from the checkout, Git history, and the bounded backup search.

The definitions follow the existing action-plane implementations and written
contracts retained in `docs/release/contract_sources/` (curated copies from the
original documentation archive). They preserve the
three phase-1 actions, three phase-2 families, four entrypoints, 20,000-token
budget, explicit verification owner, bounded fallback, proof-gap stops, and
proposal-only improvement flows. Executable backing is checked separately;
registry membership never grants permission to execute a tool.

Where the original numeric policy is unavailable, this revision makes new,
explicit conservative choices: stop after two retries, two stagnant iterations
with confidence gain below 0.05, expected gain below 0.01, or per-iteration cost
above 10 in the caller's declared cost units. These values are policy choices,
not recovered historical settings or scientifically calibrated thresholds.
The graph handlers do not implement proof-gap resolution, so no graph node is
declared a proof-gap resolver. Missing proof stops a chain.

Required output fields come from the constructors and documented evidence
requirements. Optional rejection details and absent candidates remain optional.
Capability commands point to existing first-party CLI implementations. Flow
registries describe existing functions; they do not schedule work or rewrite
source. The alignment map retains historical identifiers as descriptive
metadata, not as evidence that a fresh release has been qualified.

These are ordinary tracked source files. Runtime cleanup must never own them.
Changes require the action-plane contract and negative-control tests, plus the
critical regression pack. Historical runtime artifacts are not inputs to the
public source distribution.
