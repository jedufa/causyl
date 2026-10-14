# User Tool Shims

This folder provides stable, easy-to-remember entrypoints for common operator tasks.

Each shim forwards to the canonical script in `infra/bin/`.

These shims are compatibility wrappers. They are convenience entrypoints, not
the source of truth for managed operator state. The named front door for managed
work is the lab assistant web surface documented in
`docs/10_operator/workflows/Control_Surface_Front_Door.html`.

Note:
Some environments mount the repo with `noexec`. In that case, run shims via
`bash infra/user/<shim>` instead of executing directly.

## Available shims

- `infra/user/next-jobs`
  - show active, runnable, and blocked pipeline jobs
- `infra/user/schedule-job`
  - scheduler authoring/execution CLI (`list`, `show`, `run-now`, etc.)
- `infra/user/scheduler-watch`
  - live scheduler watch UI
- `infra/user/scheduler-create-job`
  - create a new scheduled job from recent ledger PIS items with defaults
- `infra/user/scheduler-delete-job`
  - delete a scheduled job, with optional history purge
- `infra/user/pis`
  - compatibility launcher for PIS-oriented ledger helpers
- `infra/user/ledger`
  - unified interactive launcher for ledger helpers covering both Patch Instruction Sheets and Intent Sheets
  - includes an `import-pis-from-downloads` option that scans `/mnt/c/home/user/Downloads` for `.md` files and imports the selected file into the ledger through the standard draft-creation flow
- `infra/user/pis-create-from-text`
  - create a draft PIS from pasted/copied text; writes explicit ledger scope fields (`project_id`, `workstream_type`, `scope_boundary`, `artifact_roots`), tries one best-effort `gpt-5.4` drafting call when `OPENAI_API_KEY` is set, then falls back to deterministic local drafting
- `infra/user/intent-sheet-create-from-text`
  - create a draft Intent Sheet from pasted/copied text; stores it as a durable ledger reference object under `governance/ledger/intent_sheets/`
- `infra/user/intent-sheet-next`
  - show the next best Intent Sheets to work on using canonical ordering metadata (`execution_priority`, wave, critical-path signal)
- `infra/user/test-lane`
  - run a named pytest lane: `regression`, `smoke`, `e2e`, `integration`, or `unitish`
- `infra/user/lab`
  - human-facing front door for the plane; turns a natural-language request like `lab "fix this patch"` into a structured action request and prints the executive surface
- `infra/user/labsh`
  - interactive shell for the plane; each entered line is handled as a `lab` request until `exit` or `quit`
- `infra/user/server-start`
  - start web server + scheduler lifecycle helpers
- `infra/user/server-stop`
  - stop server lifecycle
- `infra/user/server-status`
  - server/scheduler status
- `infra/user/server-watchdog`
  - fixed-choice watchdog control surface for activate/deactivate/restart, immediate audit/self-heal, and recent log/action review
- `infra/user/scheduler-postpone-active-job`
  - interrupt active once-job, postpone it (default 7 days), and resume with next runnable job
- `infra/user/scheduler-enable-all`
  - enable all scheduled jobs
- `infra/user/scheduler-disable-all`
  - disable all scheduled jobs
- `infra/user/scheduler-pause`
  - pause scheduler pipeline safely
- `infra/user/scheduler-resume`
  - resume scheduler pipeline
- `infra/user/scheduler-health`
  - one-shot scheduler health check
- `infra/user/git-commit-tag`
  - run commit/tag helper; explicit message words override the subject line but still keep journal-composed commit body/coverage
- `infra/user/git-review`
  - run git review helper
- `infra/user/venv-activate`
  - print shell command to activate project virtualenv

## Examples

```bash
bash infra/user/next-jobs --limit 15
bash infra/user/scheduler-create-job
bash infra/user/scheduler-delete-job nightly_cli_001 --yes
bash infra/user/scheduler-create-job --pis PIS-20260326-203500Z-R7K2
bash infra/user/ledger
bash infra/user/ledger create-intent-sheet --text "Intent anchor for a family of downstream work."
bash infra/user/pis
bash infra/user/pis   # compatibility view, PIS-only
bash infra/user/pis-create-from-text
bash infra/user/intent-sheet-create-from-text --text-file /tmp/intent.md --project-id verification_workflow
bash infra/user/intent-sheet-next --limit 10
bash infra/user/test-lane regression
bash infra/user/test-lane e2e tests/control_plane/webui/test_e2e_intake_route_persist_snapshot.py
bash infra/user/test-lane unitish tests/control_plane
bash infra/user/pis-create-from-text --text-file /tmp/idea.md --yes
bash infra/user/pis-create-from-text --no-api
bash infra/user/pis-create-from-text --project-id verification_workflow --workstream-type project --scope-boundary project-local --artifact-root /home/user/causyl/experiments/projects/verification_workflow
bash infra/user/schedule-job list --json
bash infra/user/schedule-job run-now repo_agent_r8p3_grok_20260314_1915
bash infra/user/server-status
bash infra/user/labsh
bash infra/user/scheduler-postpone-active-job --dry-run
bash infra/user/scheduler-watch --help
bash infra/user/git-review
bash infra/user/git-commit-tag --help
bash infra/user/venv-activate
```

`infra/user/pis-create-from-text` also reads these optional environment defaults:

- `PIS_PROJECT_ID`
- `PIS_WORKSTREAM_TYPE`
- `PIS_SCOPE_BOUNDARY`
- `PIS_ARTIFACT_ROOTS` (comma-separated)
