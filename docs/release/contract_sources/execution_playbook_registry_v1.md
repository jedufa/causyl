# Execution Playbook Registry v1

Each phase-1 action has one execution playbook.

Every playbook must declare:
- the action it serves
- required capability ids
- the execution ladder
- minimal validation expectations
- fallback eligibility class

Playbooks must not act like a general planner. They are bounded execution recipes for the small phase-1 action set.
