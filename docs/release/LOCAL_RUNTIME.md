# Local operator runtime

The plane is a privileged, single-operator application. Use a disposable checkout
for first evaluation. The offline demo needs no service or credentials.

For an explicitly enabled local service, generate a token into your shell
environment, then start the normal entrypoint:

```bash
export EIEL_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m infra.webui.server --enable-operator-runtime --host 127.0.0.1 --port 8787
```

Retrieve that token privately from your shell environment and enter it at
`http://127.0.0.1:8787/login`. Do not paste it into URLs, issues or logs. Browser
sessions expire after one hour or server restart. `POST /api/auth/logout` clears
the browser session. CLI clients send the token as `X-Admin-Token` instead.

The `EIEL_ADMIN_TOKEN` name is retained for existing Causyl integrations; it does
not select or import a simulation engine. Prefer the environment variable over
`--admin_token`, which exposes the token in process arguments.

Only login and minimal `/api/health` responses are public. Other operator routes
require authentication. Browser cross-origin requests, non-loopback Host names
and proxy-forwarded requests are rejected. Assistant credentials remain separately
scoped and do not inherit operator login.

Automatic scheduling and unrestricted agent execution remain off by default.
Starting the optional full runtime can initialize local workers and integration
state. Provider-backed execution and deployment need separate configuration.
Release tests start isolated HTTP fixtures without background workers or provider
calls. See [SECURITY.md](../../SECURITY.md).
