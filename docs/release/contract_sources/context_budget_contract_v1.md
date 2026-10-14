# Context Budget Contract v1

Maximum context budget: `20000` tokens.

Required include sections:
- `current_action_or_playbook`
- `current_objective`

Forbidden references include:
- full transcript
- chat transcript
- unrelated ledger items
- legacy replay context

Budget overflow fails closed in this phase slice.
