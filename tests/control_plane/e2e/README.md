# FCEE Stimulus E2E Lane

This lane now targets the first intent-sheet family through realistic stimuli:

- prompt-driven scenario fixtures under `fixtures/intent_sheet/fcee/`
- seeded repo state and attached artifacts such as valid and broken patches
- real phase entrypoints and the real build/verify loop where appropriate
- durable `receipt.json`, `report.json`, `snapshot.json`, and `stimulus.json` artifacts
- truthful proof-gap, failed-verification, scope-boundary, recovery, economic-stop, and full-stack checks

Run:

```bash
pytest tests/control_plane/e2e/test_action_plane_e2e.py -q
pytest -m e2e tests/control_plane/e2e/test_action_plane_e2e.py
```


Additional top-level real-life matrix:

```bash
pytest tests/control_plane/e2e/test_action_plane_real_life_use_cases.py -q
```

Lab front-door E2E lane:

```bash
pytest tests/control_plane/e2e/test_lab_cli_real_life_use_cases.py -q
python infra/bin/run_lab_cli_e2e_suite.py
```

Fixtures for the lab lane live under `tests/control_plane/e2e/fixtures/lab_cli/`.
