# Scheduler Benchmark

This harness benchmarks the candidate scheduler against the current scheduler on
the dimensions that matter for the plane:

- operator truth
- dependency admission
- terminal retirement
- restart/continuity behavior
- closeout ownership
- implementation complexity

The benchmark is adapter-driven.

What works now:
- capture a live baseline snapshot from the current scheduler
- capture synthetic production-stress snapshots without touching ledger state
- compare v2 ownership and internal wiring contracts before production cutover
- compare two previously captured snapshot JSON files
- emit strict reports that fail closed as `pending_interface` when a required
  dimension is not wired yet
- measure code-surface complexity from configured code paths

What is intentionally left as an adapter seam:
- production cutover execution; this harness validates readiness but does not
  switch canonical ownership
- independent surface capture beyond the shared projection path for surfaces
  that are not wired to the shared bundle yet

## Usage

List scenarios:

```bash
python -m benchmark.scheduler --list-scenarios
```

Compare two snapshots:

```bash
python -m benchmark.scheduler \
  --scenario one_workflow_shadow \
  --baseline snapshot:/abs/path/legacy.json \
  --candidate snapshot:/abs/path/v2.json \
  --json
```

Capture the current scheduler as baseline and compare against a candidate
snapshot:

```bash
python -m benchmark.scheduler \
  --scenario one_workflow_shadow \
  --baseline current \
  --candidate snapshot:/abs/path/v2.json \
  --job-prefixes pis_a7v1_,pis_b8w2_ \
  --family-parent-uids PIS-20260409-194437Z-310F
```

Run the synthetic production-stress suite:

```bash
python -m benchmark.scheduler \
  --scenario production_stress_shadow \
  --baseline 'synthetic:jobs=25:topology=diamond:case=kill_active' \
  --candidate 'synthetic:jobs=25:topology=diamond:case=kill_active' \
  --baseline-code-paths infra/controlplane/scheduled_jobs.py,infra/controlplane/scheduled_jobs_parts \
  --candidate-code-paths infra/controlplane/sched \
  --report-dir governance/evidence/benchmarks/scheduler/_results/production_stress_shadow \
  --json
```

Supported synthetic stress cases:
- `none`
- `kill_active`
- `kill_before_closeout`
- `stale_claim`
- `projection_corruption`
- `dependency_contradiction`

Supported synthetic topologies:
- `chain`
- `diamond`
- `wide`

## Snapshot Contract

Snapshots are JSON files with:

- `jobs`
- `receipts`
- `compiled_graphs`
- `continuity_records`
- `closeout_records`
- `surface_projections`
- `family_records`
- `code_metrics`

Missing required artifacts do not quietly pass. They are reported as
`pending_interface` so the benchmark remains honest while the candidate
interfaces are still under construction.
