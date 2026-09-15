<!-- mcp-name: io.github.AmirhosseinHanifehzadeh/aio-tests-mcp -->

# AIO Tests MCP Server

[![CI](https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)
[![M8ven Score](https://m8ven.ai/badge/mcp/amirhosseinhanifehzadeh-aio-tests-mcp-14rsfp)](https://m8ven.ai/mcp/amirhosseinhanifehzadeh-aio-tests-mcp-14rsfp)

**An open-source [MCP server](https://modelcontextprotocol.io) for
[AIO Tests](https://www.aiotests.com/) — test management for Jira.**

Connect Claude, Claude Code, Cursor, VS Code, Windsurf or any other MCP client to your
Jira test cases. Search and read them, inspect a project's field schema, browse the folder
tree, and create or update cases — Classic *and* BDD — in plain conversation.

Works with **Jira Cloud and Jira Server / Data Center**. Self-hosted, so your credentials
and test data never leave your infrastructure.

```bash
uvx aio-tests-mcp-server
```

> **Not on PyPI yet.** The `uvx` command above goes live with the first release.
> Until then, use [Running from source](#running-from-source) — it takes two commands.

> **Why this exists.** The official AIO Tests MCP server is a closed-source, remotely
> hosted service with a tenant-specific URL and Cloud-only support. This one is
> self-hostable and auditable — you can read every line, fork it, and run it inside your
> own network. It is, as far as I know, the only AIO Tests MCP server that supports
> **Jira Server / Data Center**, and it has a **read-only mode** so you can safely point
> an AI at a production instance.

---

## Tools

Ten tools, all scoped to a Jira project.

| Tool | Access | What it does |
| --- | --- | --- |
| `aio_get_project` | read | Confirm AIO Tests is enabled for a project and get its ID |
| `aio_get_test_case_schema` | read | Fields, custom fields, required fields and allowed values |
| `aio_search_test_cases` | read | Filter by title, key, folder, status, priority, type, tag, owner, covered Jira issue, automation status/key, and creation/update dates |
| `aio_get_test_case` | read | Full case detail including every step |
| `aio_get_test_case_versions` | read | Every saved version of a case |
| `aio_create_test_case` | **write** | Create a case with Classic or BDD steps |
| `aio_update_test_case` | **write** | Update fields, steps, metadata, priority, status or tags |
| `aio_get_folder_hierarchy` | read | Folder tree (or a flat list of full paths) for test cases, cycles or sets |
| `aio_create_folder` | **write** | Create a folder, creating missing parents along a path |
| `aio_get_tags` | read | Every tag configured for a project |

**Lookup fields accept names or IDs.** You can say `"Critical"` instead of `4`, or
`/Regression/Checkout` instead of a folder ID — values are resolved against the project
configuration, so the model works in the same vocabulary you see in the AIO Tests UI.

**Updates preserve formatting.** Case updates round-trip the document with rich text
enabled, so the formatting of fields you did not touch survives, and plain-text input is
escaped to match.

---

## Quick start

### 1. Get an access token

**Jira Cloud** — in Jira, open **AIO Tests → (?) icon → API Access Token** and generate one.
The token identifies your tenant, so no URL is needed.

**Jira Server/Data Center** — the AIO Tests API is served from your Jira base URL and
reuses your Jira credentials (a Personal Access Token, or username + password).

### 2. Add it to your MCP client

<details open>
<summary><b>Claude Desktop / Claude Code</b> — Jira Cloud</summary>

```json
{
  "mcpServers": {
    "aio-tests": {
      "command": "uvx",
      "args": ["aio-tests-mcp-server"],
      "env": {
        "AIO_API_TOKEN": "your_aio_access_token"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Claude Desktop / Claude Code</b> — Jira Server / Data Center</summary>

```json
{
  "mcpServers": {
    "aio-tests": {
      "command": "uvx",
      "args": ["aio-tests-mcp-server"],
      "env": {
        "AIO_ENABLED": "true",
        "JIRA_URL": "https://jira.your-company.com",
        "JIRA_PERSONAL_TOKEN": "your_personal_access_token"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Cursor / Windsurf / VS Code</b></summary>

Same JSON as above, in the client's MCP settings file (`~/.cursor/mcp.json` for Cursor,
`.vscode/mcp.json` for VS Code). Cursor also accepts a project-local `mcp.json`.
</details>

In Claude Code you can add it in one command:

```bash
claude mcp add aio-tests --env AIO_API_TOKEN=your_token -- uvx aio-tests-mcp-server
```

### 3. Ask for something

> "Search PROJ for published test cases tagged `smoke` in `/Regression/Checkout`, and show me the steps of the first one."

> "Read PROJ-142 and write BDD test cases covering the acceptance criteria."

---

## Configuration

Every setting is an environment variable; see [`.env.example`](.env.example) for the full
list. The essentials:

| Variable | Purpose |
| --- | --- |
| `AIO_API_TOKEN` | AIO Tests access token (Cloud). Setting this alone enables the server. |
| `AIO_ENABLED` | Set to `true` to enable Server/Data Center mode. |
| `JIRA_URL` | Jira base URL (Server/DC). |
| `JIRA_PERSONAL_TOKEN` | Jira PAT (Server/DC), or use `JIRA_USERNAME` + `JIRA_API_TOKEN`. |
| `READ_ONLY_MODE` | `true` hides every write tool — recommended against production. |
| `ENABLED_TOOLS` | Comma-separated allowlist of tool names. |
| `AIO_URL` | Override the API base URL. |
| `AIO_SSL_VERIFY` | `false` to skip certificate verification (testing only). |

Proxy settings (`AIO_HTTP_PROXY`, `AIO_HTTPS_PROXY`, `AIO_SOCKS_PROXY`, `AIO_NO_PROXY`),
client certificates and custom headers are supported too.

The same options are available as CLI flags:

```bash
uvx aio-tests-mcp-server --help
```

### Read-only mode

Point an AI at a production Jira and you probably want it looking, not touching:

```bash
uvx aio-tests-mcp-server --read-only
```

Write tools are then hidden from the tool list entirely, so the model never sees them.

---

## HTTP transport

For shared or containerised deployments, run it over Streamable HTTP instead of stdio:

```bash
uvx aio-tests-mcp-server --transport streamable-http --port 8000
```

The server exposes `/mcp` and a `/healthz` endpoint for Kubernetes probes.

### Multi-tenant use

One server instance can serve several users. Each client sends its own credential per
request, and the request is scoped to that user:

```json
{
  "mcpServers": {
    "aio-tests": {
      "url": "https://aio-mcp.your-company.com/mcp",
      "headers": {
        "X-Aio-Api-Token": "the_users_own_token"
      }
    }
  }
}
```

The credential to send depends on the deployment the server points at — the same one you
would put in the environment for a single-user run:

| Deployment | `X-Aio-Api-Token` holds | Sent upstream as |
| --- | --- | --- |
| Jira Cloud | an AIO Tests access token | `Authorization: AioAuth <token>` |
| Jira Server / Data Center | a Jira Personal Access Token | `Authorization: Bearer <token>` |

`Authorization: Token <token>` works as an equivalent to the `X-Aio-Api-Token` header.

---

## Running from source

```bash
git clone https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp.git
cd aio-tests-mcp
uv sync
cp .env.example .env   # then fill it in
uv run aio-tests-mcp-server
```

Run the tests:

```bash
uv run pytest
```

The suite is offline and hermetic. Unit tests cover config resolution, the client and
every mixin; the integration tests in [`tests/integration/`](tests/integration) start the
server as a subprocess, speak the MCP protocol to it over stdio and Streamable HTTP, and
let it call a stateful fake of the AIO Tests REST API over loopback — so tool filtering,
argument validation, payload building, HTTP transport and response parsing are all
exercised for real.

### Checking a real deployment

To verify the tools against your own Jira, run the live check. It drives every tool
through the MCP protocol and prints a pass/fail line for each:

```bash
uv run python scripts/live_check.py --project PROJ --env-file .env
```

It is read-only by default. Add `--write` to also exercise `aio_create_folder`,
`aio_create_test_case` and `aio_update_test_case`; those create a timestamped folder and
one test case, and never touch data that was already there.

---

## Deploying with Docker

Every merge to `main` publishes an image to the GitHub Container Registry, tagged
`latest` and with the commit SHA:

```bash
docker pull ghcr.io/amirhosseinhanifehzadeh/aio-tests-mcp:latest
```

Pin the SHA tag in production so a rollout is explicit about what it ships.

```bash
docker run -d -p 8000:8000 -e AIO_API_TOKEN=... \
  ghcr.io/amirhosseinhanifehzadeh/aio-tests-mcp:latest
```

### Confirming which build is live

`/healthz` reports the version and the commit the image was built from, so a
deployment can be checked from outside without guessing:

```bash
curl -s http://your-host/healthz
```

```json
{"status": "ok", "version": "0.1.0", "commit": "f162688..."}
```

If `commit` does not match what you expect to have deployed, the rollout pulled a
stale image — restarting the container will not help, because the image itself is
the old build. `commit` is absent only for images built without the `GIT_COMMIT`
build argument.

---

## Limitations

- **Folder rename, move and delete are not implemented.** The AIO Tests public API
  exposes no endpoint for them.
- **Test cycles and test runs are not covered yet.** The current tool set is scoped to
  test case management — cases, folders, tags and project schema. Contributions welcome.

---

## FAQ

**Is this the official AIO Tests MCP server?**
No. AIO Tests publishes its own MCP server as a hosted service — closed source, with a
tenant-specific URL, and Cloud only. This is an independent, open-source implementation
you run yourself. It is not affiliated with or endorsed by AIO Tests or Atlassian.

**Does it work with Jira Data Center or Jira Server?**
Yes. Set `AIO_ENABLED=true` and `JIRA_URL`, and authenticate with a Jira Personal Access
Token or username and password. On Server/DC the AIO Tests API is served from your Jira
base URL, so it reuses your Jira credentials. This is the main reason to pick this server
over the alternatives — the hosted ones are Cloud-only.

**Can I use it with Claude Code, Claude Desktop, Cursor or VS Code?**
Yes — any MCP client works. See [Add it to your MCP client](#2-add-it-to-your-mcp-client)
for ready-to-paste config.

**Is it safe to point at production Jira?**
Run it with `--read-only` (or `READ_ONLY_MODE=true`). Write tools are then filtered out of
the tool list entirely, so the model never sees that creating or updating is an option.

**Where do my credentials go?**
Nowhere but your own machine and your Jira instance. The server runs locally over stdio by
default and talks straight to the AIO Tests REST API. There is no intermediary service.

**Can one server instance serve a whole team?**
Yes — run it over HTTP and have each client send its own token in the `X-Aio-Api-Token`
header. See [Multi-tenant use](#multi-tenant-use).

**Does it support test cycles, executions or attachments?**
Not yet. The current tool set covers test case management — cases, folders, tags and
project schema. Cycles and executions are the obvious next step; see
[Limitations](#limitations), and issues are welcome.

---

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The test
suite covers the client, config resolution, every mixin and every tool; please keep it
green and add cases for new behaviour.

## License

[MIT](LICENSE).

Portions of this project are derived from
[mcp-atlassian](https://github.com/sooperset/mcp-atlassian) by
[@sooperset](https://github.com/sooperset), also MIT-licensed. Thanks for the
foundation.

This project is not affiliated with or endorsed by AIO Tests or Atlassian.
