# Action Family Contracts v1

Concrete Phase-2 families:
- `build_verify`
- `status_reconcile`
- `patch_apply`

No new family is valid unless it is added through the versioned contract + tests path.

Each family declares:
- trigger class
- supported entrypoints
- toolchain
- output contract
- verification owner
- default executor
