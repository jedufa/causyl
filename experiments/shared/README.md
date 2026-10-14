# Shared Experiment Helpers

This package holds helper code extracted out of individual experiment projects
when the helper is generic enough to be reused across more than one project.

Current extracted helper:
- `experiment_scaffold.py`
  - deterministic experiment tree/layout planning
  - scaffold manifest/version marker generation
  - starter contract, runner, profile, and docs stub emission
  - scaffold discovery registration and artifact-boundary-safe starter files
  - dry-run and collision-safe scaffold writes
- `experiment_registry.py`
  - scaffold-owned discovery registry for registered experiments
  - list/resolve helpers for scaffolded project discovery
- `experiment_scaffold_projection.py`
  - centralized placeholder completion rules
  - starter projection for context, resource, analysis, and external data root policy
- `runner_utils.py`
  - shared import-path bootstrapping support
  - shared canonical registry resolution for project-local `_runner/` adapters
- `run_job.py`
  - shared single-job wrapper runtime
- `manifest_runner.py`
  - shared local batch executor for explicit job manifests

Additional extracted helpers:
- `experiment_records/`
  - canonical JSON
  - deterministic hashing
  - deterministic artifact manifest construction
- `job_manifests/`
  - project-neutral manifest file I/O
  - deterministic job-spec construction
  - manifest append/dedupe operations
  - shared in-memory job result types
  - neutral batch-run contract for explicit job lists
  - shared wrapper-execution and progress helpers used by the batch executor
- `checkpoint_branching.py`
  - parent-preserving checkpoint branching
  - event-log and WAL snapshot reconstruction by hash
  - branch lineage metadata writing
- `determinism/`
  - static RNG-surface scanning with allowlist lock enforcement
  - resume-window determinism fingerprint verification
- `gpu.py`
  - GPU discovery via `nvidia-smi`
  - request normalization for `cpu|auto|cuda:...`
  - deterministic slot-to-device assignment
  - device metadata enrichment for run provenance

- `experiment_scaffold_seams.py`
  - contract/scaffold/discovery seam verification
  - end-to-end bootstrap walkthrough assembly
  - migration-note/document linkage for the experiment-definition foundation
