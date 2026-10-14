# composed_execution_graph_v1

Defines the minimal DAG-based graph contract for Phase 2 composed execution.

Rules:
- DAG only
- one terminal verifier node per graph
- no implicit state dependencies
- proof-gap continuation only when a downstream node explicitly resolves proof gaps
