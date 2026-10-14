# Artifact State Propagation v2

Phase 2 propagates explicit artifacts only.

Required properties:
- producer step
- schema/version id
- timestamp
- validity scope

Invalidations:
- upstream changes invalidate downstream derived artifacts
- failed baseline assumptions invalidate resume
