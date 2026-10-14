# Shared Job Manifests

This package defines a project-neutral batch-run contract for experiment
projects.

The shared contract is:
- a manifest is an explicit list of jobs to run
- each job has a stable `job_id`, `module`, and structured `args`
- planning is deterministic and side-effect free
- execution can record per-job `job_spec.json` and batch-level reports

Core shared pieces:
- `io.py`
  - manifest load
  - output-root resolution
  - UTC timestamp helper
- `job_spec.py`
  - deterministic CLI rendering
  - stable `job_spec.json` construction
- `ops.py`
  - manifest YAML/JSON load and write
  - append-with-dedupe
  - job fingerprinting
- `types.py`
  - shared in-memory job result dataclass

Neutral environment names:
- `EXPERIMENT_WRAPPER_ROOT`
- `EXPERIMENT_RUNS_DIR`
- `EXPERIMENT_OUTPUTS_DIR`

Engine-specific environment aliases are no longer read or exported.
