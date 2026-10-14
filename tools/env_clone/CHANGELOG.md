# Changelog

## v2.2
- Added `scripts/deploy_easy.sh` plus `infra/bin/deploy_easy` for one-command repo copy + bootstrap + verification.
- Added a `/tmp` wet-run path that reuses the real orchestration locally without SSH.
- Added `--manifests-dir` support to snapshot/bootstrap/verify so deploy staging no longer needs to mutate the source repo.
- Added `--dry-run` support to bootstrap for safe local demonstrations.
- Added automatic APT source + keyring capture/restore so SSH deploys can bootstrap hosts without manual repo trust repair.
- Added deploy-time sync of `~/.bashrc` and `.config/causyl/` into the target user's home.
- Restored missing profile files and `critical_tools.txt` so verification/bootstrap contracts are no longer stale.

## v2.1
- Fixed PPA detection to include launchpadcontent.net variants.
- Added detection of embedded PGP key blocks in Deb822 .sources files (warn + locations output).
- Bootstrap now refuses to overwrite sources containing embedded keys unless ALLOW_EMBEDDED_KEYS=1.
- Added CONTINUE_ON_ERROR (default 1) and step wrappers so bootstrap continues on failures.
- Verify made more robust; designed to keep reporting unless --strict.



## v2
- Added install profiles (`profiles/server_minimal.txt`, `profiles/desktop_extra.txt`) controlled by PROFILE env var.
- Safer APT source restore: requires `OVERWRITE_APT_SOURCES=1` to overwrite target sources.
- Added key checklist and optional STRICT_KEYS guard.
- Snapshot now captures apt policy/config and dpkg architectures.
- Added critical tools list + `verify.sh --strict` mode.
