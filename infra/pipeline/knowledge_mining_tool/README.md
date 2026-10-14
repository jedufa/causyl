# Knowledge Mining Tool

This tool scans a downloaded repository, compares its structure and vocabulary against an existing YAML knowledge base, and emits **reviewable unified diff patches** instead of mutating your canonical files.

## Defaults

- `--repo-root`: current working directory
- `--knowledge-root`: `/home/user/causyl/infra/pipeline/knowledge`
- `--output-root`: `./knowledge_mining_output/<repo-name>`
- console logging: **INFO** by default

## What it produces

For a scanned repository, the tool writes:

- `reports/scan_summary.json`
- `reports/token_frequencies.json`
- `reports/top_directory_tokens.json`
- `reports/unknown_terms.json`
- `reports/proposal_counts.json`
- `generated/knowledge_overlay/zz_auto_mined/<repo-slug>/*.yaml`
- `diffs/*.patch`
- `summary.md`
- `logs/run.log`

The patch files create or update **overlay proposal files** under:

```text
zz_auto_mined/<repo-slug>/
```

inside your knowledge root. You can review the patches and merge the selected parts manually into your canonical YAML structure.

## Usage

### Basic

```bash
python3 knowledge_miner.py \
  --repo-root /path/to/downloaded/repo
```

### Using your explicit knowledge root and output folder

```bash
python3 knowledge_miner.py \
  --repo-root /data/repos/home-assistant-core \
  --knowledge-root /home/user/causyl/infra/pipeline/knowledge \
  --output-root /tmp/knowledge_mining/home-assistant-core
```

### Restrict to certain proposal types

```bash
python3 knowledge_miner.py \
  --repo-root /data/repos/apache-airflow \
  --only-type synonyms \
  --only-type path_patterns
```

### Dry-run style cap

```bash
python3 knowledge_miner.py \
  --repo-root /data/repos/odoo \
  --limit-files 3000 \
  --progress-every 250
```

## Proposal types

Current proposal families are deliberately conservative:

- `synonyms`
- `path_patterns`
- `virtual_buckets`
- `subsystems`

This version does **not** auto-propose task families, retrieval bias rules, or failure taxonomies.

## Notes on schema compatibility

The loader reads whatever YAML is already in your knowledge directory and builds a heuristic model of:

- known terms
- canonical terms
- explicit alias/synonym relationships
- known path patterns

Because I could not inspect your actual local repository structure from this environment, the generated patches target a **review overlay directory** rather than trying to jam guessed structure directly into your canonical files.

That gives you diff-first review without gambling on undocumented local schema details.

## Requirements

- Python 3.10+
- PyYAML

PyYAML is the only non-stdlib dependency.
