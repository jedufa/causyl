# Jeff MVP

Implementation root for the `R8P3` Jeff local coding-agent contract.

This package owns:
- persistent sessions under `infra/agents/jeff/sessions/`
- explicit staged execution via LangGraph
- approval-gated patch apply and focused checks
- checkpoint + undo for applied edits
- resume support for prior sessions

The CLI entrypoint remains `infra/bin/jeff`, which is only a thin wrapper.
