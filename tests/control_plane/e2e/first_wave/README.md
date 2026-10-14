# First-Wave E2E Suite

This suite proves first-wave operator journeys through the plane.

It is intentionally not organized by intent-sheet number. The organizing unit is an operator-visible workflow that should behave like a coherent product journey.

## Contract
Each scenario must define:

- operator job to be done
- capability under test
- durable truth sources
- manual repair eliminated
- allowed shortcuts
- forbidden shortcuts
- required assertion ladder

## Allowed
- fixture seeding before execution starts
- deterministic fake runtime bindings
- deterministic failure injection
- time control
- temp-repo execution

## Forbidden
- direct state mutation after scenario start to fake success
- bypassing scheduler admission for the claimed execution path
- fabricating closeout state
- writing projection records manually
- manual cleanup that changes the asserted result

## Required Assertion Ladder
- entry truth
- execution truth
- policy truth
- restart truth
- projection truth
- closure truth
- artifact truth
- repair truth

## Tier A: Product Proof
- `test_foundation_experiment_lifecycle_e2e_restart_safe.py`
- `test_fanout_workflow_e2e_with_partial_branch_failure.py`
- `test_intent_to_family_compiler_e2e_with_closeout_projection.py`
- `test_operator_surface_truth_projection_e2e.py`

## Tier B: Contract Proof
- `test_job_budget_and_concurrency_e2e.py`
- `test_continuation_resume_e2e.py`
- `test_campaign_execution_e2e.py`
- `test_research_policy_evidence_method_e2e.py`

## Tier C: Boundary Proof
- `test_recursive_planning_e2e.py`
- `test_artifact_boundary_and_repo_hygiene_e2e.py`

## Scenario To Intent Coverage
- `test_artifact_boundary_and_repo_hygiene_e2e.py`: direct `IS-20`
- `test_campaign_execution_e2e.py`: direct `IS-10`
- `test_continuation_resume_e2e.py`: direct `IS-06`
- `test_fanout_workflow_e2e_with_partial_branch_failure.py`: direct `IS-09`
- `test_foundation_experiment_lifecycle_e2e_restart_safe.py`: direct `IS-01`, `IS-02`; supporting `IS-07`, `IS-08`
- `test_intent_to_family_compiler_e2e_with_closeout_projection.py`: direct `IS-15`; supporting `IS-16`
- `test_job_budget_and_concurrency_e2e.py`: direct `IS-04`, `IS-05`
- `test_operator_surface_truth_projection_e2e.py`: direct `IS-19`
- `test_recursive_planning_e2e.py`: direct `IS-11`
- `test_research_policy_evidence_method_e2e.py`: direct `IS-12`, `IS-13`, `IS-14`

## Coverage Posture
- Direct first-wave scenario coverage currently exists for `IS-01`, `IS-02`, `IS-04`, `IS-05`, `IS-06`, `IS-09`, `IS-10`, `IS-11`, `IS-12`, `IS-13`, `IS-14`, `IS-15`, `IS-19`, and `IS-20`.
- Weaker or indirect coverage currently applies to `IS-03`, `IS-07`, `IS-08`, `IS-16`, `IS-17`, `IS-18`, and `IS-21`.

## Why Some Sheets Are Weaker
- `IS-03` is foundational taxonomy substrate and is exercised through many scenarios, but the suite does not yet contain a dedicated operator journey whose main proof target is job-shape law itself.
- `IS-07` and `IS-08` are supporting experiment substrate. The first-wave scenarios reuse that substrate, but they are not yet centered on scaffold generation or action-policy behavior as the primary operator-visible claim.
- `IS-16`, `IS-17`, and `IS-18` are later execution-quality and automation layers. The current suite touches them indirectly through compiled family behavior and validation posture, but it does not yet prove compiled-work-order rollout, specialized verifier contracts, or PIS-improvement automation as standalone product journeys.
- `IS-21` is intentionally weaker because repo-scale and monorepo strategy proof is optional in the first-wave lane and has not yet been promoted into its own operator-facing scenario.
