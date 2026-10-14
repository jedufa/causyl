`infra/` is the canonical home for lab-critical infrastructure.

Current infrastructure roots:
- `infra/webui`: the plane web server and operator surfaces
- `infra/controlplane`: scheduler/runtime orchestration modules
- `infra/host`: host verification and execution contracts
- `infra/mobile`: mobile build/deploy infrastructure

Compatibility note:
- historical `infra.webui`, `infra.controlplane`, `infra.host`, and `infra.mobile`
  imports remain supported through namespace shims in `tools/`
- new infrastructure work should prefer `infra/...` paths

