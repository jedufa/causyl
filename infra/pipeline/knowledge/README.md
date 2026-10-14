# Deterministic knowledge assets

This directory is the canonical runtime knowledge pack for deterministic-first routing and retrieval.

## Canonical versus non-canonical assets

The live runtime boundary is explicit:

- `runtime_asset_manifest.yaml` lists canonical runtime files, legacy/reference files, and retired files.
- `task_families.yaml` is the only live task-family registry.
- `task_families.additions.yaml` has been retired after full merge into the canonical registry.
- `task_family_lifecycle.yaml` provides explicit family lifecycle status.
- `retrieval_virtual_buckets.yaml` defines virtual retrieval buckets such as `docs`, `reports`, `tests`, and `explicit_non_goal_surfaces` so retrieval rules do not pretend these are canonical ownership subsystems.

## Supplemental payloads

Some canonical files retain `supplemental` sections for provenance back to extracted bundle payloads.
Those sections are reference-only. Runtime should consume the canonical top-level structures and ignore supplemental payloads unless a provenance/debug workflow explicitly asks for them.

## Design-mode support

`task_profile_rules.yaml` now exposes explicit `design_modes` entries for:

- `design_in_repo`
- `greenfield_design`

The older `design_mode` entry remains only as a compatibility fallback.

## Legacy/reference assets

Files such as `pipeline_knowledge.yaml` and `retrieval_bias.yaml` are retained only as legacy reference material. They are not canonical runtime inputs.


## Framework and platform coverage

The knowledge pack now includes a reusable `modular_application_platform` domain for large framework, plugin, and integration-heavy repositories.

This domain adds canonical support for concepts such as:

- plugin / provider / hook
- serializer / session / service
- component / integration / entity
- frontend / backend

The intent is to improve retrieval and subsystem hypothesis quality across repositories such as workflow platforms, web frameworks, modular application stacks, and mixed UI/backend systems without overfitting to any single external project.
