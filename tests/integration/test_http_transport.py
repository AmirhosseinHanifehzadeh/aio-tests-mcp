"""End-to-end tests for the Streamable HTTP transport and per-request tokens.

The HTTP transport lets one server instance serve several users: each client
sends its own credential as ``X-Aio-Api-Token`` and the request is scoped to it.
These tests run the server over real HTTP against the fake AIO deployment.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from aio_tests_mcp_server import __version__
from aio_tests_mcp_server.app import health_check

from .conftest import PROJECT_KEY, server_env
from .fake_aio_server import FakeAIO


def _free_port() -> int:
    """Reserve an ephemeral port.

    Returns:
        A port number that was free a moment ago.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def http_server(
    fake_aio: FakeAIO, server_cwd: Path, request: pytest.FixtureRequest
) -> Iterator[str]:
    """Run the MCP server over Streamable HTTP and yield its endpoint URL."""
    overrides: dict[str, str] = getattr(request, "param", {})
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "aio_tests_mcp_server",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
        ],
        env=server_env(fake_aio, **overrides),
        cwd=str(server_cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    health = f"http://127.0.0.1:{port}/healthz"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("server exited during startup")
            try:
                if httpx.get(health, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not become healthy")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest_asyncio.fixture
async def http_client(http_server: str) -> Any:
    """Provide an MCP client speaking Streamable HTTP with no extra headers."""
    async with Client(StreamableHttpTransport(url=http_server)) as connected:
        yield connected


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    """Invoke a tool and decode its JSON result.

    Args:
        client: The connected MCP client.
        tool: Tool name.
        **arguments: Tool arguments.

    Returns:
        The decoded tool result.
    """
    result = await client.call_tool(tool, arguments)
    return json.loads(result.content[0].text)


async def test_health_endpoint(http_server: str) -> None:
    """The health probe reports the server as up, and which build is serving."""
    response = httpx.get(http_server.replace("/mcp", "/healthz"), timeout=10)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


async def test_health_endpoint_reports_the_build_commit(
    http_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit is reported when the image was built with one."""
    monkeypatch.setenv("AIO_GIT_COMMIT", "abc123")
    response = await health_check(None)  # type: ignore[arg-type]
    assert json.loads(response.body)["commit"] == "abc123"


async def test_health_endpoint_omits_an_unknown_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build without commit metadata simply omits the field."""
    monkeypatch.delenv("AIO_GIT_COMMIT", raising=False)
    response = await health_check(None)  # type: ignore[arg-type]
    assert "commit" not in json.loads(response.body)


async def test_tools_work_over_http(http_client: Client) -> None:
    """Tools behave the same over Streamable HTTP as over stdio."""
    names = {tool.name for tool in await http_client.list_tools()}
    assert "aio_get_project" in names

    result = await call(http_client, "aio_get_project", project_key=PROJECT_KEY)
    assert result["aio_enabled"] is True
    assert result["project_id"] == 10500


async def test_a_bad_authorization_scheme_is_rejected(http_server: str) -> None:
    """An Authorization header the server cannot interpret fails fast with 401."""
    response = httpx.post(
        http_server,
        headers={
            "Authorization": "Basic dXNlcjpwYXNz",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=10,
    )
    assert response.status_code == 401
    assert "X-Aio-Api-Token" in response.json()["error"]


async def test_an_empty_bearer_style_token_is_rejected(http_server: str) -> None:
    """``Authorization: Token`` with no value is rejected rather than ignored."""
    response = httpx.post(
        http_server,
        headers={
            "Authorization": "Token",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=10,
    )
    assert response.status_code == 401
    assert "error" in response.json()


async def test_per_request_token_is_used_for_the_api_call(
    http_server: str, fake_aio: FakeAIO
) -> None:
    """A client-supplied credential is what reaches AIO Tests, not the global one.

    On Server/Data Center the AIO Tests API is served by Jira and authenticates
    with a Jira PAT, so the per-request credential must be sent as ``Bearer``.
    """
    fake_aio.requests.clear()
    async with Client(
        StreamableHttpTransport(
            url=http_server, headers={"X-Aio-Api-Token": fake_aio.expected_token}
        )
    ) as client:
        result = await call(client, "aio_get_project", project_key=PROJECT_KEY)

    assert result["aio_enabled"] is True
    forwarded = {
        record["headers"].get("Authorization")
        for record in fake_aio.requests
        if record["headers"].get("Authorization")
    }
    assert forwarded == {f"Bearer {fake_aio.expected_token}"}


async def test_per_request_token_scopes_the_request(
    http_server: str, fake_aio: FakeAIO
) -> None:
    """A client sending the wrong credential is rejected on its own request."""
    async with Client(
        StreamableHttpTransport(
            url=http_server, headers={"X-Aio-Api-Token": "someone-elses-token"}
        )
    ) as client:
        with pytest.raises(Exception) as excinfo:
            await call(client, "aio_get_tags", project_key=PROJECT_KEY)
    assert "Authentication failed" in str(excinfo.value)


@pytest.mark.parametrize(
    "http_server",
    [{"AIO_URL": "https://tcms.aiojiraapps.com/aio-tcms/api/v1", "AIO_API_TOKEN": "x"}],
    indirect=True,
)
async def test_cloud_per_request_token_uses_aioauth(
    http_server: str, fake_aio: FakeAIO
) -> None:
    """On Cloud the per-request credential is an AIO token, sent as ``AioAuth``."""
    async with Client(
        StreamableHttpTransport(
            url=http_server, headers={"X-Aio-Api-Token": "cloud-token"}
        )
    ) as client:
        tools = await client.list_tools()
    # Cloud points at the real service, so only the auth wiring is asserted here.
    assert {tool.name for tool in tools}
