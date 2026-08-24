# AIO Tests MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)

An open-source [Model Context Protocol](https://modelcontextprotocol.io) server for
**[AIO Tests](https://www.aiotests.com/)**, the test management app for Jira.

It lets Claude, Cursor, VS Code, Windsurf and any other MCP client work with your real
test data: search and read test cases, inspect a project's field schema, browse the folder
tree, and create or update cases — Classic *and* BDD — without leaving the conversation.

Runs on **Jira Cloud and Server/Data Center**, self-hosted, with your credentials staying
on your machine.

> **Why this exists.** The official AIO Tests MCP server is a closed-source, remotely
> hosted service with a tenant-specific URL and Cloud-only support. This is a
> self-hostable alternative you can read, audit, fork and run in your own network —
> including on Jira Server/Data Center.

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
      "args": ["aio-tests-mcp"],
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
      "args": ["aio-tests-mcp"],
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
claude mcp add aio-tests --env AIO_API_TOKEN=your_token -- uvx aio-tests-mcp
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
uvx aio-tests-mcp --help
```

### Read-only mode

Point an AI at a production Jira and you probably want it looking, not touching:

```bash
uvx aio-tests-mcp --read-only
```

Write tools are then hidden from the tool list entirely, so the model never sees them.

---

## HTTP transport

For shared or containerised deployments, run it over Streamable HTTP instead of stdio:

```bash
uvx aio-tests-mcp --transport streamable-http --port 8000
```

The server exposes `/mcp` and a `/healthz` endpoint for Kubernetes probes.

### Multi-tenant use

One server instance can serve several users. Each client sends its own AIO Tests token
per request, and the request is scoped to that tenant:

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

`Authorization: Token <token>` works as an equivalent to the `X-Aio-Api-Token` header.

---

## Running from source

```bash
git clone https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp.git
cd aio-tests-mcp
uv sync
cp .env.example .env   # then fill it in
uv run aio-tests-mcp
```

Run the tests:

```bash
uv run pytest
```

---

## Limitations

- **Folder rename, move and delete are not implemented.** The AIO Tests public API
  exposes no endpoint for them.
- **Test cycles and test runs are not covered yet.** The current tool set is scoped to
  test case management — cases, folders, tags and project schema. Contributions welcome.

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
