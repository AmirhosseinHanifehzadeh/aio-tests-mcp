"""Fixtures wiring the MCP server to the fake AIO Tests deployment."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .fake_aio_server import KNOWN_PROJECT, FakeAIO

PROJECT_KEY = KNOWN_PROJECT
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


@pytest.fixture(scope="session")
def fake_aio() -> Iterator[FakeAIO]:
    """Run a fake AIO Tests deployment for the whole test session."""
    fake = FakeAIO()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture(scope="session")
def server_cwd(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Give the server subprocess a directory with no ``.env`` in it.

    The server loads a ``.env`` from its working directory, so running it from
    the repository root would pick up the developer's own credentials.
    """
    return tmp_path_factory.mktemp("server-cwd")


@pytest.fixture(autouse=True)
def _reset_fake(fake_aio: FakeAIO) -> Iterator[None]:
    """Restore the fake deployment between tests."""
    fake_aio.reset()
    yield


def server_env(fake_aio: FakeAIO, **overrides: str) -> dict[str, str]:
    """Build the environment for a server subprocess.

    Args:
        fake_aio: The running fake deployment.
        **overrides: Extra environment variables to set.

    Returns:
        The environment mapping, isolated from any ambient AIO/Jira settings.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AIO_", "JIRA_"))
        and key not in ("READ_ONLY_MODE", "ENABLED_TOOLS", "MCP_VERBOSE")
    }
    env.update(
        {
            "AIO_ENABLED": "true",
            "AIO_URL": fake_aio.url,
            "AIO_PERSONAL_TOKEN": fake_aio.expected_token,
            "PYTHONPATH": str(SRC_ROOT),
        }
    )
    env.update(overrides)
    return env


def make_client(fake_aio: FakeAIO, cwd: Path, **overrides: str) -> Client:
    """Create an MCP client speaking stdio to a fresh server subprocess.

    Args:
        fake_aio: The running fake deployment.
        cwd: Working directory for the subprocess.
        **overrides: Extra environment variables for the subprocess.

    Returns:
        An unconnected FastMCP client.
    """
    return Client(
        StdioTransport(
            command=sys.executable,
            args=["-m", "aio_tests_mcp_server", "--transport", "stdio"],
            env=server_env(fake_aio, **overrides),
            cwd=str(cwd),
        )
    )


@pytest_asyncio.fixture
async def client(fake_aio: FakeAIO, server_cwd: Path) -> AsyncIterator[Client]:
    """Provide a connected MCP client for a read-write server."""
    async with make_client(fake_aio, server_cwd) as connected:
        yield connected
