# Contributing

Thanks for your interest in improving the AIO Tests MCP server.

## Getting set up

```bash
git clone https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp.git
cd aio-tests-mcp
uv sync
uv run pre-commit install
```

## Before you open a PR

```bash
uv run pytest          # the full suite must be green
uv run ruff check .    # lint
uv run ruff format .   # format
```

## How the code is laid out

| Path | What lives there |
| --- | --- |
| `src/aio_tests_mcp_server/server.py` | The FastMCP tool definitions — one function per MCP tool |
| `src/aio_tests_mcp_server/app.py` | Server composition: lifespan, tool filtering, HTTP middleware |
| `src/aio_tests_mcp_server/client.py` | The HTTP client for the AIO Tests REST API |
| `src/aio_tests_mcp_server/config.py` | Credential and URL resolution from the environment |
| `src/aio_tests_mcp_server/{cases,folders,projects,tags}.py` | API mixins, composed into `AIOFetcher` |
| `src/aio_tests_mcp_server/models/` | Pydantic models that parse API payloads and render tool output |
| `tests/` | Unit tests — no network access, everything mocked |

## Conventions

- **Every tool is scoped to a Jira project** and takes `project_key` first.
- **Lookup fields accept names or IDs.** Resolve names against the project configuration
  so callers can use the vocabulary they see in the AIO Tests UI.
- **Tag write tools** with `tags={"aio", "write"}` and decorate them with
  `@check_write_access`, so read-only mode hides them.
- **Tests are offline.** Mock the client; do not hit a real Jira instance.
- Google-style docstrings, type annotations everywhere, 88-column lines.

## Adding a tool

1. Add the API call as a method on the relevant mixin.
2. Add a model in `models/` if the response needs shaping.
3. Add the tool function in `server.py` with the right tags and annotations.
4. Add tests for both the mixin method and the tool.
5. Add a row to the tool table in the README.

## Reporting bugs

Please include your deployment type (Cloud or Server/Data Center), the tool that failed,
and the error — with tokens redacted.
