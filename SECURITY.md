# Security boundary

Causyl can run commands, delegate to coding agents, edit repositories and inspect
local files. Treat the experimental operator runtime as authority over its OS user.
This source preview is intended for local development, not Internet exposure or
multi-tenant use. The local OS account and configured operator are trusted.

The normal server entrypoint refuses startup unless explicitly enabled. Public
binding is rejected, automatic scheduling defaults off, and unrestricted coding
agent execution defaults off. A shared gate runs before GET and POST dispatch:

- Host must name this loopback listener and port. Forwarding headers are rejected.
- Browser Origin must match exactly; cross-origin requests are rejected even when
  carrying a valid operator token.
- Operator APIs, static assets, documents and mutations require an operator token
  or session. Only the login page and minimal health response are public.
- Browser login creates a one-hour, bounded, process-local HttpOnly/SameSite session.
  Logout, expiry and token rotation invalidate it. Ordinary operator URLs cannot
  carry credentials. CLI clients use `X-Admin-Token`.
- Assistant routes retain bearer, operation, scope, replay and revocation checks.
  An operator token does not bypass those checks. Scoped browser-session links
  exchange into a cookie and recheck parent-token validity on every request.
- Responses forbid foreign framing and referrers; access logs omit query strings.

Real loopback tests exercise rejected reads/mutations, hostile origins, Host
spoofing, token confusion, expiry, logout and successful writes to disposable files.
This is a tested local authentication boundary, not an Internet deployment or a
comprehensive security certification. Cookies are scoped to a host, not a port;
other local services and the OS account must be trusted. Reverse-proxy and remote
ingress are deliberately unsupported by this source candidate.

See [local runtime instructions](docs/release/LOCAL_RUNTIME.md). Older clients using
operator query tokens must migrate to headers or browser login.

Use the offline demonstration for first evaluation. Do not supply real credentials
or run provider-backed tests during that evaluation. Optional adapters can invoke
external services and incur charges when explicitly configured.

Do not include secrets, real operator records or private logs in a public issue.
No private security-reporting channel is established yet; publication should enable
GitHub private vulnerability reporting before accepting sensitive reports.
