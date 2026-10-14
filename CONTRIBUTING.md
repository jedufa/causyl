# Contributing

Start with `python -m tools.release.demo` and the supported checks described in the
README. Explain a concrete problem, include a reproducer and add a focused test.
For state/recovery changes, include a failing negative control and compare durable
records with the independently observed outcome.

Use fictional data. Do not submit credentials, personal task history, model output
containing private information, datasets without redistribution rights, or generated
runtime artifacts. Retain third-party notices. Contributions to first-party code
are offered under the repository's MIT license.

Operator service, Android, external model and deployment changes need separate
validation. Passing the offline source-preview suite does not verify those paths.

The source manifest identifies the frozen release candidate. For a reviewed source
change, the release maintainer regenerates its allowlist export and manifest before
promoting the next snapshot; do not hand-edit hashes to conceal unexpected changes.
