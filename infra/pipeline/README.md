# tools/pipeline

Deterministic pipeline runtime for the AI-assisted code modification flow.

## MVP-1 Backbone

This package owns the disk-first control plane:

- `job_state.json`
- `artifact_registry.json`
- immutable run-scoped artifacts
- deterministic runner sequencing
- stages 1-4 intake/retrieval flow

## MVP-2 Planning + Operator Review

This snapshot extends the same backbone with:

- `script_5_context_ranker`
- `script_6_context_bundle`
- `script_7_patch_plan`
- `script_8_patch_generate`
- `script_9_artifact_validate`
- `script_10_patch_apply`
- `script_11_verify_static`
- `script_12_verify_runtime`
- `script_13_contract_verify`
- thin operator review rendering
- persisted operator decision artifacts

The review layer is intentionally thin:

- stage outputs remain the source of truth
- review rendering reads only registered artifacts
- operator decisions are persisted as artifacts and mirrored into `job_state.json`
- the runner stops at `WAITING_REVIEW` after stage 7

## Key files

- `core.py`  
  Shared deterministic runtime helpers, stage path mapping, config loading, JSON IO, subprocess wrapper.

- `artifact_registry.py`  
  Append-only artifact registration and lookup helpers.

- `job_state.py`  
  Branch-aware run manifest plus review gate state.

- `planning.py`  
  Shared planning logic for ranking, bundling, deterministic patch-plan fallback, and review rendering.

- `review_render.py` / `operator_decision.py`  
  Thin operator shell helpers that read/write artifacts through the backbone.

- `pipeline_runner.py`  
  Minimal runner that executes stages 1-13, preserves the review gate after stage 7, and routes verification outcomes into `FINALIZED`, `WAITING_REVIEW`, or `WAITING_RETRY_DECISION`.

## CLI entrypoints

- `infra/bin/pipeline_run.py`
- `infra/bin/pipeline_review.py`
- `infra/bin/pipeline_decide.py`
