# Capturing and testing a private environment snapshot

The source preview includes the environment-cloning scripts and generic package
profiles. It deliberately includes **no captured manifests**. The offline demo
and selected Python tests do not require a workstation snapshot.

A snapshot contains machine identity, installed-package versions, APT configuration
and potentially sensitive repository URLs or keyring material. Treat the entire
snapshot and verification report as private. Do not add them to source archives,
Git commits, screenshots or public issue attachments. The export policy rejects
`tools/env_clone/manifests/` even if it is accidentally added to the allowlist.

## Capture a new snapshot locally

Run this from the Causyl source root on the machine whose environment you intend
to inspect. Choose a new directory outside the repository; do not reuse an old
operator's snapshot or scrub one into a public fixture.

```bash
umask 077
CAUSYL_SNAPSHOT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/causyl-env-snapshot.XXXXXXXX")"
bash tools/env_clone/scripts/snapshot.sh --manifests-dir "$CAUSYL_SNAPSHOT_DIR"
```

The command captures local inventory. It does not install packages or start
Causyl agents. Review every generated file locally, including `WARNINGS.txt`,
`system_identity.txt`, APT sources and copied keyrings, before any private transfer.
An absence of warnings is not an assurance that the inventory is safe to publish.
The scripts support `--manifests-dir`; their legacy default is the local
`tools/env_clone/manifests/` directory, which the public checkout ignores.

## Test restoration in a disposable Ubuntu VM

These optional tools are not required to install or try the source preview.
Bootstrap can install system packages and alter trust configuration. Use a
separate disposable VM, not a productive workstation. Transfer the reviewed
snapshot privately and set `CAUSYL_SNAPSHOT_DIR` to its destination in that VM.
From the source root, first inspect the proposed package operations:

```bash
bash tools/env_clone/scripts/bootstrap.sh \
  --manifests-dir "$CAUSYL_SNAPSHOT_DIR" --profile server_minimal --dry-run
```

The generic `server_minimal` profile is tracked source and needs no historical
package inventory. After reviewing the dry run, an explicitly chosen restoration
inside the disposable VM can omit `--dry-run`. Do not enable APT-source overwrite,
embedded-key trust, or automatic trust options without separately reviewing those
changes. Runtime deployment is outside the verified source-preview scope.

Compare that VM against your own captured inventory:

```bash
bash tools/env_clone/scripts/verify.sh --manifests-dir "$CAUSYL_SNAPSHOT_DIR"
```

Verification prints machine details and writes `verify_report.txt` into the same
private directory. Add `--strict` to fail on missing critical tools; normal kernel,
driver and package differences can still require review. A successful script exit
is not full environment equivalence or production qualification.

`deploy_easy.sh` captures a fresh source snapshot by default. Its optional
`--skip-snapshot` mode requires a private snapshot supplied locally by the operator;
there is no bundled fallback in the public preview. Do not use deployment or
bootstrap commands as release tests, and do not run real agents for this workflow.
