# CLI Args Regression Test

This folder contains a regression test that verifies **CLI arguments do not disappear** by accident.

## What it covers

### 1) CLI *availability* (superset check)
The verifier runs each snapshot-listed CLI as:

- `python -m <module> --help`

Then it extracts option strings and asserts:

> **Current options** ⊇ **Snapshot options**

So it **fails** when an expected option is missing (deleted, renamed, parser not wired, wrong entrypoint, etc.).

### 2) Multiple layers are allowed
Because the snapshot lists **modules**, you can include:
- manifest/wrapper layer entrypoints (`tools.run_manifest`, `tools.run_job`, etc.)
- engine-level entrypoints (whatever module is the "real" engine CLI)
- helper CLIs (registry expansion, verification tools, etc.)

If it can print `--help`, it can be guarded.

## What it does NOT cover

### 1) Forwarding correctness
This does **not** prove that a top-level flag is properly forwarded into a deeper layer.

Example failure this test will NOT catch:
- `tools.run_manifest --log_level DEBUG` still exists,
  but the value never reaches the wrapper/engine due to a broken argv build.

Forwarding requires a **separate** contract test:
- an engine `--dump-argv-json` mode, or
- a wrapper artifact that records the exact command invoked.

### 2) Behavioral semantics
This does not validate that options still *mean the same thing*.
It only validates they still exist.

### 3) Defaults/types/choices
It does not check default values, types, or `choices=...`.
(You *can* extend it to do that, but it will get brittle fast.)

## Files

- `cli_args_snapshot_v1.json`
  - The committed expected option sets.
- `verify_cli_args.py`
  - The executable verifier.

## Usage

Run verification:

```bash
python -m tools.cli_regression.verify_cli_args
```

Update snapshot (intentional changes only):

```bash
python -m tools.cli_regression.verify_cli_args --update-snapshot
```

## When to update the snapshot

Only when:
- you intentionally change a CLI surface, and
- you reviewed/accepted the impact on automation and docs.

If you update the snapshot every time the test fails, congratulations: you turned your alarm into a decorative LED.
