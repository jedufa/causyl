# Action Entrypoints v1

Hard entrypoints for Phase 2:
- `POST /api/action/execute`
- `python infra/bin/action_execute.py`
- scheduler dispatch event
- snapshot drift trigger

Required payload fields:
- `task_id`
- `action_family`
- `execution_mode`
- `context_bundle_id`

Ownership boundary:
- scheduler/controller owns dispatch
- execution router owns executor selection
- executor owns bounded execution only
- scheduler/verifier owns final PASS/FAIL and proof
