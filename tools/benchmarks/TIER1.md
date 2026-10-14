# Tier 1 Benchmark Set

This is the first cheap benchmark pack for local small-model evaluation.

The point is not to test deep reasoning. The point is to test whether a small
model can reliably handle common lab chores without quietly damaging the repo.

Tier 1 tasks are intentionally narrow:
- exact file creation
- exact tracked-file rewrite
- simple markdown edits
- simple JSON rewrites
- simple config/text normalization

These are the tasks a cheap local model should win before it is trusted with
larger planning, architecture, or PIS drafting work.

Current pack:
- 12 specs under `tools/benchmarks/specs/tier1/`
- mixed tracked and untracked file tasks
- deterministic check commands only

Typical run:

```bash
python infra/bin/agents_benchmark_cli.py --spec tools/benchmarks/specs/tier1/create_untracked_note.json
```

Run the full starter pack:

```bash
python infra/bin/agents_benchmark_cli.py --suite tools/benchmarks/specs/tier1_manifest.json
```

Use this pack to answer a practical question:
"Which low-cost chores can a 3B-class model do cheaply and repeatably?"
