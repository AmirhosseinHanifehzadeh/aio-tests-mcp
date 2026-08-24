# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.

Report it privately through
[GitHub Security Advisories](https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp/security/advisories/new).
You should get an initial response within a few days.

## Handling credentials

- The server reads credentials from environment variables. **Never commit a `.env` file**
  or paste a token into an issue — `.env` is gitignored for this reason.
- Tokens are masked in logs, but `MCP_VERBOSE=true` produces detailed request logging.
  Review the output before sharing it.
- `AIO_SSL_VERIFY=false` disables certificate verification and exposes traffic to
  interception. Use it only against a test instance.
- Against a production Jira, run with `READ_ONLY_MODE=true` so write tools are never
  exposed to the model.
- In multi-tenant HTTP deployments, each request carries its own token via
  `X-Aio-Api-Token` and is scoped to that tenant. Terminate TLS in front of the server —
  it speaks plain HTTP.
