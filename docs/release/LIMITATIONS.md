# Source candidate scope

Validated entrypoints are the offline ledger/artifact demonstration, deterministic
CLI routing, action-plane contracts and synthetic end-to-end scenarios, and the
loopback HTTP authentication boundary. The explicit release suite documents this
selection. This is engineering validation, not formal Qualification.

The 37 formerly missing definitions are tracked under `governance/contracts/`.
They were reconstructed from written contracts and existing implementations.
Original JSON bytes were unavailable; numeric stop settings are explicit new
conservative policy choices. Read their provenance before changing policy.
Definitions reject incomplete safety boundaries; runtime cleanup does not own them.

The HTTP service checks authorization and browser origins before route dispatch.
It remains opt-in, loopback-only and single-operator. Scheduling and unrestricted
agent execution default off. Tests use disposable state and synthetic agent
responses; no live provider or operational deployment has been validated.

Optional provider adapters, Android, GPU workloads, deployment and the entire
historical test suite are outside this candidate's verified boundary. Dependencies
for those components need explicit installation and independent validation.
Android wrapper binaries and image assets are omitted; no APK/build claim is made.
No Docker deployment or installable Python wheel is supplied.

The distribution excludes private operational records, books, datasets, old science
evidence, binaries, unreviewed artwork, credentials and private Git history. Some
historical tests still refer to excluded records and are development references.
Signature scans and an explicit allowlist provide bounded privacy checks; they
cannot establish the absence of every encoded secret or private fact.

Publication must use the clean candidate, never the original operational history.
